"""v0.16.37 (A8, review L-11/P-13/P-14): tool-call chunk holdback + redaction.

Native-mode tool_call chunks used to stream to the client DURING generation;
the policy/canary check ran at execution time — the exact canary reached the
client before the call was blocked. Now chunks are buffered and released
after the verdict; blocked calls' args are redacted at STORAGE time (single
point, covers every read path — Delta's history reload included).

P-13 ordering: a canary-bearing call under a require_approval rule is
BLOCKED outright — never shown to a human in an approval request.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from letta.agents.letta_agent_v3 import LettaAgentV3
from letta.schemas.agent import AgentState, AgentType
from letta.schemas.block import Block
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.letta_message import MessageType, ToolCallDelta
from letta.schemas.llm_config import LLMConfig
from letta.schemas.memory import Memory
from letta.schemas.openai.chat_completion_response import FunctionCall, ToolCall, UsageStatistics
from letta.schemas.user import User
from letta.security.agent_security import BLOCKED_ARGS_REDACTION_MARKER
from letta.security.canary import CanaryChecker
from letta.security.policy import (
    PolicyAction,
    PolicyChecker,
    PolicyCondition,
    PolicyOperator,
    PolicyRule,
    ToolCallPolicy,
)

CANARY = "CANARY-8f3a-probe-91c2"


def _make_v3_agent() -> LettaAgentV3:
    actor = User(
        id=f"user-{uuid.uuid4().hex[:8]}",
        organization_id=f"org-{uuid.uuid4().hex[:8]}",
        name="test-user",
    )
    agent_state = AgentState(
        id=f"agent-{uuid.uuid4()}",
        name="holdback-test-agent",
        agent_type=AgentType.letta_v1_agent,
        llm_config=LLMConfig.default_config("gpt-4o-mini"),
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        tags=[],
        memory=Memory(
            agent_type=AgentType.letta_v1_agent,
            blocks=[Block(label="system/persona", value="test persona", limit=500)],
        ),
        system="You are a helpful assistant.",
        tools=[],
        sources=[],
        blocks=[],
    )
    with patch("letta.agents.letta_agent_v3.LLMClient.create", return_value=MagicMock()):
        return LettaAgentV3(agent_state=agent_state, actor=actor)


def _canary_tool_call(args_json: str) -> ToolCall:
    return ToolCall(id=f"call-{uuid.uuid4().hex[:8]}", function=FunctionCall(name="web_search", arguments=args_json))


class TestRedactToolCallChunk:
    """Unit tests for the chunk redaction helper."""

    def _chunk(self, tool_call=None, tool_calls=None):
        from datetime import datetime, timezone

        from letta.schemas.letta_message import ToolCallMessage

        return ToolCallMessage(
            id="message-1",
            date=datetime.now(timezone.utc),
            tool_call=tool_call or ToolCallDelta(name=None, arguments=None, tool_call_id=None),
            tool_calls=tool_calls,
        )

    def _letta_tc(self, args_json, call_id=None):
        from letta.schemas.letta_message import ToolCall as LettaToolCall

        return LettaToolCall(name="web_search", arguments=args_json, tool_call_id=call_id or f"call-{uuid.uuid4().hex[:8]}")

    def test_clean_chunk_untouched(self):
        agent = _make_v3_agent()
        agent._security_blocked_tool_call_ids = set()
        tc = self._letta_tc('{"query": "weather"}')
        chunk = self._chunk(tool_call=tc)
        out = agent._redact_tool_call_chunk(chunk)
        assert out is chunk
        assert tc.arguments == '{"query": "weather"}'

    def test_blocked_complete_call_args_replaced(self):
        agent = _make_v3_agent()
        tc = self._letta_tc(f'{{"query": "{CANARY}"}}')
        agent._security_blocked_tool_call_ids = {tc.tool_call_id}
        chunk = self._chunk(tool_call=tc)
        out = agent._redact_tool_call_chunk(chunk)
        assert out is chunk
        assert tc.arguments == BLOCKED_ARGS_REDACTION_MARKER
        assert tc.name == "web_search"  # name kept

    def test_blocked_delta_suppressed(self):
        agent = _make_v3_agent()
        delta = ToolCallDelta(name="web_search", arguments=f'{{"q": "{CANARY}"}}', tool_call_id="call-bad")
        agent._security_blocked_tool_call_ids = {"call-bad"}
        chunk = self._chunk(tool_call=delta)
        assert agent._redact_tool_call_chunk(chunk) is None

    def test_mixed_list_clean_kept_blocked_redacted(self):
        agent = _make_v3_agent()
        clean = self._letta_tc('{"query": "safe"}')
        bad = self._letta_tc(f'{{"query": "{CANARY}"}}')
        agent._security_blocked_tool_call_ids = {bad.tool_call_id}
        chunk = self._chunk(tool_calls=[clean, bad])
        out = agent._redact_tool_call_chunk(chunk)
        assert out is chunk
        assert clean.arguments == '{"query": "safe"}'
        assert bad.arguments == BLOCKED_ARGS_REDACTION_MARKER


class TestP13CanaryBeforeApproval:
    """require_approval rule + canary-bearing args -> BLOCKED, no approval request."""

    async def test_canary_call_under_approval_rule_blocked_not_requested(self):
        agent = _make_v3_agent()
        # canary armed
        agent.canary_checker = CanaryChecker()
        agent.canary_checker.canary_value = CANARY
        # policy: web_search requires approval
        agent.policy_checker = PolicyChecker(ToolCallPolicy(rules=[
            PolicyRule(
                name="approval-gate",
                condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                action=PolicyAction.REQUIRE_APPROVAL,
                priority=80,
            ),
        ]))

        tc = _canary_tool_call(f'{{"query": "{CANARY}"}}')

        from letta.helpers.tool_rule_solver import ToolRulesSolver

        messages, continue_stepping, stop_reason = await agent._handle_ai_response(
            valid_tool_names=["web_search"],
            tool_rules_solver=ToolRulesSolver(),
            usage=UsageStatistics(completion_tokens=10, prompt_tokens=10, total_tokens=20),
            content=None,
            step_id="step-1",
            initial_messages=[],
            is_final_step=False,
            run_id=None,
            is_approval_response=False,
            tool_calls=[tc],
            tool_call_denials=[],
            tool_returns=[],
            finish_reason="tool_calls",
            active_llm_config=agent.agent_state.llm_config,
        )

        # P-13: no approval request — the call was blocked outright
        assert stop_reason is None or stop_reason.stop_reason != "requires_approval"
        assert tc.id in agent._security_blocked_tool_call_ids

        # P-14: stored tool call args carry the marker, never the canary
        def _args_of(tc):
            # openai-response shape nests under .function; letta shape is flat
            if hasattr(tc, "function"):
                return tc.function.arguments
            return tc.arguments

        persisted_args = [
            _args_of(tc)
            for m in messages
            if getattr(m, "tool_calls", None)
            for tc in m.tool_calls
        ]
        assert persisted_args, "tool call message must be persisted"
        for args in persisted_args:
            assert CANARY not in (args or "")
        assert BLOCKED_ARGS_REDACTION_MARKER in "".join(a or "" for a in persisted_args)

    async def test_clean_call_under_approval_rule_requests_approval(self):
        agent = _make_v3_agent()
        agent.canary_checker = CanaryChecker()
        agent.canary_checker.canary_value = CANARY
        agent.policy_checker = PolicyChecker(ToolCallPolicy(rules=[
            PolicyRule(
                name="approval-gate",
                condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="web_search"),
                action=PolicyAction.REQUIRE_APPROVAL,
                priority=80,
            ),
        ]))

        tc = _canary_tool_call('{"query": "safe query"}')

        from letta.helpers.tool_rule_solver import ToolRulesSolver

        messages, continue_stepping, stop_reason = await agent._handle_ai_response(
            valid_tool_names=["web_search"],
            tool_rules_solver=ToolRulesSolver(),
            usage=UsageStatistics(completion_tokens=10, prompt_tokens=10, total_tokens=20),
            content=None,
            step_id="step-1",
            initial_messages=[],
            is_final_step=False,
            run_id=None,
            is_approval_response=False,
            tool_calls=[tc],
            tool_call_denials=[],
            tool_returns=[],
            finish_reason="tool_calls",
            active_llm_config=agent.agent_state.llm_config,
        )

        # clean call under approval rule -> approval requested
        assert stop_reason is not None and stop_reason.stop_reason == "requires_approval"
        assert tc.id not in agent._security_blocked_tool_call_ids


class TestStreamingHoldbackSourceGuard:
    """The buffer/release structure in _step is load-bearing — pin it."""

    def test_invocation_loop_buffers_tool_call_chunks(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "letta" / "agents" / "letta_agent_v3.py"
        text = src.read_text()
        assert "_tool_call_chunks_buffer.append(chunk)" in text
        assert "_tool_call_chunks_buffer.clear()" in text
        # release point: redaction before yield
        assert "_released = self._redact_tool_call_chunk(_tc_chunk)" in text
        # buffer defined unconditionally (approval path safe)
        assert "_tool_call_chunks_buffer: list = []" in text

    def test_buffer_release_after_handle_ai_response(self):
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "letta" / "agents" / "letta_agent_v3.py"
        text = src.read_text()
        release_line = text[: text.index("_released = self._redact_tool_call_chunk(_tc_chunk)")].count("\n")
        handle_line = text[: text.index("new_messages, self.should_continue, self.stop_reason = await self._handle_ai_response(")].count("\n")
        assert handle_line < release_line, "buffer release must come AFTER _handle_ai_response (verdict known)"
