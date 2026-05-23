"""ToolCallRecorder — writes per-tool-call records to the DB.

Called by the AgentStepRecorder after tool execution. Separate from the
recorder so that DB writes can be batched or deferred without affecting
OTel event emission.

Truncation happens here, not in the schema. The column is TEXT (unbounded).
The recorder logs when truncation occurs.
"""

import logging
from typing import Optional
from uuid import uuid4

from letta.log import get_logger

logger = get_logger(__name__)

_TRUNCATION_LIMIT = 10000


class ToolCallRecorder:
    """Writes ToolCall records to the DB.

    Usage:
        recorder = ToolCallRecorder(session)
        await recorder.record_tool_call(
            step_id=step_id, agent_id=agent_id, ...
        )
    """

    def __init__(self, session):
        self._session = session

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
    ) -> None:
        """Persist a ToolCall record to the DB."""
        from letta.orm.tool_call import ToolCall

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
        self._session.add(tool_call)
        await self._session.flush()
