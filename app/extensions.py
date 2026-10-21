"""Optional model extensions: configuration, lifecycle, and routes.

Called once by the core ``create_app`` as ``install_extensions(application,
settings)``. The extension owns its own validated settings, capability state
machine, bounded concurrency, and lifecycle while composing (never replacing)
the core lifespan:

    async with core_lifespan(app):      # core models load first
        extension startup               # states marked, loaders spawned
        yield
        extension shutdown              # engines/clients closed BEFORE core close

Heavyweight optional dependencies (torch, transformers, kittentts) are imported
only inside engine load/inference paths. With every flag disabled there are no
optional imports, downloads, threads, or network calls. Model repository IDs,
revisions, and Groq endpoints/model IDs are fixed constants in app/adapters/;
nothing security-relevant is accepted from clients.

Lifecycle guarantees:
- Capability status transitions and engine/client publication happen under one
  reentrant lock; a loader's success is accepted only while the capability is
  still ``loading`` (never resurrects a timed-out or shut-down capability).
- Every engine/client instance is closed exactly once: failed/late loads close
  in the loader worker; published assets are stashed at shutdown and closed by
  whichever of {shutdown drain, last in-flight worker} observes quiescence.
- In-flight workers (including 504 orphans) are tracked with a busy counter;
  shutdown drains with a bounded wait and never joins forever.
"""
import asyncio
import logging
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.adapters.base import (
    AdapterUnavailable,
    CapabilityState,
    MissingDependency,
    OperationTimeout,
    RateLimited,
)
from app.adapters.groq_adapter import (
    AUDIO_CONTENT_TYPES,
    GROQ_TRANSCRIBE_MODEL,
    GroqAuthError,
    GroqClient,
    GroqUpstreamError,
)
from app.adapters.kitten_adapter import (
    DEFAULT_SPEECH_VOICE,
    KITTEN_REVISION,
    KittenEngine,
)
from app.adapters.qwen_adapter import QWEN_REVISION, QWEN_REPO_ID, QwenEngine
from app.adapters.schemas import (
    CapabilityInfo,
    CapabilitiesResponse,
    GenerateRequest,
    GenerateResponse,
    SpeechRequest,
    TranscriptionResponse,
)
from app.config import PROJECT_ROOT, Settings
from app.observability import event

CAP_QWEN = "qwen-generation"
CAP_KITTEN = "kitten-speech"
CAP_GROQ_TEXT = "groq-text"
CAP_GROQ_TRANSCRIPTION = "groq-transcription"
ALL_CAPABILITIES = (CAP_QWEN, CAP_KITTEN, CAP_GROQ_TEXT, CAP_GROQ_TRANSCRIPTION)

# Slack added to the response deadline so the cooperative native deadline inside
# an engine normally fires first; the wait_for backstop covers engines stuck in
# a non-interruptible native call. Monkeypatched by tests.
RESPONSE_TIMEOUT_GRACE_SECONDS = 2.0

# Sentinel returned by the bounded worker when the capability semaphore is full.
_CAPACITY_REJECTED = object()


class ExtensionSettings(BaseSettings):
    """Validated settings for optional extensions only.

    env_file mirrors the core Settings (PROJECT_ROOT/.env) so operator
    configuration behaves consistently across core and extensions; callers may
    pass ``_env_file=None`` to read the process environment exclusively.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    enable_qwen: bool = False
    enable_kitten: bool = False
    enable_groq: bool = False
    enable_groq_transcription: bool = False
    groq_api_key: SecretStr | None = None
    generate_max_input_chars: int = Field(default=2000, ge=1, le=8000)
    generate_max_output_tokens: int = Field(default=256, ge=1, le=1024)
    speech_max_input_chars: int = Field(default=1000, ge=1, le=5000)
    transcription_max_bytes: int = Field(default=10_485_760, ge=1024, le=10_485_760)
    qwen_max_input_tokens: int = Field(default=512, ge=8, le=4096)
    qwen_max_concurrency: int = Field(default=1, ge=1, le=4)
    kitten_max_concurrency: int = Field(default=1, ge=1, le=4)
    groq_max_concurrency: int = Field(default=4, ge=1, le=16)
    transcription_max_concurrency: int = Field(default=2, ge=1, le=16)
    load_timeout_seconds: float = Field(
        default=300, gt=0, le=1800, allow_inf_nan=False
    )
    generate_timeout_seconds: float = Field(
        default=60, gt=0, le=600, allow_inf_nan=False
    )
    speech_timeout_seconds: float = Field(
        default=60, gt=0, le=600, allow_inf_nan=False
    )
    transcription_timeout_seconds: float = Field(
        default=60, gt=0, le=600, allow_inf_nan=False
    )
    groq_timeout_seconds: float = Field(
        default=30, gt=0, le=120, allow_inf_nan=False
    )

    @field_validator("groq_api_key", mode="after")
    @classmethod
    def normalize_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Trim whitespace; a blank key is normalized to None (missing)."""
        if value is None:
            return None
        stripped = value.get_secret_value().strip()
        return SecretStr(stripped) if stripped else None


@dataclass(frozen=True)
class CapabilityStatus:
    state: CapabilityState
    model: str | None = None
    revision: str | None = None
    detail: str | None = None
    voices: tuple[str, ...] | None = None


def _require_ready(runtime: "ExtensionRuntime", capability: str) -> None:
    status = runtime.statuses[capability]
    if status.state is CapabilityState.DISABLED:
        raise HTTPException(503, "Capability is disabled")
    if status.state is CapabilityState.LOADING:
        raise HTTPException(
            503, "Capability is loading; retry later", headers={"Retry-After": "5"}
        )
    if status.state is CapabilityState.UNAVAILABLE:
        raise HTTPException(503, "Capability is unavailable")


async def _run_bounded(
    runtime: "ExtensionRuntime", capability: str, job: Callable[[], Any],
    response_timeout: float,
) -> Any:
    """Run ``job`` on a worker thread under the capability semaphore.

    The worker acquires the semaphore itself and releases it in its own
    ``finally``: if the response deadline expires while a native call is still
    running, the 504 is returned but the semaphore stays held by the worker
    until the native call completes (no release on cancelled awaits). Busy
    workers are counted so shutdown defers engine/client close until the last
    worker finishes.
    """
    def wrapped() -> Any:
        semaphore = runtime.semaphores[capability]
        if not semaphore.acquire(blocking=False):
            return _CAPACITY_REJECTED
        runtime.worker_enter()
        should_finalize = False
        try:
            result = job()
        finally:
            # Release the slot only when the native call truly finished; if
            # this is the last in-flight worker at shutdown, it finalizes the
            # stashed assets (bounded shutdown never joins forever).
            should_finalize = runtime.worker_exit()
            semaphore.release()
        if should_finalize:
            runtime._finalize_assets()
        return result

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(wrapped),
            response_timeout + RESPONSE_TIMEOUT_GRACE_SECONDS,
        )
    except asyncio.TimeoutError:
        raise OperationTimeout("Capability deadline exceeded") from None
    if result is _CAPACITY_REJECTED:
        raise HTTPException(
            429, "Capability capacity reached; retry later",
            headers={"Retry-After": "1"},
        )
    return result


class ExtensionRuntime:
    """Owns capability statuses, engines, semaphores, and the loader threads."""

    def __init__(
        self,
        settings: ExtensionSettings,
        cache_dir: Path,
        local_files_only: bool,
        inference_threads: int,
        qwen_engine_factory: Callable[[], Any] | None = None,
        kitten_engine_factory: Callable[[], Any] | None = None,
        groq_client_factory: Callable[[], Any] | None = None,
    ):
        self.settings = settings
        self.statuses: dict[str, CapabilityStatus] = {
            capability: CapabilityStatus(state=CapabilityState.DISABLED)
            for capability in ALL_CAPABILITIES
        }
        self.semaphores = {
            CAP_QWEN: threading.BoundedSemaphore(settings.qwen_max_concurrency),
            CAP_KITTEN: threading.BoundedSemaphore(settings.kitten_max_concurrency),
            CAP_GROQ_TEXT: threading.BoundedSemaphore(settings.groq_max_concurrency),
            CAP_GROQ_TRANSCRIPTION: threading.BoundedSemaphore(
                settings.transcription_max_concurrency
            ),
        }
        self.qwen_engine: Any | None = None
        self.kitten_engine: Any | None = None
        self.groq_client: Any | None = None
        self.kitten_voices: tuple[str, ...] | None = None
        self._cache_dir = Path(cache_dir)
        self._local_files_only = local_files_only
        self._inference_threads = inference_threads
        self._qwen_engine_factory = qwen_engine_factory
        self._kitten_engine_factory = kitten_engine_factory
        self._groq_client_factory = groq_client_factory
        # Reentrant: helpers below may lock while a Condition wait holds it.
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._busy_workers = 0
        self._assets_to_close: tuple[Any, ...] = ()
        self._loaders: list[tuple[threading.Thread, threading.Timer]] = []
        self._shutdown = False
        self._closed = False

    # ----- status helpers -------------------------------------------------
    def _set_status(self, capability: str, **fields: Any) -> None:
        current = self.statuses[capability]
        self.statuses[capability] = CapabilityStatus(
            state=fields.get("state", current.state),
            model=fields.get("model", current.model),
            revision=fields.get("revision", current.revision),
            detail=fields.get("detail", current.detail),
            voices=fields.get("voices", current.voices),
        )

    def snapshot(self) -> dict[str, CapabilityStatus]:
        with self._lock:
            return dict(self.statuses)

    def _mark_load_timeout(self, capability: str) -> None:
        with self._lock:
            if self.statuses[capability].state is CapabilityState.LOADING:
                # Preserve model/revision; only the state/detail change.
                self._set_status(
                    capability, state=CapabilityState.UNAVAILABLE, detail="load_timeout"
                )

    def _fail_load(self, capability: str, detail: str) -> None:
        with self._lock:
            if self.statuses[capability].state is CapabilityState.LOADING:
                self._set_status(
                    capability, state=CapabilityState.UNAVAILABLE, detail=detail
                )

    # ----- worker accounting ----------------------------------------------
    def worker_enter(self) -> None:
        with self._idle:
            self._busy_workers += 1

    def worker_exit(self) -> bool:
        """Count a finished worker; True when it is the last one at shutdown."""
        with self._idle:
            self._busy_workers -= 1
            if self._busy_workers == 0 and self._shutdown:
                self._idle.notify_all()
                return True
            return False

    # ----- asset ownership ------------------------------------------------
    @staticmethod
    def _close_quietly(asset: Any) -> None:
        if asset is None:
            return
        close = getattr(asset, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as exc:
            event("extension_close_failed", logging.ERROR,
                  error_type=type(exc).__name__)

    def _stash_assets_locked(self) -> None:
        """Move published assets out of the live fields under the lock."""
        assets = [
            asset for asset in
            (self.qwen_engine, self.kitten_engine, self.groq_client)
            if asset is not None
        ]
        self.qwen_engine = None
        self.kitten_engine = None
        self.kitten_voices = None
        self.groq_client = None
        self._assets_to_close = tuple(assets)

    def _finalize_assets(self) -> None:
        """Close stashed assets exactly once (swap empties under the lock)."""
        with self._lock:
            assets = self._assets_to_close
            self._assets_to_close = ()
        for asset in assets:
            self._close_quietly(asset)

    # ----- lifecycle ------------------------------------------------------
    def startup(self) -> None:
        """Exception-safe: never aborts core startup."""
        try:
            self._startup()
        except Exception as exc:
            event("extension_startup_failed", logging.ERROR, error_type=type(exc).__name__)

    def _startup(self) -> None:
        settings = self.settings
        needs_groq = settings.enable_groq or settings.enable_groq_transcription
        if needs_groq and settings.groq_api_key is not None:
            if self._groq_client_factory is not None:
                self.groq_client = self._groq_client_factory()
            else:
                self.groq_client = GroqClient(
                    api_key=settings.groq_api_key,
                    timeout_seconds=settings.groq_timeout_seconds,
                )
        for flag, capability in (
            (settings.enable_groq, CAP_GROQ_TEXT),
            (settings.enable_groq_transcription, CAP_GROQ_TRANSCRIPTION),
        ):
            if not flag:
                continue
            if settings.groq_api_key is None:
                self._set_status(
                    capability, state=CapabilityState.UNAVAILABLE,
                    model="groq", detail="missing_api_key",
                )
            else:
                self._set_status(
                    capability, state=CapabilityState.READY,
                    model="groq",
                )
        if settings.enable_qwen:
            self._set_status(
                CAP_QWEN, state=CapabilityState.LOADING,
                model=QWEN_REPO_ID, revision=QWEN_REVISION,
            )
            self._spawn_loader(
                CAP_QWEN,
                lambda: (self._qwen_engine_factory or self._build_qwen_engine)(),
            )
        if settings.enable_kitten:
            self._set_status(
                CAP_KITTEN, state=CapabilityState.LOADING,
                model="kitten-tts-mini-0.8", revision=KITTEN_REVISION,
            )
            self._spawn_loader(
                CAP_KITTEN,
                lambda: (self._kitten_engine_factory or self._build_kitten_engine)(),
            )

    def _build_qwen_engine(self) -> Any:
        return QwenEngine(
            cache_dir=self._cache_dir,
            local_files_only=self._local_files_only,
            max_input_tokens=self.settings.qwen_max_input_tokens,
            inference_threads=self._inference_threads,
        )

    def _build_kitten_engine(self) -> Any:
        return KittenEngine(
            cache_dir=self._cache_dir,
            local_files_only=self._local_files_only,
        )

    def _spawn_loader(self, capability: str, loader: Callable[[], Any]) -> None:
        timer = threading.Timer(
            self.settings.load_timeout_seconds, self._mark_load_timeout,
            args=(capability,),
        )
        timer.daemon = True

        def run(timer: threading.Timer) -> None:
            engine: Any | None = None
            try:
                engine = loader()
                load = getattr(engine, "load", None)
                if load is not None:
                    load()
            except MissingDependency as exc:
                timer.cancel()
                self._fail_load(capability, f"missing_dependency:{exc.module}")
                self._close_quietly(engine)
                return
            except AdapterUnavailable as exc:
                timer.cancel()
                self._fail_load(capability, str(exc) or "unavailable")
                self._close_quietly(engine)
                return
            except OperationTimeout:
                timer.cancel()
                self._fail_load(capability, "load_timeout")
                self._close_quietly(engine)
                return
            except Exception as exc:
                timer.cancel()
                # Sanitized: error type only, never exception messages.
                event("extension_load_failed", logging.ERROR,
                      capability=capability, error_type=type(exc).__name__)
                self._fail_load(capability, "load_failed")
                self._close_quietly(engine)
                return
            timer.cancel()
            # Accept a successful load only while the capability is still
            # loading: a timed-out or shut-down capability is never revived,
            # and the just-finished engine is closed exactly once here.
            with self._idle:
                accepted = (
                    not self._shutdown
                    and self.statuses[capability].state is CapabilityState.LOADING
                )
                if accepted:
                    if capability == CAP_QWEN:
                        self.qwen_engine = engine
                    elif capability == CAP_KITTEN:
                        self.kitten_engine = engine
                    voices = getattr(engine, "available_voices", None)
                    if capability == CAP_KITTEN:
                        self.kitten_voices = tuple(voices) if voices else None
                    self._set_status(
                        capability, state=CapabilityState.READY, detail=None,
                        voices=tuple(voices) if voices else None,
                    )
            if not accepted:
                self._close_quietly(engine)

        thread = threading.Thread(
            target=run, args=(timer,), name=f"extension-load-{capability}", daemon=True
        )
        with self._lock:
            if self._shutdown:
                timer.cancel()
                return
            self._loaders.append((thread, timer))
        timer.start()
        thread.start()

    def shutdown(self, drain_timeout: float = 5.0) -> None:
        """Bounded, idempotent cleanup that runs before core model close.

        Assets are stashed immediately; if workers are still in flight
        (including 504 orphans) the close is deferred to the last worker via a
        bounded drain instead of joining forever.
        """
        with self._idle:
            if self._closed:
                return
            self._closed = True
            self._shutdown = True
            self._stash_assets_locked()
            if self._busy_workers:
                # Bounded drain; the final worker finalizes the stashed assets.
                self._idle.wait(drain_timeout)
            finalize_now = self._busy_workers == 0
        for thread, timer in self._loaders:
            timer.cancel()
            if thread.is_alive():
                thread.join(timeout=2.0)
        if finalize_now:
            self._finalize_assets()


def install_extensions(
    application: FastAPI,
    settings: Settings,
    *,
    extension_settings: ExtensionSettings | None = None,
    qwen_engine_factory: Callable[[], Any] | None = None,
    kitten_engine_factory: Callable[[], Any] | None = None,
    groq_client_factory: Callable[[], Any] | None = None,
) -> None:
    """Register optional capability routes and compose the core lifespan.

    Call once after the FastAPI application is constructed. Raises RuntimeError
    on a second call instead of silently duplicating routes.
    """
    if getattr(application.state, "extensions_installed", False):
        raise RuntimeError("install_extensions must be called exactly once per application")
    application.state.extensions_installed = True

    ext = extension_settings or ExtensionSettings()
    runtime = ExtensionRuntime(
        settings=ext,
        cache_dir=settings.model_cache_dir,
        local_files_only=settings.model_local_files_only,
        inference_threads=settings.inference_threads,
        qwen_engine_factory=qwen_engine_factory,
        kitten_engine_factory=kitten_engine_factory,
        groq_client_factory=groq_client_factory,
    )
    application.state.extensions = runtime

    core_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def composed_lifespan(app: FastAPI):
        async with core_lifespan(app):
            app.state.extensions.startup()
            try:
                yield
            finally:
                # Extension cleanup completes before the core lifespan close.
                app.state.extensions.shutdown()

    application.router.lifespan_context = composed_lifespan

    @application.get("/api/capabilities", response_model=CapabilitiesResponse,
                     tags=["capabilities"])
    def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(capabilities={
            name: CapabilityInfo(
                state=status.state.value, model=status.model,
                revision=status.revision, detail=status.detail,
                voices=list(status.voices) if status.voices else None,
            )
            for name, status in runtime.snapshot().items()
        })

    @application.post("/api/generate", response_model=GenerateResponse, tags=["generation"],
                      responses={code: {"detail": {}} for code in (422, 429, 500, 502, 503, 504)})
    async def generate(body: GenerateRequest):
        started = perf_counter()
        code = 500
        try:
            if body.model == "qwen3.5-0.8b":
                capability, provider = CAP_QWEN, "local"
            else:
                capability, provider = CAP_GROQ_TEXT, "groq"
            _require_ready(runtime, capability)
            if len(body.input) > ext.generate_max_input_chars:
                raise HTTPException(
                    422, f"Input exceeds {ext.generate_max_input_chars} characters"
                )
            max_output = min(
                body.max_output_tokens or ext.generate_max_output_tokens,
                ext.generate_max_output_tokens,
            )
            if provider == "local":
                engine = runtime.qwen_engine
                if engine is None or not getattr(engine, "ready", True):
                    raise HTTPException(503, "Capability is unavailable")

                def job() -> Any:
                    return engine.generate(
                        body.input, max_new_tokens=max_output,
                        deadline_seconds=ext.generate_timeout_seconds,
                    )
            else:
                client = runtime.groq_client
                if client is None:
                    raise HTTPException(503, "Capability is unavailable")

                def job() -> Any:
                    return client.chat(
                        body.input, max_completion_tokens=max_output,
                        timeout_seconds=ext.groq_timeout_seconds,
                    )
            result = await _run_bounded(runtime, capability, job, ext.generate_timeout_seconds)
            response = GenerateResponse(
                model=body.model, provider=provider, output=result.output,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                truncated_input=result.truncated_input,
                processing_time_ms=round((perf_counter() - started) * 1000, 3),
            )
            code = 200
            return response
        except HTTPException as exc:
            code = exc.status_code
            raise
        except OperationTimeout:
            code = 504
            raise HTTPException(504, "Generation timed out") from None
        except RateLimited as exc:
            code = 429
            raise HTTPException(
                429, "Upstream capacity reached; retry later",
                headers={"Retry-After": str(exc.retry_after)},
            ) from None
        except GroqAuthError:
            code = 502
            raise HTTPException(502, "Upstream provider rejected credentials") from None
        except GroqUpstreamError:
            code = 502
            raise HTTPException(502, "Upstream generation failed") from None
        except Exception as exc:
            event("extension_generate_failed", logging.ERROR, error_type=type(exc).__name__)
            code = 500
            raise HTTPException(500, "Generation failed") from None
        finally:
            event("extension_generate", model=body.model, status_code=code,
                  processing_time_ms=round((perf_counter() - started) * 1000, 3))

    @application.post("/api/speech", tags=["speech"],
                      responses={code: {"detail": {}} for code in (422, 429, 500, 503, 504)})
    async def speech(body: SpeechRequest):
        started = perf_counter()
        code = 500
        try:
            _require_ready(runtime, CAP_KITTEN)
            if len(body.input) > ext.speech_max_input_chars:
                raise HTTPException(
                    422, f"Input exceeds {ext.speech_max_input_chars} characters"
                )
            engine = runtime.kitten_engine
            if engine is None or not getattr(engine, "ready", True):
                raise HTTPException(503, "Capability is unavailable")
            voice = body.voice or DEFAULT_SPEECH_VOICE
            if runtime.kitten_voices and voice not in runtime.kitten_voices:
                raise HTTPException(422, "Unknown voice")
            result = await _run_bounded(
                runtime, CAP_KITTEN,
                lambda: engine.generate_speech(body.input, voice),
                ext.speech_timeout_seconds,
            )
            code = 200
            return Response(
                content=result.wav_bytes,
                media_type="audio/wav",
                headers={
                    "X-Sample-Rate": str(result.sample_rate),
                    "X-Processing-Time-Ms": str(round((perf_counter() - started) * 1000, 3)),
                },
            )
        except HTTPException as exc:
            code = exc.status_code
            raise
        except OperationTimeout:
            code = 504
            raise HTTPException(504, "Speech synthesis timed out") from None
        except Exception as exc:
            event("extension_speech_failed", logging.ERROR, error_type=type(exc).__name__)
            code = 500
            raise HTTPException(500, "Speech synthesis failed") from None
        finally:
            event("extension_speech", status_code=code,
                  processing_time_ms=round((perf_counter() - started) * 1000, 3))

    @application.post("/api/transcriptions", response_model=TranscriptionResponse,
                      tags=["transcriptions"],
                      responses={code: {"detail": {}} for code in (413, 415, 422, 429, 500, 502, 503, 504)})
    async def transcribe(request: Request, language: str | None = Query(default=None)):
        started = perf_counter()
        code = 500
        audio: bytes = b""
        content_type = "unknown"
        try:
            _require_ready(runtime, CAP_GROQ_TRANSCRIPTION)
            content_type = (request.headers.get("content-type") or "").split(";", 1)[0]
            content_type = content_type.strip().lower()
            if content_type not in AUDIO_CONTENT_TYPES:
                raise HTTPException(
                    415,
                    "Unsupported audio content type; use one of: "
                    + ", ".join(sorted(AUDIO_CONTENT_TYPES)),
                )
            if language is not None:
                if not re.fullmatch(r"[a-zA-Z]{2,3}", language):
                    raise HTTPException(422, "Invalid language code")
                language = language.lower()
            limit = ext.transcription_max_bytes
            declared = request.headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > limit:
                raise HTTPException(413, f"Audio exceeds {limit} bytes")
            chunks: list[bytes] = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Audio exceeds {limit} bytes")
                chunks.append(chunk)
            audio = b"".join(chunks)
            if not audio:
                raise HTTPException(422, "Empty audio body")
            client = runtime.groq_client
            if client is None:
                raise HTTPException(503, "Capability is unavailable")
            result = await _run_bounded(
                runtime, CAP_GROQ_TRANSCRIPTION,
                lambda: client.transcribe(
                    audio, content_type=content_type, language=language,
                    timeout_seconds=ext.groq_timeout_seconds,
                ),
                ext.transcription_timeout_seconds,
            )
            response = TranscriptionResponse(
                model=GROQ_TRANSCRIBE_MODEL, text=result.text,
                duration_seconds=result.duration_seconds,
                processing_time_ms=round((perf_counter() - started) * 1000, 3),
            )
            code = 200
            return response
        except HTTPException as exc:
            code = exc.status_code
            raise
        except OperationTimeout:
            code = 504
            raise HTTPException(504, "Transcription timed out") from None
        except RateLimited as exc:
            code = 429
            raise HTTPException(
                429, "Upstream capacity reached; retry later",
                headers={"Retry-After": str(exc.retry_after)},
            ) from None
        except GroqAuthError:
            code = 502
            raise HTTPException(502, "Upstream provider rejected credentials") from None
        except GroqUpstreamError:
            code = 502
            raise HTTPException(502, "Upstream transcription failed") from None
        except Exception as exc:
            event("extension_transcription_failed", logging.ERROR,
                  error_type=type(exc).__name__)
            code = 500
            raise HTTPException(500, "Transcription failed") from None
        finally:
            event("extension_transcription", content_type=content_type,
                  audio_bytes=len(audio), status_code=code,
                  processing_time_ms=round((perf_counter() - started) * 1000, 3))

    return None
