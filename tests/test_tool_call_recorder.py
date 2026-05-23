"""Tests for letta.observability.tool_call_recorder — ToolCallRecorder."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from letta.observability.tool_call_recorder import ToolCallRecorder


def _make_mock_session_factory(mock_session):
    """Create an async context manager that returns mock_session."""
    @asynccontextmanager
    async def _factory():
        yield mock_session
    return _factory


@pytest.fixture
def recorder():
    return ToolCallRecorder()


class TestToolCallRecorder:
    @pytest.mark.asyncio
    async def test_record_tool_call_creates_session(self, recorder):
        """record_tool_call creates a session via db_registry and adds a ToolCall."""
        mock_session = AsyncMock()
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("letta.server.db.db_registry") as mock_db:
            mock_db.async_session.return_value = mock_factory()
            await recorder.record_tool_call(
                step_id="step-1",
                agent_id="agent-1",
                organization_id="org-1",
                tool_name="core_memory_append",
                tool_args={"content": "hello"},
                tool_result="OK",
                duration_ns=500_000_000,
                success=True,
            )
            mock_session.add.assert_called_once()
            mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_long_result(self, recorder):
        """Tool results longer than 10000 chars are truncated."""
        mock_session = AsyncMock()
        mock_factory = _make_mock_session_factory(mock_session)
        long_result = "x" * 15000

        with patch("letta.server.db.db_registry") as mock_db:
            mock_db.async_session.return_value = mock_factory()
            await recorder.record_tool_call(
                step_id="step-1",
                agent_id="agent-1",
                organization_id="org-1",
                tool_name="archival_memory_search",
                tool_args={"query": "test"},
                tool_result=long_result,
                duration_ns=1_000_000_000,
                success=True,
            )
            tool_call_obj = mock_session.add.call_args[0][0]
            assert len(tool_call_obj.tool_result) < 15000
            assert tool_call_obj.tool_result.endswith("...[truncated]")

    @pytest.mark.asyncio
    async def test_no_truncation_for_short_result(self, recorder):
        """Short results are not truncated."""
        mock_session = AsyncMock()
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("letta.server.db.db_registry") as mock_db:
            mock_db.async_session.return_value = mock_factory()
            await recorder.record_tool_call(
                step_id="step-1",
                agent_id="agent-1",
                organization_id="org-1",
                tool_name="send_message",
                tool_args={"message": "hi"},
                tool_result="Message sent",
                duration_ns=100_000_000,
                success=True,
            )
            tool_call_obj = mock_session.add.call_args[0][0]
            assert tool_call_obj.tool_result == "Message sent"

    @pytest.mark.asyncio
    async def test_records_error(self, recorder):
        """Error details are persisted when success=False."""
        mock_session = AsyncMock()
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("letta.server.db.db_registry") as mock_db:
            mock_db.async_session.return_value = mock_factory()
            await recorder.record_tool_call(
                step_id="step-1",
                agent_id="agent-1",
                organization_id="org-1",
                tool_name="core_memory_append",
                tool_args={"content": "test"},
                tool_result=None,
                duration_ns=100_000_000,
                success=False,
                error="Permission denied",
                request_id="req-123",
            )
            tool_call_obj = mock_session.add.call_args[0][0]
            assert tool_call_obj.success is False
            assert tool_call_obj.error == "Permission denied"
            assert tool_call_obj.request_id == "req-123"

    @pytest.mark.asyncio
    async def test_db_failure_propagates(self, recorder):
        """DB errors are not swallowed — they propagate to the caller.

        The agent loop catches these with a try/except around the call.
        """
        mock_session = AsyncMock()
        mock_session.flush = AsyncMock(side_effect=Exception("DB connection lost"))
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("letta.server.db.db_registry") as mock_db:
            mock_db.async_session.return_value = mock_factory()
            with pytest.raises(Exception, match="DB connection lost"):
                await recorder.record_tool_call(
                    step_id="step-1",
                    agent_id="agent-1",
                    organization_id="org-1",
                    tool_name="send_message",
                    tool_args={},
                    tool_result=None,
                    duration_ns=100,
                    success=True,
                )
