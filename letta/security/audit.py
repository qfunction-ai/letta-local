"""AuditLogger — writes security events to the security_events table.

Single append-only table that records every security-relevant event.
No updates, no deletes. The table is the source of truth for incident
reconstruction.

Creates its own session per call via db_registry.async_session().
Same pattern as ToolCallRecorder. A failed audit write must never
break the agent loop — all errors propagate to the caller, which
wraps the call in try/except.

The audit log is the timeline, not the data store. TOOL_EXECUTED
references the ToolCall record by ID — it does not duplicate tool
args, results, or duration. The ToolCall table answers "what did
this specific call do?" The audit log answers "what happened in
chronological order?" One source of truth per fact.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from letta.log import get_logger

logger = get_logger(__name__)


class SecurityEventType(str, Enum):
    """Security event types for the audit log.

    v1 scope: only events that happen in the agent loop where we
    already have call sites. CREDENTIAL_ACCESSED, AGENT_CREATED,
    and AGENT_DELETED require call sites in shared code outside
    the loop. Add them later when that code is instrumented.
    """

    # Tool execution events
    TOOL_EXECUTED = "tool_executed"
    TOOL_DENIED = "tool_denied"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"
    TOOL_APPROVAL_GRANTED = "tool_approval_granted"
    TOOL_APPROVAL_DENIED = "tool_approval_denied"

    # Policy events (future: tool call policies)
    POLICY_VIOLATION = "policy_violation"

    # Canary events (future: output canary checks)
    CANARY_DETECTED = "canary_detected"

    # Memory mutation events
    MEMORY_BLOCK_MODIFIED = "memory_block_modified"


class AuditLogger:
    """Writes security events to the security_events table.

    Usage:
        logger = AuditLogger()
        await logger.log(
            agent_id=agent_id,
            organization_id=org_id,
            event_type=SecurityEventType.TOOL_EXECUTED,
            event_data={"tool_call_id": "toolcall-xxx"},
            step_id=step_id,
        )
    """

    def __init__(self):
        pass  # sessions created per-call

    async def log(
        self,
        agent_id: str,
        organization_id: str,
        event_type: str,
        event_data: Optional[dict] = None,
        step_id: Optional[str] = None,
        run_id: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> None:
        """Persist a security event to the audit log."""
        from letta.orm.security_event import SecurityEvent
        from letta.server.db import db_registry

        event = SecurityEvent(
            id=f"sevt-{uuid4()}",
            agent_id=agent_id,
            organization_id=organization_id,
            step_id=step_id,
            run_id=run_id,
            event_type=event_type,
            event_data=event_data or {},
            actor_id=actor_id,
            created_at=datetime.now(timezone.utc),
        )

        async with db_registry.async_session() as session:
            session.add(event)
            await session.flush()
