"""Policy rule validation errors must identify the offending rule.

The v0.16.22 bug report: a policy PUT with one invalid regex value
returned 400 "Invalid regex pattern: nothing to repeat at position 0"
with no indication WHICH rule failed. Epsilon's client swallowed the
error and the agent ran with no policy at all.

The fix: _to_policy_rule wraps validate_regex_pattern and prefixes the
error with the rule name.
"""
import pytest

from letta.server.rest_api.routers.v1.agent_policies import (
    PolicyConditionRequest,
    PolicyRuleRequest,
    _to_policy_rule,
)


def _rule(name: str, operator: str, value) -> PolicyRuleRequest:
    return PolicyRuleRequest(
        name=name,
        condition=PolicyConditionRequest(field="tool_args.path", operator=operator, value=value),
        action="deny",
        priority=90,
    )


class TestRuleNameInError:
    def test_invalid_matches_regex_names_the_rule(self):
        """Glob-style value under `matches` is an invalid regex — error must
        include the rule name."""
        with pytest.raises(ValueError) as exc_info:
            _to_policy_rule(_rule("block-sensitive-file", "matches", "*confidential*"))
        assert "block-sensitive-file" in str(exc_info.value), (
            f"error should name the rule, got: {exc_info.value}"
        )

    def test_nested_quantifier_pattern_names_the_rule(self):
        """ReDoS-rejected pattern must also name the rule."""
        with pytest.raises(ValueError) as exc_info:
            _to_policy_rule(_rule("redos-guard", "matches", "(a+)+"))
        assert "redos-guard" in str(exc_info.value)

    def test_invalid_rule_pattern_field_names_the_rule(self):
        """The Agent-OS style top-level `pattern` field is validated too."""
        req = _rule("pattern-rule", "contains", "ok")
        req = req.model_copy(update={"pattern": "*broken*"})
        with pytest.raises(ValueError) as exc_info:
            _to_policy_rule(req)
        assert "pattern-rule" in str(exc_info.value)

    def test_valid_contains_rule_passes(self):
        """Plain substring operator accepts glob-style values — this is the
        documented fix for Epsilon's config."""
        rule = _to_policy_rule(_rule("block-sensitive-file", "contains", "*confidential*"))
        assert rule.name == "block-sensitive-file"
        assert rule.condition.value == "*confidential*"

    def test_valid_regex_matches_rule_passes(self):
        rule = _to_policy_rule(_rule("regex-ok", "matches", "confidential"))
        assert rule.name == "regex-ok"
