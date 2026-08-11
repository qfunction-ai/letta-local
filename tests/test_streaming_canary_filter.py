"""Tests for StreamingCanaryFilter — rolling buffer canary detection for streaming path."""

import pytest

from letta.security.canary_output_filter import StreamingCanaryFilter, REDACTED_CANARY, CANARY_WARNING


CANARY = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"


class TestStreamingCanaryFilter:
    """Unit tests for the streaming canary rolling buffer filter."""

    def test_clean_text_passes_through(self):
        """Clean text passes through (with holdback delay)."""
        filt = StreamingCanaryFilter(CANARY)
        text = "This is a normal message about cats."
        safe, detected = filt.feed(text)
        assert detected is False
        assert CANARY not in safe
        # Should have emitted most text (holdback is len(CANARY)-1)
        remaining = filt.flush()
        full_output = safe + remaining
        assert "This is a normal message about cats." in full_output

    def test_canary_in_single_chunk_detected(self):
        """Canary in a single chunk is detected and replaced."""
        filt = StreamingCanaryFilter(CANARY)
        text = f"The secret is {CANARY} and that is bad."
        safe, detected = filt.feed(text)
        assert detected is True
        assert CANARY not in safe
        # The replacement happens in the buffer, which is held back.
        # Flush to see the full output.
        remaining = filt.flush()
        full_output = safe + remaining
        assert CANARY not in full_output
        assert REDACTED_CANARY in full_output

    def test_canary_split_across_chunks(self):
        """Canary split across two chunks is detected when the second half arrives."""
        filt = StreamingCanaryFilter(CANARY)
        half = len(CANARY) // 2
        part1 = f"Look: {CANARY[:half]}"
        part2 = f"{CANARY[half:]} done."
        safe1, det1 = filt.feed(part1)
        safe2, det2 = filt.feed(part2)
        # First chunk: no detection (only half the canary)
        assert det1 is False
        # Second chunk: detection when full canary is in buffer
        assert det2 is True
        # The canary should not appear in the combined output
        combined = safe1 + safe2 + filt.flush()
        assert CANARY not in combined

    def test_no_canary_configured(self):
        """Empty canary value means no filtering — all text passes immediately."""
        filt = StreamingCanaryFilter("")
        # Empty canary means holdback is -1 which doesn't make sense,
        # so the filter should handle it gracefully
        safe, detected = filt.feed("some text")
        # With holdback <= 0, everything is emitted
        assert detected is False

    def test_flush_releases_buffer(self):
        """flush() returns remaining buffered content."""
        filt = StreamingCanaryFilter(CANARY)
        filt.feed("short text")  # shorter than holdback, all held in buffer
        remaining = filt.flush()
        assert remaining == "short text"
        # Second flush should be empty
        assert filt.flush() == ""

    def test_empty_feed(self):
        """Empty feed returns empty output."""
        filt = StreamingCanaryFilter(CANARY)
        safe, detected = filt.feed("")
        assert safe == ""
        assert detected is False

    def test_multiple_feeds_accumulate(self):
        """Multiple clean feeds accumulate in buffer and emit incrementally."""
        filt = StreamingCanaryFilter(CANARY)
        outputs = []
        for char in "Hello world this is a long message that exceeds the holdback":
            safe, _ = filt.feed(char)
            if safe:
                outputs.append(safe)
        outputs.append(filt.flush())
        combined = "".join(outputs)
        assert "Hello world" in combined

    def test_canary_at_chunk_boundary(self):
        """Canary exactly at the split point between two chunks."""
        filt = StreamingCanaryFilter(CANARY)
        # First chunk ends right before canary starts
        part1 = "prefix text " + CANARY
        part2 = " suffix text"
        safe1, det1 = filt.feed(part1)
        safe2, det2 = filt.feed(part2)
        assert det1 is True  # Full canary in first chunk
        assert det2 is False
        combined = safe1 + safe2 + filt.flush()
        assert CANARY not in combined
        assert REDACTED_CANARY in combined
