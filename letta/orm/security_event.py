"""ORM model for security events (audit log)."""

import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from letta.orm.sqlalchemy_base import SqlalchemyBase


class SecurityEvent(SqlalchemyBase):
    """Append-only security event record for the audit log.

    No updates, no deletes. The table is the source of truth for
    incident reconstruction. The audit log is the timeline, not the
    data store — TOOL_EXECUTED events reference the ToolCall record
    by ID rather than duplicating tool args, results, or duration.
    """

    __tablename__ = "security_events"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: f"sevt-{uuid.uuid4()}"
    )
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id", ondelete="CASCADE"), index=True, doc="The agent that produced this event."
    )
    organization_id: Mapped[str] = mapped_column(
        String, ForeignKey("organizations.id"), doc="The organization ID."
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("steps.id"), nullable=True, doc="The step this event occurred during (if applicable)."
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, doc="The run ID this event occurred during (if applicable)."
    )
    event_type: Mapped[str] = mapped_column(
        String, index=True, doc="SecurityEventType value (e.g. tool_executed, tool_denied)."
    )
    event_data: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, doc="Event-type-specific data (JSON blob)."
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, doc="ID of the user or agent that triggered the event."
    )
    created_at: Mapped[Optional[object]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, doc="Timestamp when the event was recorded."
    )
