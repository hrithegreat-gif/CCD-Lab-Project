"""API-level tests for app/extensions.py.

All model/provider behaviour is replaced by explicit injectable test doubles:
no downloads, no torch/transformers/kittentts imports, and no Groq API calls.
These tests verify lifecycle, routing, validation, and state semantics only —
they are NOT real model verification.
"""
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.extensions as extensions_module
from app.adapters.base import (
    GenerationResult,
    OperationTimeout,
    RateLimited,
    SpeechResult,
    TranscriptionResult,
)
from app.adapters.groq_adapter import GroqAuthError, GroqUpstreamError
from app.adapters.kitten_adapter import encode_wav_pcm16
from app.config import Settings
from app.extensions import (
    CAP_GROQ_TEXT,
    CAP_GROQ_TRANSCRIPTION,
    CAP_KITTEN,
    CAP_QWEN,
    DEFAULT_SPEECH_VOICE,
    ExtensionSettings,
    install_extensions,
)
from tests.test_adapters import parse_wav

SECRET = "sk-test-secret-123"


class FakeQwenEngine:
    """Qwen double: gateable load, optional stuck-native-call simulation."""

    def __init__(self, load_gate: threading.Event | None = None,
                 hold_seconds: float = 0.0, order: list | None = None):
        self.ready = False
        self.calls = []
        self.closed = False
        self.close_count = 0
        self._load_gate = load_gate
        self._hold_seconds = hold_seconds
        self._order = order

    def load(self):
        if self._load_gate is not None:
            assert self._load_gate.wait(timeout=10)
        self.ready = True

    def generate(self, prompt, max_new_tokens, deadline_seconds):
        self.calls.append((prompt, max_new_tokens, deadline_seconds))
        if self._hold_seconds:
            # Deliberately ignores the deadline like a stuck native call.
            time.sleep(self._hold_seconds)
        return GenerationResult("fake output", 3, 4, False)

    def close(self):
        self.close_count += 1
        self.closed = True
        self.ready = False
        if self._order is not None:
            self._order.append("qwen_closed")


class FakeKittenEngine:
    available_voices = ("bella", "expr-voice-2-f", DEFAULT_SPEECH_VOICE)

    def __init__(self, order: list | None = None):
        self.ready = False
        self.calls = []
        self.closed = False
        self._order = order

    def load(self):
        self.ready = True

    def generate_speech(self, text, voice):
        self.calls.append((text, voice))
        return SpeechResult(
            encode_wav_pcm16(np.zeros(240, dtype=np.float32), 24000), 24000, 0.01
        )

    def close(self):
        self.closed = True
        if self._order is not None:
            self._order.append("kitten_closed")


class FakeGroqClient:
    def __init__(self, order: list | None = None):
        self.chat_calls = []
        self.transcribe_calls = []
        self.closed = False
        self.chat_error: Exception | None = None
        self.transcribe_error: Exception | None = None
        self.chat_result = GenerationResult("groq says hi", 5, 3, False)
        self.transcribe_result = TranscriptionResult("transcribed words", None)
        self._order = order

    def chat(self, prompt, max_completion_tokens, timeout_seconds=None):
        self.chat_calls.append((prompt, max_completion_tokens))
        if self.chat_error is not None:
            raise self.chat_error
        return self.chat_result

    def transcribe(self, audio, content_type, language=None, timeout_seconds=None):
        self.transcribe_calls.append((audio, content_type, language))
        if self.transcribe_error is not None:
            raise self.transcribe_error
        return self.transcribe_result

    def close(self):
        self.closed = True
        if self._order is not None:
            self._order.append("groq_closed")


def make_app(ext: ExtensionSettings, qwen_factory=None, kitten_factory=None,
             groq_factory=None, order: list | None = None) -> FastAPI:
    application = FastAPI()

    @asynccontextmanager
    async def core_lifespan(app: FastAPI):
        app.state.core_started = True
        if order is not None:
            order.append("core_startup")
        try:
            yield
        finally:
            app.state.core_closed = True
            if order is not None:
                order.append("core_cleanup")

    application.router.lifespan_context = core_lifespan
    install_extensions(
        application, Settings(_env_file=None), extension_settings=ext,
        qwen_engine_factory=qwen_factory, kitten_engine_factory=kitten_factory,
        groq_client_factory=groq_factory,
    )
    return application


def poll_status(client, capability, wanted, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get("/api/capabilities").json()["capabilities"][capability]["state"]
        if state == wanted:
            return state
        time.sleep(0.02)
    return state


def ext_settings(**overrides) -> ExtensionSettings:
    return ExtensionSettings(_env_file=None, **overrides)


def exploding_factory(name: str):
    def factory():
        raise AssertionError(f"{name} factory must not run")
    return factory


# ------------------------------------------------------------ disabled state
def test_disabled_by_default_performs_no_factories_or_optional_imports():
    ext = ext_settings()
    app = make_app(ext, qwen_factory=exploding_factory("qwen"),
                   kitten_factory=exploding_factory("kitten"),
                   groq_factory=exploding_factory("groq"))
    with TestClient(app) as client:
        capabilities = client.get("/api/capabilities").json()["capabilities"]
        assert set(capabilities) == {CAP_QWEN, CAP_KITTEN, CAP_GROQ_TEXT,
                                     CAP_GROQ_TRANSCRIPTION}
        assert all(info["state"] == "disabled" for info in capabilities.values())
        assert client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi"}).status_code == 503
        assert client.post("/api/generate", json={
            "model": "groq-gpt-oss-120b", "input": "hi"}).status_code == 503
        assert client.post("/api/speech", json={"input": "hi"}).status_code == 503
        assert client.post("/api/transcriptions", content=b"RIFF",
                           headers={"content-type": "audio/wav"}).status_code == 503
    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules
    assert "kittentts" not in sys.modules


def test_lifecycle_composes_with_core_and_closes_before_core():
    order: list = []
    ext = ext_settings(enable_qwen=True, enable_groq=True,
                       groq_api_key=SECRET)
    qwen = FakeQwenEngine(order=order)
    groq = FakeGroqClient(order=order)
    app = make_app(ext, qwen_factory=lambda: qwen, groq_factory=lambda: groq,
                   order=order)
    with TestClient(app) as client:
        assert app.state.core_started is True
        assert poll_status(client, CAP_QWEN, "ready") == "ready"
        assert poll_status(client, CAP_GROQ_TEXT, "ready") == "ready"
    assert order[-3:] == ["qwen_closed", "groq_closed", "core_cleanup"]
    assert app.state.core_closed is True
    assert qwen.closed and groq.closed
    assert app.state.extensions.qwen_engine is None
    assert app.state.extensions.groq_client is None


def test_loading_state_until_engine_ready():
    gate = threading.Event()
    ext = ext_settings(enable_qwen=True)
    app = make_app(ext, qwen_factory=lambda: FakeQwenEngine(load_gate=gate))
    with TestClient(app) as client:
        assert poll_status(client, CAP_QWEN, "loading") == "loading"
        blocked = client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi"})
        assert blocked.status_code == 503
        assert blocked.headers.get("retry-after") == "5"
        gate.set()
        assert poll_status(client, CAP_QWEN, "ready") == "ready"


def test_missing_dependency_marks_unavailable_not_ready():
    def missing():
        from app.adapters.base import MissingDependency
        raise MissingDependency("torch")

    ext = ext_settings(enable_qwen=True)
    app = make_app(ext, qwen_factory=missing)
    with TestClient(app) as client:
        assert poll_status(client, CAP_QWEN, "unavailable") == "unavailable"
        info = client.get("/api/capabilities").json()["capabilities"][CAP_QWEN]
        assert info["detail"] == "missing_dependency:torch"
        response = client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi"})
        assert response.status_code == 503
        assert response.json() == {"detail": "Capability is unavailable"}


def test_groq_without_key_is_unavailable_and_constructs_no_client():
    ext = ext_settings(enable_groq=True)
    app = make_app(ext, groq_factory=exploding_factory("groq"))
    with TestClient(app) as client:
        assert poll_status(client, CAP_GROQ_TEXT, "unavailable") == "unavailable"
        info = client.get("/api/capabilities").json()["capabilities"][CAP_GROQ_TEXT]
        assert info["detail"] == "missing_api_key"


# ------------------------------------------------------------- generate
def test_generate_qwen_success_schema_is_not_sentiment_schema():
    qwen = FakeQwenEngine()
    ext = ext_settings(enable_qwen=True)
    app = make_app(ext, qwen_factory=lambda: qwen)
    with TestClient(app) as client:
        poll_status(client, CAP_QWEN, "ready")
        response = client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "Tell me a fact.", "max_output_tokens": 5})
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"status", "model", "provider", "output",
                                "input_tokens", "output_tokens", "truncated_input",
                                "processing_time_ms"}
        assert payload["provider"] == "local"
        assert payload["output"] == "fake output"
        assert "prediction" not in payload and "confidence" not in payload
        assert qwen.calls == [("Tell me a fact.", 5, ext.generate_timeout_seconds)]


def test_generate_rejects_bad_requests():
    qwen = FakeQwenEngine()
    ext = ext_settings(enable_qwen=True, generate_max_input_chars=10)
    app = make_app(ext, qwen_factory=lambda: qwen)
    with TestClient(app) as client:
        poll_status(client, CAP_QWEN, "ready")
        assert client.post("/api/generate", json={
            "model": "qwen4", "input": "hi"}).status_code == 422
        assert client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "   "}).status_code == 422
        assert client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "x" * 11}).status_code == 422
        assert client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi",
            "max_output_tokens": 5000}).status_code == 422
        assert client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi", "temperature": 0.5}).status_code == 422


def test_generate_groq_success_uses_injected_client():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq=True, groq_api_key=SECRET)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TEXT, "ready")
        response = client.post("/api/generate", json={
            "model": "groq-gpt-oss-120b", "input": "hello", "max_output_tokens": 7})
        assert response.status_code == 200
        assert response.json()["provider"] == "groq"
        assert groq.chat_calls == [("hello", 7)]


def test_generate_groq_error_mapping():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq=True, groq_api_key=SECRET)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TEXT, "ready")
        for error, expected in (
            (GroqAuthError("rejected"), 502),
            (GroqUpstreamError("upstream"), 502),
            (RateLimited(9), 429),
            (OperationTimeout("late"), 504),
        ):
            groq.chat_error = error
            response = client.post("/api/generate", json={
                "model": "groq-gpt-oss-120b", "input": "hi"})
            assert response.status_code == expected, (error, response.text)
            if expected == 429:
                assert response.headers["retry-after"] == "9"


# ------------------------------------------------------ capacity and timeout
def test_capacity_returns_429_while_first_request_holds_slot():
    entered = threading.Event()
    release = threading.Event()

    class HoldingEngine(FakeQwenEngine):
        def generate(self, prompt, max_new_tokens, deadline_seconds):
            entered.set()
            assert release.wait(timeout=10)
            return super().generate(prompt, max_new_tokens, deadline_seconds)

    ext = ext_settings(enable_qwen=True, qwen_max_concurrency=1)
    app = make_app(ext, qwen_factory=HoldingEngine)

    import contextlib

    with TestClient(app) as client:
        poll_status(client, CAP_QWEN, "ready")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(lambda: client.post("/api/generate", json={
                "model": "qwen3.5-0.8b", "input": "first"}))
            assert entered.wait(timeout=5)
            second = pool.submit(lambda: client.post("/api/generate", json={
                "model": "qwen3.5-0.8b", "input": "second"}))
            second_response = second.result(timeout=10)
            assert second_response.status_code == 429
            assert second_response.headers.get("retry-after") == "1"
            release.set()
            assert first.result(timeout=10).status_code == 200


def test_timeout_returns_504_but_worker_keeps_semaphore_held(monkeypatch):
    monkeypatch.setattr(extensions_module, "RESPONSE_TIMEOUT_GRACE_SECONDS", 0.1)
    ext = ext_settings(enable_qwen=True, qwen_max_concurrency=1,
                       generate_timeout_seconds=0.2)
    stuck = FakeQwenEngine(hold_seconds=1.0)
    app = make_app(ext, qwen_factory=lambda: stuck)
    semaphore = app.state.extensions.semaphores[CAP_QWEN]
    with TestClient(app) as client:
        poll_status(client, CAP_QWEN, "ready")
        response = client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi"})
        assert response.status_code == 504
        # The native worker is still running; the slot must remain held.
        assert semaphore._value == 0
        deadline = time.monotonic() + 5
        while semaphore._value != 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert semaphore._value == 1
    assert stuck.calls  # the worker did run to completion


# ---------------------------------------------------------------- speech
def test_speech_returns_real_wav_bytes():
    kitten = FakeKittenEngine()
    ext = ext_settings(enable_kitten=True)
    app = make_app(ext, kitten_factory=lambda: kitten)
    with TestClient(app) as client:
        poll_status(client, CAP_KITTEN, "ready")
        response = client.post("/api/speech", json={"input": "Hello world."})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["x-sample-rate"] == "24000"
        params, _ = parse_wav(response.content)
        assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, 24000)
        assert kitten.calls == [("Hello world.", DEFAULT_SPEECH_VOICE)]


def test_speech_rejects_unknown_voice_and_oversized_input():
    kitten = FakeKittenEngine()
    ext = ext_settings(enable_kitten=True, speech_max_input_chars=5)
    app = make_app(ext, kitten_factory=lambda: kitten)
    with TestClient(app) as client:
        poll_status(client, CAP_KITTEN, "ready")
        assert client.post("/api/speech", json={
            "input": "hi", "voice": "nope"}).status_code == 422
        assert client.post("/api/speech", json={
            "input": "too long!"}).status_code == 422


# ---------------------------------------------------------- transcriptions
def test_transcription_forwards_raw_bytes_to_groq():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq_transcription=True, groq_api_key=SECRET)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TRANSCRIPTION, "ready")
        response = client.post("/api/transcriptions", content=b"RIFF-audio-bytes",
                               headers={"content-type": "audio/wav"})
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"status", "model", "text", "duration_seconds",
                                "processing_time_ms"}
        assert payload["model"] == "whisper-large-v3-turbo"
        assert payload["text"] == "transcribed words"
        assert groq.transcribe_calls == [(b"RIFF-audio-bytes", "audio/wav", None)]
        groq.transcribe_calls.clear()
        assert client.post("/api/transcriptions", content=b"x",
                           headers={"content-type": "audio/webm"},
                           params={"language": "de"}).status_code == 200
        assert groq.transcribe_calls == [(b"x", "audio/webm", "de")]


def test_transcription_rejects_unsupported_types_and_urls():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq_transcription=True, groq_api_key=SECRET)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TRANSCRIPTION, "ready")
        # No URL field exists anywhere: JSON/URL-based submissions are rejected.
        assert client.post("/api/transcriptions", content=b'{"audio_url": "https://x"}',
                           headers={"content-type": "application/json"}).status_code == 415
        assert client.post("/api/transcriptions", content=b"RIFF",
                           headers={"content-type": "text/plain"}).status_code == 415
        assert client.post("/api/transcriptions", content=b"").status_code in (415, 422)
        assert client.post("/api/transcriptions", content=b"",
                           headers={"content-type": "audio/wav"}).status_code == 422
        assert client.post("/api/transcriptions", content=b"RIFF",
                           headers={"content-type": "audio/wav"},
                           params={"language": "english"}).status_code == 422


def test_transcription_enforces_size_bound():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq_transcription=True, groq_api_key=SECRET,
                       transcription_max_bytes=1024)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TRANSCRIPTION, "ready")
        response = client.post("/api/transcriptions", content=b"x" * 2048,
                               headers={"content-type": "audio/wav"})
        assert response.status_code == 413
        assert groq.transcribe_calls == []


def test_transcription_error_mapping():
    groq = FakeGroqClient()
    ext = ext_settings(enable_groq_transcription=True, groq_api_key=SECRET)
    app = make_app(ext, groq_factory=lambda: groq)
    with TestClient(app) as client:
        poll_status(client, CAP_GROQ_TRANSCRIPTION, "ready")
        groq.transcribe_error = GroqAuthError("nope")
        assert client.post("/api/transcriptions", content=b"x",
                           headers={"content-type": "audio/wav"}).status_code == 502
        groq.transcribe_error = RateLimited(3)
        limited = client.post("/api/transcriptions", content=b"x",
                              headers={"content-type": "audio/wav"})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "3"


# ---------------------------------------------------------------- settings
def test_double_install_raises():
    app = make_app(ext_settings())
    with pytest.raises(RuntimeError):
        install_extensions(app, Settings(_env_file=None),
                           extension_settings=ext_settings())


def test_extension_settings_defaults_and_secret_masking(monkeypatch):
    ext = ext_settings()
    assert (ext.enable_qwen, ext.enable_kitten, ext.enable_groq,
            ext.enable_groq_transcription) == (False, False, False, False)
    assert ext.groq_api_key is None
    masked = ext_settings(groq_api_key=SECRET)
    assert SECRET not in repr(masked)
    monkeypatch.setenv("ENABLE_QWEN", "true")
    assert ext_settings().enable_qwen is True
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    assert ext_settings().groq_api_key.get_secret_value() == SECRET


def test_groq_api_key_whitespace_is_trimmed_and_blank_means_missing():
    assert ext_settings(groq_api_key="  sk-real  ").groq_api_key.get_secret_value() == "sk-real"
    assert ext_settings(groq_api_key="   ").groq_api_key is None
    ext = ext_settings(enable_groq=True, groq_api_key="   ")
    app = make_app(ext, groq_factory=exploding_factory("groq"))
    with TestClient(app) as client:
        info = client.get("/api/capabilities").json()["capabilities"][CAP_GROQ_TEXT]
        assert info["state"] == "unavailable"
        assert info["detail"] == "missing_api_key"


@pytest.mark.parametrize("field", [
    "load_timeout_seconds", "generate_timeout_seconds", "speech_timeout_seconds",
    "transcription_timeout_seconds", "groq_timeout_seconds",
])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_timeout_settings_reject_nan_and_infinity(field, bad):
    with pytest.raises(Exception):
        ext_settings(**{field: bad})


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_late_failed_load_closes_partial_engine_exactly_once():
    failed = FakeQwenEngine()

    class FailingLoadEngine(FakeQwenEngine):
        def load(self):
            raise RuntimeError("private load failure")

    ext = ext_settings(enable_qwen=True)
    app = make_app(ext, qwen_factory=FailingLoadEngine)
    with TestClient(app) as client:
        assert poll_status(client, CAP_QWEN, "unavailable") == "unavailable"
        info = client.get("/api/capabilities").json()["capabilities"][CAP_QWEN]
        assert info["detail"] == "load_failed"
        assert info["model"] == "Qwen/Qwen3.5-0.8B"  # model/revision preserved
    # The worker closed its own partial engine; runtime never published it.
    runtime = app.state.extensions
    assert wait_until(lambda: runtime.statuses[CAP_QWEN].state.value == "unavailable")
    assert runtime.qwen_engine is None


def test_late_success_after_timeout_never_revives_ready():
    gate = threading.Event()
    gated = FakeQwenEngine(load_gate=gate)
    ext = ext_settings(enable_qwen=True, load_timeout_seconds=0.05)
    app = make_app(ext, qwen_factory=lambda: gated)
    runtime = app.state.extensions
    runtime.startup()
    try:
        assert wait_until(lambda: runtime.statuses[CAP_QWEN].state.value == "unavailable")
        info = runtime.snapshot()[CAP_QWEN]
        assert info.detail == "load_timeout"
        assert info.revision is not None  # model/revision preserved
        gate.set()  # the native load finishes after the timeout
        assert wait_until(lambda: gated.closed and gated.close_count == 1)
        assert runtime.statuses[CAP_QWEN].state.value == "unavailable"
        assert runtime.qwen_engine is None
    finally:
        runtime.shutdown(drain_timeout=0.1)
    assert gated.close_count == 1  # closed exactly once overall


def test_shutdown_during_active_loader_closes_engine_once():
    gate = threading.Event()
    gated = FakeQwenEngine(load_gate=gate)
    ext = ext_settings(enable_qwen=True)
    app = make_app(ext, qwen_factory=lambda: gated)
    runtime = app.state.extensions
    runtime.startup()  # registers and starts the loader before shutdown
    runtime.shutdown(drain_timeout=0.1)
    gate.set()
    assert wait_until(lambda: gated.closed and gated.close_count == 1)
    assert runtime.qwen_engine is None
    assert runtime.statuses[CAP_QWEN].state.value != "ready"


def test_shutdown_defers_engine_close_until_inflight_worker_finishes(monkeypatch):
    monkeypatch.setattr(extensions_module, "RESPONSE_TIMEOUT_GRACE_SECONDS", 0.1)
    stuck = FakeQwenEngine(hold_seconds=1.0)
    ext = ext_settings(enable_qwen=True, qwen_max_concurrency=1,
                       generate_timeout_seconds=0.2)
    app = make_app(ext, qwen_factory=lambda: stuck)
    runtime = app.state.extensions
    with TestClient(app) as client:
        poll_status(client, CAP_QWEN, "ready")
        response = client.post("/api/generate", json={
            "model": "qwen3.5-0.8b", "input": "hi"})
        assert response.status_code == 504
        runtime.shutdown(drain_timeout=0.05)  # bounded: returns while worker runs
        assert runtime.qwen_engine is None  # stashed, but not closed yet
        assert not stuck.closed
    # The last in-flight worker finalizes the stashed engine exactly once.
    assert wait_until(lambda: stuck.closed and stuck.close_count == 1)


# ------------------------------------------------------------ requirement files
def _pin_lines(path):
    lines = []
    for raw in open(path, encoding="utf-8"):
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            lines.append(line)
    return lines


def test_qwen_profile_inherits_all_baseline_pins_except_two_hf_pins():
    baseline = _pin_lines("requirements.txt")
    qwen = _pin_lines("requirements-qwen.txt")
    qwen_set = set(qwen)
    exceptions = {"huggingface-hub==0.30.2", "tokenizers==0.21.1"}
    missing = [pin for pin in baseline if pin not in qwen_set and pin not in exceptions]
    assert missing == [], f"Qwen profile dropped baseline pins: {missing}"
    assert "huggingface-hub==1.33.0" in qwen_set
    assert "tokenizers==0.23.3" in qwen_set
    assert any(pin.startswith("torch==2.14.1+cpu") for pin in qwen)
    assert "transformers==5.19.0" in qwen_set


def test_kitten_profile_includes_full_baseline_and_shared_optionals():
    kitten = open("requirements-kitten.txt", encoding="utf-8").read()
    assert "-r requirements.txt" in kitten
    assert "-r requirements-models.txt" in kitten
    shared = _pin_lines("requirements-models.txt")
    shared_text = "\n".join(shared)
    assert "kittentts @ https://github.com/KittenML/KittenTTS/releases/download/0.8/" in shared_text
    assert "misaki[en]>=0.9.4" in shared
    assert "torch==2.14.1+cpu" in shared_text  # misaki pulls torch; keep it CPU-only
    assert not any(pin.startswith("num2words") for pin in shared)  # from wheel/misaki metadata


def test_transcription_max_bytes_hard_capped_at_10_mib():
    from pydantic import ValidationError

    assert ext_settings().transcription_max_bytes == 10_485_760
    assert ext_settings(transcription_max_bytes=10_485_760).transcription_max_bytes == 10_485_760
    with pytest.raises(ValidationError):
        ext_settings(transcription_max_bytes=10_485_761)
