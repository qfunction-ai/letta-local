"""Canary output filter — scans assistant messages for canary token leaks.

The existing CanaryChecker intercepts tool calls containing canary tokens.
But an agent can bypass this by typing the canary token directly in its
assistant message text. The output filter closes this gap by scanning
assistant messages before they reach the user.

This is defense-in-depth alongside the tool-call canary check:
- Layer 1: CanaryChecker blocks tool calls with canary tokens (existing)
- Layer 2: CanaryOutputFilter redacts canary tokens from assistant messages (this module)

The filter uses exact substring matching on the full canary value
(e.g., ``CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789``). No regex.
The CANARY- prefix makes false positives negligible since the full UUID
is astronomically unlikely to appear in normal text.

Fail-open: if the filter crashes, the message passes through unmodified.
A broken filter must never break the agent loop.
"""

from __future__ import annotations

import copy
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


class CanaryOutputFilter:
    """Scans assistant messages for the exact canary token value.

    Usage:
        filt = CanaryOutputFilter()
        filt.scan(message, canary_value="CANARY-abc123...")
    """

    def scan(self, message: "LettaMessageUnion", canary_value: str) -> Optional[str]:
        """Scan a message for the canary value. Returns redacted text or None if clean.

        Args:
            message: The message to scan.
            canary_value: The exact canary string to look for.

        Returns:
            The redacted text if the canary was found, None if the message is clean.
            The caller decides what to do with the result.
        """
        if not canary_value:
            return None

        # Only assistant messages carry user-visible text
        from letta.schemas.letta_message import MessageType

        if message.message_type != MessageType.assistant_message:
            return None

        # Extract text content
        content = message.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Content is a list of content parts — concatenate text parts
            parts = []
            for part in content:
                if hasattr(part, "text") and isinstance(part.text, str):
                    parts.append(part.text)
            text = "".join(parts)
        else:
            return None

        if canary_value in text:
            return text.replace(canary_value, REDACTED_CANARY)

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
    split across chunks.

    Usage:
        filt = StreamingCanaryFilter(canary_value)
        for chunk in stream:
            safe_text, detected = filt.feed(chunk_text)
            if detected:
                await log_canary_output_detected(...)
            yield safe_text
        # On stream end:
        yield filt.flush()

    The buffer holds back ``len(canary_value) - 1`` characters to catch
    a canary token that is split across chunk boundaries. This adds a
    small delay (one canary-length window) before content is emitted.
    """

    def __init__(self, canary_value: str):
        self.canary_value = canary_value
        self._holdback = max(len(canary_value) - 1, 0) if canary_value else 0
        self._buffer: str = ""
        self._warning_appended = False

    def feed(self, text: str) -> tuple[str, bool]:
        """Feed a text delta into the buffer. Returns (safe_text, was_detected).

        The returned safe_text may be empty (if all content is held back
        in the buffer). ``was_detected`` is True if a canary token was
        found and replaced in this feed cycle.
        """
        if not text or not self.canary_value:
            # No canary configured — pass through everything immediately
            return text, False
            return "", False

        self._buffer += text
        detected = False

        # Check for canary in the buffer
        if self.canary_value in self._buffer:
            self._buffer = self._buffer.replace(self.canary_value, REDACTED_CANARY)
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
        """
        remaining = self._buffer
        self._buffer = ""
        if self._warning_appended or remaining == "":
            return remaining
        return remaining

    def mark_warning(self) -> None:
        """Mark that the canary warning should be appended on flush."""
        self._warning_appended = True
