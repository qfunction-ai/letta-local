"""v0.16.37 (review L-13): policy mutations are audited.

A policy change is a security-relevant event — a removed deny rule is an
attack the old policy would have stopped. Every REST mutation (PUT, PATCH,
DELETE) must emit a POLICY_UPDATED audit event carrying the full policy.

Note: the plan asserted "no DELETE/reset policy endpoint exists (verified)"
— that was wrong. DELETE /{agent_id}/policy exists and resets to allow-all,
silently removing every deny rule. It is audited too (method="delete",
event_data carries the policy being deleted).
"""

from unittest.mock import AsyncMock, patch

import pytest

from letta.schemas.user import User
from letta.security.audit import SecurityEventType
from letta.security.audit_helpers import log_policy_updated
from letta.server.rest_api.routers.v1.agent_policies import _save_policy
from letta.security.policy import ToolCallPolicy


def _actor() -> User:
    return User(id="user-12345678", organization_id="org-12345678", name="test-user")


class TestLogPolicyUpdatedHelper:
    async def test_event_carries_method_and_full_policy(self):
        audit_logger = AsyncMock()
        policy = ToolCallPolicy(denied_tools=["web_search"]).model_dump()

        with patch("letta.security.audit_helpers.audit_log", new=AsyncMock()) as mock_log:
            await log_policy_updated(audit_logger, "agent-1", _actor(), policy, method="put")

        mock_log.assert_awaited_once()
        args = mock_log.await_args.args
        assert args[3] == SecurityEventType.POLICY_UPDATED
        assert args[4] == {"method": "put", "policy": policy}

    async def test_actor_id_derived_from_actor(self):
        audit_logger = AsyncMock()
        with patch("letta.security.audit_helpers.audit_log", new=AsyncMock()) as mock_log:
            await log_policy_updated(audit_logger, "agent-1", _actor(), {}, method="patch")
        # audit_log derives actor_id/organization_id from the actor internally;
        # the actor must be passed through, not dropped
        assert mock_log.await_args.args[2] is not None  # actor passed through


class TestSavePolicyAudits:
    async def test_save_policy_emits_audit_event(self):
        """_save_policy must emit one POLICY_UPDATED event after the save."""

        class _FakeSession:
            async def execute(self, stmt):
                class _R:
                    def scalar_one_or_none(self):
                        return None

                return _R()

            def add(self, obj):
                pass

            async def flush(self):
                pass

        import contextlib

        @contextlib.asynccontextmanager
        async def _fake_session():
            yield _FakeSession()

        policy = ToolCallPolicy(denied_tools=["web_search"])

        with patch(
            "letta.server.db.db_registry.async_session", new=lambda: _fake_session()
        ), patch(
            "letta.security.audit_helpers.audit_log", new=AsyncMock()
        ) as mock_log:
            await _save_policy("agent-1", _actor(), policy, method="put")

        mock_log.assert_awaited_once()
        args = mock_log.await_args.args
        assert args[3] == SecurityEventType.POLICY_UPDATED
        assert args[4]["method"] == "put"
        assert args[4]["policy"]["denied_tools"] == ["web_search"]


class TestAllMutationEndpointsAudited:
    """Source guard: PUT, PATCH, and DELETE all route through the audit path."""

    def test_every_mutation_endpoint_audits(self):
        from pathlib import Path

        src = Path(
            __import__("letta.server.rest_api.routers.v1.agent_policies", fromlist=["x"]).__file__
        ).read_text()
        assert '_save_policy(agent_id, actor, policy, method="put")' in src
        assert '_save_policy(agent_id, actor, existing, method="patch")' in src
        assert 'method="delete"' in src, "DELETE (reset to allow-all) must be audited — most dangerous mutation"
