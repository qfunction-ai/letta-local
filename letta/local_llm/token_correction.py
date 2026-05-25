"""Token estimate correction for local LLM inference servers.

Local LLM adapters (Ollama, vLLM, llama.cpp) estimate prompt tokens
using a bytes/4 heuristic: ``len(prompt.encode("utf-8")) // 4``. This
underestimates real token counts for models with expensive chat templates
(Qwen, LLaMA, Mistral, DeepSeek) by 2-5x.

This module provides two correction paths:

1. **Static correction table** — per-model-family multipliers, derived
   from benchmark measurements against real inference server responses.
   Used on the first LLM call (cold start) before any server data is
   available.

2. **Live calibration** — after the first LLM call returns server-
   reported ``prompt_tokens``, the ``LiveTokenCalibration`` class caches
   the ratio (server_reported / bytes4_estimate) for that model.
   Subsequent pre-call estimates use the live ratio instead of the table.

The two paths ensure accuracy at every stage:
- Cold start: static table (or conservative default if model not in table)
- Warm: live ratio from last server response
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Static correction table
# ---------------------------------------------------------------------------

TOKEN_ESTIMATE_CORRECTION: dict[str, float | None] = {
    # Model prefix -> multiplier to apply on top of bytes/4 estimate.
    # These are empirically measured ratios: (real_tokens / bytes4_estimate).
    # Values populated by running the benchmark script against real
    # Ollama/vLLM responses.  ``None`` means "not yet measured" — the
    # caller falls back to DEFAULT_TOKEN_CORRECTION.
    "qwen": None,       # placeholder — measured by benchmark
    "llama": None,      # placeholder — measured by benchmark
    "mistral": None,     # placeholder — measured by benchmark
    "phi": None,         # placeholder — measured by benchmark
    "gemma": None,       # placeholder — measured by benchmark
    "deepseek": None,    # placeholder — measured by benchmark
}

# Conservative default: bytes/4 underestimates for most subword tokenizers,
# not overestimates.  2.5x is conservative enough to prevent context overflow
# for models not yet in the table.
DEFAULT_TOKEN_CORRECTION = 2.5


def get_token_correction(model: str | None) -> float:
    """Get the static token estimate correction factor for a model.

    Performs a case-insensitive prefix match against the model name.
    If the model is in the table but the value is ``None`` (not yet
    measured), falls back to ``DEFAULT_TOKEN_CORRECTION``.

    Args:
        model: Model name string (e.g. "qwen2.5-7b-instruct").
               If None, returns the default.

    Returns:
        Correction factor as a float.  Multiply the bytes/4 estimate
        by this factor to get a more accurate pre-call token estimate.
    """
    if not model:
        return DEFAULT_TOKEN_CORRECTION
    model_lower = model.lower()
    for prefix, factor in TOKEN_ESTIMATE_CORRECTION.items():
        if prefix in model_lower:
            if factor is not None:
                return factor
            # Model is in the table but not yet measured — break out
            # and use the default instead of continuing to search.
            break
    return DEFAULT_TOKEN_CORRECTION


# ---------------------------------------------------------------------------
# Live calibration
# ---------------------------------------------------------------------------

class LiveTokenCalibration:
    """Caches server-reported token counts to calibrate subsequent estimates.

    On the first LLM call, only the static correction table is available.
    After the first call returns server-reported ``prompt_tokens``, we
    cache the ratio (server_reported / bytes4_estimate) for that model.
    Subsequent pre-call estimates use the live ratio instead of the table.

    This gives us two paths:
    - Cold start: static table (or DEFAULT_TOKEN_CORRECTION if model not
      in table)
    - Warm: live ratio from last server response

    The class is per-agent-instance: each agent gets its own calibration
    state that persists across steps within a run.
    """

    def __init__(self) -> None:
        self._model_ratios: dict[str, float] = {}  # model -> live ratio

    def update(self, model: str, server_prompt_tokens: int, bytes4_estimate: int) -> None:
        """Update the live ratio from a server-reported token count.

        Called after each LLM response that includes server-reported
        ``prompt_tokens``.  The ratio is cached by model name so that
        subsequent pre-call estimates for the same model are calibrated.

        Args:
            model: Model name string.
            server_prompt_tokens: The ``prompt_tokens`` reported by the
                inference server in its response.
            bytes4_estimate: The bytes/4 estimate that was computed
                before the LLM call.
        """
        if bytes4_estimate > 0 and server_prompt_tokens > 0:
            ratio = server_prompt_tokens / bytes4_estimate
            self._model_ratios[model] = ratio

    def get_correction(self, model: str | None) -> float:
        """Get the best available correction factor for a model.

        Checks the live calibration cache first (most accurate), then
        falls back to the static correction table, then to the default.

        Args:
            model: Model name string.  If None, returns the default.

        Returns:
            Correction factor as a float.
        """
        if model and model in self._model_ratios:
            return self._model_ratios[model]
        return get_token_correction(model)

    def reset(self) -> None:
        """Reset all cached ratios.

        Called at the start of each agent run to avoid stale calibration
        from a previous run that may have used a different model.
        """
        self._model_ratios.clear()

    def get_cached_ratios(self) -> dict[str, float]:
        """Return a copy of the cached model ratios (for debugging)."""
        return dict(self._model_ratios)
