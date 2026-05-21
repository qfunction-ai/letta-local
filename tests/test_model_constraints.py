"""Tests for the ModelConstraints system and LLMConfig integration."""

import pytest

from letta.schemas.llm_config import LLMConfig, ModelConstraints


# === ModelConstraints class ===

def test_model_constraints_defaults():
    """ModelConstraints has sensible defaults."""
    c = ModelConstraints()
    assert c.tool_calling_mode == "auto"
    assert c.tool_call_retry_count == 0
    assert c.disable_structured_output is False
    assert c.disable_streaming is False
    assert c.min_context_window == 4096
    assert c.force_external_summarizer is False
    assert c.json_repair_level == "basic"


def test_model_constraints_prompt_mode():
    """Can set tool_calling_mode to 'prompt'."""
    c = ModelConstraints(tool_calling_mode="prompt")
    assert c.tool_calling_mode == "prompt"


def test_model_constraints_custom_values():
    """ModelConstraints accepts all custom values."""
    c = ModelConstraints(
        tool_calling_mode="prompt",
        tool_call_retry_count=5,
        disable_structured_output=True,
        disable_streaming=True,
        min_context_window=2048,
        force_external_summarizer=True,
        json_repair_level="aggressive",
    )
    assert c.tool_calling_mode == "prompt"
    assert c.tool_call_retry_count == 5
    assert c.disable_structured_output is True
    assert c.disable_streaming is True
    assert c.min_context_window == 2048
    assert c.force_external_summarizer is True
    assert c.json_repair_level == "aggressive"


def test_model_constraints_serialization():
    """ModelConstraints round-trips through model_dump/model_validate."""
    c = ModelConstraints(
        tool_calling_mode="prompt",
        tool_call_retry_count=3,
        disable_structured_output=True,
        json_repair_level="aggressive",
    )
    dumped = c.model_dump()
    restored = ModelConstraints.model_validate(dumped)
    assert restored.tool_calling_mode == "prompt"
    assert restored.tool_call_retry_count == 3
    assert restored.disable_structured_output is True
    assert restored.json_repair_level == "aggressive"


# === Auto-apply validator ===

def test_large_context_no_constraints():
    """Large context window (>8K) gets no auto-applied constraints."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=131072,
        handle="test/test-model",
    )
    assert config.constraints is None


def test_medium_context_no_constraints():
    """Exactly 8K context window gets no auto-applied constraints."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=8192,
        handle="test/test-model",
    )
    assert config.constraints is None


def test_small_context_auto_applies():
    """Small context window (<8K) gets auto-applied constraints."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
    )
    assert config.constraints is not None


def test_auto_apply_force_external_summarizer():
    """Small context auto-applies force_external_summarizer=True."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
    )
    assert config.constraints.force_external_summarizer is True


def test_auto_apply_disable_structured_output():
    """Small context auto-applies disable_structured_output=True."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
    )
    assert config.constraints.disable_structured_output is True


def test_auto_apply_aggressive_json_repair():
    """Small context auto-applies json_repair_level='aggressive'."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
    )
    assert config.constraints.json_repair_level == "aggressive"


def test_auto_apply_tool_call_retry_count():
    """Small context auto-applies tool_call_retry_count=2."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
    )
    assert config.constraints.tool_call_retry_count == 2


# === Explicit constraints not overwritten ===

def test_explicit_constraints_preserved():
    """When constraints are explicitly set, auto-apply doesn't override."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
        constraints=ModelConstraints(
            tool_calling_mode="prompt",
            tool_call_retry_count=5,
        ),
    )
    assert config.constraints.tool_calling_mode == "prompt"
    assert config.constraints.tool_call_retry_count == 5


def test_explicit_constraints_none_triggers_auto_apply():
    """When constraints=None explicitly, auto-apply still kicks in for small context."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test-model",
        constraints=None,
    )
    assert config.constraints is not None


# === Extended model_endpoint_type Literal ===

@pytest.mark.parametrize("endpoint_type", [
    "localai",
    "llamacpp",
    "llamafile",
    "mlx",
    "openai_compatible",
    "bitnet",
])
def test_new_model_endpoint_types_accepted(endpoint_type):
    """All new provider type strings accepted in LLMConfig.model_endpoint_type."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type=endpoint_type,
        context_window=8192,
        handle="test/test-model",
    )
    assert config.model_endpoint_type == endpoint_type


# === Edge cases ===

def test_very_small_context_window():
    """Even 1K context window triggers auto-apply."""
    config = LLMConfig(
        model="tiny-model",
        model_endpoint_type="openai",
        context_window=1024,
        handle="test/tiny",
    )
    assert config.constraints is not None
    assert config.constraints.force_external_summarizer is True


def test_boundary_context_window_8191():
    """8191 (just under 8K) triggers auto-apply."""
    config = LLMConfig(
        model="test-model",
        model_endpoint_type="openai",
        context_window=8191,
        handle="test/test",
    )
    assert config.constraints is not None
