"""Per-run and per-step token budget enforcement for local model agents.

For local model users, a token budget is a VRAM budget: "don't let the
context grow past X tokens or the model OOMs." The TokenBudget class
checks cumulative token usage after each LLM call and returns a
decision indicating whether the budget is exceeded.

Budget settings are read from agent metadata (not LLMConfig) to avoid
modifying HIGH-activity schema files:

- ``token_budget_run``: int | None — max cumulative tokens per run
- ``token_budget_step``: int | None — max tokens per single step
- ``token_budget_context_ratio``: float — fraction of context_window
  to allow (default 0.7, matching common vLLM --gpu-memory-utilization)

The ``StopReasonType.max_tokens_exceeded`` enum value already exists
in the fork and maps to ``RunStatus.failed``. No schema changes needed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenBudgetDecision:
    """Result of a token budget check.

    Attributes:
        exceeded: True if the budget is exceeded.
        reason: Human-readable reason string (for audit logging).
        budget_type: Which budget was exceeded: "step", "run",
            or "context_window".
    """

    exceeded: bool
    reason: str
    budget_type: str  # "step", "run", "context_window"


class TokenBudget:
    """Per-run and per-step token budget enforcement.

    Initialized from agent metadata keys:
    - token_budget_run: int | None (max tokens per run, default None = unlimited)
    - token_budget_step: int | None (max tokens per step, default None = unlimited)
    - token_budget_context_ratio: float (fraction of context window to allow, default 0.7)

    A TokenBudget is created at the start of each run and checked after
    each LLM call. If over budget, the step loop stops with
    StopReasonType.max_tokens_exceeded.

    The context_window_ratio (default 0.7) is the key parameter for local
    model users. It means "stop the agent before the context fills 70% of
    the model's context window, leaving headroom." The default matches the
    common --gpu-memory-utilization 0.7 setting for vLLM. At 0.85, vLLM's
    scheduler starts dropping requests or OOMing — it doesn't gracefully
    degrade.
    """

    def __init__(
        self,
        max_run_tokens: int | None = None,
        max_step_tokens: int | None = None,
        context_window_limit: int | None = None,
        context_window_ratio: float = 0.7,
    ) -> None:
        self.max_run_tokens = max_run_tokens
        self.max_step_tokens = max_step_tokens
        self.context_window_limit = context_window_limit
        self.context_window_ratio = context_window_ratio
        self._run_tokens_used: int = 0

    def check(self, step_tokens: int, total_run_tokens: int) -> TokenBudgetDecision:
        """Check if the token budget is exceeded after a step.

        Called after each LLM call where usage.total_tokens is updated.

        Args:
            step_tokens: prompt_tokens + completion_tokens for this step.
            total_run_tokens: cumulative total_tokens for the run.

        Returns:
            TokenBudgetDecision with exceeded flag and reason.
        """
        self._run_tokens_used = total_run_tokens

        # 1. Per-step check — catches single calls that are pathological
        if self.max_step_tokens is not None and step_tokens > self.max_step_tokens:
            return TokenBudgetDecision(
                exceeded=True,
                reason=f"step_tokens={step_tokens} exceeds max_step_tokens={self.max_step_tokens}",
                budget_type="step",
            )

        # 2. Per-run check — cumulative budget across all steps
        if self.max_run_tokens is not None and total_run_tokens > self.max_run_tokens:
            return TokenBudgetDecision(
                exceeded=True,
                reason=f"run_tokens={total_run_tokens} exceeds max_run_tokens={self.max_run_tokens}",
                budget_type="run",
            )

        # 3. Context window check — the VRAM budget for local models
        #    This is the most important check for local inference users.
        if self.context_window_limit is not None:
            effective_limit = int(self.context_window_limit * self.context_window_ratio)
            if total_run_tokens > effective_limit:
                return TokenBudgetDecision(
                    exceeded=True,
                    reason=(
                        f"run_tokens={total_run_tokens} exceeds "
                        f"{self.context_window_ratio:.0%} of "
                        f"context_window={self.context_window_limit} "
                        f"(effective_limit={effective_limit})"
                    ),
                    budget_type="context_window",
                )

        return TokenBudgetDecision(exceeded=False, reason="", budget_type="")

    @property
    def run_tokens_used(self) -> int:
        """Total tokens consumed so far in this run."""
        return self._run_tokens_used
