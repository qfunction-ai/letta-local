"""ORM model for per-tool-call records."""

import uuid
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from letta.orm.sqlalchemy_base import SqlalchemyBase
from letta.schemas.tool_call import ToolCall as PydanticToolCall


class ToolCall(SqlalchemyBase):
    """Per-tool-call record persisted alongside the step."""

    __tablename__ = "tool_calls"
    __pydantic_model__ = PydanticToolCall

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: f"toolcall-{uuid.uuid4()}"
    )
    step_id: Mapped[str] = mapped_column(
        String, ForeignKey("steps.id", ondelete="CASCADE"), index=True, doc="The step this tool call belongs to."
    )
    organization_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, doc="The organization ID."
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, doc="The agent ID."
    )
    tool_name: Mapped[str] = mapped_column(String, doc="Name of the tool that was executed.")
    tool_args: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, doc="Arguments passed to the tool (JSON)."
    )
    tool_result: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Result returned by the tool."
    )
    duration_ns: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True, doc="Tool execution duration in nanoseconds."
    )
    success: Mapped[bool] = mapped_column(
        Boolean, default=True, doc="Whether the tool execution succeeded."
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Error message if the tool execution failed."
    )
    request_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, doc="Request ID for correlation."
    )
    created_at: Mapped[Optional[object]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), doc="Timestamp when the record was created."
    )

    step = relationship("Step", back_populates="tool_calls")
