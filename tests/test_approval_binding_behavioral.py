"""F2 (v0.16.37 release-check, reviewer-approved): behavioral approval tests.

The approval binding logic was inline in the agent loops and only covered by
source-shape guards. These tests pin BEHAVIOR through production entry points
(`_step` for V3, `_step`/`_handle_ai_response` for V2), so they stay green
across the extraction refactor (`_extract_approval_actions` on V3, the
single-call binding block on V2) — that green-before/green-after pair is the
proof the refactor changed nothing.

Coverage required by the review:
  (a) exact-call binding — an approval releases ONLY the call it names, with
      the REQUEST's arguments (the response cannot invent args); unknown IDs
      release nothing; approving Y does not release X;
  (b) genuine-approval source — assistant text never satisfies the gate;
      an approval-role response with approvals=None releases nothing;
  (c) one production-path test per agent driving the real handler;
  (d) run-scoping probe — a matching tool_call_id arriving for a different
      run/agent must release nothing (if unscoped: recorded finding, see the
      probe test's documentation).
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from letta.agents.helpers import _maybe_get_approval_messages
from letta.agents.letta_agent_v2 import LettaAgentV2
from letta.agents.letta_agent_v3 import LettaAgentV3
from letta.schemas.agent import AgentState, AgentType
from letta.schemas.block import Block
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.letta_message import ApprovalReturn
from letta.schemas.llm_config import LLMConfig
from letta.schemas.memory import Memory
from letta.schemas.message import Message, MessageRole, ToolReturn
from openai.types.chat.chat_completion_message_tool_call import (
    Function as OpenAIFunction,
)
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageFunctionToolCall as OpenAIToolCall,
)
from letta.schemas.openai.chat_completion_response import UsageStatistics
from letta.schemas.step_metrics import StepMetrics
from letta.schemas.user import User


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

_SEC_PATCHERS: list = []


@pytest.fixture(autouse=True)
def _restore_sec_loaders():
    """Undo the module-level _sec loader stubs after every test so they do
    not leak into other test files in the same run."""
    yield
    while _SEC_PATCHERS:
        _SEC_PATCHERS.pop().stop()


def _make_agent(cls) -> LettaAgentV3 | LettaAgentV2:
    actor = User(
        id=f"user-{uuid.uuid4().hex[:8]}",
        organization_id=f"org-{uuid.uuid4().hex[:8]}",
        name="test-user",
    )
    agent_state = AgentState(
        id=f"agent-{uuid.uuid4()}",
        name="approval-test-agent",
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
        message_ids=[f"message-{uuid.uuid4()}"],  # production agents always carry message_ids
    )
    with patch(f"letta.agents.{cls.__module__.split('.')[-1]}.LLMClient.create", return_value=MagicMock()):
        return cls(agent_state=agent_state, actor=actor)


def _make_v3_agent() -> LettaAgentV3:
    return _make_agent(LettaAgentV3)


def _make_v2_agent() -> LettaAgentV2:
    return _make_agent(LettaAgentV2)


def _oai_tool_call(name: str, args_json: str, call_id: str | None = None) -> OpenAIToolCall:
    return OpenAIToolCall(
        id=call_id or f"call-{uuid.uuid4().hex[:8]}",
        function=OpenAIFunction(name=name, arguments=args_json),
        type="function",
    )


def _system_message(agent_id: str) -> Message:
    from letta.schemas.message import TextContent

    return Message(
        id=f"message-{uuid.uuid4()}",
        role=MessageRole.system,
        content=[TextContent(text="You are a helpful assistant.")],
        agent_id=agent_id,
        model="gpt-4o-mini",
        created_at=datetime.now(timezone.utc),
    )


def _approval_request(agent_id: str, tool_calls: list[OpenAIToolCall], step_id: str = "step-approval-1") -> Message:
    return Message(
        id=f"message-{uuid.uuid4()}",
        role=MessageRole.approval,
        content=[],
        agent_id=agent_id,
        model="gpt-4o-mini",
        tool_calls=tool_calls,
        tool_call_id=tool_calls[0].id,
        created_at=datetime.now(timezone.utc),
        step_id=step_id,
    )


def _approval_response(agent_id: str, approvals=None, tool_call_id=None, approve=None, denial_reason=None) -> Message:
    return Message(
        id=f"message-{uuid.uuid4()}",
        role=MessageRole.approval,
        content=[],
        agent_id=agent_id,
        model="gpt-4o-mini",
        approvals=approvals,
        tool_call_id=tool_call_id,
        approve=approve,
        denial_reason=denial_reason,
        created_at=datetime.now(timezone.utc),
    )


def _llm_adapter_mock() -> MagicMock:
    """Adapter mock for driving _step: no LLM call happens on the approval
    path; on the fall-through path the invocation yields nothing."""
    adapter = MagicMock()
    adapter.supports_token_streaming.return_value = False
    adapter.content = None
    adapter.reasoning_content = None
    adapter.finish_reason = None
    adapter.message_id = f"message-{uuid.uuid4()}"
    adapter.tool_call = None
    from letta.schemas.usage import LettaUsageStatistics

    adapter.usage = LettaUsageStatistics(completion_tokens=0, prompt_tokens=0, total_tokens=0, step_count=1)

    async def _empty_invocation(*args, **kwargs):
        return
        yield  # pragma: no cover

    adapter.invoke_llm.side_effect = _empty_invocation
    return adapter


def _capture_executions(agent, executed: list) -> None:
    """Patch the tool executor so we can assert WHICH calls were released.
    The executor receives target_tool (looked up from agent_state.tools) and
    the parsed args — together they identify the released call and prove the
    arguments came from the REQUEST, not the response."""

    async def _fake_execute_tool(target_tool, tool_args, agent_state, agent_step_span, step_id, **kwargs):
        executed.append({"name": target_tool.name if target_tool else "unknown", "args": dict(tool_args)})
        result = MagicMock()
        result.status = "success"
        result.func_return = "ok"
        return result

    agent._execute_tool = _fake_execute_tool


def _agent_tools(agent) -> None:
    """Give the agent real Tool records so target_tool lookup resolves."""
    from letta.schemas.tool import Tool

    agent.agent_state.tools = [
        Tool(id=f"tool-{uuid.uuid4()}", name="web_search"),
        Tool(id=f"tool-{uuid.uuid4()}", name="file_write"),
    ]


def _stub_step_infra(agent) -> None:
    """Stub DB-touching step infrastructure; the approval logic stays real.
    The policy/canary loaders are stubbed at the module the agent imports them
    from (`_sec` in letta_agent_v3/letta_agent_v2) — a fresh PolicyChecker
    with an empty policy (what init_security installs) is permissive, so the
    approval binding behavior is exercised with policy as a no-op."""
    agent._refresh_messages = AsyncMock(side_effect=lambda msgs, **kw: msgs)
    agent.client_skills = []  # normally set by the public step() entry point
    # Run hardening (token budget + circuit breaker) is initialized at the
    # public step()/stream() entry points; replicate it here since we drive
    # _step directly.
    from letta.agents import agent_hardening as _hard

    _hard.init_run_hardening(agent)
    agent._get_valid_tools = AsyncMock(return_value=[{"name": "web_search"}, {"name": "file_write"}])
    agent_mod = type(agent).__module__
    import importlib

    _sec = importlib.import_module(agent_mod)._sec
    _SEC_PATCHERS.append(patch.object(_sec, "load_tool_call_policy", new=AsyncMock(return_value=None)))
    _SEC_PATCHERS[-1].start()
    _SEC_PATCHERS.append(patch.object(_sec, "load_canary", new=AsyncMock(return_value=None)))
    _SEC_PATCHERS[-1].start()
    # V2 persists inside _handle_ai_response via message_manager; stub the
    # write (identity) so the approval logic runs without a DB. Inert for V3,
    # which returns messages to the caller instead.
    agent.message_manager.create_many_messages_async = AsyncMock(side_effect=lambda msgs, **kw: msgs)
    agent.agent_manager.update_message_ids_async = AsyncMock(return_value=None)
    # The fall-through (non-approval) LLM branch checkpoints the step start
    # through the DB; stub it (V3 approval tests never reach it).
    agent._step_checkpoint_start = AsyncMock(
        return_value=(MagicMock(), MagicMock(), StepMetrics(id="step-approval-1", agent_id=agent.agent_state.id), MagicMock())
    )
    agent.step_manager.get_step_metrics_async = AsyncMock(
        return_value=StepMetrics(id="step-approval-1", agent_id=agent.agent_state.id)
    )


# --------------------------------------------------------------------------
# V3 production path: drive _step (the real handler) with approval pairs.
# These are the characterization tests: they run green on 4047471 (inline
# block) and must stay green after the extraction refactor.
# --------------------------------------------------------------------------

ARGS_X = '{"query": "request-args-x"}'
ARGS_Y = '{"path": "request-args-y"}'


async def _drive_v3_step(agent, messages):
    """Consume the _step async generator; return (stop_reason, chunks)."""
    chunks = []
    from letta.schemas.llm_config import LLMConfig as _LLMConfig

    adapter = _llm_adapter_mock()
    agen = agent._step(
        messages=messages,
        llm_adapter=adapter,
        run_id=f"run-{uuid.uuid4()}",
    )
    async for chunk in agen:
        chunks.append(chunk)
    return agent.stop_reason, chunks


class TestV3ApprovalBindingProductionPath:
    async def test_approve_one_of_two_releases_only_approved(self):
        """(a) Response approves only X -> exactly X executes, with the
        REQUEST's arguments; Y is not released."""
        agent = _make_v3_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        y = _oai_tool_call("file_write", ARGS_Y, call_id="call-y")
        request = _approval_request(agent.agent_state.id, [x, y])
        response = _approval_response(
            agent.agent_state.id,
            approvals=[ApprovalReturn(tool_call_id="call-x", approve=True)],
        )

        stop_reason, _ = await _drive_v3_step(agent, [_system_message(agent.agent_state.id), request, response])

        assert len(executed) == 1, f"exactly one call must be released, got {executed}"
        assert executed[0]["name"] == "web_search"
        assert executed[0]["args"] == {"query": "request-args-x"}, "released call must carry the REQUEST's arguments"

    async def test_unknown_id_releases_nothing(self):
        """(a) A response naming an unknown ID releases nothing — the
        malformed-approval path stops the step with invalid_tool_call."""
        agent = _make_v3_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent.agent_state.id, [x])
        response = _approval_response(
            agent.agent_state.id,
            approvals=[ApprovalReturn(tool_call_id="call-unknown-zzz", approve=True)],
        )

        stop_reason, _ = await _drive_v3_step(agent, [_system_message(agent.agent_state.id), request, response])

        assert executed == [], "an approval naming an unknown ID must release nothing"
        assert stop_reason is not None and stop_reason.stop_reason == "invalid_tool_call"

    async def test_denial_extracted_with_reason(self):
        """Deny Y + approve X -> X executes, Y produces a denial record with
        the response's reason."""
        agent = _make_v3_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        y = _oai_tool_call("file_write", ARGS_Y, call_id="call-y")
        request = _approval_request(agent.agent_state.id, [x, y])
        response = _approval_response(
            agent.agent_state.id,
            approvals=[
                ApprovalReturn(tool_call_id="call-x", approve=True),
                ApprovalReturn(tool_call_id="call-y", approve=False, reason="not allowed"),
            ],
        )

        stop_reason, chunks = await _drive_v3_step(agent, [_system_message(agent.agent_state.id), request, response])

        assert len(executed) == 1 and executed[0]["name"] == "web_search", "approved X executes, denied Y does not"
        # The denial must surface as a tool return carrying the response's
        # reason (denials are converted to error ToolReturns in
        # _handle_ai_response).
        all_text = " ".join(
            str(getattr(chunk, "content", "")) for chunk in chunks
        ) + " ".join(str(m) for m in agent.response_messages)
        assert "not allowed" in all_text, f"denial reason must surface, got: {all_text[:400]}"
        assert "call-y" in all_text or "file_write" in all_text, "denial must name the denied call"

    async def test_text_only_approval_response_releases_nothing(self):
        """(b) approval-role response with approvals=None (text only)
        releases nothing."""
        agent = _make_v3_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent.agent_state.id, [x])
        response = _approval_response(agent.agent_state.id, approvals=None)

        stop_reason, _ = await _drive_v3_step(agent, [_system_message(agent.agent_state.id), request, response])

        assert executed == [], "approvals=None must release nothing"
        assert stop_reason is not None and stop_reason.stop_reason == "invalid_tool_call"


# --------------------------------------------------------------------------
# V2 production path: drive _step (single-call legacy binding) and
# _handle_ai_response directly (approve-waive / deny-error semantics).
# --------------------------------------------------------------------------


async def _drive_v2_step(agent, messages, input_messages_to_persist=None):
    """Consume the V2 _step async generator; return (stop_reason, chunks).
    input_messages_to_persist mirrors production: the approval response is
    the new input message (step() passes the persist list from
    _prepare_in_context_messages_no_persist_async)."""
    chunks = []
    agen = agent._step(
        messages=messages,
        llm_adapter=_llm_adapter_mock(),
        run_id=f"run-{uuid.uuid4()}",
        input_messages_to_persist=input_messages_to_persist,
    )
    async for chunk in agen:
        chunks.append(chunk)
    return agent.stop_reason, chunks


class TestV2ApprovalBindingProductionPath:
    async def test_matched_approval_releases_request_call(self):
        """V2 single-call binding: response matching the request's call_id
        releases that call with the REQUEST's arguments."""
        agent = _make_v2_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent.agent_state.id, [x])
        response = _approval_response(agent.agent_state.id, tool_call_id="call-x", approve=True)

        stop_reason, _ = await _drive_v2_step(
            agent, [_system_message(agent.agent_state.id), request, response], input_messages_to_persist=[response]
        )

        assert len(executed) == 1, f"matched approval must release exactly one call, got {executed}"
        assert executed[0]["name"] == "web_search"
        assert executed[0]["args"] == {"query": "request-args-x"}, "released call must carry the REQUEST's arguments"

    async def test_mismatched_approval_ignored(self):
        """V2 exact-call binding: a response whose tool_call_id does not match
        the request is not an approval — nothing is released, the step falls
        through to the LLM path (which here yields no tool call -> LLMError,
        and crucially NOTHING executed)."""
        from letta.errors import LLMError

        agent = _make_v2_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent.agent_state.id, [x])
        response = _approval_response(agent.agent_state.id, tool_call_id="call-other", approve=True)

        with pytest.raises(LLMError):
            await _drive_v2_step(
                agent, [_system_message(agent.agent_state.id), request, response], input_messages_to_persist=[response]
            )

        assert executed == [], "a mismatched approval response must release nothing"

    async def test_handle_ai_response_approval_waives_and_executes(self):
        """V2 _handle_ai_response: is_approval=True waives the approval rule
        and executes the call."""
        from letta.schemas.openai.chat_completion_response import UsageStatistics

        agent = _make_v2_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        from letta.helpers.tool_rule_solver import ToolRulesSolver

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        persisted, should_continue, stop_reason = await agent._handle_ai_response(
            x,
            ["web_search", "file_write"],
            agent.agent_state,
            ToolRulesSolver(),
            UsageStatistics(completion_tokens=0, prompt_tokens=0, total_tokens=0),
            reasoning_content=None,
            pre_computed_assistant_message_id=None,
            step_id="step-approval-1",
            initial_messages=None,
            agent_step_span=None,
            is_final_step=False,
            run_id=f"run-{uuid.uuid4()}",
            step_metrics=StepMetrics(id="step-approval-1", agent_id=agent.agent_state.id),
            is_approval=True,
            is_denial=False,
            denial_reason=None,
        )

        assert len(executed) == 1 and executed[0]["name"] == "web_search", "approval must waive-and-execute"

    async def test_handle_ai_response_denial_returns_error_result(self):
        """V2 _handle_ai_response: is_denial=True produces an error tool
        result carrying the denial reason, and executes nothing."""
        from letta.schemas.openai.chat_completion_response import UsageStatistics

        agent = _make_v2_agent()
        _agent_tools(agent)
        _stub_step_infra(agent)
        executed = []
        _capture_executions(agent, executed)

        from letta.helpers.tool_rule_solver import ToolRulesSolver

        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        persisted, should_continue, stop_reason = await agent._handle_ai_response(
            x,
            ["web_search", "file_write"],
            agent.agent_state,
            ToolRulesSolver(),
            UsageStatistics(completion_tokens=0, prompt_tokens=0, total_tokens=0),
            reasoning_content=None,
            pre_computed_assistant_message_id=None,
            step_id="step-approval-1",
            initial_messages=None,
            agent_step_span=None,
            is_final_step=False,
            run_id=f"run-{uuid.uuid4()}",
            step_metrics=StepMetrics(id="step-approval-1", agent_id=agent.agent_state.id),
            is_approval=False,
            is_denial=True,
            denial_reason="denied by human",
        )

        assert executed == [], "a denial must not execute the tool"
        denial_text = " ".join(str(m) for m in persisted)
        assert "denied by human" in denial_text, f"denial reason must surface in the tool result, got: {denial_text[:400]}"


# --------------------------------------------------------------------------
# Helper-level tests: _maybe_get_approval_messages (the genuine-approval
# gate) and the extraction inputs it feeds.
# --------------------------------------------------------------------------


class TestApprovalMessageGate:
    def test_approval_pair_extracted(self):
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(agent_id, tool_call_id="call-x", approve=True)

        req, resp = _maybe_get_approval_messages([_system_message(agent_id), request, response])

        assert req is not None and resp is not None, "a trailing approval pair must be extracted"
        assert resp.tool_call_id == "call-x"

    def test_assistant_text_never_satisfies_gate(self):
        """(b) The gate requires BOTH trailing messages to be approval-role.
        An assistant message that merely TALKS about approving (even echoing
        the call id) must not satisfy the gate."""
        from letta.schemas.message import TextContent

        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        chatty = Message(
            id=f"message-{uuid.uuid4()}",
            role=MessageRole.assistant,
            content=[TextContent(text=f"Sure, I approve call-x ({ARGS_X}).")],
            agent_id=agent_id,
            model="gpt-4o-mini",
            created_at=datetime.now(timezone.utc),
        )

        req, resp = _maybe_get_approval_messages([_system_message(agent_id), request, chatty])
        assert req is None and resp is None, "assistant text must never satisfy the approval gate"

    def test_request_without_response_releases_nothing(self):
        """A pending approval request with no response yet must not be
        treated as an approval pair."""
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])

        req, resp = _maybe_get_approval_messages([_system_message(agent_id), request])
        assert req is None and resp is None, "request alone must not satisfy the gate"

    def test_tool_result_after_pair_breaks_gate(self):
        """The temporal gate: once anything follows the approval pair, it is
        no longer 'pending' — this is the only thing standing between a
        stale approval pair and a later run (see the run-scoping FINDING in
        plans/V016_38_L3_L1_PLAN.md)."""
        from letta.schemas.message import TextContent

        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(agent_id, tool_call_id="call-x", approve=True)
        followup = Message(
            id=f"message-{uuid.uuid4()}",
            role=MessageRole.user,
            content=[TextContent(text="next turn")],
            agent_id=agent_id,
            model="gpt-4o-mini",
            created_at=datetime.now(timezone.utc),
        )

        req, resp = _maybe_get_approval_messages([_system_message(agent_id), request, response, followup])
        assert req is None and resp is None, "a completed approval pair must not be re-consumed"


# --------------------------------------------------------------------------
# Direct unit tests for the extracted helpers (F2 extraction targets).
# The behavioral tests above prove _step still behaves; these pin the
# helpers' own contracts.
# --------------------------------------------------------------------------


class TestV3ExtractApprovalActions:
    def _extract(self, request, response, messages=None):
        from letta.agents.letta_agent_v3 import _extract_approval_actions

        return _extract_approval_actions(request, response, messages or [])

    def test_approve_one_of_two(self):
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        y = _oai_tool_call("file_write", ARGS_Y, call_id="call-y")
        request = _approval_request(agent_id, [x, y])
        response = _approval_response(
            agent_id, approvals=[ApprovalReturn(tool_call_id="call-x", approve=True)]
        )

        tool_calls, denials, returns = self._extract(request, response)

        assert [tc.id for tc in tool_calls] == ["call-x"], "only the approved call is released"
        assert tool_calls[0].function.arguments == ARGS_X, "released call carries the REQUEST's arguments"
        assert denials == [] and returns == []

    def test_unknown_id_releases_nothing(self):
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(
            agent_id, approvals=[ApprovalReturn(tool_call_id="call-unknown", approve=True)]
        )

        tool_calls, denials, returns = self._extract(request, response)

        assert tool_calls == [] and denials == [] and returns == [], "unknown ID releases nothing (caller flags malformed)"

    def test_denial_carries_response_reason(self):
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(
            agent_id,
            approvals=[ApprovalReturn(tool_call_id="call-x", approve=False, reason="not on my watch")],
        )

        tool_calls, denials, returns = self._extract(request, response)

        assert tool_calls == []
        assert len(denials) == 1
        assert denials[0].id == "call-x"
        assert denials[0].reason == "not on my watch"

    def test_backfill_id_legacy_case(self):
        """Legacy responses approve by assistant-message id (message-*) — the
        extractor maps that onto the request's first call (backfill)."""
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(
            agent_id, approvals=[ApprovalReturn(tool_call_id="message-abc123", approve=True)]
        )

        tool_calls, _, _ = self._extract(request, response)

        assert [tc.id for tc in tool_calls] == ["call-x"], "message-prefixed approval releases the backfill call"

    def test_client_side_tool_return_passthrough(self):
        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        tr = ToolReturn(tool_call_id="call-x", status="success", output="done client-side")
        response = _approval_response(agent_id, approvals=[tr])

        tool_calls, denials, returns = self._extract(request, response)

        assert returns == [tr], "client-side executions pass through untouched"
        assert tool_calls == [], "a ToolReturn is not an approval — the call is not re-released"


class TestV2ApprovalResponseMatchesRequest:
    def test_match(self):
        from letta.agents.letta_agent_v2 import _approval_response_matches_request

        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(agent_id, tool_call_id="call-x", approve=True)

        assert _approval_response_matches_request(request, response) is True

    def test_mismatch(self):
        from letta.agents.letta_agent_v2 import _approval_response_matches_request

        agent_id = f"agent-{uuid.uuid4()}"
        x = _oai_tool_call("web_search", ARGS_X, call_id="call-x")
        request = _approval_request(agent_id, [x])
        response = _approval_response(agent_id, tool_call_id="call-other", approve=True)

        assert _approval_response_matches_request(request, response) is False

    def test_degenerate_empty_request_matches_wart(self):
        """WART, pinned as-is: a degenerate request with no calls and a
        response with tool_call_id=None 'match' (None == None). This is the
        ORIGINAL inline behavior, preserved verbatim by the extraction. It
        is unreachable through the real gate in any dangerous way: _step
        would then index tool_calls[0] and the IndexError is caught by the
        step error handler — nothing executes. If someone scopes this later,
        this test flipping forces a conscious decision."""
        from letta.agents.letta_agent_v2 import _approval_response_matches_request

        agent_id = f"agent-{uuid.uuid4()}"
        # degenerate: a request carrying no calls (built directly — the
        # fixture builder requires at least one call)
        request = Message(
            id=f"message-{uuid.uuid4()}",
            role=MessageRole.approval,
            content=[],
            agent_id=agent_id,
            model="gpt-4o-mini",
            tool_calls=[],
            created_at=datetime.now(timezone.utc),
        )
        response = _approval_response(agent_id, tool_call_id=None, approve=True)

        assert _approval_response_matches_request(request, response) is True
