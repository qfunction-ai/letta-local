"""Content validator — detects prompt injection patterns in tool arguments.

Scans string values in tool_args for known prompt injection patterns.
Used by the CONTAINS_INJECTION policy operator.

Pattern categories:
1. Direct instruction overrides: "ignore previous instructions", "disregard the above"
2. Role redefinition: "you are now a", "you are actually a"
3. Role-play / chat markers: "system:", "[INST]", "<<SYS>>"
4. Delimiter injection: "--- SYSTEM ---", "### SYSTEM PROMPT ###"
5. Hidden unicode: zero-width characters, right-to-left overrides
6. Encoded payloads: base64-encoded instruction strings

Usage in policy engine:
    The CONTAINS_INJECTION operator in PolicyCondition delegates to
    ContentValidator.check(). No direct calls from agent files.
"""

from __future__ import annotations

import base64
from typing import Optional

try:
    import regex as re
except ImportError:
    import re


# ---------------------------------------------------------------------------
# Direct instruction override patterns
# ---------------------------------------------------------------------------

INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE), "instruction_override"),
    (re.compile(r"disregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)", re.IGNORECASE), "instruction_override"),
    (re.compile(r"forget\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE), "instruction_override"),
    (re.compile(r"you\s+are\s+(?:now|actually)\s+(?:a|an)\s+", re.IGNORECASE), "role_redefinition"),
    (re.compile(r"new\s+instructions?\s*:", re.IGNORECASE), "instruction_override"),
    (re.compile(r"override\s+(?:system|safety|security)\s+(?:prompt|instructions?)", re.IGNORECASE), "system_override"),
]


# ---------------------------------------------------------------------------
# Role-play / chat markers
# ---------------------------------------------------------------------------

ROLE_MARKERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*system\s*:", re.IGNORECASE | re.MULTILINE), "system_marker"),
    (re.compile(r"^\s*\[INST\]", re.IGNORECASE | re.MULTILINE), "inst_marker"),
    (re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE), "sys_marker"),
    (re.compile(r"---\s*SYSTEM\s*(?:PROMPT)?\s*---", re.IGNORECASE), "system_delimiter"),
    (re.compile(r"###\s*SYSTEM\s*(?:PROMPT)?\s*###", re.IGNORECASE), "system_delimiter"),
]


# ---------------------------------------------------------------------------
# Hidden unicode characters
# ---------------------------------------------------------------------------

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060\u2061\u2062\u2063]")
_RTL_OVERRIDE = re.compile(r"[\u202e\u202d\u202b\u202a]")


# ---------------------------------------------------------------------------
# Base64-encoded instruction detection
# ---------------------------------------------------------------------------

# Only scan base64-looking strings up to 500 chars to avoid performance issues
# on large documents.
_BASE64_MAX_SCAN_LENGTH = 500
_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")
_BASE64_KEYWORDS = ("ignore", "system", "instruction", "override", "forget")


class ContentValidator:
    """Stateless checker — call ContentValidator.check(value)."""

    @staticmethod
    def check(value: str) -> Optional[str]:
        """Check a string for prompt injection patterns.

        Returns the label of the first detected pattern, or None if clean.
        """
        if not isinstance(value, str) or not value:
            return None

        # Direct instruction overrides
        for pattern, label in INJECTION_PATTERNS:
            if pattern.search(value):
                return label

        # Role-play markers
        for pattern, label in ROLE_MARKERS:
            if pattern.search(value):
                return label

        # Hidden unicode
        if _ZERO_WIDTH.search(value):
            return "hidden_unicode_zero_width"
        if _RTL_OVERRIDE.search(value):
            return "hidden_unicode_rtl_override"

        # Base64-encoded instructions — only scan short strings
        if len(value) <= _BASE64_MAX_SCAN_LENGTH:
            for match in _BASE64_PATTERN.finditer(value):
                try:
                    decoded = base64.b64decode(match.group()).decode("utf-8", errors="ignore")
                    lower = decoded.lower()
                    if any(kw in lower for kw in _BASE64_KEYWORDS):
                        return "base64_encoded_instruction"
                except Exception:
                    pass

        return None
