"""Validated local settings. Model sources are fixed in the allowlisted registry."""
import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Worst-case JSON encoding is 12 bytes per character (a non-BMP character
# becomes two \uXXXX escapes) plus the request envelope.
_MAX_REQUEST_BYTES_HEADROOM = 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    model_cache_dir: Path = PROJECT_ROOT / "models" / "cache"
    model_local_files_only: bool = False
    max_input_chars: int = Field(default=2000, ge=1, le=10000)
    max_input_tokens: int = Field(default=256, ge=8, le=512)
    inference_threads: int = Field(default=1, ge=1, le=8)
    max_concurrent_inferences: int = Field(default=2, ge=1, le=16)
    inference_timeout_seconds: float = Field(default=15, gt=0, le=120)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Empty or whitespace-only means local-only mode: /api/* and /metrics are
    # unauthenticated. A nonempty value requires `Authorization: Bearer <key>`.
    api_key: SecretStr = Field(default=SecretStr(""))
    # Byte-level request body cap enforced before and during body reads.
    max_request_bytes: int = Field(default=65536, ge=1024, le=10_485_760)
    # False keeps blocking startup (existing tests depend on load-before-ready).
    background_model_load: bool = False

    @field_validator("model_cache_dir")
    @classmethod
    def resolve_cache_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("api_key")
    @classmethod
    def reject_unusable_key(cls, value: SecretStr) -> SecretStr:
        # Whitespace-only keys would look enabled but never authenticate.
        return SecretStr(value.get_secret_value().strip())

    @model_validator(mode="after")
    def request_limit_covers_inputs(self) -> "Settings":
        floor = self.max_input_chars * 12 + _MAX_REQUEST_BYTES_HEADROOM
        if self.max_request_bytes < floor:
            raise ValueError(
                f"max_request_bytes ({self.max_request_bytes}) is too small for "
                f"max_input_chars ({self.max_input_chars}); needs at least {floor} "
                "so valid JSON bodies are never rejected as too large"
            )
        return self

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key.get_secret_value())

    def bearer_matches(self, presented: str) -> bool:
        """Constant-time Bearer comparison with length-normalized digests."""
        return secrets.compare_digest(
            _auth_digest(presented.encode("utf-8")),
            _auth_digest(self.api_key.get_secret_value().encode("utf-8")),
        )


def _auth_digest(value: bytes) -> bytes:
    # HMAC with a public fixed key only equalizes digest lengths so the
    # timing of the final comparison does not depend on key or token length.
    return hmac.new(
        b"ai-serving-lab-auth-length-normalizer", value, hashlib.sha256
    ).digest()
