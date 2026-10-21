"""Groq text-generation and Whisper transcription clients (OpenAI-compatible).

Verified sources (2026-10-13):
- https://console.groq.com/docs/models  ->  ``openai/gpt-oss-120b`` (131,072
  context, 65,536 max completion tokens)
- https://console.groq.com/docs/speech-to-text  ->  ``whisper-large-v3-turbo``,
  POST https://api.groq.com/openai/v1/audio/transcriptions, 25 MB free tier,
  supported uploads: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm,
  response formats json/verbose_json/text.

The upstream base URL and model IDs are fixed module constants. The API key is
held as a SecretStr and is never included in exceptions or logs. Upstream error
bodies are never echoed; only the HTTP status and Groq's short error ``code``.
"""
import threading
from typing import Callable

import httpx
from pydantic import SecretStr

from app.adapters.base import (
    GenerationResult,
    OperationTimeout,
    RateLimited,
    TranscriptionResult,
)

GROQ_API_BASE = "https://api.groq.com/openai/v1"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"

# Fixed allowlist of Groq error codes considered safe to surface to clients.
# Upstream bodies and codes are untrusted data: any code outside this set is
# reduced to the bare HTTP status so nothing upstream-controlled (keys, user
# input, vendor text) can leak through error messages.
SAFE_GROQ_ERROR_CODES = frozenset({
    "invalid_api_key",
    "model_not_found",
    "rate_limit_exceeded",
    "quota_exceeded",
    "tokens_exceeded",
    "context_length_exceeded",
    "file_too_large",
    "invalid_request_error",
    "service_unavailable",
    "internal_error",
    "billing_not_active",
})


class GroqAuthError(RuntimeError):
    """The configured credentials were rejected by the provider."""


class GroqUpstreamError(RuntimeError):
    """A non-timeout failure talking to the provider."""


# Map of accepted request content types -> upload filename for Groq.
# Every extension maps to one of Groq's documented supported file types.
AUDIO_CONTENT_TYPES: dict[str, str] = {
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
    "audio/wave": "audio.wav",
    "audio/vnd.wave": "audio.wav",
    "audio/mpeg": "audio.mp3",
    "audio/mp3": "audio.mp3",
    "audio/mpga": "audio.mpga",
    "audio/flac": "audio.flac",
    "audio/x-flac": "audio.flac",
    "audio/ogg": "audio.ogg",
    "application/ogg": "audio.ogg",
    "audio/webm": "audio.webm",
    "audio/mp4": "audio.m4a",
    "audio/m4a": "audio.m4a",
    "audio/x-m4a": "audio.m4a",
}


def _safe_upstream_detail(response: httpx.Response) -> str:
    """HTTP status plus an allowlisted Groq error code; never arbitrary data."""
    code = ""
    try:
        candidate = response.json().get("error", {}).get("code", "")
        if isinstance(candidate, str) and candidate in SAFE_GROQ_ERROR_CODES:
            code = candidate
    except ValueError:
        pass
    return f"HTTP {response.status_code}" + (f" ({code})" if code else "")


class GroqClient:
    """Thin httpx client for the two allowlisted Groq endpoints."""

    def __init__(
        self,
        api_key: SecretStr,
        client_factory: Callable[[], httpx.Client] | None = None,
        timeout_seconds: float = 30.0,
    ):
        self._api_key = api_key
        self._client: httpx.Client | None = None
        self._timeout_seconds = float(timeout_seconds)
        self._client_factory = client_factory
        self._client_lock = threading.Lock()

    def _ensure_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                if self._client_factory is not None:
                    self._client = self._client_factory()
                else:
                    self._client = httpx.Client(
                        base_url=GROQ_API_BASE,
                        timeout=httpx.Timeout(
                            self._timeout_seconds,
                            connect=min(10.0, self._timeout_seconds),
                        ),
                    )
            return self._client

    def _post(self, path: str, timeout_seconds: float | None, **kwargs) -> httpx.Response:
        client = self._ensure_client()
        # Attached per request so injected test clients need no header setup.
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            return client.post(
                path, headers=headers, timeout=timeout_seconds or self._timeout_seconds,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise OperationTimeout("Groq request timed out") from exc
        except httpx.HTTPError as exc:
            raise GroqUpstreamError("Groq request failed") from exc

    @staticmethod
    def _map_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code in (401, 403):
            raise GroqAuthError(
                f"Groq rejected credentials ({_safe_upstream_detail(response)})"
            )
        if response.status_code == 429:
            retry_after = 5
            raw = response.headers.get("retry-after")
            if raw and raw.isdigit():
                retry_after = int(raw)
            raise RateLimited(retry_after)
        raise GroqUpstreamError(f"Groq upstream error ({_safe_upstream_detail(response)})")

    def chat(
        self, prompt: str, max_completion_tokens: int, timeout_seconds: float | None = None
    ) -> GenerationResult:
        response = self._post(
            "/chat/completions",
            timeout_seconds,
            json={
                "model": GROQ_TEXT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": max_completion_tokens,
                "temperature": 0,
            },
        )
        self._map_status(response)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
            return GenerationResult(
                output=str(content or ""),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                truncated_input=False,
            )
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise GroqUpstreamError("Groq response was malformed") from exc

    def transcribe(
        self,
        audio: bytes,
        content_type: str,
        language: str | None = None,
        timeout_seconds: float | None = None,
    ) -> TranscriptionResult:
        filename = AUDIO_CONTENT_TYPES.get(content_type)
        if filename is None:
            raise ValueError("unsupported_audio_content_type")
        data: dict[str, str] = {"model": GROQ_TRANSCRIBE_MODEL, "response_format": "json"}
        if language:
            data["language"] = language
        response = self._post(
            "/audio/transcriptions",
            timeout_seconds,
            files={"file": (filename, audio, content_type)},
            data=data,
        )
        self._map_status(response)
        try:
            text = str(response.json()["text"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GroqUpstreamError("Groq response was malformed") from exc
        return TranscriptionResult(text=text, duration_seconds=None)

    def close(self) -> None:
        with self._client_lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()
