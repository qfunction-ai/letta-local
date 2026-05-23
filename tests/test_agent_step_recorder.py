"""Tests for letta.observability.agent_step_recorder — AgentStepRecorder."""

import pytest
from unittest.mock import MagicMock, patch

from letta.observability.agent_step_recorder import AgentStepRecorder


@pytest.fixture
def enabled_recorder():
    """Recorder with tracing enabled."""
    with patch("letta.observability.agent_step_recorder.AgentStepRecorder") as mock_cls:
        recorder = AgentStepRecorder()
        recorder._is_tracing_enabled = True
        recorder._log_event = MagicMock()
        recorder._log_attributes = MagicMock()
        return recorder


@pytest.fixture
def disabled_recorder():
    """Recorder with tracing disabled (default)."""
    recorder = AgentStepRecorder()
    recorder._is_tracing_enabled = False
    recorder._log_event = MagicMock()
    recorder._log_attributes = MagicMock()
    return recorder


class TestOnMemoryRebuilt:
    def test_emits_block_refresh(self, enabled_recorder):
        enabled_recorder.on_memory_rebuilt(
            block_count=3,
            system_prompt_changed=False,
            memory_changed=True,
            system_prompt_tokens=500,
        )
        enabled_recorder._log_event.assert_called()
        call_args = enabled_recorder._log_event.call_args_list
        assert call_args[0][0][0] == "memory.block_refresh"
        assert call_args[0][1]["attributes"]["memory.block_count"] == 3
        assert call_args[0][1]["attributes"]["memory.memory_changed"] is True

    def test_emits_rebuilt_when_changed(self, enabled_recorder):
        enabled_recorder.on_memory_rebuilt(
            block_count=2,
            system_prompt_changed=True,
            memory_changed=False,
            system_prompt_tokens=800,
        )
        assert enabled_recorder._log_event.call_count == 2
        assert enabled_recorder._log_event.call_args_list[1][0][0] == "memory.system_prompt_rebuilt"

    def test_no_emit_when_nothing_changed(self, enabled_recorder):
        enabled_recorder.on_memory_rebuilt(
            block_count=1,
            system_prompt_changed=False,
            memory_changed=False,
        )
        assert enabled_recorder._log_event.call_count == 1  # only block_refresh

    def test_disabled_recorder_no_ops(self, disabled_recorder):
        disabled_recorder.on_memory_rebuilt(
            block_count=3,
            system_prompt_changed=True,
            memory_changed=True,
        )
        disabled_recorder._log_event.assert_not_called()


class TestOnContextComposed:
    def test_emits_context_attributes(self, enabled_recorder):
        enabled_recorder.on_context_composed(
            message_count=10,
            prompt_tokens=5000,
            window_limit=8000,
            available_tools=["send_message", "core_memory_append"],
            tool_calling_mode="native",
        )
        enabled_recorder._log_attributes.assert_called_once()
        attrs = enabled_recorder._log_attributes.call_args[0][0]
        assert attrs["context.message_count"] == 10
        assert attrs["context.total_prompt_tokens"] == 5000
        assert attrs["context.window_limit"] == 8000
        assert attrs["context.pressure_ratio"] == 0.625
        assert "send_message" in attrs["context.available_tools"]
        assert attrs["context.tool_calling_mode"] == "native"

    def test_pressure_ratio_zero_window(self, enabled_recorder):
        enabled_recorder.on_context_composed(
            message_count=5,
            prompt_tokens=1000,
            window_limit=0,
            available_tools=[],
            tool_calling_mode="prompt",
        )
        attrs = enabled_recorder._log_attributes.call_args[0][0]
        assert attrs["context.pressure_ratio"] == 0

    def test_disabled_recorder_no_ops(self, disabled_recorder):
        disabled_recorder.on_context_composed(
            message_count=10,
            prompt_tokens=5000,
            window_limit=8000,
            available_tools=["send_message"],
            tool_calling_mode="native",
        )
        disabled_recorder._log_attributes.assert_not_called()


class TestOnLlmResponse:
    def test_emits_reasoning_captured(self, enabled_recorder):
        enabled_recorder.on_llm_response(
            reasoning_content="I should search archival memory for the answer",
            action_taken="archival_memory_search",
            model_name="gpt-4",
        )
        enabled_recorder._log_event.assert_called_once()
        assert enabled_recorder._log_event.call_args[0][0] == "reasoning.captured"
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert "archival memory" in attrs["reasoning.content"]
        assert attrs["reasoning.action_taken"] == "archival_memory_search"
        assert attrs["reasoning.model"] == "gpt-4"

    def test_truncates_long_reasoning(self, enabled_recorder):
        long_reasoning = "x" * 10000
        enabled_recorder.on_llm_response(
            reasoning_content=long_reasoning,
            action_taken="send_message",
            model_name="gpt-4",
        )
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert len(attrs["reasoning.content"]) == 5000

    def test_none_reasoning(self, enabled_recorder):
        enabled_recorder.on_llm_response(
            reasoning_content=None,
            action_taken="send_message",
            model_name="gpt-4",
        )
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["reasoning.content"] == ""

    def test_disabled_recorder_no_ops(self, disabled_recorder):
        disabled_recorder.on_llm_response(
            reasoning_content="test",
            action_taken="send_message",
            model_name="gpt-4",
        )
        disabled_recorder._log_event.assert_not_called()


class TestOnToolExecuted:
    def test_core_memory_append(self, enabled_recorder):
        enabled_recorder.on_tool_executed(
            tool_name="core_memory_append",
            tool_args={"content": "User likes python"},
            duration_ns=500_000_000,
            success=True,
        )
        enabled_recorder._log_event.assert_called_once()
        assert enabled_recorder._log_event.call_args[0][0] == "memory.block_write"
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["memory.operation"] == "append"

    def test_core_memory_replace(self, enabled_recorder):
        enabled_recorder.on_tool_executed(
            tool_name="core_memory_replace",
            tool_args={"old_content": "x", "new_content": "y"},
            duration_ns=300_000_000,
            success=True,
        )
        assert enabled_recorder._log_event.call_args[0][0] == "memory.block_write"
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["memory.operation"] == "replace"

    def test_archival_memory_search(self, enabled_recorder):
        enabled_recorder.on_tool_executed(
            tool_name="archival_memory_search",
            tool_args={"query": "project goals"},
            duration_ns=1_000_000_000,
            success=True,
            result_count=5,
        )
        assert enabled_recorder._log_event.call_args[0][0] == "memory.archival_search"
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["memory.result_count"] == 5

    def test_archival_memory_search_no_count(self, enabled_recorder):
        """When result_count is not provided, defaults to 0."""
        enabled_recorder.on_tool_executed(
            tool_name="archival_memory_search",
            success=True,
        )
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["memory.result_count"] == 0

    def test_non_memory_tool(self, enabled_recorder):
        enabled_recorder.on_tool_executed(
            tool_name="conversation_search",
            duration_ns=100_000_000,
            success=True,
        )
        # No memory-specific event emitted, but still records tool execution
        enabled_recorder._log_event.assert_not_called()

    def test_disabled_recorder_no_ops(self, disabled_recorder):
        disabled_recorder.on_tool_executed(
            tool_name="core_memory_append",
            success=True,
        )
        disabled_recorder._log_event.assert_not_called()


class TestOnSummarizationCompleted:
    def test_emits_summarization_event(self, enabled_recorder):
        enabled_recorder.on_summarization_completed(
            trigger_reason="forced_clear",
            eviction_count=15,
            tokens_before=8000,
            tokens_after=3000,
            latency_ns=2_500_000_000,
        )
        enabled_recorder._log_event.assert_called_once()
        assert enabled_recorder._log_event.call_args[0][0] == "summarization.completed"
        attrs = enabled_recorder._log_event.call_args[1]["attributes"]
        assert attrs["summarization.trigger_reason"] == "forced_clear"
        assert attrs["summarization.eviction_count"] == 15
        assert attrs["summarization.tokens_before"] == 8000
        assert attrs["summarization.tokens_after"] == 3000
        assert attrs["summarization.latency_ms"] == 2500

    def test_disabled_recorder_no_ops(self, disabled_recorder):
        disabled_recorder.on_summarization_completed(
            trigger_reason="threshold",
            eviction_count=5,
            tokens_before=8000,
            tokens_after=4000,
            latency_ns=1_000_000_000,
        )
        disabled_recorder._log_event.assert_not_called()
