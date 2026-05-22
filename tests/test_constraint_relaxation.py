"""Tests for ModelConstraints.relax_constraints_after_probe."""

import pytest

from letta.schemas.llm_config import ModelConstraints


class TestRelaxConstraintsAfterProbe:
    def test_native_mode_relaxes_aggressive_defaults(self):
        """When probe confirms native tool calling, aggressive constraints are relaxed."""
        constraints = ModelConstraints(
            tool_calling_mode="auto",
            tool_call_retry_count=3,
            disable_structured_output=True,
            json_repair_level="aggressive",
        )

        constraints.relax_constraints_after_probe("native")

        assert constraints.tool_call_retry_count == 0
        assert constraints.disable_structured_output is False
        assert constraints.json_repair_level == "basic"

    def test_prompt_mode_does_not_relax(self):
        """When probe resolves to prompt mode, constraints stay intact."""
        constraints = ModelConstraints(
            tool_calling_mode="auto",
            tool_call_retry_count=3,
            disable_structured_output=True,
            json_repair_level="aggressive",
        )

        constraints.relax_constraints_after_probe("prompt")

        assert constraints.tool_call_retry_count == 3
        assert constraints.disable_structured_output is True
        assert constraints.json_repair_level == "aggressive"

    def test_already_relaxed_constraints_stay_relaxed(self):
        """Relaxing already-relaxed constraints is a no-op."""
        constraints = ModelConstraints(
            tool_calling_mode="native",
            tool_call_retry_count=0,
            disable_structured_output=False,
            json_repair_level="basic",
        )

        constraints.relax_constraints_after_probe("native")

        # No changes needed
        assert constraints.tool_call_retry_count == 0
        assert constraints.disable_structured_output is False
        assert constraints.json_repair_level == "basic"

    def test_none_repair_level_not_touched(self):
        """json_repair_level='none' is not overwritten by relaxation."""
        constraints = ModelConstraints(
            tool_calling_mode="auto",
            tool_call_retry_count=3,
            disable_structured_output=True,
            json_repair_level="none",
        )

        constraints.relax_constraints_after_probe("native")

        # 'none' is not 'aggressive', so it stays as-is
        assert constraints.json_repair_level == "none"
        # But the other fields still relax
        assert constraints.tool_call_retry_count == 0
        assert constraints.disable_structured_output is False

    def test_partial_constraints_relax_only_relevant(self):
        """Only relax fields that are set to aggressive defaults."""
        constraints = ModelConstraints(
            tool_calling_mode="auto",
            tool_call_retry_count=2,  # not 3, but still > 0
            disable_structured_output=False,  # already False
            json_repair_level="aggressive",
        )

        constraints.relax_constraints_after_probe("native")

        assert constraints.tool_call_retry_count == 0  # > 0 gets zeroed
        assert constraints.disable_structured_output is False  # unchanged
        assert constraints.json_repair_level == "basic"  # aggressive → basic

    def test_end_to_end_auto_apply_then_relax(self):
        """Simulate the real flow: auto-apply constraints, then relax after probe."""
        from letta.schemas.llm_config import LLMConfig

        # Create a config for a local model — this triggers apply_default_constraints
        config = LLMConfig(
            model="llama3.1:8b",
            model_endpoint_type="ollama",
            model_endpoint="http://localhost:11434",
            context_window=8192,
        )

        # Verify auto-applied constraints
        assert config.constraints is not None
        assert config.constraints.disable_structured_output is True
        assert config.constraints.json_repair_level == "aggressive"
        assert config.constraints.tool_call_retry_count == 3

        # Simulate probe resolving to "native"
        config.constraints.relax_constraints_after_probe("native")

        # Verify relaxation
        assert config.constraints.disable_structured_output is False
        assert config.constraints.json_repair_level == "basic"
        assert config.constraints.tool_call_retry_count == 0
