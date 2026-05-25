"""Circuit breaker for agent step loops — breaks consecutive error death spirals.

When context overflows a local model's real token limit, the LLM API
returns an error. The agent records the error and either retries
(heartbeat) or returns the error. Without a circuit breaker, the agent
loops indefinitely: each retry faces an even larger context, every
attempt fails, and the conversation history keeps growing.

The ``AgentCircuitBreaker`` tracks consecutive error counts per category.
When a configurable threshold is exceeded, it triggers a recovery action
(``"auto_compact"``) instead of allowing another retry. The agent then
force-clears the context window (memory blocks persist across compactions)
and retries with the compacted context. If compaction also fails, the
agent returns a structured error — the circuit breaker stays open.

Default thresholds:
- ``llm_api_error``: 3 consecutive errors before auto-compact
- ``context_window_overflow``: 2 consecutive overflows before auto-compact

These are conservative: three LLM errors is enough to be confident the
context is genuinely bloated, and two context overflows means the
compaction logic itself isn't keeping up.
"""

from __future__ import annotations

from typing import Optional


class AgentCircuitBreaker:
    """Breaks the agent out of consecutive error death spirals.

    Tracks consecutive error counts per category. When a threshold
    is exceeded, triggers a recovery action instead of retrying.

    Usage::

        cb = AgentCircuitBreaker()
        # On error:
        action = cb.record_error("llm_api_error")
        if action == "auto_compact":
            # force-clear context and retry
        # On success:
        cb.record_success()
    """

    DEFAULT_THRESHOLDS: dict[str, int] = {
        "llm_api_error": 3,           # after 3 consecutive LLM errors, compact
        "context_window_overflow": 2,  # after 2 overflows, force compact
    }

    def __init__(self, thresholds: dict[str, int] | None = None) -> None:
        self._consecutive_errors: dict[str, int] = {}
        self._thresholds = thresholds or dict(self.DEFAULT_THRESHOLDS)
        self._last_action: str | None = None

    def record_success(self) -> None:
        """Reset all error counters on success.

        Called after a successful step completes. A single success
        resets all categories — the death spiral is broken.
        """
        self._consecutive_errors.clear()
        self._last_action = None

    def record_error(self, error_type: str) -> Optional[str]:
        """Record an error and return a recovery action if threshold exceeded.

        Args:
            error_type: Error category string (e.g. "llm_api_error",
                "context_window_overflow").

        Returns:
            ``"auto_compact"`` if the threshold for this error type
            is exceeded, ``None`` otherwise.
        """
        self._consecutive_errors[error_type] = self._consecutive_errors.get(error_type, 0) + 1
        count = self._consecutive_errors[error_type]
        threshold = self._thresholds.get(error_type, 3)

        if count >= threshold:
            self._last_action = "auto_compact"
            return "auto_compact"

        self._last_action = None
        return None

    def get_counts(self) -> dict[str, int]:
        """Return a copy of the current error counts."""
        return dict(self._consecutive_errors)

    @property
    def last_action(self) -> str | None:
        """The recovery action from the last record_error call."""
        return self._last_action

    @property
    def is_open(self) -> bool:
        """True if the circuit breaker has triggered and not been reset.

        An open circuit breaker means the agent should not retry —
        it should return a structured error to the client.
        """
        return self._last_action is not None

    def reset(self) -> None:
        """Reset all state. Called at the start of a new agent run."""
        self._consecutive_errors.clear()
        self._last_action = None
