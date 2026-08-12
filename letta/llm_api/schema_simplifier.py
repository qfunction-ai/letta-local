"""Schema simplification for small-model tool-calling compatibility.

Small models (4B-14B parameters) struggle with complex JSON schemas:
optional parameters, enum constraints, array types, and long descriptions
all increase token count and confuse the model's tool-call generation.

This module provides `simplify_tool_schemas()` which strips optional
parameters, replaces enums with string + description, and truncates
descriptions to reduce schema complexity.  It is opt-in via
`ModelConstraints.simplify_tool_schemas`.
"""

import copy
import re
from typing import Any, Dict, List

# Maximum description length (characters) before truncation.
_MAX_DESC_CHARS = 200

# Maximum number of sentences to keep when truncating descriptions.
_MAX_DESC_SENTENCES = 2


def _truncate_description(desc: str) -> str:
    """Truncate a description to at most 2 sentences or 200 characters.

    Splits on sentence-ending punctuation followed by whitespace or end
    of string.  Keeps the first 2 sentences, then clamps to 200 chars.
    Appends "..." if any truncation occurred.
    """
    if not desc or len(desc) <= _MAX_DESC_CHARS:
        return desc

    original = desc
    # Split into sentences: match text up to . ! ? followed by space or end
    sentences = re.split(r'(?<=[.!?])\s+', desc.strip())
    kept = []
    total = 0
    for s in sentences[:_MAX_DESC_SENTENCES]:
        if total + len(s) > _MAX_DESC_CHARS:
            # Truncate mid-sentence if we're over the char limit
            remaining = _MAX_DESC_CHARS - total
            if remaining > 20:
                kept.append(s[:remaining].rstrip())
            break
        kept.append(s)
        total += len(s) + 1  # +1 for the space

    result = " ".join(kept)
    if len(result) > _MAX_DESC_CHARS:
        result = result[:_MAX_DESC_CHARS].rstrip()

    # Append ellipsis if we actually truncated
    if len(result) < len(original):
        result = result.rstrip() + "..."

    return result


def _simplify_property(props: Dict[str, Any]) -> Dict[str, Any]:
    """Simplify a single property's schema.

    - Replaces `enum` with `type: string` and appends enum values to description
    - Truncates long descriptions
    - Recursively simplifies nested `items` in array types
    """
    simplified = copy.deepcopy(props)

    # Replace enum with string + description
    if "enum" in simplified:
        enum_vals = simplified.pop("enum")
        # Keep the original type if it's string, otherwise set to string
        simplified["type"] = "string"
        # Append enum values to description
        enum_str = ", ".join(str(v) for v in enum_vals)
        existing_desc = simplified.get("description", "")
        if existing_desc:
            simplified["description"] = f"{existing_desc} (one of: {enum_str})"
        else:
            simplified["description"] = f"One of: {enum_str}"

    # Truncate description
    if "description" in simplified:
        simplified["description"] = _truncate_description(simplified["description"])

    # Recursively simplify array items
    if simplified.get("type") == "array" and "items" in simplified:
        simplified["items"] = _simplify_property(simplified["items"])

    return simplified


def _should_simplify(tool: Dict[str, Any], max_params: int) -> bool:
    """Check if a tool schema is complex enough to warrant simplification."""
    params = tool.get("parameters", {})
    if not isinstance(params, dict):
        return False

    properties = params.get("properties", {})
    required = set(params.get("required", []))
    optional_count = len(properties) - len(required & set(properties.keys()))

    # Simplify if there are optional params to strip
    if optional_count > 0:
        return True

    # Simplify if any property has an enum (including in nested array items)
    for prop in properties.values():
        if not isinstance(prop, dict):
            continue
        if "enum" in prop:
            return True
        # Check nested array items for enums
        if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
            if "enum" in prop["items"]:
                return True

    # Simplify if total params exceeds threshold
    if len(properties) > max_params:
        return True

    return False


def simplify_tool_schemas(tools: List[Dict[str, Any]], max_params: int = 5) -> List[Dict[str, Any]]:
    """Simplify tool schemas for small-model compatibility.

    For each tool that is complex enough (has optional params, enums, or
    exceeds max_params), this function:

    1. Strips optional parameters (keeps only required params)
    2. Replaces `enum` constraints with `type: string` + description
    3. Truncates descriptions to 2 sentences or 200 characters
    4. Removes `additionalProperties` if present

    Tools that are already simple pass through unchanged.

    Args:
        tools: List of tool dicts in OpenAI function format:
            ``{"name": ..., "description": ..., "parameters": {...}}``
        max_params: Maximum parameters per tool before simplification kicks in.
            Tools with fewer than max_params required params and no enums
            are left alone.

    Returns:
        Simplified list of tool dicts (shallow copy, original not mutated).
    """
    result = []
    for tool in tools:
        if not _should_simplify(tool, max_params):
            result.append(tool)
            continue

        simplified = copy.deepcopy(tool)
        params = simplified.get("parameters", {})
        if not isinstance(params, dict):
            result.append(tool)
            continue

        properties = params.get("properties", {})
        required = set(params.get("required", []))

        # Keep only required properties
        kept_properties = {}
        for name, prop in properties.items():
            if name in required:
                kept_properties[name] = _simplify_property(prop)

        params["properties"] = kept_properties
        params["required"] = list(required & set(properties.keys()))

        # Remove additionalProperties
        params.pop("additionalProperties", None)

        # Truncate tool-level description
        if "description" in simplified:
            simplified["description"] = _truncate_description(simplified["description"])

        result.append(simplified)

    return result
