"""Tests for letta.llm_api.schema_simplifier."""

import pytest
from letta.llm_api.schema_simplifier import (
    simplify_tool_schemas,
    _truncate_description,
    _simplify_property,
)


class TestSimplifyToolSchemas:
    """Tests for simplify_tool_schemas()."""

    def test_simplify_strips_optional_params(self):
        """Tool with 6 params (1 required, 5 optional) → 1 param."""
        tool = {
            "name": "archival_memory_search",
            "description": "Search archival memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter tags."},
                    "tag_match_mode": {"type": "string", "enum": ["any", "all"], "description": "Match mode."},
                    "top_k": {"type": "integer", "description": "Max results."},
                    "start_datetime": {"type": "string", "description": "Start date."},
                    "end_datetime": {"type": "string", "description": "End date."},
                },
                "required": ["query"],
            },
        }
        result = simplify_tool_schemas([tool])
        params = result[0]["parameters"]["properties"]
        assert "query" in params
        assert "tags" not in params
        assert "tag_match_mode" not in params
        assert "top_k" not in params
        assert "start_datetime" not in params
        assert "end_datetime" not in params
        assert len(params) == 1

    def test_simplify_replaces_enum(self):
        """Param with enum: ["any", "all"] → type: string + description updated."""
        tool = {
            "name": "test_tool",
            "description": "Test tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["any", "all"], "description": "Match mode."},
                },
                "required": ["mode"],
            },
        }
        result = simplify_tool_schemas([tool])
        prop = result[0]["parameters"]["properties"]["mode"]
        assert "enum" not in prop
        assert prop["type"] == "string"
        assert "any" in prop["description"]
        assert "all" in prop["description"]

    def test_simplify_truncates_description(self):
        """Long description → first 2 sentences or 200 chars, whichever is shorter."""
        long_desc = (
            "This is the first sentence of a very long description. "
            "This is the second sentence with more detail. "
            "This is the third sentence that should be cut off because it exceeds the limit. "
        ) * 5  # Make it well over 200 chars

        tool = {
            "name": "test_tool",
            "description": long_desc,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": long_desc},
                    "optional_param": {"type": "string", "description": "Optional."},  # triggers simplification
                },
                "required": ["query"],
            },
        }
        result = simplify_tool_schemas([tool])
        # Tool-level description truncated
        assert len(result[0]["description"]) <= 203  # 200 + "..."
        # Property description truncated
        prop_desc = result[0]["parameters"]["properties"]["query"]["description"]
        assert len(prop_desc) <= 203

    def test_simplify_leaves_simple_tools_unchanged(self):
        """Tool with 1 required param, no enum → unchanged."""
        tool = {
            "name": "send_message",
            "description": "Send a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message to send."},
                },
                "required": ["message"],
            },
        }
        result = simplify_tool_schemas([tool])
        # Should be the same tool (possibly same object, but at least same content)
        assert result[0]["name"] == "send_message"
        assert result[0]["parameters"]["properties"] == tool["parameters"]["properties"]

    def test_simplify_preserves_required_params(self):
        """Required params are never stripped, even when simplifying."""
        tool = {
            "name": "memory",
            "description": "Memory operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command."},
                    "path": {"type": "string", "description": "The path."},
                    "old_string": {"type": "string", "description": "Old text."},
                    "new_string": {"type": "string", "description": "New text."},
                },
                "required": ["command", "path", "old_string", "new_string"],
            },
        }
        result = simplify_tool_schemas([tool])
        params = result[0]["parameters"]["properties"]
        # All required params preserved
        assert "command" in params
        assert "path" in params
        assert "old_string" in params
        assert "new_string" in params
        assert len(params) == 4

    def test_simplify_array_type(self):
        """Array param: stripped if optional, kept if required (with items simplified)."""
        tool = {
            "name": "conversation_search",
            "description": "Search conversations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "roles": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["assistant", "user", "tool"]},
                        "description": "Roles to filter by.",
                    },
                },
                "required": ["query"],  # roles is optional
            },
        }
        result = simplify_tool_schemas([tool])
        params = result[0]["parameters"]["properties"]
        # query (required) kept, roles (optional) stripped
        assert "query" in params
        assert "roles" not in params

    def test_simplify_array_required_with_enum_items(self):
        """Required array param with enum items: kept, items enum replaced."""
        tool = {
            "name": "test_tool",
            "description": "Test.",
            "parameters": {
                "type": "object",
                "properties": {
                    "roles": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["a", "b", "c"]},
                        "description": "Roles.",
                    },
                },
                "required": ["roles"],
            },
        }
        result = simplify_tool_schemas([tool])
        prop = result[0]["parameters"]["properties"]["roles"]
        assert prop["type"] == "array"
        # Items should have enum replaced
        assert "enum" not in prop["items"]
        assert prop["items"]["type"] == "string"
        assert "a" in prop["items"]["description"]

    def test_simplify_multiple_tools(self):
        """Multiple tools: simple ones pass through, complex ones simplified."""
        tools = [
            {
                "name": "send_message",
                "description": "Send a message.",
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "Msg."}},
                    "required": ["message"],
                },
            },
            {
                "name": "archival_memory_search",
                "description": "Search archival memory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Query."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags."},
                        "top_k": {"type": "integer", "description": "Max results."},
                    },
                    "required": ["query"],
                },
            },
        ]
        result = simplify_tool_schemas(tools)
        # First tool (simple) unchanged
        assert len(result[0]["parameters"]["properties"]) == 1
        # Second tool (complex) simplified
        assert len(result[1]["parameters"]["properties"]) == 1
        assert "query" in result[1]["parameters"]["properties"]

    def test_simplify_removes_additional_properties(self):
        """additionalProperties is removed when simplifying."""
        tool = {
            "name": "test_tool",
            "description": "Test tool with optional params.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query."},
                    "extra": {"type": "string", "description": "Extra optional param."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
        result = simplify_tool_schemas([tool])
        assert "additionalProperties" not in result[0]["parameters"]

    def test_simplify_does_not_mutate_original(self):
        """Original tool list is not mutated."""
        tool = {
            "name": "test_tool",
            "description": "Test tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query."},
                    "optional": {"type": "string", "description": "Optional."},
                },
                "required": ["query"],
            },
        }
        original_props = dict(tool["parameters"]["properties"])
        simplify_tool_schemas([tool])
        assert tool["parameters"]["properties"] == original_props


class TestTruncateDescription:
    """Tests for _truncate_description()."""

    def test_short_description_unchanged(self):
        """Short descriptions pass through unchanged."""
        desc = "Search archival memory for a specific term."
        assert _truncate_description(desc) == desc

    def test_long_description_truncated(self):
        """Long descriptions are truncated to <= 200 chars + ellipsis."""
        desc = "This is a sentence. " * 30  # ~480 chars
        result = _truncate_description(desc)
        assert len(result) <= 203  # 200 + "..."
        assert result.endswith("...")

    def test_two_sentence_limit(self):
        """Descriptions are cut to at most 2 sentences."""
        desc = (
            "First sentence here. "
            "Second sentence here. "
            "Third sentence here that should not appear. "
        ) * 10
        result = _truncate_description(desc)
        # Should not contain the third sentence's unique text
        assert "should not appear" not in result


class TestSimplifyProperty:
    """Tests for _simplify_property()."""

    def test_enum_replaced(self):
        """Enum is replaced with string type and description."""
        prop = {"type": "string", "enum": ["any", "all"], "description": "Match mode."}
        result = _simplify_property(prop)
        assert "enum" not in result
        assert result["type"] == "string"
        assert "any" in result["description"]
        assert "all" in result["description"]

    def test_nested_array_items_simplified(self):
        """Array items with enums are recursively simplified."""
        prop = {
            "type": "array",
            "items": {"type": "string", "enum": ["a", "b"], "description": "Items."},
            "description": "Array param.",
        }
        result = _simplify_property(prop)
        assert result["type"] == "array"
        assert "enum" not in result["items"]
        assert "a" in result["items"]["description"]
