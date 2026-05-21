"""Tests for the tool call JSON repair pipeline and parallel tool call handling."""

import json

import pytest

from letta.agents.helpers import _safe_load_tool_call_str
from letta.schemas.llm_config import LLMConfig, ModelConstraints


# === Valid JSON ===

def test_valid_json():
    """Valid JSON passes through unchanged."""
    result = _safe_load_tool_call_str('{"message": "hello"}')
    assert result == {"message": "hello"}


def test_valid_nested_json():
    """Valid nested JSON passes through."""
    result = _safe_load_tool_call_str('{"function": "send_message", "params": {"message": "hi"}}')
    assert result == {"function": "send_message", "params": {"message": "hi"}}


# === Repair: extra braces ===

def test_extra_closing_brace():
    """Extra closing brace is handled by clean_json."""
    result = _safe_load_tool_call_str('{"message": "hello"}}')
    assert result == {"message": "hello"}


def test_missing_closing_brace():
    """Missing closing brace is handled by clean_json."""
    result = _safe_load_tool_call_str('{"message": "hello"')
    assert result == {"message": "hello"}


# === Parallel tool call splitting ===

def test_parallel_tool_call_leading_closing_brace():
    """}{ between two objects: take the first valid object."""
    result = _safe_load_tool_call_str('}{"message": "hello"}{"message": "world"}')
    assert result == {"message": "hello"}


def test_parallel_tool_call_two_objects():
    """Two JSON objects back to back: take the first."""
    result = _safe_load_tool_call_str('{"message": "hello"}{"message": "world"}')
    assert result == {"message": "hello"}


# === Code-fenced JSON (common with small models) ===

def test_code_fenced_json():
    """JSON wrapped in markdown code fences is extracted."""
    result = _safe_load_tool_call_str('```json\n{"message": "hello"}\n```')
    assert result == {"message": "hello"}


def test_code_fenced_nested_json():
    """Nested JSON in code fences is extracted."""
    result = _safe_load_tool_call_str(
        '```json\n{"function": "send_message", "params": {"message": "hi"}}\n```'
    )
    assert result == {"function": "send_message", "params": {"message": "hi"}}


# === Edge cases ===

def test_empty_string():
    """Empty string returns empty dict."""
    result = _safe_load_tool_call_str("")
    assert result == {}


def test_garbage_text():
    """Non-JSON text returns empty dict."""
    result = _safe_load_tool_call_str("Hello, I am a helpful assistant.")
    assert result == {}


def test_escaped_quotes():
    """Double-escaped quotes are handled."""
    result = _safe_load_tool_call_str('{"message": "He said \\"hi\\""}')
    assert result == {"message": 'He said "hi"'}


# === With aggressive config ===

def test_aggressive_config_embedded_json():
    """With aggressive config, JSON embedded in surrounding text is extracted."""
    config = LLMConfig(
        model="test",
        model_endpoint_type="openai",
        context_window=4096,
        handle="test/test",
    )
    result = _safe_load_tool_call_str(
        'Some text {"function": "send_message", "params": {"message": "hi"}} more text',
        llm_config=config,
    )
    assert result == {"function": "send_message", "params": {"message": "hi"}}


# === With no config (basic mode) ===

def test_basic_mode_no_config():
    """Without llm_config, basic repair still works."""
    result = _safe_load_tool_call_str('{"message": "hello"}}')
    assert result == {"message": "hello"}


# === Real-world-like tool call patterns ===

def test_send_message_tool_call():
    """Simulates a send_message tool call from a small model."""
    raw = '{"function": "send_message", "params": {"message": "Hello! How can I help?"}}'
    result = _safe_load_tool_call_str(raw)
    assert result["function"] == "send_message"
    assert result["params"]["message"] == "Hello! How can I help?"


def test_core_memory_replace_tool_call():
    """Simulates a core_memory_replace tool call."""
    raw = json.dumps({
        "function": "core_memory_replace",
        "params": {
            "label": "human",
            "old_content": "",
            "new_content": "Name: Alice, Likes: coffee",
        },
    })
    result = _safe_load_tool_call_str(raw)
    assert result["function"] == "core_memory_replace"
    assert result["params"]["label"] == "human"
    assert "Alice" in result["params"]["new_content"]
