"""Tests for letta.observability.step_recorder_integration."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from letta.observability.step_recorder_integration import (
    mark_summarization_start,
    record_summarization_completed,
    record_context_composed,
    record_llm_response,
    record_tool_executed,
    record_tool_executed_batch,
    record_compaction_completed,
    record_memory_rebuilt_explicit,
    _summarize_state,
)


def _make_agent():
    """Create a mock agent with the attributes the integration module reads."""
    agent = MagicMock()
    agent.agent_state.llm_config.context_window = 8000
    agent.agent_state.memory.blocks = [MagicMock(), MagicMock()]
    agent.usage.total_tokens = 5000
    agent.last_step_usage = MagicMock()
    agent.last_step_usage.prompt_tokens = 3000
    agent.last_step_usage.completion_tokens = 500
    return agent


class TestMarkSummarizationStart:
    def test_stores_start_state(self):
        agent = _make_agent()
        mark_summarization_start(agent)
        assert id(agent) in _summarize_state
        start_ns, tokens_before = _summarize_state[id(agent)]
        assert tokens_before == 5000
        assert start_ns > 0
        # Clean up
        _summarize_state.pop(id(agent), None)

    def test_handles_missing_usage(self):
        agent = _make_agent()
        agent.usage.total_tokens = None
        # Should not crash
        type(agent.usage).total_tokens = PropertyMock(side_effect=AttributeError)
        mark_summarization_start(agent)
        # Clean up
        _summarize_state.pop(id(agent), None)
        del type(agent.usage).total_tokens


class TestRecordSummarizationCompleted:
    def test_reads_start_state_and_calls_recorder(self):
        agent = _make_agent()
        mark_summarization_start(agent)
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_summarization_completed(agent, trigger_reason="post_loop")
            mock_recorder.on_summarization_completed.assert_called_once()
            call_kwargs = mock_recorder.on_summarization_completed.call_args[1]
            assert call_kwargs["trigger_reason"] == "post_loop"
            assert call_kwargs["tokens_before"] == 5000
            assert call_kwargs["latency_ns"] > 0
        # State should be cleaned up
        assert id(agent) not in _summarize_state

    def test_no_start_state_defaults_to_zero(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_summarization_completed(agent, trigger_reason="post_loop")
            call_kwargs = mock_recorder.on_summarization_completed.call_args[1]
            assert call_kwargs["tokens_before"] == 0
            assert call_kwargs["latency_ns"] == 0


class TestRecordContextComposed:
    def test_extracts_agent_state(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_context_composed(agent, messages=["m1", "m2", "m3"], valid_tools=[{"name": "send_message"}])
            mock_recorder.on_context_composed.assert_called_once()
            call_kwargs = mock_recorder.on_context_composed.call_args[1]
            assert call_kwargs["message_count"] == 3
            assert call_kwargs["window_limit"] == 8000
            assert call_kwargs["prompt_tokens"] == 3000
            assert call_kwargs["available_tools"] == ["send_message"]


class TestRecordLlmResponse:
    def test_uses_per_step_usage(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_llm_response(agent, reasoning_content="thinking", model_name="gpt-4")
            mock_recorder.on_llm_response.assert_called_once()
            call_kwargs = mock_recorder.on_llm_response.call_args[1]
            assert call_kwargs["prompt_tokens"] == 3000
            assert call_kwargs["completion_tokens"] == 500


class TestRecordToolExecuted:
    def test_passes_through_params(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_tool_executed(agent, tool_name="core_memory_append", duration_ns=100, success=True)
            mock_recorder.on_tool_executed.assert_called_once()


class TestRecordToolExecutedBatch:
    def test_passes_through_params(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_tool_executed_batch(agent, tool_names=["t1", "t2"], tool_results=[{"success": True}, {"success": False}], total_duration_ns=200)
            mock_recorder.on_tool_executed_batch.assert_called_once()


class TestRecordCompactionCompleted:
    def test_passes_through_params(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_compaction_completed(agent, trigger="context_window_exceeded", messages_before=50, messages_after=10, tokens_before=8000, tokens_after=3000)
            mock_recorder.on_compaction_completed.assert_called_once()


class TestRecordMemoryRebuiltExplicit:
    def test_passes_through_params(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder") as mock_get:
            mock_recorder = MagicMock()
            mock_get.return_value = mock_recorder
            record_memory_rebuilt_explicit(agent, block_count=3, system_prompt_changed=True, memory_changed=True)
            mock_recorder.on_memory_rebuilt.assert_called_once()


class TestExceptionResilience:
    """All integration functions swallow exceptions — tracing never crashes the agent."""

    def test_record_context_composed_swallows_exceptions(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder", side_effect=RuntimeError("boom")):
            record_context_composed(agent, messages=[], valid_tools=[])  # should not raise

    def test_record_llm_response_swallows_exceptions(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder", side_effect=RuntimeError("boom")):
            record_llm_response(agent, reasoning_content=None, model_name="test")  # should not raise

    def test_record_tool_executed_swallows_exceptions(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder", side_effect=RuntimeError("boom")):
            record_tool_executed(agent, tool_name="test")  # should not raise

    def test_mark_summarization_start_swallows_exceptions(self):
        agent = MagicMock()
        del agent.usage  # cause AttributeError
        mark_summarization_start(agent)  # should not raise

    def test_record_summarization_completed_swallows_exceptions(self):
        agent = _make_agent()
        with patch("letta.observability.step_recorder_integration._get_recorder", side_effect=RuntimeError("boom")):
            record_summarization_completed(agent, trigger_reason="test")  # should not raise
