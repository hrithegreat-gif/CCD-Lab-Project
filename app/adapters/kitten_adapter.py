"""Kitten TTS Mini 0.8 CPU speech synthesis via the official ONNX implementation.

Verified sources (2026-10-13):
- https://huggingface.co/KittenML/kitten-tts-mini-0.8 (80M params, ~79MB ONNX;
  commit c0272566..., files: config.json, kitten_tts_mini_v0_8.onnx, voices.npz)
- Official wheel https://github.com/KittenML/KittenTTS/releases/download/0.8/
  kittentts-0.8.0-py3-none-any.whl (inspected directly): the legacy ``kittentts``
  package exposes ``kittentts.onnx_model.KittenTTS_1_Onnx`` and downloads assets
  without revision pinning, so this adapter pins the files itself via
  ``hf_hub_download(..., revision=...)`` and constructs that ONNX class directly.

The unrelated PyPI package ``kittentts`` (0.1.x by a third party) shares the
import name but has different code; only the pinned KittenML wheel is supported.
"""
import gc
import io
import json
import wave
from pathlib import Path

import numpy as np

from app.adapters.base import (
    AdapterUnavailable,
    SpeechResult,
    require_modules,
)

KITTEN_REPO_ID = "KittenML/kitten-tts-mini-0.8"
# Immutable allowlisted revision (verified 2026-10-13 via the HF API).
KITTEN_REVISION = "c02725660cea441db4c383af69f1f26f5cd00947"
KITTEN_WHEEL_URL = (
    "https://github.com/KittenML/KittenTTS/releases/download/0.8/"
    "kittentts-0.8.0-py3-none-any.whl"
)
KITTEN_MODEL_FILENAME = "kitten_tts_mini_v0_8.onnx"
KITTEN_VOICES_FILENAME = "voices.npz"
KITTEN_SAMPLE_RATE = 24000
# Hardcoded in the pinned wheel; matched against the downloaded config.
CANONICAL_VOICES = (
    "expr-voice-2-m", "expr-voice-2-f",
    "expr-voice-3-m", "expr-voice-3-f",
    "expr-voice-4-m", "expr-voice-4-f",
    "expr-voice-5-m", "expr-voice-5-f",
)
# From config.json at the pinned revision.
DEFAULT_SPEECH_VOICE = "expr-voice-5-m"
_REQUIRED_MODULES = ("kittentts", "phonemizer")


def encode_wav_pcm16(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode float samples in [-1, 1] as a mono 16-bit PCM WAV in memory."""
    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    if array.size == 0:
        raise ValueError("empty_audio")
    if not np.isfinite(array).all():
        raise ValueError("non_finite_audio")
    if array.max() > 1.0 or array.min() < -1.0:
        array = np.clip(array, -1.0, 1.0)
    pcm = (array * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


class KittenEngine:
    """Real ONNX TTS against pinned Kitten Mini 0.8 assets."""

    def __init__(self, cache_dir: Path, local_files_only: bool):
        self._cache_dir = Path(cache_dir)
        self._local_files_only = local_files_only
        self._tts = None
        self._voice_lookup: dict[str, str] = {}

    @property
    def ready(self) -> bool:
        return self._tts is not None

    @property
    def available_voices(self) -> tuple[str, ...]:
        return tuple(sorted(self._voice_lookup)) if self._voice_lookup else CANONICAL_VOICES

    def _download(self, filename: str) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(
            repo_id=KITTEN_REPO_ID,
            filename=filename,
            revision=KITTEN_REVISION,
            cache_dir=str(self._cache_dir),
            local_files_only=self._local_files_only,
        ))

    @staticmethod
    def _wire_espeak_backend() -> None:
        """Point phonemizer at the bundled espeak-ng when available.

        Without this, phonemizer needs a system-wide espeak-ng install. When the
        wiring fails but a system espeak-ng exists, the default lookup still works.
        """
        try:
            import espeakng_loader
            from phonemizer.backend.espeak.wrapper import EspeakWrapper
        except ImportError:
            return
        try:
            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        except Exception as exc:  # pragma: no cover - depends on wheel contents
            raise AdapterUnavailable("espeak_backend_unusable") from exc

    def load(self) -> None:
        """Download pinned assets and build the ONNX session with a warm-up."""
        require_modules(_REQUIRED_MODULES)
        try:
            config_path = self._download("config.json")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if config.get("model_file") != KITTEN_MODEL_FILENAME:
                raise AdapterUnavailable("unexpected_model_asset")
            model_path = self._download(KITTEN_MODEL_FILENAME)
            voices_path = self._download(KITTEN_VOICES_FILENAME)
        except (AdapterUnavailable, OSError, ValueError):
            raise
        except Exception as exc:
            raise AdapterUnavailable("asset_download_failed") from exc

        try:
            self._wire_espeak_backend()
            from kittentts.onnx_model import KittenTTS_1_Onnx

            tts = KittenTTS_1_Onnx(
                model_path=str(model_path),
                voices_path=str(voices_path),
                speed_priors=config.get("speed_priors", {}) or {},
                voice_aliases=config.get("voice_aliases", {}) or {},
            )
        except AdapterUnavailable:
            raise
        except Exception as exc:
            self.close()
            raise AdapterUnavailable("espeak_backend_missing") from exc

        lookup = {name: name for name in CANONICAL_VOICES}
        for alias, canonical in (config.get("voice_aliases", {}) or {}).items():
            if canonical in lookup:
                lookup[alias.lower()] = canonical
        self._voice_lookup = lookup
        self._tts = tts
        # Readiness requires a real synthesis, not just downloaded files.
        try:
            result = self.generate_speech("Hello.", DEFAULT_SPEECH_VOICE)
            if result.duration_seconds <= 0:
                raise AdapterUnavailable("warmup_silent")
        except Exception:
            self.close()
            raise

    def generate_speech(self, text: str, voice: str) -> SpeechResult:
        tts = self._tts
        if tts is None:
            raise RuntimeError("Kitten engine is not loaded")
        canonical = self._voice_lookup.get(voice.strip().lower())
        if canonical is None:
            raise ValueError("unknown_voice")
        audio = tts.generate(text, voice=canonical, speed=1.0)
        wav_bytes = encode_wav_pcm16(np.asarray(audio), KITTEN_SAMPLE_RATE)
        sample_count = int(np.asarray(audio, dtype=np.float32).size)
        return SpeechResult(
            wav_bytes=wav_bytes,
            sample_rate=KITTEN_SAMPLE_RATE,
            duration_seconds=round(sample_count / KITTEN_SAMPLE_RATE, 6),
        )

    def close(self) -> None:
        self._tts = None
        self._voice_lookup = {}
        gc.collect()
