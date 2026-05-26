"""AgentStepRecorder — receives step lifecycle events, emits OTel data.

This is a facade, not a GoF observer. The agent loop calls these methods
at defined phase boundaries. The recorder decides what to emit to OTel.
No-op when tracing is disabled.

This is the ONLY modification to the agent loop — call sites at phase
boundaries, not dozens of scattered log_event() invocations.

Every method wraps its work in try/except so a broken recorder never
crashes the agent loop. If tracing is down, the agent still runs.
"""

from typing import Optional

from letta.log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenInference semantic conventions — inlined here, not a separate module.
# Split out when it grows past 50 lines.
# ---------------------------------------------------------------------------


def _set_llm_attributes(model_name: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Set LLM-specific OpenInference attributes on the current span."""
    from opentelemetry import trace

    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_attribute("llm.model_name", model_name)
        span.set_attribute("llm.token_count.prompt", prompt_tokens)
        span.set_attribute("llm.token_count.completion", completion_tokens)


def _set_tool_attributes(tool_name: str, tool_type: str = "custom") -> None:
    """Set tool-specific OpenInference attributes on the current span."""
    from opentelemetry import trace

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

    Integration into the agent loop requires call sites at these
    phase boundaries (V2: 5, V3: 7 with compaction + batch tools):

    1. on_memory_rebuilt  — after _rebuild_memory (V2, V3)
    2. on_context_composed — after request data is built (V2, V3)
    3. on_llm_response    — after LLM response processed (V2, V3)
    4. on_tool_executed   — after single tool execution (V2)
       on_tool_executed_batch — after parallel tool execution (V3)
    5. on_summarization_completed — after summarization (V2)
       on_compaction_completed — after compaction (V3)
    """

    def __init__(self):
        from letta.otel.tracing import (
            log_event,
            log_attributes,
        )
        self._log_event = log_event
        self._log_attributes = log_attributes

    @property
    def _is_tracing_enabled(self):
        """Check tracing status dynamically — no cached stale value."""
        from letta.otel.tracing import _is_tracing_initialized
        return _is_tracing_initialized

    # -- Phase 1: Memory rebuild -------------------------------------------

    def on_memory_rebuilt(
        self,
        block_count: int,
        system_prompt_changed: bool,
        memory_changed: bool,
        system_prompt_tokens: Optional[int] = None,
    ) -> None:
        """After _rebuild_memory completes.

        Emits: memory.block_refresh, memory.system_prompt_rebuilt
        Attributes: memory.block_count, memory.system_prompt_tokens

        Rare in normal operation — only fires when memory blocks or
        system prompt content actually changed. Most steps skip this.
        """
        if not self._is_tracing_enabled:
            return
        try:
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
        except Exception:
            logger.debug("on_memory_rebuilt: tracing emission failed", exc_info=True)

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
        try:
            self._log_attributes({
                "context.message_count": message_count,
                "context.total_prompt_tokens": prompt_tokens,
                "context.window_limit": window_limit,
                "context.pressure_ratio": round(prompt_tokens / window_limit, 3) if window_limit else 0,
                "context.available_tools": ",".join(available_tools),
                "context.tool_calling_mode": tool_calling_mode,
            })
        except Exception:
            logger.debug("on_context_composed: tracing emission failed", exc_info=True)

    # -- Phase 3: LLM response processed ------------------------------------

    def on_llm_response(
        self,
        reasoning_content: Optional[str],
        model_name: str,
        reasoning_type: str = "inner_thoughts",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """After LLM response is processed.

        Emits: reasoning.captured span event
        Sets: llm.model_name, llm.token_count.prompt, llm.token_count.completion

        prompt_tokens and completion_tokens are PER-CALL values from
        last_step_usage, NOT cumulative totals from self.usage.
        The integration module is responsible for passing the right
        values — the recorder does not guess.

        Framing: "reasoning capture", NOT "decision tracing". The model
        doesn't decide — it generates. The reasoning is post-hoc narration
        of a process that's opaque even to the model itself.
        """
        if not self._is_tracing_enabled:
            return
        try:
            self._log_event("reasoning.captured", attributes={
                "reasoning.content": (reasoning_content or "")[:5000],
                "reasoning.type": reasoning_type,
                "reasoning.model": model_name,
            })
            _set_llm_attributes(model_name, prompt_tokens, completion_tokens)
        except Exception:
            logger.debug("on_llm_response: tracing emission failed", exc_info=True)

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
        """After a single tool execution completes (V2 path).

        Single entry point for all tool execution events. Routes memory
        tool names to specialized events internally. No separate
        on_memory_tool_call method — one public method, one call site.

        The result_count parameter is for tools that return structured
        results (e.g., archival_memory_search). The call site passes
        the structured count, not the recorder guessing from the
        serialized string.

        Emits: memory.block_write, memory.archival_search,
               memory.archival_insert for memory tools.
        Sets: tool.name, tool.type on current span.
        """
        if not self._is_tracing_enabled:
            return
        try:
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

            _set_tool_attributes(tool_name)
        except Exception:
            logger.debug("on_tool_executed: tracing emission failed", exc_info=True)

    def on_tool_executed_batch(
        self,
        tool_names: list[str],
        tool_results: list[dict],
        total_duration_ns: Optional[int] = None,
    ) -> None:
        """After parallel tool execution completes (V3 path).

        Emits one event per tool in the batch, plus a batch summary
        event. Each tool gets its own tool.name/tool.type attributes.

        tool_results is a list of dicts with keys: tool_name, success,
        duration_ns, result_count (optional).
        """
        if not self._is_tracing_enabled:
            return
        try:
            for i, name in enumerate(tool_names):
                result = tool_results[i] if i < len(tool_results) else {}
                self._log_event("tool.batch_item", attributes={
                    "tool.name": name,
                    "tool.batch_index": i,
                    "tool.batch_size": len(tool_names),
                    "tool.success": result.get("success", True),
                })
            self._log_event("tool.batch_completed", attributes={
                "tool.batch_size": len(tool_names),
                "tool.batch_names": ",".join(tool_names),
                "tool.batch_duration_ns": total_duration_ns or 0,
            })
        except Exception:
            logger.debug("on_tool_executed_batch: tracing emission failed", exc_info=True)

    # -- Phase 5: Summarization / Compaction ---------------------------------

    def on_summarization_completed(
        self,
        trigger_reason: str,
        eviction_count: int,
        tokens_before: int,
        tokens_after: int,
        latency_ns: int,
    ) -> None:
        """After V2 context window summarization completes.

        Emits: summarization.completed span event
        Attributes: summarization.trigger_reason, eviction_count,
                    tokens_before, tokens_after, latency_ms
        """
        if not self._is_tracing_enabled:
            return
        try:
            self._log_event("summarization.completed", attributes={
                "summarization.trigger_reason": trigger_reason,
                "summarization.eviction_count": eviction_count,
                "summarization.tokens_before": tokens_before,
                "summarization.tokens_after": tokens_after,
                "summarization.latency_ms": latency_ns // 1_000_000,
            })
        except Exception:
            logger.debug("on_summarization_completed: tracing emission failed", exc_info=True)

    def on_compaction_completed(
        self,
        trigger: str,
        messages_before: int,
        messages_after: int,
        tokens_before: Optional[int],
        tokens_after: Optional[int],
        latency_ns: int,
    ) -> None:
        """After V3 compaction completes.

        Emits: compaction.completed span event
        Attributes: compaction.trigger, messages_before/after,
                    tokens_before/after, latency_ms
        """
        if not self._is_tracing_enabled:
            return
        try:
            self._log_event("compaction.completed", attributes={
                "compaction.trigger": trigger,
                "compaction.messages_before": messages_before,
                "compaction.messages_after": messages_after,
                "compaction.tokens_before": tokens_before or 0,
                "compaction.tokens_after": tokens_after or 0,
                "compaction.latency_ms": latency_ns // 1_000_000,
            })
        except Exception:
            logger.debug("on_compaction_completed: tracing emission failed", exc_info=True)
