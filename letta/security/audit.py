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

    Only events that are actually emitted by the agent loop are listed
    here. Dead values have been removed — if you need a new event type,
    add it here AND wire it into the agent loop before shipping.
    """

    # Tool execution events
    TOOL_EXECUTED = "tool_executed"
    TOOL_DENIED = "tool_denied"
    TOOL_APPROVAL_REQUESTED = "tool_approval_requested"

    # Canary events
    CANARY_DETECTED = "canary_detected"

    # Agent output events
    MESSAGE_SENT = "message_sent"


# Tool classification: maps tool names to categories for audit event_data.
# Used by classify_tool() to add a tool_category field to tool_executed events.
MEMORY_WRITE_TOOLS = frozenset({
    "core_memory_append", "core_memory_replace", "memory_replace",
    "memory_insert", "memory_rethink", "memory_apply_patch",
})
MEMORY_READ_TOOLS = frozenset({
    "archival_memory_search", "conversation_search",
})
ARCHIVAL_WRITE_TOOLS = frozenset({
    "archival_memory_insert",
})
NETWORK_TOOLS = frozenset({
    "web_search", "fetch_webpage",
})


def classify_tool(tool_name: str) -> str | None:
    """Return tool_category string for audit event_data, or None for unclassified tools."""
    if tool_name in MEMORY_WRITE_TOOLS:
        return "memory_write"
    if tool_name in MEMORY_READ_TOOLS:
        return "memory_read"
    if tool_name in ARCHIVAL_WRITE_TOOLS:
        return "archival_write"
    if tool_name in NETWORK_TOOLS:
        return "network"
    return None


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


# ---------------------------------------------------------------------------
# Audit helper functions — use these instead of calling audit_logger.log()
# directly. They handle try/except and event_data construction so callers
# don't have to repeat the same boilerplate.
# ---------------------------------------------------------------------------


async def audit_log(
    audit_logger: AuditLogger,
    agent_id: str,
    actor,
    event_type: str,
    event_data: dict,
    step_id: str,
    run_id: str | None,
    label: str = "",
    actor_id: str | None = None,
) -> None:
    """Write an audit log entry. Never raises — errors are logged and swallowed.

    This is the canonical way to write audit events. Prefer this over
    calling ``audit_logger.log()`` directly, which requires the caller
    to wrap the call in its own try/except.
    """
    try:
        await audit_logger.log(
            agent_id=agent_id,
            organization_id=actor.organization_id if actor else None,
            event_type=event_type,
            event_data=event_data,
            step_id=step_id,
            run_id=run_id,
            actor_id=actor_id or (actor.id if actor else None),
        )
    except Exception as e:
        suffix = f" ({label})" if label else ""
        logger.warning(f"Failed to write audit log{suffix}: {e}")


def tool_denied_event(tool_name: str, reason: str) -> dict:
    """Build event_data for a tool_denied event with tool_category."""
    data = {"tool_name": tool_name, "reason": reason}
    cat = classify_tool(tool_name)
    if cat:
        data["tool_category"] = cat
    return data


def canary_detected_event(tool_name: str) -> dict:
    """Build event_data for a canary_detected event with tool_category."""
    data = {"tool_name": tool_name}
    cat = classify_tool(tool_name)
    if cat:
        data["tool_category"] = cat
    return data
