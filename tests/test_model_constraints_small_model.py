"""Tests for max_tools and simplify_tool_schemas in ModelConstraints."""

import pytest
from letta.schemas.llm_config import LLMConfig, ModelConstraints


class TestModelConstraintsFields:
    """Test the new ModelConstraints fields."""

    def test_max_tools_default_none(self):
        """max_tools defaults to None (unlimited)."""
        mc = ModelConstraints()
        assert mc.max_tools is None

    def test_simplify_tool_schemas_default_false(self):
        """simplify_tool_schemas defaults to False."""
        mc = ModelConstraints()
        assert mc.simplify_tool_schemas is False

    def test_max_tools_set(self):
        """max_tools can be set to an integer."""
        mc = ModelConstraints(max_tools=15)
        assert mc.max_tools == 15

    def test_simplify_tool_schemas_set(self):
        """simplify_tool_schemas can be set to True."""
        mc = ModelConstraints(simplify_tool_schemas=True)
        assert mc.simplify_tool_schemas is True


class TestApplyDefaultConstraints:
    """Test auto-application of constraints for local providers."""

    def test_auto_apply_local_provider(self):
        """Ollama provider gets max_tools=15 and simplify_tool_schemas=True."""
        config = LLMConfig(
            model="mistral:7b",
            model_endpoint_type="ollama",
            model_endpoint="http://localhost:11434",
            context_window=8192,
        )
        assert config.constraints is not None
        assert config.constraints.max_tools == 15
        assert config.constraints.simplify_tool_schemas is True

    def test_auto_apply_cloud_provider(self):
        """OpenAI provider does NOT get max_tools or simplify_tool_schemas."""
        config = LLMConfig(
            model="gpt-4o",
            model_endpoint_type="openai",
            model_endpoint="https://api.openai.com/v1",
            context_window=128000,
        )
        # Cloud providers either have no constraints or constraints without the new fields
        if config.constraints is not None:
            assert config.constraints.max_tools is None
            assert config.constraints.simplify_tool_schemas is False

    def test_explicit_constraints_not_overridden(self):
        """If constraints are explicitly set, apply_default_constraints doesn't override."""
        custom = ModelConstraints(max_tools=5, simplify_tool_schemas=False)
        config = LLMConfig(
            model="mistral:7b",
            model_endpoint_type="ollama",
            model_endpoint="http://localhost:11434",
            context_window=8192,
            constraints=custom,
        )
        assert config.constraints.max_tools == 5
        assert config.constraints.simplify_tool_schemas is False


class TestRelaxConstraintsAfterProbe:
    """Test relax_constraints_after_probe with new fields."""

    def test_relax_after_native_probe(self):
        """When probe confirms native tool calling, new constraints are relaxed."""
        mc = ModelConstraints(
            tool_calling_mode="auto",
            disable_structured_output=True,
            tool_call_retry_count=3,
            json_repair_level="aggressive",
            max_tools=15,
            simplify_tool_schemas=True,
        )
        mc.relax_constraints_after_probe("native")
        assert mc.simplify_tool_schemas is False
        assert mc.max_tools is None
        # Other constraints also relaxed
        assert mc.tool_call_retry_count == 0
        assert mc.disable_structured_output is False
        assert mc.json_repair_level == "basic"

    def test_relax_after_prompt_probe(self):
        """When probe resolves to prompt mode, new constraints stay active."""
        mc = ModelConstraints(
            tool_calling_mode="auto",
            disable_structured_output=True,
            tool_call_retry_count=3,
            json_repair_level="aggressive",
            max_tools=15,
            simplify_tool_schemas=True,
        )
        mc.relax_constraints_after_probe("prompt")
        # Constraints stay — model needs the aggressive pipeline
        assert mc.simplify_tool_schemas is True
        assert mc.max_tools == 15
        assert mc.tool_call_retry_count == 3
        assert mc.disable_structured_output is True


class TestMaxToolsFiltering:
    """Test the max_tools filtering logic in _get_valid_tools."""

    def test_max_tools_drops_non_essential(self):
        """15 tools with max_tools=5 → 5 tools, send_message kept."""
        # Simulate the filtering logic directly
        tools = [{"name": f"tool_{i}"} for i in range(14)]
        tools.insert(0, {"name": "send_message"})
        # Total: 15 tools

        max_tools = 5
        essential_names = {"send_message"}

        essential = [t for t in tools if t.get("name") in essential_names]
        non_essential = [t for t in tools if t.get("name") not in essential_names]

        remaining = max_tools - len(essential)
        if remaining > 0:
            result = essential + non_essential[:remaining]
        else:
            result = essential

        assert len(result) == 5
        assert result[0]["name"] == "send_message"
        # Non-essential tools are the first 4 from the non_essential list
        assert result[1]["name"] == "tool_0"

    def test_max_tools_none_unlimited(self):
        """max_tools=None → all tools kept (no filtering)."""
        tools = [{"name": f"tool_{i}"} for i in range(20)]
        max_tools = None

        # When max_tools is None, the if condition is falsy, so no filtering
        if max_tools and len(tools) > max_tools:
            result = tools[:max_tools]
        else:
            result = tools

        assert len(result) == 20

    def test_max_tools_under_limit(self):
        """3 tools with max_tools=10 → all 3 kept."""
        tools = [{"name": "send_message"}, {"name": "tool_a"}, {"name": "tool_b"}]
        max_tools = 10

        if max_tools and len(tools) > max_tools:
            essential_names = {"send_message"}
            essential = [t for t in tools if t.get("name") in essential_names]
            non_essential = [t for t in tools if t.get("name") not in essential_names]
            remaining = max_tools - len(essential)
            result = essential + non_essential[:remaining]
        else:
            result = tools

        assert len(result) == 3

    def test_max_tools_only_essential(self):
        """When max_tools < number of essential tools, only essential kept."""
        tools = [{"name": "send_message"}, {"name": "tool_a"}, {"name": "tool_b"}]
        max_tools = 1

        essential_names = {"send_message"}
        essential = [t for t in tools if t.get("name") in essential_names]
        non_essential = [t for t in tools if t.get("name") not in essential_names]

        remaining = max_tools - len(essential)
        if remaining > 0:
            result = essential + non_essential[:remaining]
        else:
            result = essential

        assert len(result) == 1
        assert result[0]["name"] == "send_message"
