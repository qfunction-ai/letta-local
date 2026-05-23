import base64
import json
import re
from datetime import datetime
from typing import Any

# Precompiled regex for surrogate range U+D800..U+DFFF — much faster than char-by-char ord() loop
_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def sanitize_unicode_surrogates(value: Any) -> Any:
    """Recursively remove invalid Unicode surrogate characters from strings.

    Unicode surrogate pairs (U+D800 to U+DFFF) are used internally by UTF-16 encoding
    but are invalid as standalone characters in UTF-8. When present, they cause
    UnicodeEncodeError when encoding to UTF-8, breaking API requests that need to
    serialize data to JSON.

    This function sanitizes:
    - Strings: removes unpaired surrogates that can't be encoded to UTF-8
    - Dicts: recursively sanitizes all string values
    - Lists: recursively sanitizes all elements
    - Other types: returned as-is

    Args:
        value: The value to sanitize

    Returns:
        The sanitized value with surrogate characters removed from all strings
    """
    if isinstance(value, str):
        # Remove lone surrogate characters (U+D800 to U+DFFF) which are invalid in UTF-8.
        # re.sub runs in C and is orders of magnitude faster than a char-by-char Python loop,
        # which was blocking the asyncio event loop on large LLM payloads.
        try:
            return _SURROGATE_RE.sub("", value)
        except Exception:
            # Fallback: try encode with errors="replace" which replaces surrogates with �
            try:
                return value.encode("utf-8", errors="replace").decode("utf-8")
            except Exception:
                # Last resort: return original (should never reach here)
                return value
    elif isinstance(value, dict):
        # Recursively sanitize dictionary keys and values
        return {sanitize_unicode_surrogates(k): sanitize_unicode_surrogates(v) for k, v in value.items()}
    elif isinstance(value, list):
        # Recursively sanitize list elements
        return [sanitize_unicode_surrogates(item) for item in value]
    elif isinstance(value, tuple):
        # Recursively sanitize tuple elements (return as tuple)
        return tuple(sanitize_unicode_surrogates(item) for item in value)
    else:
        # Return other types as-is (int, float, bool, None, etc.)
        return value


def sanitize_null_bytes(value: Any) -> Any:
    """Recursively remove null bytes (0x00) from strings.

    PostgreSQL TEXT columns don't accept null bytes in UTF-8 encoding, which causes
    asyncpg.exceptions.CharacterNotInRepertoireError when data with null bytes is inserted.

    This function sanitizes:
    - Strings: removes all null bytes
    - Dicts: recursively sanitizes all string values
    - Lists: recursively sanitizes all elements
    - Other types: returned as-is

    Args:
        value: The value to sanitize

    Returns:
        The sanitized value with null bytes removed from all strings
    """
    if isinstance(value, str):
        # Remove null bytes from strings
        return value.replace("\x00", "")
    elif isinstance(value, dict):
        # Recursively sanitize dictionary keys and values
        return {sanitize_null_bytes(k): sanitize_null_bytes(v) for k, v in value.items()}
    elif isinstance(value, list):
        # Recursively sanitize list elements
        return [sanitize_null_bytes(item) for item in value]
    elif isinstance(value, tuple):
        # Recursively sanitize tuple elements (return as tuple)
        return tuple(sanitize_null_bytes(item) for item in value)
    else:
        # Return other types as-is (int, float, bool, None, etc.)
        return value


def json_loads(data):
    return json.loads(data, strict=False)


def json_dumps(data, indent=2) -> str:
    """Serialize data to JSON string, sanitizing null bytes to prevent PostgreSQL errors.

    PostgreSQL TEXT columns reject null bytes (0x00) in UTF-8 encoding. This function
    sanitizes all strings in the data structure before JSON serialization to prevent
    asyncpg.exceptions.CharacterNotInRepertoireError.

    Args:
        data: The data to serialize
        indent: JSON indentation level (default: 2)

    Returns:
        JSON string with null bytes removed from all string values
    """
    # Sanitize null bytes before serialization to prevent PostgreSQL errors
    sanitized_data = sanitize_null_bytes(data)

    def safe_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, bytes):
            try:
                decoded = obj.decode("utf-8")
                # Also sanitize decoded bytes
                return decoded.replace("\x00", "")
            except Exception:
                # TODO: this is to handle Gemini thought signatures, b64 decode this back to bytes when sending back to Gemini
                return base64.b64encode(obj).decode("utf-8")
        raise TypeError(f"Type {type(obj)} not serializable")

    return json.dumps(sanitized_data, indent=indent, default=safe_serializer, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool call JSON repair — fork-specific logic for handling malformed LLM output
# ---------------------------------------------------------------------------

def _extract_first_json_object(s: str) -> str:
    """Extract the first balanced JSON object from a string.

    Handles parallel tool calls (}{  boundary), leading/trailing
    noise, and nested braces. Returns the first complete JSON object
    found, or the original string if no balanced object is found.

    Limitation: does not handle braces inside JSON string values
    (e.g., {"key": "hello {world}"}). This is acceptable because
    LLM output in the tool-calling context does not produce
    unbalanced braces inside string values while also concatenating
    two JSON objects.
    """
    depth = 0
    start = None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    return s[start:i + 1]
    # No balanced object found — return original
    return s


def safe_load_tool_call_str(tool_call_args_str: str, llm_config=None) -> dict:
    """Lenient JSON → dict with fallback repair strategies for degraded models.

    Moved from helpers.py to keep fork-specific repair logic in a
    module with zero upstream overlap.

    Repair intensity depends on ModelConstraints.json_repair_level:
    - "none": fail fast, return {} on JSON errors
    - "basic": try clean_json repair pipeline (default)
    - "aggressive": also try regex extraction of JSON-like structures
    """
    # Handle parallel tool calling — extract first complete JSON object
    if "}{" in tool_call_args_str:
        tool_call_args_str = _extract_first_json_object(tool_call_args_str)

    try:
        tool_args = json.loads(tool_call_args_str, strict=False)
        if not isinstance(tool_args, dict):
            # Anthropic sometimes returns weird nested JSON
            tool_args = json.loads(tool_args)
        return tool_args
    except json.JSONDecodeError:
        pass

    # Envelope-aware extraction for prompt-based tool calling
    # ({"function": "...", "params": {...}} format)
    # This runs before clean_json so the envelope structure is preserved
    if "function" in tool_call_args_str and "params" in tool_call_args_str:
        try:
            extracted = _extract_first_json_object(tool_call_args_str)
            candidate = json.loads(extracted, strict=False)
            if isinstance(candidate, dict) and "function" in candidate and "params" in candidate:
                return candidate
        except json.JSONDecodeError:
            pass

    # Determine repair level from constraints
    repair_level = "basic"
    if llm_config is not None and llm_config.constraints is not None:
        repair_level = llm_config.constraints.json_repair_level

    if repair_level == "none":
        return {}

    # Basic repair: use the existing clean_json pipeline
    if repair_level in ("basic", "aggressive"):
        try:
            from letta.local_llm.json_parser import clean_json
            tool_args = clean_json(tool_call_args_str)
            if isinstance(tool_args, dict):
                return tool_args
        except Exception:
            pass

    # Aggressive repair: regex extraction of JSON-like structures
    if repair_level == "aggressive":
        # Try to find a balanced JSON object
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', tool_call_args_str)
        if json_match:
            try:
                tool_args = json.loads(json_match.group(), strict=False)
                if isinstance(tool_args, dict):
                    return tool_args
            except json.JSONDecodeError:
                pass

        # Last resort: try removing common noise characters
        cleaned = re.sub(r'[\x00-\x1f\x7f]', '', tool_call_args_str)
        try:
            tool_args = json.loads(cleaned, strict=False)
            if isinstance(tool_args, dict):
                return tool_args
        except json.JSONDecodeError:
            pass

    return {}
