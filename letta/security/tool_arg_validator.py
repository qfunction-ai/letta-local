"""Tool argument validator — validates tool args before execution.

Checks tool arguments against the tool's JSON schema and
detects common attack patterns (path traversal, SQL injection
markers). Called before PolicyChecker.check() in the tool
execution pipeline.

If validation fails, the caller returns a TOOL_DENIED
PolicyDecision with matched_rule="argument_validation_failed".
No new SecurityEventType is needed — TOOL_DENIED with the
matched_rule field carries the specific reason.

Usage in agent_security.py:
    from letta.security.tool_arg_validator import validate_tool_args
    error = validate_tool_args(tool_name, tool_args, tool_schema)
    if error:
        # return PolicyDecision(allowed=False, matched_rule="argument_validation_failed", ...)

Opt-in via agent.tool_arg_validation_enabled (default False).
Fail-open: if the validator crashes, it returns None (args pass through).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    import regex as re
except ImportError:
    import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Attack patterns — checked on all string args regardless of schema
# ---------------------------------------------------------------------------

# Path traversal: ../, ..\, %2e%2e, /etc/passwd, /etc/shadow
_PATH_TRAVERSAL = re.compile(
    r"\.\./|\.\.\\|%2e%2e|%2e%2e/|(?:^|/)(?:etc/(?:passwd|shadow)|proc/self/environ)",
    re.IGNORECASE,
)

# SQL injection markers in args that shouldn't contain SQL
_SQL_INJECTION = re.compile(
    r"(?:';\s*(?:drop|delete|update|insert|truncate)\b|--\s|/\*|\bor\s+1\s*=\s*1\b)",
    re.IGNORECASE,
)


def validate_tool_args(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_schema: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Validate tool arguments against schema and attack patterns.

    Returns an error string if validation fails, None if args are valid.
    The caller should use the error string as the reason in a TOOL_DENIED
    PolicyDecision with matched_rule="argument_validation_failed".

    Args:
        tool_name: Name of the tool being called.
        tool_args: The tool arguments dict from the LLM.
        tool_schema: Optional JSON schema dict for the tool's parameters.
            If None, only attack pattern checks are performed.

    Returns:
        Error string if validation fails, None if valid.
    """
    try:
        if not isinstance(tool_args, dict):
            return None  # Nothing to validate

        # --- Attack pattern checks (always run, regardless of schema) ---
        for key, value in tool_args.items():
            if isinstance(value, str):
                if _PATH_TRAVERSAL.search(value):
                    return f"Path traversal detected in argument '{key}'"
                if _SQL_INJECTION.search(value):
                    return f"Potential SQL injection in argument '{key}'"

        # --- Schema-based validation (only if schema provided) ---
        if tool_schema is None:
            return None  # No schema = no schema validation

        properties = tool_schema.get("properties", {})
        required_fields = set(tool_schema.get("required", []))

        # Check required fields are present
        missing = required_fields - set(tool_args.keys())
        if missing:
            return f"Missing required argument(s): {', '.join(sorted(missing))}"

        # Check types for provided args
        for key, value in tool_args.items():
            if key not in properties:
                continue  # Extra args — don't reject, schema may be permissive

            prop_schema = properties[key]
            expected_type = prop_schema.get("type")

            if expected_type and not _check_type(value, expected_type, prop_schema):
                return f"Argument '{key}' expected type {expected_type}, got {type(value).__name__}"

        return None

    except Exception as e:
        logger.warning(f"Tool arg validation failed (fail-open): {e}")
        return None


def _check_type(value: Any, expected_type: str, prop_schema: dict) -> bool:
    """Check if a value matches the expected JSON Schema type.

    Returns True if the type matches, False otherwise.
    """
    if expected_type == "string":
        return isinstance(value, str)
    elif expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_type == "boolean":
        return isinstance(value, bool)
    elif expected_type == "array":
        return isinstance(value, list)
    elif expected_type == "object":
        return isinstance(value, dict)
    elif expected_type == "null":
        return value is None
    return True  # Unknown type — don't reject
