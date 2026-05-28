"""Tests for letta.security.policy — PolicyChecker, ToolCallPolicy, PolicyAction."""

import pytest
from letta.security.policy import PolicyAction, PolicyChecker, ToolCallPolicy


class TestPolicyAction:
    def test_action_values(self):
        assert PolicyAction.ALLOW == "allow"
        assert PolicyAction.DENY == "deny"
        assert PolicyAction.REQUIRE_APPROVAL == "require_approval"

    def test_action_is_string(self):
        assert isinstance(PolicyAction.ALLOW, str)


class TestToolCallPolicy:
    def test_default_policy_is_permissive(self):
        policy = ToolCallPolicy()
        assert policy.denied_tools == []
        assert policy.approval_required_tools == []

    def test_policy_with_denied_tools(self):
        policy = ToolCallPolicy(denied_tools=["web_search", "fetch_webpage"])
        assert len(policy.denied_tools) == 2
        assert "web_search" in policy.denied_tools

    def test_policy_with_approval_tools(self):
        policy = ToolCallPolicy(approval_required_tools=["archival_memory_insert"])
        assert len(policy.approval_required_tools) == 1

    def test_policy_serialization(self):
        policy = ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["archival_memory_insert"],
        )
        data = policy.model_dump()
        restored = ToolCallPolicy(**data)
        assert restored.denied_tools == ["web_search"]
        assert restored.approval_required_tools == ["archival_memory_insert"]

    def test_policy_json_roundtrip(self):
        policy = ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["archival_memory_insert"],
        )
        import json
        data = json.loads(policy.model_dump_json())
        restored = ToolCallPolicy(**data)
        assert restored.denied_tools == ["web_search"]
        assert restored.approval_required_tools == ["archival_memory_insert"]


class TestPolicyChecker:
    def test_default_policy_allows_all(self):
        checker = PolicyChecker()
        decision = checker.check("web_search")
        assert decision.allowed is True
        assert decision.action == PolicyAction.ALLOW
        decision = checker.check("core_memory_append")
        assert decision.allowed is True
        decision = checker.check("archival_memory_insert")
        assert decision.allowed is True

    def test_none_policy_allows_all(self):
        checker = PolicyChecker(policy=None)
        decision = checker.check("web_search")
        assert decision.allowed is True

    def test_deny_takes_precedence(self):
        """If a tool is in both denied and approval_required, deny wins."""
        policy = ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["web_search"],
        )
        checker = PolicyChecker(policy=policy)
        decision = checker.check("web_search")
        assert decision.allowed is False
        assert decision.action == PolicyAction.DENY

    def test_denied_tool(self):
        policy = ToolCallPolicy(denied_tools=["web_search", "fetch_webpage"])
        checker = PolicyChecker(policy=policy)
        decision = checker.check("web_search")
        assert decision.allowed is False
        assert decision.action == PolicyAction.DENY
        decision = checker.check("fetch_webpage")
        assert decision.allowed is False
        assert decision.action == PolicyAction.DENY
        decision = checker.check("core_memory_append")
        assert decision.allowed is True

    def test_approval_required_tool(self):
        policy = ToolCallPolicy(approval_required_tools=["archival_memory_insert", "core_memory_replace"])
        checker = PolicyChecker(policy=policy)
        decision = checker.check("archival_memory_insert")
        assert decision.action == PolicyAction.REQUIRE_APPROVAL
        decision = checker.check("core_memory_replace")
        assert decision.action == PolicyAction.REQUIRE_APPROVAL
        decision = checker.check("core_memory_append")
        assert decision.allowed is True

    def test_mixed_policy(self):
        policy = ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["archival_memory_insert"],
        )
        checker = PolicyChecker(policy=policy)
        decision = checker.check("web_search")
        assert decision.action == PolicyAction.DENY
        decision = checker.check("archival_memory_insert")
        assert decision.action == PolicyAction.REQUIRE_APPROVAL
        decision = checker.check("core_memory_append")
        assert decision.allowed is True

    def test_update_policy(self):
        checker = PolicyChecker()
        decision = checker.check("web_search")
        assert decision.allowed is True

        new_policy = ToolCallPolicy(denied_tools=["web_search"])
        checker.update_policy(new_policy)
        decision = checker.check("web_search")
        assert decision.allowed is False

    def test_update_policy_resets(self):
        checker = PolicyChecker(policy=ToolCallPolicy(denied_tools=["web_search"]))
        decision = checker.check("web_search")
        assert decision.allowed is False

        checker.update_policy(ToolCallPolicy())  # allow all
        decision = checker.check("web_search")
        assert decision.allowed is True
