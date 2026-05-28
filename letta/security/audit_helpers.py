"""Audit logging helpers — topic-specific wrappers around audit_log().

Each helper constructs the correct event_data dict so that callers
don't repeat the same boilerplate at every call site. All helpers
delegate to audit_log() which handles try/except internally, so
callers never need to wrap these in try/except.

Usage in agent files:
    from letta.security import audit_helpers as _ah
    await _ah.log_tool_executed(self.audit_logger, self.agent_id, self.actor, ...)
    await _ah.log_tool_denied(self.audit_logger, self.agent_id, self.actor, ...)
    await _ah.log_message_sent(self.audit_logger, self.agent_id, self.actor, ...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from letta.security.audit import audit_log, classify_tool, SecurityEventType

if TYPE_CHECKING:
    from letta.security.audit import AuditLogger
    from letta.schemas.user import User


async def log_tool_executed(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_call_id: str,
    tool_name: str,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a tool_executed audit event."""
    tool_category = classify_tool(tool_name)
    event_data = {"tool_call_id": tool_call_id, "tool_name": tool_name}
    if tool_category:
        event_data["tool_category"] = tool_category
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.TOOL_EXECUTED, event_data,
        step_id, run_id, "tool_executed",
    )


async def log_tool_denied(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    reason: str,
    step_id: str | None,
    run_id: str | None,
    matched_rule: str | None = None,
) -> None:
    """Log a tool_denied audit event."""
    event_data = {"tool_name": tool_name, "reason": reason}
    if matched_rule:
        event_data["matched_rule"] = matched_rule
    tool_category = classify_tool(tool_name)
    if tool_category:
        event_data["tool_category"] = tool_category
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.TOOL_DENIED, event_data,
        step_id, run_id, "tool_denied",
    )


async def log_tool_approval_requested(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    reason: str,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a tool_approval_requested audit event."""
    event_data = {"tool_name": tool_name, "reason": reason}
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.TOOL_APPROVAL_REQUESTED, event_data,
        step_id, run_id, "tool_approval_requested",
    )


async def log_canary_detected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a canary_detected audit event."""
    from letta.security.audit import canary_detected_event
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.CANARY_DETECTED, canary_detected_event(tool_name),
        step_id, run_id, "canary_detected",
    )


async def log_message_sent(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a message_sent audit event."""
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.MESSAGE_SENT, {"agent_id": agent_id},
        step_id, run_id, "message_sent",
    )


async def log_canary_output_detected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a canary_output_detected audit event.

    Fired when the output filter redacts a canary token from an
    assistant message. Distinct from CANARY_DETECTED which fires
    on tool-call interception.
    """
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.CANARY_OUTPUT_DETECTED, {"agent_id": agent_id},
        step_id, run_id, "canary_output_detected",
    )


async def log_secret_detected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    label: str,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a secret_detected audit event.

    Fired when the secret scanner detects a potential secret in tool
    arguments. The label identifies the type (e.g., "AWS Access Key ID",
    "High-entropy secret").
    """
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.SECRET_DETECTED, {"tool_name": tool_name, "label": label},
        step_id, run_id, "secret_detected",
    )
