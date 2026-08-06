"""ToolCallRecorder — writes per-tool-call records to the DB.

Called directly from the agent loop after tool execution. Separate from
AgentStepRecorder (OTel) so that DB writes can fail without affecting
event emission, and vice versa.

Creates its own session per call via db_registry. One session per tool
call — simple, correct, no batching. If batching is needed later, add
a buffer and flush mechanism.

Truncation happens here, not in the schema. The column is TEXT (unbounded).
The recorder logs when truncation occurs.

tool_args sanitization: the observability table is NOT a forensic data
dump. Sensitive keys (content, message, query) are redacted to [REDACTED]
before storage. The audit log (security_events) stores only tool_call_id
and tool_name — it doesn't duplicate args. The recorder follows the same
discipline: record what happened, not everything that was said.
"""

from typing import Any, Optional
from uuid import uuid4

from letta.log import get_logger

logger = get_logger(__name__)

_TRUNCATION_LIMIT = 10000

# Keys in tool_args dicts that are known to contain sensitive content
# (PII, credentials, full message text, etc.). Values are replaced with
# [REDACTED] before storage. The key names are preserved so the schema
# is still queryable — you can see that a tool received a "content" arg,
# you just can't read what was in it.
_SENSITIVE_ARG_KEYS = frozenset({
    "content", "old_content", "new_content",
    "message", "query",
})


def _parse_retrieval_results(tool_result: Any) -> Optional[list[dict]]:
    """Extract passage IDs and similarity scores from archival_memory_search results.

    The tool returns a list of dicts with 'id', 'content', 'tags',
    and optionally 'relevance' (with rrf_score, vector_rank, fts_rank).
    This function extracts just the audit-relevant fields: passage ID and
    similarity scores. Returns None if the result is not parseable.

    Fail-open: any exception returns None.
    """
    try:
        if not isinstance(tool_result, list):
            return None
        results = []
        for item in tool_result:
            if not isinstance(item, dict) or "id" not in item:
                continue
            entry = {"passage_id": item["id"]}
            relevance = item.get("relevance", {})
            if isinstance(relevance, dict):
                if relevance.get("rrf_score") is not None:
                    entry["rrf_score"] = relevance["rrf_score"]
                if relevance.get("vector_rank") is not None:
                    entry["vector_rank"] = relevance["vector_rank"]
                if relevance.get("fts_rank") is not None:
                    entry["fts_rank"] = relevance["fts_rank"]
            results.append(entry)
        return results if results else None
    except Exception:
        return None


def _sanitize_tool_args(tool_args: Optional[dict]) -> Optional[dict]:
    """Redact known-sensitive keys from tool_args before DB storage.

    Returns a shallow copy with sensitive values replaced by [REDACTED].
    Non-sensitive keys are preserved as-is. Nested dicts are not recursed
    — tool args from the LLM are flat key-value pairs in practice.
    """
    if not tool_args:
        return tool_args
    sanitized = {}
    for k, v in tool_args.items():
        if k in _SENSITIVE_ARG_KEYS:
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 500:
            # Truncate long string values that aren't in the explicit
            # sensitive set. 500 chars is enough to see the structure
            # without storing entire API responses.
            sanitized[k] = v[:500] + "...[truncated]"
        else:
            sanitized[k] = v
    return sanitized


class ToolCallRecorder:
    """Writes ToolCall records to the DB.

    Usage:
        recorder = ToolCallRecorder()
        await recorder.record_tool_call(
            step_id=step_id, agent_id=agent_id, ...
        )
    """

    def __init__(self):
        pass  # sessions created per-call

    async def record_tool_call(
        self,
        step_id: str,
        agent_id: str,
        organization_id: Optional[str],
        tool_name: str,
        tool_args: Optional[dict],
        tool_result: Optional[str],
        duration_ns: Optional[int],
        success: bool,
        error: Optional[str] = None,
        request_id: Optional[str] = None,
        retrieval_results: Optional[list[dict]] = None,
    ) -> None:
        """Persist a ToolCall record to the DB.

        Args:
            retrieval_results: Optional list of {passage_id, rrf_score,
                vector_rank, fts_rank} dicts. When present (for
                archival_memory_search), stored in tool_args JSON
                under the '_retrieval_results' key for audit purposes.
        """
        from letta.orm.tool_call import ToolCall
        from letta.server.db import db_registry

        # Sanitize tool_args before storage
        tool_args = _sanitize_tool_args(tool_args)

        # Attach retrieval results to tool_args for audit (archival_memory_search)
        if retrieval_results:
            tool_args = tool_args or {}
            tool_args["_retrieval_results"] = retrieval_results

        if tool_result and len(tool_result) > _TRUNCATION_LIMIT:
            logger.debug(
                f"ToolCall result truncated: {len(tool_result)} -> "
                f"{_TRUNCATION_LIMIT} chars (tool={tool_name}, step={step_id})"
            )
            tool_result = tool_result[:_TRUNCATION_LIMIT] + "...[truncated]"

        tool_call = ToolCall(
            id=f"toolcall-{uuid4()}",
            step_id=step_id,
            organization_id=organization_id,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            duration_ns=duration_ns,
            success=success,
            error=error,
            request_id=request_id,
        )

        async with db_registry.async_session() as session:
            session.add(tool_call)
            await session.flush()
