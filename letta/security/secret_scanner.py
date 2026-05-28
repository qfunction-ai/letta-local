"""Secret pattern scanner — entropy + regex detection for tool arguments.

Detects potential secrets (API keys, tokens, passwords, private keys)
in tool arguments using two signals:

1. Entropy: High Shannon entropy (>=4.5 bits/char) in strings of 20+
   characters. This is the primary signal — it catches unknown formats
   without needing to enumerate every provider's secret format.

2. Regex: Well-known secret formats (AWS keys, GitHub tokens, PEM
   private keys, etc.) as a confirmatory signal. The list stays short
   by design — entropy handles the long tail.

This module is the Letta fork's counterpart to shared/code_safety.py
in the Delta codebase. The entropy + regex logic is duplicated because
the two codebases can't share imports. Changes to one should be
mirrored in the other. See: shared/code_safety.py SECRET_PATTERNS.

Usage in policy engine:
    The CONTAINS_SECRET operator in PolicyCondition delegates to
    SecretPatternChecker.check(). No direct calls from agent files.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Optional

try:
    import regex as re
except ImportError:
    import re

from letta.log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Entropy detection (primary signal)
# ---------------------------------------------------------------------------

# Variable names that suggest a secret value. Used by the AST visitor
# in Delta's code_safety.py; kept here for consistency.
_KEY_LIKE_NAMES = re.compile(
    r"(?i)(?:api[_-]?key|apikey|access[_-]?key|secret|token|"
    r"password|credential|private[_-]?key|auth[_-]?token|bearer)",
)

_ENTROPY_THRESHOLD = 4.5   # bits per character
_ENTROPY_MIN_LENGTH = 20   # minimum string length to check


def shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy of a string in bits per character."""
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def is_high_entropy(s: str) -> bool:
    """Return True if the string has high enough entropy to be a secret."""
    return len(s) >= _ENTROPY_MIN_LENGTH and shannon_entropy(s) >= _ENTROPY_THRESHOLD


# ---------------------------------------------------------------------------
# Regex patterns (confirmatory signal)
# ---------------------------------------------------------------------------

# Each entry: (compiled_regex, label)
# Label is used in audit events and warning messages.
# Deliberately short — entropy catches the long tail.
SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"), "Private key (PEM)"),
    (re.compile(r"gh[psou]_[A-Za-z0-9_]{36,}"), "GitHub token"),
    (re.compile(r"xox[bpa]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(r"(?:sk|rk)_live_[A-Za-z0-9]{24,}"), "Stripe key"),
]


class SecretPatternChecker:
    """Stateless checker — call SecretPatternChecker.check(value)."""

    @staticmethod
    def check(value: str) -> Optional[str]:
        """Check a string for secret patterns. Returns a label or None.

        Two signals:
        1. Regex: known-format matches (AWS keys, GitHub tokens, etc.)
        2. Entropy: high-entropy substrings of 20+ chars

        Returns the label of the first match (e.g., "AWS Access Key ID",
        "High-entropy secret"), or None if no secret is detected.
        """
        if not isinstance(value, str) or not value:
            return None

        # Regex check — known formats
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(value):
                return label

        # Entropy check — scan for high-entropy substrings
        # Extract "word-like" tokens (sequences of non-whitespace 20+ chars)
        for token in re.findall(r"\S{20,}", value):
            if is_high_entropy(token):
                return "High-entropy secret"

        return None
