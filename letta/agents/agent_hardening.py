"""Agent hardening — circuit breaker and token budget lifecycle.

Owns all circuit breaker and token budget state management so that
HIGH-activity shared agent files (V2, V3) don't store fork-local
state or methods. The agent files import this module and call its
functions via delegation — one import line, zero fork-local methods.

The circuit breaker is lazy-created and cached on the agent object
via a private attribute. The token budget is a pure function that
reads from agent_state.metadata — no self needed.

This module exists because base_agent_v2.py has 14 upstream commits
(MODERATE activity). Adding fork-local state or methods to it creates
merge conflicts on every upstream change. The right pattern is: fork
logic in a new module, shared file gets one import line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from letta.agents.base_agent_v2 import BaseAgentV2
    from letta.agents.circuit_breaker import AgentCircuitBreaker
    from letta.agents.token_budget import TokenBudget

# Private attribute names for caching on the agent object.
# Prefixed with underscore to signal "internal, don't touch".
_CB_ATTR = "_circuit_breaker"
_TB_ATTR = "_token_budget"


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def get_circuit_breaker(agent: "BaseAgentV2") -> "AgentCircuitBreaker":
    """Lazy-create and cache the circuit breaker on the agent.

    The circuit breaker is stateless across runs (reset at start of each
    run), so caching it is safe. It's created once per agent process.
    """
    cb = getattr(agent, _CB_ATTR, None)
    if cb is None:
        from letta.agents.circuit_breaker import AgentCircuitBreaker

        cb = AgentCircuitBreaker()
        setattr(agent, _CB_ATTR, cb)
    return cb


def reset_circuit_breaker(agent: "BaseAgentV2") -> None:
    """Reset circuit breaker at the start of a new run."""
    get_circuit_breaker(agent).reset()


def record_circuit_breaker_error(agent: "BaseAgentV2", error_type: str) -> Optional[str]:
    """Record an error and return the recovery action if threshold exceeded.

    Returns "auto_compact" if the consecutive error threshold for this
    error type is exceeded, None otherwise.
    """
    return get_circuit_breaker(agent).record_error(error_type)


def record_circuit_breaker_success(agent: "BaseAgentV2") -> None:
    """Reset all circuit breaker counters on a successful step.

    A single success breaks the death spiral.
    """
    get_circuit_breaker(agent).record_success()


def get_circuit_breaker_counts(agent: "BaseAgentV2") -> dict[str, int]:
    """Return the current consecutive error counts per category."""
    return get_circuit_breaker(agent).get_counts()


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def create_token_budget(agent_state) -> "TokenBudget":
    """Create a TokenBudget from agent metadata.

    Pure function — reads from agent_state.metadata and
    agent_state.llm_config.context_window. No self needed.

    Budget settings are stored in agent.metadata, not in LLMConfig
    (which is HIGH-activity upstream). Budgets are resource management,
    not security — they stay separate from the policy engine.

    Metadata keys:
    - token_budget_run: int | None — max cumulative tokens per run
    - token_budget_step: int | None — max tokens per single step
    - token_budget_context_ratio: float — fraction of context_window
      to allow (default 0.7, matching common vLLM --gpu-memory-utilization)
    """
    from letta.agents.token_budget import TokenBudget

    metadata = getattr(agent_state, "metadata", None) or {}
    return TokenBudget(
        max_run_tokens=metadata.get("token_budget_run"),
        max_step_tokens=metadata.get("token_budget_step"),
        context_window_limit=agent_state.llm_config.context_window,
        context_window_ratio=metadata.get("token_budget_context_ratio", 0.7),
    )


# ---------------------------------------------------------------------------
# Combined initialization
# ---------------------------------------------------------------------------


def init_run_hardening(agent: "BaseAgentV2") -> None:
    """Initialize token budget and reset circuit breaker for a new run.

    Call this once at the start of each step()/stream() entry point.
    Replaces two lines:
        self.token_budget = self._create_token_budget(self.agent_state)
        self.circuit_breaker.reset()
    with one:
        _ah.init_run_hardening(self)
    """
    agent.token_budget = create_token_budget(agent.agent_state)
    reset_circuit_breaker(agent)
