"""Source guards for the v0.16.37 approval wiring (review P-3/P-13, L-12).

The V2 policy-approval branch was unreachable dead code (the engine returned
allowed=False for require_approval, so the deny branch consumed it first).
The V3 loop treated policy REQUIRE_APPROVAL as flat DENY. Both are wired
now, and the ORDER of the branches is load-bearing:

    canary check -> approval request -> deny -> tool-rule approval -> execute

P-13: a canary-bearing call must be blocked BEFORE any approval request is
built (the request displays raw args to a human). P-3: the approval must be
bound to the exact call (tool_call_id match) and waive only the approval rule.

These AST guards pin the structure so a refactor cannot silently revert to
the dead-code ordering.
"""

import ast
from pathlib import Path

V2 = Path(__file__).resolve().parents[1] / "letta" / "agents" / "letta_agent_v2.py"
V3 = Path(__file__).resolve().parents[1] / "letta" / "agents" / "letta_agent_v3.py"


def _parse(path):
    return ast.parse(path.read_text())


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _ifs_in_order(fn):
    """All If nodes in `fn`, in source order, with their test unparsed."""
    ifs = [n for n in ast.walk(fn) if isinstance(n, ast.If)]
    ifs.sort(key=lambda n: n.lineno)
    return [(n.lineno, ast.unparse(n.test)) for n in ifs]


def _source_segment(path, node):
    lines = path.read_text().splitlines()
    return "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])


class TestV2ApprovalOrder:
    def test_branch_order_canary_before_approval_before_deny(self):
        fn = _find_function(_parse(V2), "_handle_ai_response")
        assert fn is not None

        canary = [ln for ln, t in _ifs_in_order(fn) if "canary_checker.check" in t]
        approval = [ln for ln, t in _ifs_in_order(fn) if "require_approval" in t and "policy_decision.action" in t]
        deny = [ln for ln, t in _ifs_in_order(fn) if "not policy_decision.allowed" in t]

        assert canary, "V2 canary check missing in _handle_ai_response"
        assert approval, "V2 policy require_approval branch missing (L-12 regression)"
        assert deny, "V2 deny branch missing"
        assert canary[0] < approval[0], "P-13: canary check must run BEFORE the approval request is built"
        assert approval[0] < deny[0], "require_approval must be checked BEFORE the generic deny branch (or it is dead code)"

    def test_v2_passes_waive_approval(self):
        fn = _find_function(_parse(V2), "_handle_ai_response")
        assert _source_segment(V2, fn).count("waive_approval=bool(is_approval)") == 1

    def test_approval_binding_check_present(self):
        """P-3 condition 1: approval response tool_call_id must match the request."""
        src = V2.read_text()
        assert "approval_response.tool_call_id != requested_call_id" in src
        assert "approval_response = None" in src


class TestV3ApprovalWiring:
    def test_policy_decisions_computed_before_approval_gate(self):
        fn = _find_function(_parse(V3), "_handle_ai_response")
        assert fn is not None

        stash_lines = []
        for n in ast.walk(fn):
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "policy_decisions":
                stash_lines.append(n.lineno)
            elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "policy_decisions" for t in n.targets):
                stash_lines.append(n.lineno)
        assert stash_lines, "V3 policy_decisions stash missing"

        gate_lines = [
            ln
            for ln, t in _ifs_in_order(fn)
            if "is_approval_response" in t and "not" in t
        ]
        assert gate_lines, "V3 approval gate not found"
        assert min(stash_lines) < min(gate_lines), "V3 policy check must run BEFORE the approval gate (P-13)"

    def test_canary_exclusion_in_approval_candidates(self):
        """P-13: canary-bearing calls are excluded from approval requests."""
        fn = _find_function(_parse(V3), "_handle_ai_response")
        src = _source_segment(V3, fn)
        assert "_canary_bearing(t)" in src
        assert "and not _canary_bearing(t)" in src

    def test_run_one_consumes_stashed_decision(self):
        fn = _find_function(_parse(V3), "_run_one")
        assert fn is not None
        src = _source_segment(V3, fn)
        assert 'spec.get("policy_decision")' in src
        assert src.count("await _sec.check_policy") <= 1, (
            "_run_one should consume the stashed decision; at most one "
            "check_policy call (the defensive fallback) is allowed"
        )

    def test_run_one_canary_before_deny(self):
        fn = _find_function(_parse(V3), "_run_one")
        assert fn is not None
        canary = [ln for ln, t in _ifs_in_order(fn) if "canary_checker.check" in t]
        deny = [ln for ln, t in _ifs_in_order(fn) if "not policy_decision.allowed" in t]
        assert canary and deny
        assert canary[0] < deny[0], "P-13: canary check must precede the deny branch in _run_one"
