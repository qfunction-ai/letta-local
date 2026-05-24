"""ORM model for tool call policies — per-agent security policy storage."""

import uuid
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta.orm.mixins import OrganizationMixin
from letta.orm.sqlalchemy_base import SqlalchemyBase


class ToolCallPolicyModel(SqlalchemyBase, OrganizationMixin):
    """Per-agent security policy for tool calls.

    Stored in a separate table (not on the agent state) to avoid
    modifying shared schema files. The policy is loaded when the
    agent is initialized and cached for the step.

    The policy JSON blob has the shape:
    {
        "denied_tools": ["web_search", ...],
        "approval_required_tools": ["archival_memory_insert", ...]
    }
    """

    __tablename__ = "tool_call_policies"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: f"tcp-{uuid.uuid4()}",
        doc="Primary key.",
    )
    agent_id: Mapped[str] = mapped_column(
        String, ForeignKey("agents.id"), unique=True,
        doc="The agent this policy belongs to.",
    )
    policy: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        doc="ToolCallPolicy as JSON: {denied_tools: [...], approval_required_tools: [...]}",
    )
    created_at: Mapped[Optional[object]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), doc="Timestamp when the policy was created.",
    )
    updated_at: Mapped[Optional[object]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        doc="Timestamp when the policy was last updated.",
    )

    agent = relationship("Agent", back_populates="tool_call_policy")
