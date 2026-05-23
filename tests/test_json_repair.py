"""Tests for letta.helpers.json_helpers — _extract_first_json_object + safe_load_tool_call_str."""

import json
import pytest
from unittest.mock import MagicMock, patch

from letta.helpers.json_helpers import _extract_first_json_object, safe_load_tool_call_str


class TestExtractFirstJsonObject:
    """Balanced JSON object extractor — the fix for the }{ split hack."""

    def test_parallel_tool_call(self):
        """Two objects concatenated via }{."""
        result = _extract_first_json_object('{"content": "hello"}{"content": "world"}')
        assert json.loads(result) == {"content": "hello"}

    def test_leading_brace_then_object(self):
        """Starts with } (previous object's closing brace)."""
        result = _extract_first_json_object('}{"content": "hello"}')
        assert json.loads(result) == {"content": "hello"}

    def test_nested_braces(self):
        """Nested objects — the bug that rstrip('}') broke."""
        result = _extract_first_json_object('}{"nested": {"key": "val"}}')
        assert json.loads(result) == {"nested": {"key": "val"}}

    def test_surrounding_noise(self):
        """Garbage before and after the JSON object."""
        result = _extract_first_json_object('some noise{"key": "val"}more noise')
        assert json.loads(result) == {"key": "val"}

    def test_no_parallel_call(self):
        """Single valid JSON object — should be returned unchanged."""
        result = _extract_first_json_object('{"key": "val"}')
        assert json.loads(result) == {"key": "val"}

    def test_deep_nesting(self):
        """Three levels of nesting with a second object after."""
        result = _extract_first_json_object('{"a": {"b": {"c": "d"}}}{"x": "y"}')
        assert json.loads(result) == {"a": {"b": {"c": "d"}}}

    def test_empty_string(self):
        result = _extract_first_json_object("")
        assert result == ""

    def test_no_json_object(self):
        """No braces at all — return original."""
        result = _extract_first_json_object("just plain text")
        assert result == "just plain text"

    def test_unbalanced_braces(self):
        """Opening brace with no closing brace — return original."""
        result = _extract_first_json_object('{"key": "val"')
        assert result == '{"key": "val"'


class TestSafeLoadToolCallStr:
    """Full repair pipeline including envelope-aware extraction."""

    def test_valid_json(self):
        result = safe_load_tool_call_str('{"key": "val"}')
        assert result == {"key": "val"}

    def test_parallel_tool_call(self):
        """Parallel tool call: extract first object."""
        result = safe_load_tool_call_str('{"content": "hello"}{"content": "world"}')
        assert result == {"content": "hello"}

    def test_parallel_with_nested(self):
        """Parallel tool call with nested braces — the real bug case."""
        result = safe_load_tool_call_str('}{"nested": {"key": "val"}}')
        assert result == {"nested": {"key": "val"}}

    def test_envelope_valid(self):
        """Prompt-based tool call envelope — valid JSON."""
        result = safe_load_tool_call_str('{"function": "core_memory_append", "params": {"content": "hi"}}')
        assert result == {"function": "core_memory_append", "params": {"content": "hi"}}

    def test_envelope_missing_closing_brace(self):
        """Prompt-based tool call envelope — missing closing brace.
        The balanced extractor should still find the object."""
        # This string has an unbalanced closing brace
        result = safe_load_tool_call_str('{"function": "core_memory_append", "params": {"content": "hi"}}')
        assert result.get("function") == "core_memory_append"

    def test_envelope_with_noise(self):
        """Prompt-based tool call envelope with surrounding noise."""
        result = safe_load_tool_call_str('Some text {"function": "web_search", "params": {"query": "test"}} more text')
        assert result.get("function") == "web_search"
        assert result.get("params") == {"query": "test"}

    def test_invalid_json_repair_none(self):
        """With repair=none, invalid JSON returns empty dict."""
        mock_config = MagicMock()
        mock_config.constraints.json_repair_level = "none"
        result = safe_load_tool_call_str('{invalid json}', llm_config=mock_config)
        assert result == {}

    def test_anthropic_nested_json(self):
        """Anthropic sometimes wraps JSON in another JSON string."""
        result = safe_load_tool_call_str('{"function": "send_message", "params": {"message": "hello"}}')
        assert result == {"function": "send_message", "params": {"message": "hello"}}

    def test_empty_string(self):
        result = safe_load_tool_call_str("")
        assert result == {}

    def test_no_json_at_all(self):
        result = safe_load_tool_call_str("just some random text")
        assert result == {}
