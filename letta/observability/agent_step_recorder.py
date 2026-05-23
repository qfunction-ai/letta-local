"""AgentStepRecorder — receives step lifecycle events, emits OTel data.

This is a facade, not a GoF observer. The agent loop calls these methods
at defined phase boundaries. The recorder decides what to emit to OTel.
No-op when tracing is disabled.

This is the ONLY modification to the agent loop — 5 call sites at phase
boundaries, not dozens of scattered log_event() invocations.
"""

from typing import Optional

from opentelemetry import trace

from letta.log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# OpenInference semantic conventions — inlined here, not a separate module.
# Three functions, ~15 lines. Split out when it grows past 50.
# ---------------------------------------------------------------------------

_SPAN_KINDS = {
    "agent": "AGENT",
    "llm": "LLM",
    "tool": "TOOL",
    "retriever": "RETRIEVER",
    "chain": "CHAIN",
}


def _set_span_kind(kind: str) -> None:
    """Set the OpenInference span.kind attribute on the current span."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("openinference.span.kind", _SPAN_KINDS.get(kind, kind))


def _set_llm_attributes(model_name: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Set LLM-specific OpenInference attributes on the current span."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("llm.model_name", model_name)
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
        span.set_attribute("llm.token_count.completion", completion_tokens)


def _set_tool_attributes(tool_name: str, tool_type: str = "custom") -> None:
    """Set tool-specific OpenInference attributes on the current span."""
    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.type", tool_type)


# ---------------------------------------------------------------------------
# AgentStepRecorder
# ---------------------------------------------------------------------------

class AgentStepRecorder:
    """Receives step lifecycle events and emits OTel data.

    The agent loop calls these methods at defined phase boundaries.
    The recorder decides what to emit to OTel. No-op when tracing
    is disabled.

    Integration into the agent loop requires exactly 5 call sites:
    1. on_memory_rebuilt  — after _rebuild_memory_async (base_agent.py)
    2. on_context_composed — after _create_llm_request_data_async (letta_agent.py)
    3. on_llm_response    — after processing AI response (letta_agent.py)
    4. on_tool_executed   — after tool execution (letta_agent.py)
    5. on_summarization_completed — after summarization (letta_agent.py)
    """

    def __init__(self):
        from letta.otel.tracing import (
            _is_tracing_initialized,
            log_event,
            log_attributes,
        )
        self._is_tracing_enabled = _is_tracing_initialized
        self._log_event = log_event
        self._log_attributes = log_attributes

    # -- Phase 1: Memory rebuild -------------------------------------------

    def on_memory_rebuilt(
        self,
        block_count: int,
        system_prompt_changed: bool,
        memory_changed: bool,
        system_prompt_tokens: Optional[int] = None,
    ) -> None:
        """After _rebuild_memory_async completes.

        Emits: memory.block_refresh, memory.system_prompt_rebuilt
        Attributes: memory.block_count, memory.system_prompt_tokens
        """
        if not self._is_tracing_enabled:
            return

        self._log_event("memory.block_refresh", attributes={
            "memory.block_count": block_count,
            "memory.system_prompt_changed": system_prompt_changed,
            "memory.memory_changed": memory_changed,
        })
        if system_prompt_changed or memory_changed:
            self._log_event("memory.system_prompt_rebuilt", attributes={
                "memory.system_prompt_tokens": system_prompt_tokens or 0,
                "memory.block_count": block_count,
            })
            _set_span_kind("retriever")

    # -- Phase 2: Context window composition --------------------------------

    def on_context_composed(
        self,
        message_count: int,
        prompt_tokens: int,
        window_limit: int,
        available_tools: list[str],
        tool_calling_mode: str,
    ) -> None:
        """After request data is built.

        Emits: span attributes on current span
        Attributes: context.message_count, context.pressure_ratio, etc.
        """
        if not self._is_tracing_enabled:
            return

        self._log_attributes({
            "context.message_count": message_count,
            "context.total_prompt_tokens": prompt_tokens,
            "context.window_limit": window_limit,
            "context.pressure_ratio": round(prompt_tokens / window_limit, 3) if window_limit else 0,
            "context.available_tools": ",".join(available_tools),
            "context.tool_calling_mode": tool_calling_mode,
        })
        _set_span_kind("agent")

    # -- Phase 3: LLM response processed ------------------------------------

    def on_llm_response(
        self,
        reasoning_content: Optional[str],
        action_taken: str,
        model_name: str,
        reasoning_type: str = "inner_thoughts",
    ) -> None:
        """After LLM response is processed.

        Emits: reasoning.captured span event
        Attributes: reasoning.content, reasoning.action_taken, etc.

        Framing: "reasoning capture", NOT "decision tracing". The model
        doesn't decide — it generates. The reasoning is post-hoc narration
        of a process that's opaque even to the model itself.
        """
        if not self._is_tracing_enabled:
            return

        self._log_event("reasoning.captured", attributes={
            "reasoning.content": (reasoning_content or "")[:5000],
            "reasoning.type": reasoning_type,
            "reasoning.action_taken": action_taken,
            "reasoning.model": model_name,
        })
        _set_span_kind("llm")

    # -- Phase 4: Tool execution --------------------------------------------

    def on_tool_executed(
        self,
        tool_name: str,
        tool_args: Optional[dict] = None,
        tool_result: Optional[str] = None,
        duration_ns: Optional[int] = None,
        success: bool = True,
        error: Optional[str] = None,
        result_count: Optional[int] = None,
    ) -> None:
        """After a tool execution completes.

        Single entry point for all tool execution events. Routes memory
        tool names to specialized events internally. No separate
        on_memory_tool_call method — one public method, one call site.

        The result_count parameter is for tools that return structured
        results (e.g., archival_memory_search). The call site passes
        the structured count, not the recorder guessing from the
        serialized string.

        Emits: memory.block_write, memory.archival_search,
               memory.archival_insert for memory tools.
        Sets: openinference.span.kind = "TOOL" for all tools.
        """
        if not self._is_tracing_enabled:
            return

        # Memory tool specialization
        if tool_name in ("core_memory_append", "core_memory_replace"):
            self._log_event("memory.block_write", attributes={
                "memory.tool_name": tool_name,
                "memory.operation": "append" if "append" in tool_name else "replace",
            })
        elif tool_name == "archival_memory_search":
            self._log_event("memory.archival_search", attributes={
                "memory.result_count": result_count if result_count is not None else 0,
            })
        elif tool_name == "archival_memory_insert":
            self._log_event("memory.archival_insert", attributes={
                "memory.content_length": len(tool_args.get("content", "")) if tool_args else 0,
            })

        _set_span_kind("tool")
        _set_tool_attributes(tool_name)

    # -- Phase 5: Summarization ---------------------------------------------

    def on_summarization_completed(
        self,
        trigger_reason: str,
        eviction_count: int,
        tokens_before: int,
        tokens_after: int,
        latency_ns: int,
    ) -> None:
        """After context window summarization completes.

        Emits: summarization.completed span event
        Attributes: summarization.trigger_reason, eviction_count,
                    tokens_before, tokens_after, latency_ms
        """
        if not self._is_tracing_enabled:
            return

        self._log_event("summarization.completed", attributes={
            "summarization.trigger_reason": trigger_reason,
            "summarization.eviction_count": eviction_count,
            "summarization.tokens_before": tokens_before,
            "summarization.tokens_after": tokens_after,
            "summarization.latency_ms": latency_ns // 1_000_000,
        })
