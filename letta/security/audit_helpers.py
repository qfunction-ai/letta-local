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

from typing import TYPE_CHECKING

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
        SecurityEventType.CANARY_OUTPUT_DETECTED,
        {"agent_id": agent_id, "match_type": "strict"},
        step_id, run_id, "canary_output_detected",
    )


async def log_canary_output_suspected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    match: dict,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a canary_output_suspected audit event (v0.16.37 Part B).

    Skeleton-tier match: a garbled/deleted variant of the canary appeared in
    output. AUDIT-ONLY — no redaction, no CANARY_WARNING. The event is the
    signal; fuzzy redaction would corrupt legitimate output. Distinct event
    TYPE (not a label): consumers dispatch on event_type, and suspected is
    not confirmed.
    """
    event_data = {
        "agent_id": agent_id,
        "match_type": "skeleton",
        "matched_segments": match.get("matched_segments", []),
        "matched_ratio": match.get("matched_ratio"),
        "location": "canary_block",
    }
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.CANARY_OUTPUT_SUSPECTED, event_data,
        step_id, run_id, "canary_output_suspected",
    )


async def log_secret_detected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    label: str,
    step_id: str | None,
    run_id: str | None,
    matched_rule: str | None = None,
) -> None:
    """Log a secret_detected audit event.

    Fired when the secret scanner detects a potential secret in tool
    arguments. The label identifies the type (e.g., "AWS Access Key ID",
    "High-entropy secret").
    """
    event_data = {"tool_name": tool_name, "label": label}
    if matched_rule is not None:
        event_data["matched_rule"] = matched_rule
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.SECRET_DETECTED, event_data,
        step_id, run_id, "secret_detected",
    )


async def log_injection_detected(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    label: str,
    step_id: str | None,
    run_id: str | None,
    matched_rule: str | None = None,
) -> None:
    """Log an injection_detected audit event.

    Fired when the content validator detects a potential prompt injection
    in tool arguments. The label identifies the pattern type (e.g.,
    "instruction_override", "system_marker", "hidden_unicode_zero_width").
    """
    event_data = {"tool_name": tool_name, "label": label}
    if matched_rule is not None:
        event_data["matched_rule"] = matched_rule
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.INJECTION_DETECTED, event_data,
        step_id, run_id, "injection_detected",
    )


async def log_policy_audit_matched(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    tool_name: str,
    matched_rule: str,
    step_id: str | None,
    run_id: str | None,
) -> None:
    """Log a policy_audit_matched audit event.

    Fired when a custom AUDIT rule (not contains_secret / contains_injection)
    matches during policy evaluation. AUDIT is log-and-continue (v0.16.37):
    these events fire regardless of the terminal decision's outcome.
    """
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.POLICY_AUDIT_MATCHED, {"tool_name": tool_name, "matched_rule": matched_rule},
        step_id, run_id, "policy_audit_matched",
    )


async def log_policy_updated(
    audit_logger: "AuditLogger",
    agent_id: str,
    actor: "User | None",
    policy: dict,
    method: str,
) -> None:
    """Log a policy_updated audit event (v0.16.37, review L-13).

    Fired when a tool call policy is mutated via the REST API (PUT or
    PATCH). The event_data carries the full new policy — payloads are
    small, and the prior state is recoverable from the previous event.
    POST /policy/evaluate is a dry-run and is unlogged BY DESIGN.
    """
    await audit_log(
        audit_logger, agent_id, actor,
        SecurityEventType.POLICY_UPDATED,
        {"method": method, "policy": policy},
        None, None, "policy_updated",
    )
