"""Shared primitives for optional capability adapters.

Everything here is import-safe without torch/transformers/kittentts installed:
only the standard library is used. Dependency presence is probed with
``importlib.util.find_spec`` (metadata lookup), never by importing the module.
"""
import importlib.util
from dataclasses import dataclass
from enum import Enum


class CapabilityState(str, Enum):
    """Lifecycle states surfaced by GET /api/capabilities."""

    DISABLED = "disabled"
    LOADING = "loading"
    UNAVAILABLE = "unavailable"
    READY = "ready"


class MissingDependency(RuntimeError):
    """An optional dependency required by an enabled capability is absent."""

    def __init__(self, module: str):
        self.module = module
        super().__init__(f"missing optional dependency: {module}")


class AdapterUnavailable(RuntimeError):
    """An enabled capability cannot run; the message is a safe reason code."""


class OperationTimeout(RuntimeError):
    """A native inference or upstream call exceeded its deadline."""


class RateLimited(RuntimeError):
    """The provider rejected the request for capacity reasons."""

    def __init__(self, retry_after: int):
        self.retry_after = max(1, min(int(retry_after), 120))
        super().__init__("provider rate limit reached")


@dataclass(frozen=True)
class GenerationResult:
    output: str
    input_tokens: int
    output_tokens: int
    truncated_input: bool


@dataclass(frozen=True)
class SpeechResult:
    wav_bytes: bytes
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    duration_seconds: float | None


def module_available(name: str) -> bool:
    """Return True when ``name`` can be found without executing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def require_modules(names: tuple[str, ...]) -> None:
    """Raise :class:`MissingDependency` naming the first absent module."""
    for name in names:
        if not module_available(name):
            raise MissingDependency(name)
