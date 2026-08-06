"""Tests for letta.security.tool_arg_validator — validate_tool_args."""

import pytest

from letta.security.tool_arg_validator import validate_tool_args


class TestToolArgValidator:
    """Unit tests for the tool argument validator."""

    def _schema(self):
        """Standard schema for testing."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        }

    def test_valid_args_pass(self):
        """Valid args matching schema -> None (pass)."""
        assert validate_tool_args("archival_memory_search", {"query": "test", "top_k": 5}, self._schema()) is None

    def test_path_traversal_detected(self):
        """Path traversal in string arg -> error."""
        err = validate_tool_args("file_read", {"path": "../../../etc/passwd"}, None)
        assert err is not None
        assert "Path traversal" in err

    def test_missing_required_field(self):
        """Missing required field -> error."""
        err = validate_tool_args("archival_memory_search", {"top_k": 5}, self._schema())
        assert err is not None
        assert "Missing required" in err
        assert "query" in err

    def test_type_mismatch(self):
        """Type mismatch (string expected, int provided) -> error."""
        err = validate_tool_args("archival_memory_search", {"query": 123}, self._schema())
        assert err is not None
        assert "expected type" in err
        assert "string" in err

    def test_no_schema_passes(self):
        """No schema provided -> only attack patterns checked, valid args pass."""
        assert validate_tool_args("file_read", {"path": "/tmp/safe.txt"}, None) is None

    def test_sql_injection_detected(self):
        """SQL injection markers in string arg -> error."""
        err = validate_tool_args("db_query", {"query": "'; DROP TABLE users; --"}, None)
        assert err is not None
        assert "SQL injection" in err

    def test_fail_open_on_crash(self):
        """Validator crash -> fail-open, returns None (no crash)."""
        # Patch _check_type to raise — the try/except in validate_tool_args
        # catches it and returns None
        import unittest.mock
        with unittest.mock.patch(
            "letta.security.tool_arg_validator._check_type",
            side_effect=RuntimeError("boom"),
        ):
            schema = {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }
            result = validate_tool_args("test", {"query": "test"}, schema)
            assert result is None

    def test_empty_args_pass(self):
        """Empty args dict -> None (no schema) or error (with required fields)."""
        assert validate_tool_args("noop", {}, None) is None

    def test_array_type_check(self):
        """Array type validation."""
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            "required": ["tags"],
        }
        assert validate_tool_args("test", {"tags": ["a", "b"]}, schema) is None
        err = validate_tool_args("test", {"tags": "not-an-array"}, schema)
        assert err is not None
        assert "expected type" in err

    def test_boolean_type_check(self):
        """Boolean type validation (bool is not int)."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
            "required": ["flag"],
        }
        assert validate_tool_args("test", {"flag": True}, schema) is None
        err = validate_tool_args("test", {"flag": 1}, schema)
        assert err is not None
        assert "expected type" in err
