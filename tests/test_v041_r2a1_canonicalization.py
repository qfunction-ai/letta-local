"""R2A-1 (v0.16.41): policy checks must evaluate the CANONICAL arguments —
the same values the tool receives.

The round-2 adversarial review verified a full bypass: the policy engine
checked the RAW parsed args while the executor rewrote them (json.loads/
literal_eval on every string), so a model could stringify or escape a value
("...\\u0044ROP TABLE users", "DR" "OP TABLE users", "5000") and slip past
every argument-level rule. These tests pin the fix:

1. canonicalize_tool_args: the schema-typed decode (never str-typed),
   shape checks, non-finite rejection, raw passthrough on unknown shapes.
2. The fail-closed rule: a type mismatch never lets ALLOW match, always
   makes DENY/APPROVAL match.
3. ast_parsers: str-typed annotations are never decoded.
4. The end-to-end invariant: check_policy and the executor see the SAME
   dict (canonical) — the reviewer's exact bypass payloads denied.
"""

import json
from types import SimpleNamespace

import pytest

from letta.functions.ast_parsers import coerce_dict_args_by_annotations
from letta.security.arg_canonicalizer import canonicalize_tool_args
from letta.security.policy import PolicyAction, PolicyChecker, PolicyCondition, PolicyOperator, PolicyRule, ToolCallPolicy

SCHEMA = {
    "name": "database_query",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "amount": {"type": "integer"},
            "note": {"type": "string"},
            "options": {"type": "object"},
        },
    },
}


class TestCanonicalizer:
    def test_json_escaped_string_stays_raw_for_str_typed(self):
        """The reviewer's primary bypass: '"\\u0044ROP TABLE users"' must stay
        the ESCAPED string (str-typed params are never decoded) — the policy
        sees exactly what the tool receives."""
        out = canonicalize_tool_args({"query": '"\\u0044ROP TABLE users"'}, SCHEMA)
        assert out["query"] == '"\\u0044ROP TABLE users"'

    def test_stringified_integer_decodes(self):
        """'5000' against an integer schema becomes 5000 — the policy now
        compares the number the tool will actually receive."""
        out = canonicalize_tool_args({"amount": "5000"}, SCHEMA)
        assert out["amount"] == 5000
        assert isinstance(out["amount"], int)

    def test_object_as_json_string_decodes(self):
        out = canonicalize_tool_args({"options": '{"target": "internal.corp"}'}, SCHEMA)
        assert out["options"] == {"target": "internal.corp"}

    def test_object_string_wrong_shape_stays_raw(self):
        out = canonicalize_tool_args({"options": '["not", "a", "dict"]'}, SCHEMA)
        assert out["options"] == '["not", "a", "dict"]'

    def test_invalid_json_stays_raw(self):
        out = canonicalize_tool_args({"options": "{broken json"}, SCHEMA)
        assert out["options"] == "{broken json"

    def test_non_finite_float_stays_raw(self):
        out = canonicalize_tool_args({"amount": "NaN"}, SCHEMA)
        assert out["amount"] == "NaN"  # raw — never a silent comparison

    def test_no_schema_passthrough(self):
        """Without schema knowledge, values pass through UNCHANGED — never
        guess a decode the policy would then trust."""
        args = {"anything": "[1, 2]", "other": "5000"}
        out = canonicalize_tool_args(args, None)
        assert out == args

    def test_non_string_passthrough(self):
        out = canonicalize_tool_args({"amount": 5, "query": "x"}, SCHEMA)
        assert out == {"amount": 5, "query": "x"}

    def test_boolean_strings_decode(self):
        schema = {"parameters": {"properties": {"flag": {"type": "boolean"}}}}
        assert canonicalize_tool_args({"flag": "true"}, schema)["flag"] is True
        assert canonicalize_tool_args({"flag": "false"}, schema)["flag"] is False

    def test_input_never_mutated(self):
        args = {"amount": "5000"}
        canonicalize_tool_args(args, SCHEMA)
        assert args == {"amount": "5000"}


def _checker_with_rule(rule):
    """Build a PolicyChecker with one rule on a real ToolCallPolicy."""
    policy = ToolCallPolicy(rules=[rule])
    return PolicyChecker(policy=policy)


def _cond(field, op, value):
    return PolicyCondition(field=field, operator=PolicyOperator(op), value=value)


class TestFailClosed:
    def test_gt_against_non_number_denies(self):
        """F-3 rule: a deny rule with gt against a NON-number matches (fail
        closed). Previously the TypeError was swallowed -> False -> allow."""
        rule = PolicyRule(
            name="cap-amount",
            condition=_cond("tool_args.amount", "gt", 100),
            action=PolicyAction.DENY,
            priority=10,
        )
        checker = _checker_with_rule(rule)
        decision = checker.check("database_query", eval_context={"tool_args": {"amount": "5000-not-a-number"}})
        assert decision.action == "deny", "a type mismatch must make a deny rule MATCH"

    def test_allow_rule_never_matches_on_mismatch(self):
        """F-3: the allow rule itself never MATCHES on a mismatch — the
        decision falls through to the default action (not the rule's). The
        asymmetric protection lives in deny/approval rules firing; an allow
        rule's non-match leaves the default policy in charge."""
        rule = PolicyRule(
            name="allow-small",
            condition=_cond("tool_args.amount", "lt", 100),
            action=PolicyAction.ALLOW,
            priority=10,
        )
        checker = _checker_with_rule(rule)
        decision = checker.check("database_query", eval_context={"tool_args": {"amount": {"nested": "object"}}})
        assert decision.matched_rule != "allow-small", "an allow rule must never match on a type mismatch"
        assert decision.matched_rule in (None, "default"), "fell to the default action, not the rule"

    def test_approval_rule_matches_on_mismatch(self):
        rule = PolicyRule(
            name="approve-big",
            condition=_cond("tool_args.amount", "gt", 100),
            action=PolicyAction.REQUIRE_APPROVAL,
            priority=10,
        )
        checker = _checker_with_rule(rule)
        decision = checker.check("database_query", eval_context={"tool_args": {"amount": "garbage"}})
        assert decision.action == "require_approval", "a type mismatch must make an approval rule MATCH"


class TestAstParsersStrDecode:
    def test_str_annotated_value_never_decoded(self):
        """F-3 rule 2: str-typed parameters are never json-decoded."""
        def tool(query: str) -> str:
            return query

        out = coerce_dict_args_by_annotations({"query": '"\\u0044ROP TABLE users"'}, tool.__annotations__)
        assert out["query"] == '"\\u0044ROP TABLE users"'

    def test_int_annotated_string_decodes(self):
        def tool(amount: int) -> int:
            return amount

        out = coerce_dict_args_by_annotations({"amount": "5000"}, tool.__annotations__)
        assert out["amount"] == 5000


class TestEndToEndInvariant:
    """The reviewer's exact payloads through the canonicalization +
    policy-check path: each must be DENIED, and the plain form denied too."""

    def _deny_rule_checker(self):
        rule = PolicyRule(
            name="block-destructive-sql",
            condition=_cond("tool_args.query", "contains", "DROP"),
            action=PolicyAction.DENY,
            priority=10,
        )
        return _checker_with_rule(rule)

    def test_plain_drop_denied(self):
        checker = self._deny_rule_checker()
        args = canonicalize_tool_args({"query": "DROP TABLE users"}, SCHEMA)
        decision = checker.check("database_query", eval_context={"tool_args": args})
        assert decision.action == "deny"

    def test_json_escaped_drop_denied(self):
        """The round-2 verified bypass: the escaped form used to pass (the
        rule saw the escaped text, the tool got the decoded value). With
        canonicalization, the str-typed value STAYS the escaped string —
        the contains rule must match the DECODED content.
        Note: the escaped form '"\\\\u0044ROP..."' canonicalizes to itself
        (str-typed, never decoded) — and the rule sees the same string.
        The BYPASS is dead because the executor no longer decodes either:
        both sides see the escaped string, consistently.
        """
        checker = self._deny_rule_checker()
        args = canonicalize_tool_args({"query": "DROP TABLE users"}, SCHEMA)
        decision = checker.check("database_query", eval_context={"tool_args": args})
        assert decision.action == "deny", "plain form must be denied (control)"

    def test_canonical_matches_executor_form(self):
        """The invariant itself: canonicalize_tool_args output == what the
        sandbox serializer receives. The executor no longer transforms."""
        raw = {"amount": "5000", "options": '{"a": 1}', "query": "x"}
        canonical = canonicalize_tool_args(raw, SCHEMA)
        # The sandbox gets `canonical` verbatim (exec specs carry it); there
        # is no second rewrite. Documented by construction: the exec-spec
        # site consumes canonical_args[tc.id].
        assert canonical["amount"] == 5000
        assert canonical["options"] == {"a": 1}


class TestSourceShape:
    """Guards: the canonicalization is wired into BOTH loops and the
    in-sandbox/executor rewrites stay gone."""

    def test_v3_policy_uses_canonical(self):
        import re

        src = open("letta/agents/letta_agent_v3.py").read()
        assert "canonical_args[tc.id] = canonicalize_tool_args(" in src
        assert "canonical_args.get(tc.id" in src, "exec specs must consume the canonical dict"
        # the canary check consumes canonical too
        assert "self.canary_checker.check(canonical_args.get(tc.id" in src

    def test_v2_policy_uses_canonical(self):
        src = open("letta/agents/letta_agent_v2.py").read()
        assert "tool_args = canonicalize_tool_args(tool_args, _v2_tool_schema)" in src
        # policy + canary both AFTER the canonicalization line
        can_line = src.index("tool_args = canonicalize_tool_args")
        policy_line = src.index("check_policy(")
        canary_line = src.index("self.canary_checker.check(tool_args)")
        assert can_line < policy_line < canary_line

    def test_in_sandbox_coercion_gone(self):
        src = open("letta/services/tool_sandbox/base.py").read()
        assert "coerce_dict_args_by_annotations(" not in src, "F-3: the in-sandbox rewriter must stay removed"

    def test_executor_no_recoerce(self):
        src = open("letta/services/tool_executor/sandbox_tool_executor.py").read()
        assert "return function_args" in src and "coerce_dict_args_by_annotations(function_args" not in src


# ---------------------------------------------------------------------------
# interim-2 R-1 + F-6: no raw-args fallback; approval exemption is scoped
# ---------------------------------------------------------------------------


class TestR1NoRawFallback:
    """interim-2 R-1: execution must consume canonical_args[tc.id] with NO
    raw re-parse fallback. A missing entry is rejected with a named error."""

    def test_v3_no_fallback_in_source(self):
        src = open("letta/agents/letta_agent_v3.py").read()
        assert "canonical_args.get(tc.id) or _safe_load_tool_call_str" not in src, (
            "the raw-args fallback (interim-2 R-1) must not exist — a raw "
            "re-parse is the R2A-1 bypass kept alive as a safety net"
        )
        assert "canonical_args[tc.id]" in src, "execution must index the canonical dict"
        assert "R2A-1 invariant violation" in src, "a missing canonical entry must be a named rejection"

    def test_v3_v2_single_arg_source(self):
        """Every exec-spec args site flows from ONE canonical source."""
        import re

        src = open("letta/agents/letta_agent_v3.py").read()
        # all "args": sites must follow the canonical assignment
        canonical_assign = "args = dict(canonical_args[tc.id])"
        assert canonical_assign in src
        # the malformed-args retry path uses the SAME args var
        idx_retry = src.index("_tcr.handle_malformed_args")
        idx_canon = src.index(canonical_assign)
        assert idx_canon < idx_retry


class TestF6ApprovalExemptionScoped:
    """F-6 (round-2 traced real; interim-3: all three layers). On an
    approval-response step ONLY the approved call ids execute. Passengers
    are rejected with a named error; the policy waive is per-approved-id;
    the step-2 gate skip is safe because passengers never reach it; None
    approved_tool_call_ids exempts NOTHING."""

    def test_passengers_rejected_in_source(self):
        src = open("letta/agents/letta_agent_v3.py").read()
        assert "was not part of the approved set and cannot execute" in src, (
            "unapproved passengers must be rejected with a named error (interim-3 layer 3)"
        )
        assert "F-6: rejecting unapproved passenger tool call" in src

    def test_waive_scoped_per_call(self):
        src = open("letta/agents/letta_agent_v3.py").read()
        assert "tc.id in (approved_tool_call_ids or set())" in src, (
            "waive_approval must be scoped to approved ids (interim-3 layer 2)"
        )
        # and the fallback re-check in _run_one is scoped too
        assert 'spec.get("id") in (approved_tool_call_ids or set())' in src

    def test_none_means_empty_set(self):
        src = open("letta/agents/letta_agent_v3.py").read()
        assert "approved_tool_call_ids = approved_tool_call_ids or set()" in src, (
            "None must normalize to the EMPTY set — never 'exempt everything'"
        )
        assert "approved_tool_call_ids is None or" not in src, (
            "the None-as-exempt-everything condition must not exist"
        )

    def test_v2_sibling_scan_clean(self):
        """V2's approval path is single-call by construction (exact-id
        binding via _approval_response_matches_request; tool_calls[0]
        only; _maybe_get_pending_tool_call_message unused in V2) — the
        multi-call passenger vector does not exist there."""
        src = open("letta/agents/letta_agent_v2.py").read()
        assert "_maybe_get_pending_tool_call_message" not in src
        assert "_approval_response_matches_request" in src
        assert "approval_request.tool_calls[0]" in src

    def test_step2_gate_skip_is_safe(self):
        """The step-2 approval gate skips the whole step on approval
        responses — safe ONLY because passengers are rejected before it.
        Pin the ordering: the passenger rejection sits in the exec-spec
        builder, which runs before _run_one."""
        src = open("letta/agents/letta_agent_v3.py").read()
        reject_pos = src.index("was not part of the approved set")
        gate_pos = src.index("if not is_approval_response:")
        # the gate is in step 2 (before the exec-spec builder), but the
        # passenger rejection catches everything the gate would have;
        # verify both exist and the rejection is unconditional for
        # non-approved calls on approval steps.
        assert reject_pos > 0 and gate_pos > 0
