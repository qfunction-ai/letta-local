"""Source guard for the prompt-mode empty-step nudge (v0.16.27).

Pins the structural properties of the fix — the properties whose
regression would reintroduce either the silent-death bug or a
continuation loop:

1. The nudge branch is gated on ALL THREE conditions: prompt mode,
   mid-turn (tool-role messages in response_messages), and the
   `_empty_step_nudged` bound.
2. The bound flag is set BEFORE the nudge heartbeat is built.
3. The notice branch emits EMPTY_RESPONSE_NOTICE and sets the pending
   marker consumed by stream().
4. BRANCH ORDER: the nudge sits strictly AFTER the required-tools
   (uncalled) check — required-tool heartbeats must win first.
5. Both marker attributes are initialized in BaseAgentV2's
   _initialize_state (not left to instance freshness — instance-reusing
   paths would leak a set flag across turns).
6. stream() consumes and clears the notice marker.

Behavioral/e2e coverage: smoke check 4.9 (prompt-mode agent, multi-topic
prompt, no-silent-death assertion). The model behavior is stochastic —
this guard is the deterministic pin.
"""
from pathlib import Path


def _v3_source() -> str:
    return Path("letta/agents/letta_agent_v3.py").read_text()


def _v2_source() -> str:
    return Path("letta/agents/letta_agent_v2.py").read_text()


def test_nudge_gate_three_conditions():
    src = _v3_source()
    # All three gates present in _handle_ai_response's nudge branch
    # (mode read is line-wrapped in the source — assert the fragments)
    assert 'getattr(active_llm_config, "tool_calling_mode", None)' in src
    assert 'active_llm_config, "resolved_tool_calling_mode", None' in src
    assert 'getattr(m, "role", None) == "tool" for m in self.response_messages' in src
    assert "not self._empty_step_nudged" in src


def test_nudge_bound_set_before_heartbeat():
    src = _v3_source()
    bound_pos = src.find("self._empty_step_nudged = True")
    heartbeat_pos = src.find("The previous step produced only reasoning")
    assert bound_pos != -1 and heartbeat_pos != -1
    assert bound_pos < heartbeat_pos, "bound flag must be set BEFORE building the nudge"


def test_notice_branch_and_marker():
    src = _v3_source()
    assert "EMPTY_RESPONSE_NOTICE = (" in src, "module-level constant missing"
    assert "self._pending_notice_text = EMPTY_RESPONSE_NOTICE" in src
    assert "content=[TextContent(text=EMPTY_RESPONSE_NOTICE)]" in src


def test_branch_order_nudge_after_required_tools():
    """The nudge must sit in the ELSE of the uncalled-required-tools check.
    If the nudge fires before required-tool enforcement, skill-state
    heartbeats get preempted — a correctness regression."""
    src = _v3_source()
    uncalled_pos = src.find("get_uncalled_required_tools")
    gate_pos = src.find('getattr(active_llm_config, "tool_calling_mode", None)')
    assert uncalled_pos != -1 and gate_pos != -1
    assert uncalled_pos < gate_pos, "nudge branch must come AFTER the required-tools check"


def test_initialize_state_inits():
    """Both markers initialized in shared _initialize_state next to
    response_messages — explicit contract for instance-reusing paths."""
    src = _v2_source()
    for line in (
        "self._empty_step_nudged = False",
        "self._pending_notice_text = None",
        "self._pending_notice_step_id = None",
    ):
        assert line in src, f"missing init: {line}"
    resp_pos = src.find("self.response_messages = []")
    init_pos = src.find("self._empty_step_nudged = False")
    assert resp_pos != -1 and init_pos != -1
    assert abs(resp_pos - init_pos) < 400, "marker inits belong in _initialize_state next to response_messages"


def test_stream_consumes_and_clears_marker():
    src = _v3_source()
    consume_pos = src.find("if self._pending_notice_text is not None")
    clear_pos = src.find("self._pending_notice_text = None")
    assert consume_pos != -1, "stream() must consume the pending notice marker"
    assert clear_pos > consume_pos, "marker must be cleared after consumption"
