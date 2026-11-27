"""Canonicalize model-supplied tool-call arguments BEFORE any security check.

R2A-1 (v0.16.41, round-2 adversarial review — verified): the policy engine,
canary filter and approval flow all evaluated the RAW parsed arguments,
while the executor then REWROTE them (server-side coercion JSON-decodes and
literal_evals strings via coerce_dict_args_by_annotations; the removed
in-sandbox coercion did the same). A model could stringify or escape a
value ("\\u0044ROP TABLE users", "DR" "OP TABLE users", "5000") so every
argument-level rule saw the ESCAPED text while the tool received the
decoded value. The README's own example deny rule was bypassable.

THE FIX: canonicalize ONCE, here, before the checks. The canonical dict is
what the policy, the canary check, the approval request and the executor
all consume. Rules:
- Values whose schema type is array/object and arrive as JSON STRINGS are
  json.loads'd and shape-checked (the same decode S-1's serializer applies
  at render time — the executor no longer decodes).
- Values whose annotation/schema type is `str` are NEVER decoded (F-3).
- Values for other types that arrive as strings parse per type
  (int/float/bool), preserving the executor's existing coercion semantics.
- Anything unparseable stays RAW — the fail-closed logic in the policy
  evaluator (a type mismatch makes deny/approval rules match) handles it:
  a value the canonicalizer couldn't normalize is exactly the "type
  doesn't match" case that should trip deny rules.
"""

import json
from typing import Any, Dict, Optional


def canonicalize_tool_args(
    args: Optional[Dict[str, Any]],
    schema: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return the canonical form of model-supplied tool arguments.

    Args:
        args: the raw parsed tool-call arguments (possibly with stringified
            or escaped values, as models send).
        schema: the tool's json_schema ({"parameters": {"properties": {...}}}),
            or the args_json_schema directly. Optional — without a schema,
            only the str-typed-skips-decode rule is unknowable, so values
            pass through unchanged (raw), and fail-closed rules trip.

    The output is a NEW dict; the input is never mutated.
    """
    if not args:
        return dict(args or {})

    properties = None
    if isinstance(schema, dict):
        params = schema.get("parameters", schema)
        if isinstance(params, dict) and isinstance(params.get("properties"), dict):
            properties = params["properties"]

    out: Dict[str, Any] = {}
    for name, value in args.items():
        spec = properties.get(name) if properties else None
        out[name] = _canonicalize_value(value, spec)
    return out


def _canonicalize_value(value: Any, spec: Optional[Dict[str, Any]]) -> Any:
    if not isinstance(value, str):
        # Non-strings are already typed; pass through.
        return value

    if not isinstance(spec, dict):
        # No schema knowledge: keep raw (never guess a decode the policy
        # would then trust — the fail-closed rules handle raw strings).
        return value

    declared = spec.get("type")

    # F-3 rule 2: str-typed parameters are NEVER decoded. The escaped
    # text IS the value.
    if declared == "string":
        return value

    # array/object arriving as a JSON string: decode + shape-check.
    if declared in ("array", "object"):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value  # unparseable -> raw -> fail-closed rules trip
        if declared == "array" and not isinstance(decoded, list):
            return value
        if declared == "object" and not isinstance(decoded, dict):
            return value
        return decoded

    # integer/number/boolean arriving as a string: parse per type,
    # mirroring coerce_dict_args_by_annotations' arg_type(value).
    if declared == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if declared == "number":
        try:
            f = float(value)
            # NaN/Infinity (json accepts them) are non-finite: keep raw —
            # the serializer rejects non-finite, and rules shouldn't
            # silently compare against them either.
            if f != f or f in (float("inf"), float("-inf")):
                return value
            return f
        except ValueError:
            return value
    if declared == "boolean":
        low = value.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        return value

    # Unknown declared type: raw.
    return value
