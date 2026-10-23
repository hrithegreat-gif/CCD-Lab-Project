"""Validated local settings. Model sources are fixed in the allowlisted registry."""
import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    model_cache_dir: Path = PROJECT_ROOT / "models" / "cache"
    model_local_files_only: bool = False
    max_image_bytes: int = Field(default=5242880, ge=1024, le=10000000)
    max_image_pixels: int = Field(default=16777216, ge=1000000, le=50000000)
    conf_threshold: float = Field(default=0.25, ge=0.05, le=0.95, allow_inf_nan=False)
    iou_threshold: float = Field(default=0.45, ge=0.10, le=0.95, allow_inf_nan=False)
    max_detections: int = Field(default=100, ge=1, le=300)
    inference_threads: int = Field(default=1, ge=1, le=8)
    max_concurrent_inferences: int = Field(default=1, ge=1, le=16)
    inference_timeout_seconds: float = Field(default=15, gt=0, le=120)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Empty or whitespace-only means local-only mode: /api/* and /metrics are
    # unauthenticated. A nonempty value requires `Authorization: Bearer <key>`.
    api_key: SecretStr = Field(default=SecretStr(""))
    # Byte-level request body cap enforced before and during body reads.
    max_request_bytes: int = Field(default=5246976, ge=2048, le=10004096)
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
        if 'max_request_bytes' not in self.model_fields_set:
            self.max_request_bytes = self.max_image_bytes + 4096
        if self.max_request_bytes < self.max_image_bytes + 1024:
            raise ValueError('max_request_bytes must cover max_image_bytes plus 1024 bytes')
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
