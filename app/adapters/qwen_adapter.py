"""Native Qwen3.5-0.8B CPU text generation via Hugging Face Transformers.

Verified sources (2026-10-13):
- https://huggingface.co/Qwen/Qwen3.5-0.8B  (model card; commit 2fc0636...)
- https://huggingface.co/docs/transformers/model_doc/qwen3_5  (text generation
  quickstart; "Use Qwen3_5ForCausalLM for text-only generation")
- Mapping verified against transformers 5.19.0 with the real pinned config and
  NO weights: MODEL_FOR_CAUSAL_LM_MAPPING_NAMES["qwen3_5"] == "Qwen3_5ForCausalLM",
  and AutoConfig for the pinned revision yields Qwen3_5Config. So
  AutoModelForCausalLM.from_pretrained builds the documented text-only class
  (the full multimodal checkpoint's vision weights are simply not consumed).
  A runtime assertion below fails the load loudly if that ever changes.

The repo ID and revision are fixed constants; neither is configurable by
operators or clients. torch/transformers are imported only inside ``load``.
The deadline criteria instance is created per generate() call, so concurrent
generations (QWEN_MAX_CONCURRENCY > 1) never share deadline state.
"""
import gc
import time
from pathlib import Path

from app.adapters.base import (
    AdapterUnavailable,
    GenerationResult,
    OperationTimeout,
    require_modules,
)

QWEN_REPO_ID = "Qwen/Qwen3.5-0.8B"
# Immutable allowlisted revision (main branch commit, verified 2026-10-13).
QWEN_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
# Verified against transformers 5.19.0 with the pinned config (no weights).
QWEN_EXPECTED_MODEL_CLASS = "Qwen3_5ForCausalLM"
# transformers imports before torch in the probe order; both are required.
_REQUIRED_MODULES = ("transformers", "torch")
_WARMUP_PROMPT = "Hi"
_WARMUP_MAX_NEW_TOKENS = 4


class QwenEngine:
    """Greedy CPU generation against the allowlisted Qwen3.5-0.8B revision."""

    def __init__(
        self,
        cache_dir: Path,
        local_files_only: bool,
        max_input_tokens: int,
        inference_threads: int,
    ):
        self._cache_dir = Path(cache_dir)
        self._local_files_only = local_files_only
        self._max_input_tokens = max_input_tokens
        self._inference_threads = inference_threads
        self._tokenizer = None
        self._model = None
        self._criteria_cls = None  # deadline criteria class, created in load()

    @property
    def ready(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def load(self) -> None:
        """Download pinned assets (unless cached) and build the CPU model.

        Raises MissingDependency when torch/transformers are absent, and
        AdapterUnavailable/OperationTimeout/ValueError/RuntimeError on asset,
        mapping, or warm-up failure.
        """
        require_modules(_REQUIRED_MODULES)
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(max(1, self._inference_threads))
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                QWEN_REPO_ID,
                revision=QWEN_REVISION,
                cache_dir=str(self._cache_dir),
                local_files_only=self._local_files_only,
            )
            model = AutoModelForCausalLM.from_pretrained(
                QWEN_REPO_ID,
                revision=QWEN_REVISION,
                dtype=torch.float32,
                cache_dir=str(self._cache_dir),
                local_files_only=self._local_files_only,
            )
            model.eval()
            if type(model).__name__ != QWEN_EXPECTED_MODEL_CLASS:
                raise AdapterUnavailable("unexpected_model_class")
        except Exception:
            self.close()
            raise

        self._tokenizer = tokenizer
        self._model = model

        from transformers import StoppingCriteria

        class _DeadlineCriteria(StoppingCriteria):
            """Cooperative deadline checked between generated tokens."""

            def __init__(self):
                self.deadline: float | None = None
                self.triggered = False

            def begin(self, seconds: float) -> None:
                self.deadline = time.monotonic() + seconds
                self.triggered = False

            def __call__(self, input_ids, scores, **kwargs) -> bool:  # noqa: ANN001
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    self.triggered = True
                    return True
                return False

        self._criteria_cls = _DeadlineCriteria
        # Readiness requires a real warm-up generation, not just downloaded files.
        try:
            self.generate(_WARMUP_PROMPT, max_new_tokens=_WARMUP_MAX_NEW_TOKENS,
                          deadline_seconds=60.0)
        except Exception:
            self.close()
            raise

    def generate(
        self, prompt: str, max_new_tokens: int, deadline_seconds: float
    ) -> GenerationResult:
        """Generate greedily; raises OperationTimeout at the cooperative deadline."""
        model, tokenizer = self._model, self._tokenizer
        criteria_cls = self._criteria_cls
        if model is None or tokenizer is None or criteria_cls is None:
            raise RuntimeError("Qwen engine is not loaded")
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens must be positive")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")

        # The pinned chat template renders the non-thinking generation prompt
        # (an empty <think> block) for this model, which is non-thinking by default.
        templated = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        full_ids = tokenizer(templated)["input_ids"]
        # input_tokens counts the raw user text, not the rendered template.
        user_tokens = tokenizer(prompt)["input_ids"]
        truncated_input = len(full_ids) > self._max_input_tokens
        kept = full_ids[: self._max_input_tokens]

        import torch

        inputs = {
            "input_ids": torch.tensor([kept], dtype=torch.long),
            "attention_mask": torch.ones((1, len(kept)), dtype=torch.long),
        }
        # Fresh criteria per call: concurrent generations must not share state.
        criteria = criteria_cls()
        criteria.begin(deadline_seconds)
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                stopping_criteria=[criteria],
            )
        if criteria.triggered:
            raise OperationTimeout("Qwen generation exceeded its deadline")
        new_ids = output[0][len(kept):]
        text = tokenizer.decode(new_ids, skip_special_tokens=True)
        return GenerationResult(
            output=text,
            input_tokens=len(user_tokens),
            output_tokens=int(new_ids.shape[0]),
            truncated_input=truncated_input,
        )

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        self._criteria_cls = None
        gc.collect()
