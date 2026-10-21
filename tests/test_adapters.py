"""Unit tests for the optional adapters.

These tests use explicit test doubles (httpx.MockTransport clients and stub
engines). They do NOT download model weights, import torch/transformers/
kittentts, call the Groq API, or verify real model behaviour in any way.
"""
import io
import wave

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

import app.adapters.qwen_adapter as qwen_module
from app.adapters.base import (
    MissingDependency,
    OperationTimeout,
    RateLimited,
)
from app.adapters.groq_adapter import (
    AUDIO_CONTENT_TYPES,
    GROQ_API_BASE,
    GROQ_TEXT_MODEL,
    GROQ_TRANSCRIBE_MODEL,
    GroqAuthError,
    GroqClient,
    GroqUpstreamError,
)
from app.adapters.kitten_adapter import (
    CANONICAL_VOICES,
    KITTEN_SAMPLE_RATE,
    KittenEngine,
    encode_wav_pcm16,
)
from app.adapters.qwen_adapter import QWEN_REPO_ID, QWEN_REVISION, QwenEngine

SECRET = "sk-test-secret-123"


# --------------------------------------------------------------------- WAV
def parse_wav(payload: bytes):
    handle = wave.open(io.BytesIO(payload), "rb")
    params = handle.getparams()
    frames = handle.readframes(params.nframes)
    handle.close()
    return params, np.frombuffer(frames, dtype="<i2")


def test_encode_wav_pcm16_produces_mono_16bit_wav():
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    payload = encode_wav_pcm16(samples, 24000)
    params, values = parse_wav(payload)
    assert params.nchannels == 1
    assert params.sampwidth == 2
    assert params.framerate == 24000
    assert values.tolist() == [0, 16383, -16383, 32767, -32767]


def test_encode_wav_pcm16_rejects_empty_and_non_finite():
    with pytest.raises(ValueError):
        encode_wav_pcm16(np.zeros(0, dtype=np.float32), 24000)
    with pytest.raises(ValueError):
        encode_wav_pcm16(np.array([np.inf], dtype=np.float32), 24000)


# ------------------------------------------------------------------- Qwen
def test_qwen_allowlist_constants_are_pinned():
    assert QWEN_REPO_ID == "Qwen/Qwen3.5-0.8B"
    assert len(QWEN_REVISION) == 40
    int(QWEN_REVISION, 16)  # immutable commit SHA


def test_qwen_load_reports_missing_dependency_without_importing(tmp_path, monkeypatch):
    def require_missing(modules):
        raise MissingDependency(modules[0])

    monkeypatch.setattr(qwen_module, "require_modules", require_missing)
    engine = QwenEngine(cache_dir=tmp_path, local_files_only=True,
                        max_input_tokens=512, inference_threads=1)
    with pytest.raises(MissingDependency) as excinfo:
        engine.load()
    assert excinfo.value.module in ("transformers", "torch")
    assert engine.ready is False


def test_qwen_generate_requires_loaded_engine(tmp_path):
    engine = QwenEngine(cache_dir=tmp_path, local_files_only=True,
                        max_input_tokens=512, inference_threads=1)
    with pytest.raises(RuntimeError):
        engine.generate("hi", max_new_tokens=4, deadline_seconds=1.0)


# ----------------------------------------------------------------- Kitten
def test_kitten_voice_allowlist_matches_pinned_model():
    assert len(CANONICAL_VOICES) == 8
    assert KITTEN_SAMPLE_RATE == 24000
    assert all(v.startswith("expr-voice-") for v in CANONICAL_VOICES)


def test_kitten_generate_resolves_alias_and_rejects_unknown_voice():
    engine = KittenEngine(cache_dir="unused", local_files_only=True)
    engine._voice_lookup = {"bella": "expr-voice-2-f", "expr-voice-2-f": "expr-voice-2-f"}
    seen = {}

    class StubTTS:
        def generate(self, text, voice, speed):
            seen["voice"] = voice
            return np.zeros(240, dtype=np.float32)

    engine._tts = StubTTS()
    result = engine.generate_speech("Hello there.", "BELLA")
    assert seen["voice"] == "expr-voice-2-f"
    assert result.sample_rate == 24000
    assert result.wav_bytes.startswith(b"RIFF")
    with pytest.raises(ValueError):
        engine.generate_speech("Hello there.", "not-a-voice")


def test_kitten_generate_requires_loaded_engine():
    engine = KittenEngine(cache_dir="unused", local_files_only=True)
    with pytest.raises(RuntimeError):
        engine.generate_speech("hi", "expr-voice-2-m")


# ------------------------------------------------------------------- Groq
def groq_handler(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=GROQ_API_BASE)


def test_groq_chat_request_shape_and_result_mapping():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.read()
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hello!"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        })

    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(handler))
    result = client.chat("Say hi", max_completion_tokens=64)
    assert result.output == "hello!"
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    assert result.truncated_input is False
    assert captured["url"] == f"{GROQ_API_BASE}/chat/completions"
    assert captured["auth"] == f"Bearer {SECRET}"
    assert b'"model":"' + GROQ_TEXT_MODEL.encode() in captured["body"]
    assert b'"max_completion_tokens":64' in captured["body"]
    client.close()


def test_groq_transcribe_sends_multipart_with_pinned_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "transcribed words"})

    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(handler))
    result = client.transcribe(b"RIFF-bytes", content_type="audio/wav", language="de")
    assert result.text == "transcribed words"
    assert captured["url"] == f"{GROQ_API_BASE}/audio/transcriptions"
    assert b'name="model"\r\n\r\n' + GROQ_TRANSCRIBE_MODEL.encode() in captured["body"]
    assert b'name="language"\r\n\r\nde' in captured["body"]
    assert b'name="file"; filename="audio.wav"' in captured["body"]
    assert b"RIFF-bytes" in captured["body"]
    client.close()


def test_groq_transcribe_rejects_unsupported_content_type():
    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(
        lambda request: httpx.Response(500)))
    with pytest.raises(ValueError):
        client.transcribe(b"x", content_type="text/plain")


@pytest.mark.parametrize("status,expected", [
    (401, GroqAuthError), (403, GroqAuthError), (500, GroqUpstreamError),
    (503, GroqUpstreamError),
])
def test_groq_status_error_mapping(status, expected):
    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(
        lambda request: httpx.Response(status, json={"error": {"message": "boom"}})))
    with pytest.raises(expected) as excinfo:
        client.chat("hi", max_completion_tokens=8)
    assert SECRET not in str(excinfo.value)
    assert "boom" not in str(excinfo.value)
    client.close()


def test_groq_error_codes_are_allowlisted_only():
    def respond(code):
        return GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(
            lambda request: httpx.Response(500, json={"error": {"code": code}})))

    trusted = respond("invalid_api_key")
    with pytest.raises(GroqUpstreamError) as excinfo:
        trusted.chat("hi", max_completion_tokens=8)
    assert "(invalid_api_key)" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)

    untrusted = respond("sk-leak-or-user-input")
    with pytest.raises(GroqUpstreamError) as excinfo:
        untrusted.chat("hi", max_completion_tokens=8)
    assert "sk-leak-or-user-input" not in str(excinfo.value)
    assert str(excinfo.value).endswith("(HTTP 500)")


def test_groq_rate_limit_maps_retry_after():
    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(
        lambda request: httpx.Response(429, headers={"Retry-After": "9"}, json={})))
    with pytest.raises(RateLimited) as excinfo:
        client.chat("hi", max_completion_tokens=8)
    assert excinfo.value.retry_after == 9
    client.close()


def test_groq_timeout_maps_to_operation_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(handler))
    with pytest.raises(OperationTimeout):
        client.chat("hi", max_completion_tokens=8)
    client.close()


def test_groq_malformed_json_maps_to_upstream_error():
    client = GroqClient(SecretStr(SECRET), client_factory=lambda: groq_handler(
        lambda request: httpx.Response(200, json={"unexpected": True})))
    with pytest.raises(GroqUpstreamError):
        client.chat("hi", max_completion_tokens=8)
    client.close()


def test_groq_content_type_map_only_contains_documented_types():
    documented = {"wav", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "webm", "flac"}
    for extension in AUDIO_CONTENT_TYPES.values():
        assert extension.removeprefix("audio.") in documented
    assert "audio/wav" in AUDIO_CONTENT_TYPES
    assert "text/plain" not in AUDIO_CONTENT_TYPES
