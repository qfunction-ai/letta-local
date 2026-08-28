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


class TestLoopDetectionApi:
    """loop_detection API exposure (v0.16.28).

    The engine-side control existed but no API schema could carry it —
    a fully-built security feature unreachable by every real consumer.
    These tests pin the three-field contract, the enabled=True API
    default (Delta review: an API default of False would re-ship the
    silently-decorative control), boundary rejection of unknown keys,
    and — the only test that proves the control FIRES — a functional
    PolicyChecker denial.
    """

    def _req(self, **overrides) -> "LoopDetectionRequest":
        from letta.server.rest_api.routers.v1.agent_policies import LoopDetectionRequest

        return LoopDetectionRequest(**overrides)

    def test_functional_loop_detection_fires(self):
        """THE firing proof (Delta review): PolicyChecker is pure. With
        threshold=2, the third identical call denies with
        matched_rule='loop_detection'."""
        from letta.security.policy import LoopDetectionConfig, PolicyChecker, ToolCallPolicy

        policy = ToolCallPolicy(loop_detection=LoopDetectionConfig(enabled=True, window=5, threshold=2))
        checker = PolicyChecker(policy)
        ctx = {"tool_name": "web_search", "tool_args": {"query": "same"}, "tool_call_count": 0}
        # First call: clean
        d1 = checker.check("web_search", eval_context=ctx)
        assert d1.allowed
        checker.record_call("web_search", {"query": "same"})
        # threshold=2 semantics: the SECOND identical call (first repeat) denies
        # (check counts prior occurrences: 1 >= threshold-1 = 1)
        d2 = checker.check("web_search", eval_context=ctx)
        assert not d2.allowed
        assert d2.matched_rule == "loop_detection"

    def test_functional_disabled_never_fires(self):
        from letta.security.policy import LoopDetectionConfig, PolicyChecker, ToolCallPolicy

        policy = ToolCallPolicy(loop_detection=LoopDetectionConfig(enabled=False, window=5, threshold=2))
        checker = PolicyChecker(policy)
        ctx = {"tool_name": "web_search", "tool_args": {"query": "same"}, "tool_call_count": 0}
        for _ in range(5):
            assert checker.check("web_search", eval_context=ctx).allowed
            checker.record_call("web_search", {"query": "same"})

    def test_round_trip_all_three_fields(self):
        from letta.server.rest_api.routers.v1.agent_policies import (
            ToolCallPolicyRequest,
            _to_policy,
            _to_response,
        )

        req = ToolCallPolicyRequest(loop_detection=self._req(enabled=True, window=4, threshold=2))
        policy = _to_policy(req)
        assert policy.loop_detection is not None
        assert policy.loop_detection.enabled is True  # API default True — PUTting the block means ON
        assert policy.loop_detection.window == 4
        assert policy.loop_detection.threshold == 2
        resp = _to_response("agent-test", policy)
        assert resp.loop_detection is not None
        assert (resp.loop_detection.enabled, resp.loop_detection.window, resp.loop_detection.threshold) == (True, 4, 2)

    def test_request_default_enabled_true(self):
        req = self._req()
        assert req.enabled is True
        assert req.window == 5 and req.threshold == 3

    def test_unknown_key_rejected(self):
        """window_size must 422 at the boundary, not silently match
        nothing (the Epsilon audit-cycle burn)."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._req(window_size=5)

    def test_ge_constraints(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._req(window=1)
        with pytest.raises(ValidationError):
            self._req(threshold=1)

    def test_internal_config_forbids_unknown_too(self):
        import pytest
        from pydantic import ValidationError

        from letta.security.policy import LoopDetectionConfig

        with pytest.raises(ValidationError):
            LoopDetectionConfig(window_size=5)
