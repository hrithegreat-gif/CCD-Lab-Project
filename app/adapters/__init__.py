"""Optional model adapters for app/extensions.py.

Modules in this package import heavyweight optional dependencies (torch,
transformers, kittentts) only inside engine ``load``/inference code paths.
Importing this package with all capability flags disabled performs no optional
imports, downloads, or network calls.
"""
from app.adapters.base import (
    AdapterUnavailable,
    CapabilityState,
    GenerationResult,
    MissingDependency,
    OperationTimeout,
    SpeechResult,
    TranscriptionResult,
    module_available,
)

__all__ = [
    "AdapterUnavailable",
    "CapabilityState",
    "GenerationResult",
    "MissingDependency",
    "OperationTimeout",
    "SpeechResult",
    "TranscriptionResult",
    "module_available",
]
