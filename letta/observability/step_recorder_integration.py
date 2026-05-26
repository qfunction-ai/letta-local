"""Step recorder integration — adapter between agent loop and AgentStepRecorder.

This module is the ONLY place that imports AgentStepRecorder. The agent
loop files (V2, V3) import this module and call its public functions.
One import, one function call per phase boundary. No recorder objects
leak into the agent loop.

Lazy recorder caching: the recorder is created once per agent process
and cached on the agent object via _get_recorder(). The recorder is
stateless (checks tracing status dynamically), so it never needs
clearing between runs.

No dry_run guard: the agent loop's control flow already prevents
recording calls from being reached during dry runs (dry_run returns
early from _step() before any recording call site). Adding a redundant
guard would require storing dry_run state on the agent — fork-local
state in a HIGH-activity shared file. Not worth it.

Expensive value computation: some recorder parameters require work
(e.g., counting memory blocks, extracting tool names). The integration
module computes these only after the tracing-enabled check passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from letta.helpers.datetime_helpers import get_utc_timestamp_ns
from letta.log import get_logger

if TYPE_CHECKING:
    from letta.agents.base_agent_v2 import BaseAgentV2

logger = get_logger(__name__)

# Attribute name used to cache the recorder on the agent object.
# Prefixed with underscore to signal "internal, don't touch".
_RECORDER_ATTR = "_step_recorder"


def _get_recorder(agent: "BaseAgentV2"):
    """Lazy-create and cache the AgentStepRecorder on the agent.

    Returns the cached recorder if one exists, creates one otherwise.
    The recorder is stateless — it checks tracing status dynamically
    on every call, so it never needs clearing between runs.
    """
    recorder = getattr(agent, _RECORDER_ATTR, None)
    if recorder is None:
        from letta.observability.agent_step_recorder import AgentStepRecorder

        recorder = AgentStepRecorder()
        setattr(agent, _RECORDER_ATTR, recorder)
    return recorder


# ---------------------------------------------------------------------------
# Summarization timing — module-level state to avoid fork-local agent attrs
# ---------------------------------------------------------------------------

_summarize_state: dict[int, tuple[int, int]] = {}
# agent id -> (start_ns, tokens_before)


def mark_summarization_start(agent: "BaseAgentV2") -> None:
    """Call before summarize_conversation_history() to capture start state.

    Stores start timestamp and current token count in module-level dict.
    record_summarization_completed() reads and clears it.
    """
    try:
        _summarize_state[id(agent)] = (
            get_utc_timestamp_ns(),
            agent.usage.total_tokens,
        )
    except Exception:
        logger.debug("mark_summarization_start failed", exc_info=True)


# ---------------------------------------------------------------------------
# Public API — one function per phase boundary
# ---------------------------------------------------------------------------


def record_memory_rebuilt_explicit(
    agent: "BaseAgentV2",
    block_count: int,
    system_prompt_changed: bool,
    memory_changed: bool,
    system_prompt_tokens: Optional[int] = None,
) -> None:
    """Call after _rebuild_memory() with explicit change flags.

    Use this when the caller has already computed whether the system
    prompt or memory changed (avoids the conservative defaults in
    record_memory_rebuilt).
    """
    try:
        recorder = _get_recorder(agent)
        recorder.on_memory_rebuilt(
            block_count=block_count,
            system_prompt_changed=system_prompt_changed,
            memory_changed=memory_changed,
            system_prompt_tokens=system_prompt_tokens,
        )
    except Exception:
        logger.debug("record_memory_rebuilt_explicit failed", exc_info=True)


def record_context_composed(
    agent: "BaseAgentV2",
    messages: list,
    valid_tools: list[dict],
    tool_calling_mode: str = "native",
) -> None:
    """Call after request data is built (V2 and V3).

    Extracts message_count, prompt_tokens, and window_limit from
    available data. The prompt_tokens estimate comes from the LLM
    adapter usage if available, otherwise from context_token_estimate.
    """
    try:
        recorder = _get_recorder(agent)
        message_count = len(messages)
        window_limit = agent.agent_state.llm_config.context_window
        # Use per-step usage if available, otherwise context estimate
        step_usage = getattr(agent, "last_step_usage", None)
        if step_usage and step_usage.prompt_tokens:
            prompt_tokens = step_usage.prompt_tokens
        else:
            prompt_tokens = getattr(agent, "context_token_estimate", 0) or 0
        tool_names = [t["name"] for t in valid_tools] if valid_tools else []
        recorder.on_context_composed(
            message_count=message_count,
            prompt_tokens=prompt_tokens,
            window_limit=window_limit,
            available_tools=tool_names,
            tool_calling_mode=tool_calling_mode,
        )
    except Exception:
        logger.debug("record_context_composed failed", exc_info=True)


def record_llm_response(
    agent: "BaseAgentV2",
    reasoning_content: Optional[str],
    model_name: str,
    reasoning_type: str = "inner_thoughts",
) -> None:
    """Call after LLM response is processed (V2 and V3).

    Uses last_step_usage for per-call token counts, NOT cumulative
    self.usage. The integration module extracts the right values so
    the recorder doesn't have to guess.
    """
    try:
        recorder = _get_recorder(agent)
        # Per-step usage (not cumulative)
        step_usage = getattr(agent, "last_step_usage", None)
        prompt_tokens = step_usage.prompt_tokens if step_usage and step_usage.prompt_tokens else 0
        completion_tokens = step_usage.completion_tokens if step_usage and step_usage.completion_tokens else 0
        recorder.on_llm_response(
            reasoning_content=reasoning_content,
            model_name=model_name,
            reasoning_type=reasoning_type,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except Exception:
        logger.debug("record_llm_response failed", exc_info=True)


def record_tool_executed(
    agent: "BaseAgentV2",
    tool_name: str,
    tool_args: Optional[dict] = None,
    tool_result: Optional[str] = None,
    duration_ns: Optional[int] = None,
    success: bool = True,
    error: Optional[str] = None,
    result_count: Optional[int] = None,
) -> None:
    """Call after single tool execution completes (V2 path).

    Skips recording for denials and client-side tool returns — those
    are not real tool executions.
    """
    try:
        recorder = _get_recorder(agent)
        recorder.on_tool_executed(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            duration_ns=duration_ns,
            success=success,
            error=error,
            result_count=result_count,
        )
    except Exception:
        logger.debug("record_tool_executed failed", exc_info=True)


def record_tool_executed_batch(
    agent: "BaseAgentV2",
    tool_names: list[str],
    tool_results: list[dict],
    total_duration_ns: Optional[int] = None,
) -> None:
    """Call after parallel tool execution completes (V3 path).

    tool_results is a list of dicts with keys: tool_name, success,
    duration_ns, result_count (optional).
    """
    try:
        recorder = _get_recorder(agent)
        recorder.on_tool_executed_batch(
            tool_names=tool_names,
            tool_results=tool_results,
            total_duration_ns=total_duration_ns,
        )
    except Exception:
        logger.debug("record_tool_executed_batch failed", exc_info=True)


def record_summarization_completed(
    agent: "BaseAgentV2",
    trigger_reason: str,
    eviction_count: int = 0,
) -> None:
    """Call after V2 summarization completes.

    Reads start state from mark_summarization_start() and computes
    latency and token delta automatically. No timing variables needed
    at the call site.
    """
    try:
        start_ns, tokens_before = _summarize_state.pop(id(agent), (0, 0))
        latency_ns = get_utc_timestamp_ns() - start_ns if start_ns else 0
        tokens_after = agent.usage.total_tokens
        recorder = _get_recorder(agent)
        recorder.on_summarization_completed(
            trigger_reason=trigger_reason,
            eviction_count=eviction_count,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            latency_ns=latency_ns,
        )
    except Exception:
        logger.debug("record_summarization_completed failed", exc_info=True)


def record_compaction_completed(
    agent: "BaseAgentV2",
    trigger: str,
    messages_before: int,
    messages_after: int,
    tokens_before: Optional[int],
    tokens_after: Optional[int],
    latency_ns: int = 0,
) -> None:
    """Call after V3 compaction completes."""
    try:
        recorder = _get_recorder(agent)
        recorder.on_compaction_completed(
            trigger=trigger,
            messages_before=messages_before,
            messages_after=messages_after,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            latency_ns=latency_ns,
        )
    except Exception:
        logger.debug("record_compaction_completed failed", exc_info=True)
