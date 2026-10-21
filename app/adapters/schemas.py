"""REST schemas for the optional model extensions.

These are intentionally separate from the binary sentiment schemas in
app/schemas.py; /api/generate responses must never be forced into the
InferenceResponse contract.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CapabilityStateName = Literal["disabled", "loading", "unavailable", "ready"]
GenerateModelName = Literal["qwen3.5-0.8b", "groq-gpt-oss-120b"]


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: GenerateModelName
    input: str = Field(min_length=1, max_length=8000, strict=True)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1024)

    @field_validator("input")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Input must contain non-whitespace text")
        return value


class GenerateResponse(BaseModel):
    status: Literal["success"] = "success"
    model: str
    provider: Literal["local", "groq"]
    output: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    truncated_input: bool
    processing_time_ms: float = Field(ge=0)


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=5000, strict=True)
    voice: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("input")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Input must contain non-whitespace text")
        return value


class TranscriptionResponse(BaseModel):
    status: Literal["success"] = "success"
    model: str
    text: str
    duration_seconds: float | None = Field(default=None, ge=0)
    processing_time_ms: float = Field(ge=0)


class CapabilityInfo(BaseModel):
    state: CapabilityStateName
    model: str | None = None
    revision: str | None = None
    detail: str | None = None
    voices: list[str] | None = None


class CapabilitiesResponse(BaseModel):
    capabilities: dict[str, CapabilityInfo]
