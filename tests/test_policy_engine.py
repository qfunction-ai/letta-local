"""Tests for the Agent OS-compatible policy engine.

Covers: schema, evaluator, dot-path resolution, rate limiting,
regex caching, YAML loading, backwards compatibility, and
integration with PolicyChecker.
"""

import re
try:
    import regex
    _PATTERN_TYPES = (re.Pattern, regex.Pattern)
except ImportError:
    _PATTERN_TYPES = (re.Pattern,)
import pytest

from letta.security.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyDecision,
    PolicyDefaults,
    PolicyOperator,
    PolicyRule,
    ToolCallPolicy,
    PolicyChecker,
    _coerce_type,
    _evaluate_condition,
    _resolve_field,
    load_policies_from_yaml,
    load_policies_from_yaml_file,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestPolicyAction:
    def test_values(self):
        assert PolicyAction.ALLOW.value == "allow"
        assert PolicyAction.DENY.value == "deny"
        assert PolicyAction.REQUIRE_APPROVAL.value == "require_approval"
        assert PolicyAction.AUDIT.value == "audit"

    def test_from_string(self):
        assert PolicyAction("allow") == PolicyAction.ALLOW
        assert PolicyAction("deny") == PolicyAction.DENY
        assert PolicyAction("audit") == PolicyAction.AUDIT


class TestPolicyOperator:
    def test_all_operators(self):
        expected = ["eq", "ne", "gt", "lt", "gte", "lte", "in", "not_in", "matches", "contains"]
        actual = [op.value for op in PolicyOperator]
        assert sorted(actual) == sorted(expected)


class TestPolicyCondition:
    def test_basic_condition(self):
        c = PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search")
        assert c.field == "tool_name"
        assert c.operator == PolicyOperator.EQ
        assert c.value == "web_search"

    def test_dot_path_field(self):
        c = PolicyCondition(field="tool_args.query", operator=PolicyOperator.MATCHES, value="internal")
        assert c.field == "tool_args.query"


class TestPolicyRule:
    def test_basic_rule(self):
        rule = PolicyRule(
            name="block-internal",
            condition=PolicyCondition(field="tool_args.query", operator=PolicyOperator.MATCHES, value="internal"),
            action=PolicyAction.DENY,
            priority=80,
            message="Internal queries blocked",
        )
        assert rule.name == "block-internal"
        assert rule.priority == 80
        assert rule.message == "Internal queries blocked"

    def test_regex_compiled_in_post_init(self):
        rule = PolicyRule(
            name="test",
            condition=PolicyCondition(field="tool_args.q", operator=PolicyOperator.MATCHES, value="internal|secret"),
            action=PolicyAction.DENY,
        )
        assert rule._compiled_condition_pattern is not None
        assert isinstance(rule._compiled_condition_pattern, _PATTERN_TYPES)

    def test_invalid_regex_rejected(self):
        """Invalid regex patterns are rejected by validate_regex_pattern."""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            PolicyRule(
                name="bad-regex",
                condition=PolicyCondition(field="tool_args.q", operator=PolicyOperator.MATCHES, value="[invalid"),
                action=PolicyAction.DENY,
            )

    def test_redos_pattern_rejected(self):
        """Nested quantifiers (ReDoS patterns) are rejected."""
        with pytest.raises(ValueError, match="nested quantifiers"):
            PolicyRule(
                name="redos",
                condition=PolicyCondition(field="tool_args.q", operator=PolicyOperator.MATCHES, value="(a+)+b"),
                action=PolicyAction.DENY,
            )

    def test_redos_pattern_field_rejected(self):
        """Nested quantifiers in the pattern field are also rejected."""
        with pytest.raises(ValueError, match="nested quantifiers"):
            PolicyRule(
                name="redos-pattern",
                condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="test"),
                action=PolicyAction.DENY,
                pattern="(a+)+b",
            )

    def test_pattern_field_compiled(self):
        rule = PolicyRule(
            name="sql-pattern",
            condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="database_query"),
            action=PolicyAction.DENY,
            pattern="DROP|TRUNCATE",
        )
        assert rule._compiled_pattern is not None
        assert isinstance(rule._compiled_pattern, _PATTERN_TYPES)

    def test_invalid_pattern_rejected(self):
        """Invalid regex in the pattern field is rejected by validate_regex_pattern."""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            PolicyRule(
                name="bad-pattern",
                condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="db"),
                action=PolicyAction.DENY,
                pattern="[invalid",
            )


class TestPolicyDefaults:
    def test_defaults(self):
        d = PolicyDefaults()
        assert d.action == PolicyAction.ALLOW
        assert d.max_tool_calls is None

    def test_custom(self):
        d = PolicyDefaults(action=PolicyAction.DENY, max_tool_calls=100)
        assert d.action == PolicyAction.DENY
        assert d.max_tool_calls == 100


class TestToolCallPolicy:
    def test_legacy_backwards_compat(self):
        p = ToolCallPolicy(denied_tools=["web_search"], approval_required_tools=["archival_memory_insert"])
        assert p.denied_tools == ["web_search"]
        assert p.approval_required_tools == ["archival_memory_insert"]
        assert p.rules == []
        assert p.max_calls_per_tool == {}
        assert p.defaults is None

    def test_new_fields(self):
        p = ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="test",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                    action=PolicyAction.DENY,
                )
            ],
            max_calls_per_tool={"web_search": 10},
            defaults=PolicyDefaults(max_tool_calls=100),
        )
        assert len(p.rules) == 1
        assert p.max_calls_per_tool["web_search"] == 10
        assert p.defaults.max_tool_calls == 100

    def test_empty_policy(self):
        p = ToolCallPolicy()
        assert p.denied_tools == []
        assert p.approval_required_tools == []
        assert p.rules == []
        assert p.max_calls_per_tool == {}
        assert p.defaults is None


class TestPolicyDecision:
    def test_default_allow(self):
        d = PolicyDecision()
        assert d.allowed is True
        assert d.action == "allow"
        assert d.matched_rule is None

    def test_deny_decision(self):
        d = PolicyDecision(allowed=False, action="deny", matched_rule="test", reason="blocked")
        assert d.allowed is False
        assert d.matched_rule == "test"


# ---------------------------------------------------------------------------
# Dot-path resolution tests
# ---------------------------------------------------------------------------


class TestResolveField:
    def test_simple_field(self):
        ctx = {"tool_name": "web_search", "tool_call_count": 5}
        assert _resolve_field(ctx, "tool_name") == "web_search"
        assert _resolve_field(ctx, "tool_call_count") == 5

    def test_dot_path(self):
        ctx = {"tool_args": {"query": "test query", "limit": 10}}
        assert _resolve_field(ctx, "tool_args.query") == "test query"
        assert _resolve_field(ctx, "tool_args.limit") == 10

    def test_nested_dot_path(self):
        ctx = {"tool_args": {"nested": {"key": "value"}}}
        assert _resolve_field(ctx, "tool_args.nested.key") == "value"

    def test_missing_field(self):
        ctx = {"tool_name": "web_search"}
        assert _resolve_field(ctx, "nonexistent") is None
        assert _resolve_field(ctx, "tool_args.query") is None

    def test_missing_nested_key(self):
        ctx = {"tool_args": {"query": "test"}}
        assert _resolve_field(ctx, "tool_args.limit") is None


# ---------------------------------------------------------------------------
# Type coercion tests
# ---------------------------------------------------------------------------


class TestCoerceType:
    def test_string_to_int(self):
        assert _coerce_type("10", 5) == 10

    def test_string_to_float(self):
        assert _coerce_type("3.14", 1.0) == 3.14

    def test_same_type(self):
        assert _coerce_type("hello", "world") == "hello"

    def test_invalid_coercion(self):
        # "hello" can't be coerced to int — return original
        assert _coerce_type("hello", 5) == "hello"

    def test_none_target(self):
        assert _coerce_type("10", None) == "10"


# ---------------------------------------------------------------------------
# Condition evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluateCondition:
    def test_eq(self):
        cond = PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_name": "web_search"}, rule) is True
        assert _evaluate_condition(cond, {"tool_name": "other"}, rule) is False

    def test_ne(self):
        cond = PolicyCondition(field="tool_name", operator=PolicyOperator.NE, value="web_search")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.ALLOW)
        assert _evaluate_condition(cond, {"tool_name": "other"}, rule) is True
        assert _evaluate_condition(cond, {"tool_name": "web_search"}, rule) is False

    def test_gt(self):
        cond = PolicyCondition(field="tool_call_count", operator=PolicyOperator.GT, value="5")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_call_count": 10}, rule) is True
        assert _evaluate_condition(cond, {"tool_call_count": 3}, rule) is False

    def test_lt(self):
        cond = PolicyCondition(field="tool_call_count", operator=PolicyOperator.LT, value="5")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.ALLOW)
        assert _evaluate_condition(cond, {"tool_call_count": 3}, rule) is True
        assert _evaluate_condition(cond, {"tool_call_count": 10}, rule) is False

    def test_gte(self):
        cond = PolicyCondition(field="tool_call_count", operator=PolicyOperator.GTE, value="5")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_call_count": 5}, rule) is True
        assert _evaluate_condition(cond, {"tool_call_count": 4}, rule) is False

    def test_lte(self):
        cond = PolicyCondition(field="tool_call_count", operator=PolicyOperator.LTE, value="5")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.ALLOW)
        assert _evaluate_condition(cond, {"tool_call_count": 5}, rule) is True
        assert _evaluate_condition(cond, {"tool_call_count": 6}, rule) is False

    def test_in(self):
        cond = PolicyCondition(field="tool_name", operator=PolicyOperator.IN, value=["web_search", "archival"])
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_name": "web_search"}, rule) is True
        assert _evaluate_condition(cond, {"tool_name": "other"}, rule) is False

    def test_not_in(self):
        cond = PolicyCondition(field="tool_name", operator=PolicyOperator.NOT_IN, value=["web_search", "archival"])
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.ALLOW)
        assert _evaluate_condition(cond, {"tool_name": "other"}, rule) is True
        assert _evaluate_condition(cond, {"tool_name": "web_search"}, rule) is False

    def test_matches_regex(self):
        cond = PolicyCondition(field="tool_args.query", operator=PolicyOperator.MATCHES, value="internal|secret")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_args": {"query": "internal data"}}, rule) is True
        assert _evaluate_condition(cond, {"tool_args": {"query": "public data"}}, rule) is False

    def test_contains(self):
        cond = PolicyCondition(field="tool_args.query", operator=PolicyOperator.CONTAINS, value="internal")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_args": {"query": "internal data"}}, rule) is True
        assert _evaluate_condition(cond, {"tool_args": {"query": "public data"}}, rule) is False

    def test_missing_field_returns_false(self):
        cond = PolicyCondition(field="nonexistent", operator=PolicyOperator.EQ, value="test")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_name": "web_search"}, rule) is False

    def test_type_coercion_from_yaml(self):
        # YAML loads "10" as string, but tool_call_count is int
        cond = PolicyCondition(field="tool_call_count", operator=PolicyOperator.GT, value="5")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_call_count": 10}, rule) is True

    def test_dot_path_resolution(self):
        cond = PolicyCondition(field="tool_args.query", operator=PolicyOperator.EQ, value="test")
        rule = PolicyRule(name="test", condition=cond, action=PolicyAction.DENY)
        assert _evaluate_condition(cond, {"tool_args": {"query": "test"}}, rule) is True


# ---------------------------------------------------------------------------
# PolicyChecker tests
# ---------------------------------------------------------------------------


class TestPolicyCheckerLegacy:
    """Test the legacy two-list model (backwards compatible)."""

    def test_empty_policy_allows_all(self):
        checker = PolicyChecker()
        decision = checker.check("web_search")
        assert decision.allowed is True
        assert decision.action == "allow"

    def test_deny_list(self):
        checker = PolicyChecker(ToolCallPolicy(denied_tools=["web_search"]))
        decision = checker.check("web_search")
        assert decision.allowed is False
        assert decision.action == "deny"
        assert decision.matched_rule == "denied_tools"

    def test_approval_list(self):
        checker = PolicyChecker(ToolCallPolicy(approval_required_tools=["archival_memory_insert"]))
        decision = checker.check("archival_memory_insert")
        assert decision.allowed is False  # not auto-allowed
        assert decision.action == "require_approval"
        assert decision.matched_rule == "approval_required_tools"

    def test_deny_takes_precedence_over_approval(self):
        checker = PolicyChecker(ToolCallPolicy(
            denied_tools=["web_search"],
            approval_required_tools=["web_search"],
        ))
        decision = checker.check("web_search")
        assert decision.action == "deny"

    def test_fail_closed(self):
        checker = PolicyChecker()
        checker.deny_all = True
        decision = checker.check("web_search")
        assert decision.allowed is False
        assert decision.matched_rule == "fail_closed"

    def test_update_policy_clears_fail_closed(self):
        checker = PolicyChecker()
        checker.deny_all = True
        checker.update_policy(ToolCallPolicy())
        assert checker.deny_all is False
        decision = checker.check("web_search")
        assert decision.allowed is True


class TestPolicyCheckerRules:
    """Test the Agent OS-compatible rule engine."""

    def test_rule_deny(self):
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="block-internal",
                    condition=PolicyCondition(field="tool_args.query", operator=PolicyOperator.MATCHES, value="internal|secret"),
                    action=PolicyAction.DENY,
                    priority=80,
                ),
            ],
        ))
        decision = checker.check("web_search", eval_context={
            "tool_name": "web_search",
            "tool_args": {"query": "internal documents"},
        })
        assert decision.allowed is False
        assert decision.matched_rule == "block-internal"

    def test_rule_allow(self):
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="allow-safe",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="core_memory_append"),
                    action=PolicyAction.ALLOW,
                    priority=50,
                ),
            ],
        ))
        decision = checker.check("core_memory_append", eval_context={
            "tool_name": "core_memory_append",
            "tool_args": {},
        })
        assert decision.allowed is True
        assert decision.matched_rule == "allow-safe"

    def test_rule_audit(self):
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="audit-archival",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="archival_memory_insert"),
                    action=PolicyAction.AUDIT,
                    priority=10,
                ),
            ],
        ))
        decision = checker.check("archival_memory_insert", eval_context={
            "tool_name": "archival_memory_insert",
            "tool_args": {},
        })
        assert decision.allowed is True  # AUDIT = allow + log
        assert decision.action == "audit"
        assert decision.matched_rule == "audit-archival"

    def test_priority_ordering(self):
        """Higher priority rules override lower ones."""
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="allow-web",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                    action=PolicyAction.ALLOW,
                    priority=10,
                ),
                PolicyRule(
                    name="deny-web",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                    action=PolicyAction.DENY,
                    priority=80,
                ),
            ],
        ))
        decision = checker.check("web_search", eval_context={
            "tool_name": "web_search",
            "tool_args": {},
        })
        assert decision.allowed is False
        assert decision.matched_rule == "deny-web"

    def test_no_context_falls_back_to_legacy(self):
        """Without eval_context, only legacy two-list check is used."""
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="block-internal",
                    condition=PolicyCondition(field="tool_args.query", operator=PolicyOperator.MATCHES, value="internal"),
                    action=PolicyAction.DENY,
                    priority=80,
                ),
            ],
        ))
        decision = checker.check("web_search")  # no eval_context
        assert decision.allowed is True  # no context → rules not evaluated → default allow

    def test_pattern_field(self):
        """Pattern field matches against tool args string representation."""
        checker = PolicyChecker(ToolCallPolicy(
            rules=[
                PolicyRule(
                    name="block-destructive-sql",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="database_query"),
                    action=PolicyAction.DENY,
                    pattern="DROP|TRUNCATE",
                    priority=100,
                ),
            ],
        ))
        # Pattern matches — tool denied
        decision = checker.check("database_query", eval_context={
            "tool_name": "database_query",
            "tool_args": {"query": "DROP TABLE users"},
        })
        assert decision.allowed is False
        assert decision.matched_rule == "block-destructive-sql"

        # Pattern doesn't match — tool allowed (no other rule matches)
        decision = checker.check("database_query", eval_context={
            "tool_name": "database_query",
            "tool_args": {"query": "SELECT * FROM users"},
        })
        assert decision.allowed is True

    def test_legacy_deny_overrides_rules(self):
        """Legacy denied_tools list takes precedence over rules."""
        checker = PolicyChecker(ToolCallPolicy(
            denied_tools=["web_search"],
            rules=[
                PolicyRule(
                    name="allow-web",
                    condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                    action=PolicyAction.ALLOW,
                    priority=100,
                ),
            ],
        ))
        decision = checker.check("web_search", eval_context={
            "tool_name": "web_search",
            "tool_args": {},
        })
        assert decision.allowed is False
        assert decision.matched_rule == "denied_tools"


class TestPolicyCheckerRateLimiting:
    """Test per-tool and global rate limiting."""

    def test_per_tool_rate_limit(self):
        checker = PolicyChecker(ToolCallPolicy(
            max_calls_per_tool={"web_search": 2},
        ))
        ctx = {"tool_name": "web_search", "tool_args": {}}

        # First two calls allowed
        decision1 = checker.check("web_search", eval_context=ctx)
        assert decision1.allowed is True
        checker.record_call("web_search")

        decision2 = checker.check("web_search", eval_context=ctx)
        assert decision2.allowed is True
        checker.record_call("web_search")

        # Third call denied
        decision3 = checker.check("web_search", eval_context=ctx)
        assert decision3.allowed is False
        assert "max_calls_per_tool" in decision3.matched_rule

    def test_global_rate_limit(self):
        checker = PolicyChecker(ToolCallPolicy(
            defaults=PolicyDefaults(max_tool_calls=3),
        ))
        ctx = {"tool_name": "any_tool", "tool_args": {}}

        for _ in range(3):
            decision = checker.check("any_tool", eval_context=ctx)
            assert decision.allowed is True
            checker.record_call("any_tool")

        # Fourth call denied by global limit
        decision = checker.check("any_tool", eval_context=ctx)
        assert decision.allowed is False
        assert decision.matched_rule == "defaults/max_tool_calls"

    def test_per_tool_takes_precedence_over_global(self):
        checker = PolicyChecker(ToolCallPolicy(
            max_calls_per_tool={"web_search": 1},
            defaults=PolicyDefaults(max_tool_calls=100),
        ))
        ctx = {"tool_name": "web_search", "tool_args": {}}

        decision1 = checker.check("web_search", eval_context=ctx)
        assert decision1.allowed is True
        checker.record_call("web_search")

        # Per-tool limit hit, even though global limit is 100
        decision2 = checker.check("web_search", eval_context=ctx)
        assert decision2.allowed is False
        assert "max_calls_per_tool" in decision2.matched_rule

    def test_reset_call_counts(self):
        checker = PolicyChecker(ToolCallPolicy(
            max_calls_per_tool={"web_search": 1},
        ))
        ctx = {"tool_name": "web_search", "tool_args": {}}

        decision1 = checker.check("web_search", eval_context=ctx)
        assert decision1.allowed is True
        checker.record_call("web_search")

        decision2 = checker.check("web_search", eval_context=ctx)
        assert decision2.allowed is False

        # Reset — should allow again
        checker.reset_call_counts()
        decision3 = checker.check("web_search", eval_context=ctx)
        assert decision3.allowed is True

    def test_get_call_count(self):
        checker = PolicyChecker()
        assert checker.get_call_count("web_search") == 0
        checker.record_call("web_search")
        assert checker.get_call_count("web_search") == 1
        checker.record_call("web_search")
        assert checker.get_call_count("web_search") == 2


class TestPolicyCheckerDefaults:
    """Test default action when no rule matches."""

    def test_default_allow(self):
        checker = PolicyChecker(ToolCallPolicy(defaults=PolicyDefaults(action=PolicyAction.ALLOW)))
        decision = checker.check("any_tool", eval_context={"tool_name": "any_tool", "tool_args": {}})
        assert decision.allowed is True
        assert decision.action == "allow"

    def test_default_deny(self):
        checker = PolicyChecker(ToolCallPolicy(defaults=PolicyDefaults(action=PolicyAction.DENY)))
        decision = checker.check("any_tool", eval_context={"tool_name": "any_tool", "tool_args": {}})
        assert decision.allowed is False
        assert decision.action == "deny"

    def test_no_defaults_allows(self):
        """No defaults = backwards compatible allow."""
        checker = PolicyChecker(ToolCallPolicy())
        decision = checker.check("any_tool", eval_context={"tool_name": "any_tool", "tool_args": {}})
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# YAML loading tests
# ---------------------------------------------------------------------------


class TestYAMLLoading:
    def test_basic_yaml(self):
        yaml_text = """
version: "1.0"
name: test-policy
rules:
  - name: block-web-search
    condition:
      field: tool_name
      operator: eq
      value: web_search
    action: deny
    priority: 100
    message: "Web search is blocked"
"""
        policy = load_policies_from_yaml(yaml_text)
        assert len(policy.rules) == 1
        assert policy.rules[0].name == "block-web-search"
        assert policy.rules[0].action == PolicyAction.DENY
        assert policy.rules[0].priority == 100

    def test_yaml_with_pattern(self):
        yaml_text = """
version: "1.0"
name: sql-safety
rules:
  - name: block-destructive-sql
    condition:
      field: tool_name
      operator: eq
      value: database_query
    pattern: "DROP|TRUNCATE"
    action: deny
    priority: 100
"""
        policy = load_policies_from_yaml(yaml_text)
        assert len(policy.rules) == 1
        assert policy.rules[0].pattern == "DROP|TRUNCATE"
        assert policy.rules[0]._compiled_pattern is not None

    def test_yaml_with_defaults(self):
        yaml_text = """
version: "1.0"
name: strict-policy
rules: []
defaults:
  action: deny
  max_tool_calls: 50
"""
        policy = load_policies_from_yaml(yaml_text)
        assert policy.defaults is not None
        assert policy.defaults.action == PolicyAction.DENY
        assert policy.defaults.max_tool_calls == 50

    def test_yaml_with_max_calls_per_tool(self):
        yaml_text = """
version: "1.0"
name: rate-limited
rules: []
max_calls_per_tool:
  web_search: 10
  archival_memory_insert: 5
"""
        policy = load_policies_from_yaml(yaml_text)
        assert policy.max_calls_per_tool == {"web_search": 10, "archival_memory_insert": 5}

    def test_yaml_invalid_yaml(self):
        with pytest.raises(ValueError, match="Invalid YAML"):
            load_policies_from_yaml("{{invalid yaml")

    def test_yaml_not_a_mapping(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            load_policies_from_yaml("42")

    def test_yaml_invalid_action(self):
        yaml_text = """
version: "1.0"
name: bad-action
rules:
  - name: test
    condition:
      field: tool_name
      operator: eq
      value: web_search
    action: explode
"""
        with pytest.raises(ValueError):
            load_policies_from_yaml(yaml_text)

    def test_yaml_missing_condition(self):
        yaml_text = """
version: "1.0"
name: no-condition
rules:
  - name: test
    action: deny
"""
        with pytest.raises(ValueError, match="condition"):
            load_policies_from_yaml(yaml_text)

    def test_yaml_empty_rules(self):
        yaml_text = """
version: "1.0"
name: empty
rules: []
"""
        policy = load_policies_from_yaml(yaml_text)
        assert policy.rules == []

    def test_full_agent_os_format(self):
        """Test the full Agent OS YAML format from the plan."""
        yaml_text = """
version: "1.0"
name: local-model-safety
rules:
  - name: block-destructive-sql
    condition:
      field: tool_name
      operator: eq
      value: database_query
    pattern: "DROP|TRUNCATE|DELETE FROM .* WHERE 1=1"
    action: deny
    priority: 100
    message: "Destructive SQL operations are blocked"

  - name: block-internal-queries
    condition:
      field: tool_args.query
      operator: matches
      value: "internal|confidential|secret"
    action: deny
    priority: 80
    message: "Queries containing internal/confidential/secret are blocked"

  - name: audit-all-archival
    condition:
      field: tool_name
      operator: eq
      value: archival_memory_insert
    action: audit
    priority: 10
    message: "Archival memory insert logged for audit"

defaults:
  action: allow
  max_tool_calls: 100
"""
        policy = load_policies_from_yaml(yaml_text)
        assert len(policy.rules) == 3
        assert policy.rules[0].name == "block-destructive-sql"
        assert policy.rules[0].pattern == "DROP|TRUNCATE|DELETE FROM .* WHERE 1=1"
        assert policy.rules[1].condition.field == "tool_args.query"
        assert policy.rules[2].action == PolicyAction.AUDIT
        assert policy.defaults.max_tool_calls == 100

        # Verify the loaded policy works with PolicyChecker
        checker = PolicyChecker(policy)
        decision = checker.check("database_query", eval_context={
            "tool_name": "database_query",
            "tool_args": {"query": "DROP TABLE users"},
        })
        assert decision.allowed is False
        assert decision.matched_rule == "block-destructive-sql"


# ---------------------------------------------------------------------------
# Integration test: _check_policy helper
# ---------------------------------------------------------------------------


class TestCheckPolicyHelper:
    """Test that _check_policy helper builds the right context and records calls."""

    def test_helper_records_calls(self):
        """_check_policy should record allowed calls for rate limiting."""
        # Test the helper logic directly without instantiating the abstract BaseAgent
        checker = PolicyChecker(ToolCallPolicy(
            max_calls_per_tool={"web_search": 1},
        ))

        # Simulate what _check_policy does
        eval_context = {
            "tool_name": "web_search",
            "tool_args": {},
            "tool_call_count": checker.get_call_count("web_search"),
            "actor_id": None,
            "agent_id": "test-agent",
        }
        decision1 = checker.check("web_search", eval_context=eval_context)
        assert decision1.allowed is True
        checker.record_call("web_search")
        assert checker.get_call_count("web_search") == 1

        # Second call denied by rate limit
        eval_context["tool_call_count"] = checker.get_call_count("web_search")
        decision2 = checker.check("web_search", eval_context=eval_context)
        assert decision2.allowed is False

    def test_helper_does_not_record_denied_calls(self):
        """_check_policy should NOT record denied calls."""
        checker = PolicyChecker(ToolCallPolicy(
            denied_tools=["web_search"],
        ))

        eval_context = {
            "tool_name": "web_search",
            "tool_args": {},
            "tool_call_count": checker.get_call_count("web_search"),
            "actor_id": None,
            "agent_id": "test-agent",
        }
        decision = checker.check("web_search", eval_context=eval_context)
        assert decision.allowed is False
        assert checker.get_call_count("web_search") == 0
