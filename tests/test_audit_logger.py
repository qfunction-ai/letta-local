"""Tests for letta.security.audit — AuditLogger and SecurityEventType."""

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from letta.security.audit import AuditLogger, SecurityEventType


class TestSecurityEventType:
    def test_event_type_values(self):
        assert SecurityEventType.TOOL_EXECUTED == "tool_executed"
        assert SecurityEventType.TOOL_DENIED == "tool_denied"
        assert SecurityEventType.TOOL_APPROVAL_REQUESTED == "tool_approval_requested"
        assert SecurityEventType.TOOL_APPROVAL_GRANTED == "tool_approval_granted"
        assert SecurityEventType.TOOL_APPROVAL_DENIED == "tool_approval_denied"
        assert SecurityEventType.POLICY_VIOLATION == "policy_violation"
        assert SecurityEventType.CANARY_DETECTED == "canary_detected"
        assert SecurityEventType.MEMORY_BLOCK_MODIFIED == "memory_block_modified"

    def test_event_type_is_string(self):
        """SecurityEventType is a str enum, so values are strings."""
        assert isinstance(SecurityEventType.TOOL_EXECUTED, str)


def _make_mock_session_factory():
    """Create a mock async session factory for testing.

    Same pattern as test_tool_call_recorder.py — uses
    @asynccontextmanager to match the real db_registry.async_session()
    async context manager interface.
    """
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    @asynccontextmanager
    async def _session_factory():
        yield mock_session

    return _session_factory, mock_session


class TestAuditLogger:
    def test_init(self):
        logger = AuditLogger()
        assert logger is not None

    @pytest.mark.asyncio
    async def test_log_creates_session_and_writes(self):
        factory, mock_session = _make_mock_session_factory()

        with patch("letta.server.db.db_registry") as mock_registry:
            mock_registry.async_session = factory
            logger = AuditLogger()
            await logger.log(
                agent_id="agent-123",
                organization_id="org-456",
                event_type=SecurityEventType.TOOL_EXECUTED,
                event_data={"tool_call_id": "toolcall-789", "tool_name": "core_memory_append"},
                step_id="step-001",
                run_id="run-001",
                actor_id="user-001",
            )

        # Session.add was called once
        mock_session.add.assert_called_once()
        # Session.flush was called once
        mock_session.flush.assert_called_once()

        # Verify the event object
        event = mock_session.add.call_args[0][0]
        assert event.id.startswith("sevt-")
        assert event.agent_id == "agent-123"
        assert event.organization_id == "org-456"
        assert event.event_type == "tool_executed"
        assert event.event_data == {"tool_call_id": "toolcall-789", "tool_name": "core_memory_append"}
        assert event.step_id == "step-001"
        assert event.run_id == "run-001"
        assert event.actor_id == "user-001"

    @pytest.mark.asyncio
    async def test_log_with_minimal_args(self):
        """Only required args — optional fields default to None."""
        factory, mock_session = _make_mock_session_factory()

        with patch("letta.server.db.db_registry") as mock_registry:
            mock_registry.async_session = factory
            logger = AuditLogger()
            await logger.log(
                agent_id="agent-123",
                organization_id="org-456",
                event_type=SecurityEventType.TOOL_DENIED,
                event_data={"tool_name": "web_search", "reason": "policy_violation"},
            )

        event = mock_session.add.call_args[0][0]
        assert event.step_id is None
        assert event.run_id is None
        assert event.actor_id is None
        assert event.event_data == {"tool_name": "web_search", "reason": "policy_violation"}

    @pytest.mark.asyncio
    async def test_log_default_event_data_is_empty_dict(self):
        """event_data defaults to {} if not provided."""
        factory, mock_session = _make_mock_session_factory()

        with patch("letta.server.db.db_registry") as mock_registry:
            mock_registry.async_session = factory
            logger = AuditLogger()
            await logger.log(
                agent_id="agent-123",
                organization_id="org-456",
                event_type=SecurityEventType.CANARY_DETECTED,
            )

        event = mock_session.add.call_args[0][0]
        assert event.event_data == {}

    @pytest.mark.asyncio
    async def test_log_db_failure_propagates(self):
        """DB errors propagate to the caller — the agent loop catches them."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock(side_effect=Exception("DB connection lost"))

        @asynccontextmanager
        async def _factory():
            yield mock_session

        with patch("letta.server.db.db_registry") as mock_registry:
            mock_registry.async_session = _factory
            logger = AuditLogger()
            with pytest.raises(Exception, match="DB connection lost"):
                await logger.log(
                    agent_id="agent-123",
                    organization_id="org-456",
                    event_type=SecurityEventType.TOOL_EXECUTED,
                )

    @pytest.mark.asyncio
    async def test_log_generates_unique_ids(self):
        """Each call generates a unique event ID."""
        factory, mock_session = _make_mock_session_factory()

        with patch("letta.server.db.db_registry") as mock_registry:
            mock_registry.async_session = factory
            logger = AuditLogger()
            await logger.log(
                agent_id="agent-123",
                organization_id="org-456",
                event_type=SecurityEventType.TOOL_EXECUTED,
            )
            await logger.log(
                agent_id="agent-123",
                organization_id="org-456",
                event_type=SecurityEventType.TOOL_EXECUTED,
            )

        # Two calls — two add calls
        assert mock_session.add.call_count == 2
        # Different IDs
        id1 = mock_session.add.call_args_list[0][0][0].id
        id2 = mock_session.add.call_args_list[1][0][0].id
        assert id1 != id2
