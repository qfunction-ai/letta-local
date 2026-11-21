"""Canary output filter — scans assistant messages for canary token leaks.

The existing CanaryChecker intercepts tool calls containing canary tokens.
But an agent can bypass this by typing the canary token directly in its
assistant message text. The output filter closes this gap by scanning
assistant messages before they reach the user.

This is defense-in-depth alongside the tool-call canary check:
- Layer 1: CanaryChecker blocks tool calls with canary tokens (existing)
- Layer 2: CanaryOutputFilter redacts canary tokens from assistant messages (this module)

Matching is tolerant of token-boundary obfuscation: up to
``_MAX_GAP`` whitespace or zero-width characters may appear between any
two canary characters (``CANARY- PROBE- TEST- 2026`` matches). The gap
is BOUNDED, not ``*``, so the maximum match length stays computable —
the streaming holdback window depends on it. The CANARY- prefix plus
the full value keeps false positives negligible.

Fail-open: if the filter crashes, the message passes through unmodified.
A broken filter must never break the agent loop.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

from letta.log import get_logger

if TYPE_CHECKING:
    from letta.schemas.letta_message import LettaMessageUnion

logger = get_logger(__name__)

# Redaction marker replacing the canary value when detected.
REDACTED_CANARY = "[REDACTED_CANARY]"

# Warning appended to the message when a canary is detected.
CANARY_WARNING = (
    "\n\n[SECURITY WARNING: A prompt exfiltration attempt was detected and blocked. "
    "The canary token embedded in the system prompt was found in this message.]"
)

# Whitespace and zero-width characters tolerated between canary characters.
_GAP_CLASS = r"[\s\u200b\u200c\u200d\ufeff]"

# Maximum number of gap characters tolerated between any two canary
# characters. Bounded (not `*`) so the maximum match length — and thus
# the streaming holdback window — stays computable. An unbounded gap
# ("C A N A R Y ...") cannot be covered by any fixed holdback.
_MAX_GAP = 4


@lru_cache(maxsize=32)
def _canary_pattern(canary_value: str) -> "re.Pattern[str]":
    """Compile the whitespace/zero-width-tolerant pattern for a canary value.

    Cached: the streaming filter compiles once per constructor and scan()
    (which receives the canary per call) reuses the same compiled pattern.
    """
    gap = _GAP_CLASS + "{0,%d}" % _MAX_GAP
    return re.compile(gap.join(re.escape(c) for c in canary_value))


# ---------------------------------------------------------------------------
# Skeleton tier (v0.16.37 Part B, review P-2) — AUDIT-ONLY fuzzy matching.
#
# The strict tier is insertion-tolerant only: deletion variants
# ("CANARY-eps-08-2026" from "CANARY-eps-08-hidden-2026") never fire — no
# redaction, no event, no forensics. Small models garble transcriptions by
# DROPPING characters, so the strict tier inherits the model's fidelity
# limits (design principle from the deletion-bypass ruling).
#
# The skeleton tier closes the unlogged-exfiltration gap without touching
# output: it fires an AUDIT event, never redacts. Fuzzy redaction
# false-positives on legitimate course transcripts; fuzziness stays out of
# the output path.
#
# Match rule (P-2 distance window): segment the canary on non-alphanumeric
# separators. A skeleton match requires the marker (segment 0) in the text,
# at least N-2 of the remaining N-1 segments IN ORDER within a window of
# <= 2x canary length starting at the marker, and a matched span
# (marker start -> last matched segment end) covering >= 50% of the canary
# length. The window kills the paragraph-nine false positive; the span
# floor kills the marker-plus-trivial-fragments false positive.
#
# Canaries with fewer than 4 segments get no skeleton tier (too little
# signal to match fuzzily).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _skeleton_segments(canary_value: str) -> Optional[tuple]:
    """Segment a canary value on non-alphanumeric separators.

    Returns a tuple of segments, or None when the canary is too short for
    the skeleton tier (fewer than 4 segments).
    """
    segs = tuple(s for s in re.split(r"[^A-Za-z0-9]+", canary_value) if s)
    if len(segs) < 4:
        return None
    return segs


def scan_skeleton(text: str, canary_value: str) -> Optional[dict]:
    """Skeleton-tier scan: garbled/deleted canary variants, AUDIT-ONLY.

    Returns None when nothing matched, else:
        {"match_type": "skeleton", "matched_segments": [...],
         "matched_ratio": float}
    The caller logs the event; the output is NEVER modified.
    """
    if not text or not canary_value:
        return None

    segs = _skeleton_segments(canary_value)
    if segs is None:
        return None

    marker = segs[0]
    remaining = segs[1:]
    window_len = 2 * len(canary_value)
    min_span = 0.5 * len(canary_value)

    for m in re.finditer(re.escape(marker), text):
        p = m.start()
        window = text[p : p + window_len]

        # In-order matching of the remaining segments within the window
        pos = 0
        matched = []
        for seg in remaining:
            idx = window.find(seg, pos)
            if idx != -1:
                matched.append(seg)
                pos = idx + len(seg)

        if len(matched) < len(remaining) - 2:
            continue

        # Matched span: marker start -> last matched segment end (in text
        # coordinates). P-2: ratio measured over the matched span, not the
        # whole message.
        if matched:
            span = pos  # end of last matched segment, window-relative
        else:
            span = m.end() - p
        if span < min_span:
            continue

        return {
            "match_type": "skeleton",
            "matched_segments": [marker] + matched,
            "matched_ratio": round(span / len(canary_value), 3),
        }

    return None


class CanaryOutputFilter:
    """Scans assistant messages for the canary token value (obfuscation-tolerant).

    Usage:
        filt = CanaryOutputFilter()
        filt.scan(message, "CANARY-abc123...")
    """

    def scan(self, message: "LettaMessageUnion", canary_value: str) -> Optional[str]:
        """Scan a message for the canary value. Returns redacted text or None if clean.

        Detection and redaction tolerate up to _MAX_GAP whitespace/zero-width
        characters between canary characters.

        Args:
            message: The message to scan.
            canary_value: The exact canary string to look for.

        Returns:
            The redacted text if the canary was found, None if the message is clean.
            The caller decides what to do with the result.
        """
        if not canary_value:
            return None

        text = _extract_assistant_text(message)
        if text is None:
            return None

        pattern = _canary_pattern(canary_value)
        if pattern.search(text):
            return pattern.sub(REDACTED_CANARY, text)

        return None


def _extract_assistant_text(message: "LettaMessageUnion") -> Optional[str]:
    """Extract the text of an assistant message, or None for other types."""
    from letta.schemas.letta_message import MessageType

    if message.message_type != MessageType.assistant_message:
        return None

    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if hasattr(part, "text") and isinstance(part.text, str):
                parts.append(part.text)
        return "".join(parts)
    return None


def scan_skeleton_message(message: "LettaMessageUnion", canary_value: str) -> Optional[dict]:
    """Skeleton-tier scan of a complete message (audit-only, no redaction)."""
    try:
        text = _extract_assistant_text(message)
        if text is None:
            return None
        return scan_skeleton(text, canary_value)
    except Exception as e:
        logger.warning(f"Skeleton message scan failed (fail-open, audit-only): {e}")
        return None


def apply_canary_filter_to_message(
    message: "LettaMessageUnion",
    canary_value: str,
) -> "LettaMessageUnion":
    """Apply canary output filter to a single message.

    If the canary is detected, the message content is redacted and
    a security warning is appended. Returns the original message
    (not a copy) if clean, or a modified copy if redacted.

    Fail-open: if any exception occurs, returns the original message.
    """
    try:
        filt = CanaryOutputFilter()
        redacted = filt.scan(message, canary_value)
        if redacted is None:
            return message

        # Redact: create a shallow copy with modified content
        redacted_with_warning = redacted + CANARY_WARNING
        msg_copy = copy.copy(message)

        if isinstance(message.content, str):
            msg_copy.content = redacted_with_warning
        elif isinstance(message.content, list):
            # Replace the content with a single text part
            msg_copy.content = redacted_with_warning

        return msg_copy
    except Exception as e:
        logger.warning(f"Canary output filter failed (fail-open): {e}")
        return message


class StreamingCanaryFilter:
    """Rolling buffer canary filter for the streaming path.

    The non-streaming ``apply_canary_filter_to_message`` scans complete
    messages. The streaming path yields token-level text deltas. This
    filter buffers incoming deltas to detect canary tokens that may be
    split across chunks — including chunks that split the canary at ANY
    character position or interleave obfuscation gaps.

    Usage:
        filt = StreamingCanaryFilter(canary_value)
        for chunk in stream:
            safe_text, detected = filt.feed(chunk_text)
            if detected:
                await log_canary_output_detected(...)
            yield safe_text
        # On stream end:
        yield filt.flush()  # held-back tail + CANARY_WARNING if detected

    The buffer holds back ``max_match_len - 1`` characters — the longest
    possible tolerant match (canary length plus gaps) minus one — so a
    canary split across (or straddling) chunk boundaries cannot leak.
    This adds a small delay before content is emitted.
    """

    def __init__(self, canary_value: str):
        self.canary_value = canary_value
        self._pattern = _canary_pattern(canary_value) if canary_value else None
        # Max tolerant match length: L canary chars + (L-1) gaps of up to
        # _MAX_GAP each. Hold back everything but the last char of a
        # potential match.
        L = len(canary_value) if canary_value else 0
        self._holdback = max(L + (L - 1) * _MAX_GAP - 1, 0)
        self._buffer: str = ""
        self._warning_appended = False
        # v0.16.37 Part B (B3): full-text accumulator for the skeleton tier.
        # The skeleton check runs at flush(), when the full text is known —
        # it NEVER gates emission, so the v0.16.25 holdback math is untouched
        # by construction. Raw input is accumulated (pre-redaction): garbled
        # variants are exactly what strict redaction does NOT catch.
        self._full_text: str = ""
        self.skeleton_match: Optional[dict] = None

    def feed(self, text: str) -> tuple[str, bool]:
        """Feed a text delta into the buffer. Returns (safe_text, was_detected).

        The returned safe_text may be empty (if all content is held back
        in the buffer). ``was_detected`` is True if a canary token was
        found and replaced in this feed cycle.
        """
        if not text or not self.canary_value:
            # No canary configured — pass through everything immediately
            return text, False

        self._full_text += text
        self._buffer += text
        detected = False

        # Check for the canary (obfuscation-tolerant) in the buffer
        if self._pattern.search(self._buffer):
            self._buffer = self._pattern.sub(REDACTED_CANARY, self._buffer)
            detected = True

        # Emit all but the holdback (might be start of a split canary)
        if len(self._buffer) <= self._holdback:
            return "", detected

        emit = self._buffer[: len(self._buffer) - self._holdback]
        self._buffer = self._buffer[len(self._buffer) - self._holdback :]
        return emit, detected

    def flush(self) -> str:
        """Flush remaining buffer content. Call once when the stream ends.

        Appends the security warning if a canary was detected at any point.
        Idempotent: a second call returns "".
        """
        # Skeleton tier (B3): audit-only detection on the full text. Runs at
        # flush so the whole message is available; does not modify output.
        try:
            self.skeleton_match = scan_skeleton(self._full_text, self.canary_value)
        except Exception as e:
            logger.warning(f"Skeleton scan failed (fail-open, audit-only): {e}")
            self.skeleton_match = None

        remaining = self._buffer
        self._buffer = ""
        if self._warning_appended:
            self._warning_appended = False
            if remaining:
                return remaining + CANARY_WARNING
            return CANARY_WARNING
        return remaining

    def mark_warning(self) -> None:
        """Mark that the canary warning should be appended on flush."""
        self._warning_appended = True
