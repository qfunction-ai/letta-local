"""Tests for the policy AUDIT consumer path (agent_security.check_policy).

v0.16.37 (review L-5 + P-7): AUDIT is log-and-continue in the engine, so
collected audit entries are logged regardless of the terminal outcome, and
the model-facing audit_warning text is gated to memory-write tools only.
"""

from types import SimpleNamespace

import pytest

from letta.security.agent_security import check_policy
from letta.security.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyOperator,
    PolicyRule,
    PolicyChecker,
    ToolCallPolicy,
)


def _make_agent(rules=None):
    agent = SimpleNamespace()
    agent.policy_checker = PolicyChecker(ToolCallPolicy(rules=rules or []))
    agent.audit_logger = SimpleNamespace()
    agent.actor = None
    agent.agent_id = "test-agent"
    agent.tool_arg_validation_enabled = False
    return agent


def _secret_audit_rule(priority=90):
    # The real default-rule shape: CONTAINS_SECRET on tool_args
    return PolicyRule(
        name="secret-in-tool-args",
        condition=PolicyCondition(field="tool_args", operator=PolicyOperator.CONTAINS_SECRET, value=True),
        action=PolicyAction.AUDIT,
        priority=priority,
        message="Tool args contain what appears to be secret material.",
    )


def _deny_rule(priority=50):
    return PolicyRule(
        name="deny-web",
        condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
        action=PolicyAction.DENY,
        priority=priority,
    )


SECRET_ARGS = {"note": "AKIAIOSFODNN7EXAMPLE", "query": "hello"}  # AWS Access Key ID shape


class TestAuditEntryLogging:
    @pytest.mark.asyncio
    async def test_secret_audit_entry_logged_even_when_denied(self, monkeypatch):
        """The forensically important case: secret seen AND call denied."""
        from letta.security import audit_helpers
        logged = []

        async def fake_log(logger, agent_id, actor, tool_name, label, step_id, run_id, matched_rule=None):
            logged.append(("secret", tool_name, label, matched_rule))

        monkeypatch.setattr(audit_helpers, "log_secret_detected", fake_log)
        agent = _make_agent(rules=[_secret_audit_rule(), _deny_rule()])
        decision = await check_policy(agent, "web_search", SECRET_ARGS, step_id="s1", run_id="r1")
        assert decision.allowed is False
        assert decision.matched_rule == "deny-web"
        assert len(decision.audit_entries) == 1
        assert logged and logged[0][1] == "web_search"

    @pytest.mark.asyncio
    async def test_custom_audit_rule_logs_generic_event(self, monkeypatch):
        from letta.security import audit_helpers
        logged = []

        async def fake_log(logger, agent_id, actor, tool_name, matched_rule, step_id, run_id):
            logged.append(("audit", tool_name, matched_rule))

        monkeypatch.setattr(audit_helpers, "log_policy_audit_matched", fake_log)
        agent = _make_agent(rules=[
            PolicyRule(
                name="watch-filesystem",
                condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="file_read"),
                action=PolicyAction.AUDIT,
                priority=90,
            ),
        ])
        decision = await check_policy(agent, "file_read", {"path": "/etc/hosts"})
        assert decision.allowed is True
        assert logged == [("audit", "file_read", "watch-filesystem")]


class TestAuditWarningGate:
    """P-7: warning text only for memory-write tools; event fires for all."""

    @pytest.mark.asyncio
    async def test_memory_write_tool_gets_warning(self, monkeypatch):
        from letta.security import audit_helpers

        async def noop(*a, **k):
            pass

        monkeypatch.setattr(audit_helpers, "log_secret_detected", noop)
        agent = _make_agent(rules=[_secret_audit_rule()])
        decision = await check_policy(agent, "memory_insert", SECRET_ARGS)
        assert decision.audit_warning is not None
        assert "secret" in decision.audit_warning.lower()

    @pytest.mark.asyncio
    async def test_non_memory_tool_no_warning_but_event_logged(self, monkeypatch):
        from letta.security import audit_helpers
        logged = []

        async def fake_log(logger, agent_id, actor, tool_name, label, step_id, run_id, matched_rule=None):
            logged.append(tool_name)

        monkeypatch.setattr(audit_helpers, "log_secret_detected", fake_log)
        agent = _make_agent(rules=[_secret_audit_rule()])
        decision = await check_policy(agent, "web_search", SECRET_ARGS)
        # Event logged, but no model-facing warning
        assert decision.audit_warning is None
        assert logged == ["web_search"]

    def test_default_rule_name_is_secret_in_tool_args(self):
        from letta.security.agent_security import _inject_default_secret_rule

        agent = _make_agent()
        _inject_default_secret_rule(agent)
        names = [r.name for r in agent.policy_checker.policy.rules]
        assert "secret-in-tool-args" in names
        assert "secret-in-memory-write" not in names
