"""Pydantic schema for per-tool-call records."""

from datetime import datetime
from typing import Dict, Optional

from pydantic import Field

from letta.schemas.enums import PrimitiveType
from letta.schemas.letta_base import LettaBase


class ToolCallBase(LettaBase):
    __id_prefix__ = PrimitiveType.TOOL_CALL.value


class ToolCall(ToolCallBase):
    id: str = Field(..., description="Unique identifier for the tool call.")
    step_id: str = Field(..., description="The step this tool call belongs to.")
    organization_id: Optional[str] = Field(None, description="The organization ID.")
    agent_id: Optional[str] = Field(None, description="The agent ID.")
    tool_name: str = Field(..., description="Name of the tool that was executed.")
    tool_args: Optional[Dict] = Field(None, description="Arguments passed to the tool (JSON).")
    tool_result: Optional[str] = Field(None, description="Result returned by the tool.")
    duration_ns: Optional[int] = Field(None, description="Tool execution duration in nanoseconds.")
    success: bool = Field(True, description="Whether the tool execution succeeded.")
    error: Optional[str] = Field(None, description="Error message if the tool execution failed.")
    request_id: Optional[str] = Field(None, description="Request ID for correlation.")
    created_at: Optional[datetime] = Field(None, description="Timestamp when the record was created.")
