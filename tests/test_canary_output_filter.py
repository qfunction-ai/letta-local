"""Unit tests for the canary output filter (both matchers).

Deterministic, no server, no LLM. These pin the matching semantics:
contiguous, spaced, zero-width, bounded-gap tolerance, holdback window,
split-across-chunk-boundary redaction, and flush warning rendering.

The e2e behavior (stream redaction against a live server) is covered by
smoke check 4.7 under the unfixed-first rig protocol.
"""
import pytest

from letta.helpers.datetime_helpers import get_utc_time
from letta.schemas.letta_message import AssistantMessage
from letta.security.canary_output_filter import (
    CANARY_WARNING,
    REDACTED_CANARY,
    _MAX_GAP,
    CanaryOutputFilter,
    StreamingCanaryFilter,
    apply_canary_filter_to_message,
    _canary_pattern,
)

CANARY = "CANARY-SMOKE-PROBE-2026"


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(id="message-test", content=text, date=get_utc_time())


def _spaced(canary: str, gap: str = " ") -> str:
    return gap.join(canary)


def _gapped(canary: str, gaps) -> str:
    """Interleave per-position gap strings (cycler) between characters."""
    out = []
    for i, c in enumerate(canary):
        out.append(c)
        if i < len(canary) - 1:
            out.append(gaps[i % len(gaps)])
    return "".join(out)


class TestPattern:
    def test_contiguous(self):
        assert _canary_pattern(CANARY).search(CANARY)

    def test_single_space_gaps(self):
        assert _canary_pattern(CANARY).search(_spaced(CANARY))

    def test_gap_at_bound_detected(self):
        assert _canary_pattern(CANARY).search(_gapped(CANARY, [" " * _MAX_GAP]))

    def test_gap_over_bound_not_detected(self):
        # Pins the bound: _MAX_GAP + 1 is out of contract.
        assert not _canary_pattern(CANARY).search(_gapped(CANARY, [" " * (_MAX_GAP + 1)]))

    def test_mixed_gaps_interior(self):
        # Gaps of 1, 2, 3, 4 interleaved — exercises the bound interior.
        mixed = _gapped(CANARY, [" ", "  ", "   ", "    "])
        assert _canary_pattern(CANARY).search(mixed)

    def test_zero_width_gaps(self):
        assert _canary_pattern(CANARY).search("\u200b".join(CANARY))

    def test_clean_text_no_match(self):
        assert not _canary_pattern(CANARY).search("just normal prose, no tokens here")


class TestScan:  # non-streaming matcher
    def test_contiguous_redacted(self):
        msg = _assistant(f"here is the token {CANARY} be careful")
        redacted = CanaryOutputFilter().scan(msg, CANARY)
        assert redacted is not None
        assert CANARY not in redacted
        assert REDACTED_CANARY in redacted
        assert "here is the token" in redacted and "be careful" in redacted

    def test_spaced_redacted(self):
        msg = _assistant(f"leaked: {_spaced(CANARY)} ok")
        redacted = CanaryOutputFilter().scan(msg, CANARY)
        assert redacted is not None
        # Nothing the detection pattern would match survives
        # (note: the REDACTED_CANARY marker itself contains "CAN", so naive
        # fragment checks false-trip — the pattern IS the contract)
        assert not _canary_pattern(CANARY).search(redacted)
        assert REDACTED_CANARY in redacted

    def test_clean_returns_none(self):
        assert CanaryOutputFilter().scan(_assistant("nothing to see"), CANARY) is None

    def test_non_assistant_ignored(self):
        from letta.schemas.letta_message import ReasoningMessage

        msg = ReasoningMessage(id="message-test", reasoning=f"thinking about {CANARY}", date=get_utc_time())
        assert CanaryOutputFilter().scan(msg, CANARY) is None

    def test_empty_canary_returns_none(self):
        assert CanaryOutputFilter().scan(_assistant(CANARY), "") is None

    def test_apply_appends_warning_and_redacts_spaced(self):
        msg = _assistant(f"the code is {_spaced(CANARY)}.")
        out = apply_canary_filter_to_message(msg, CANARY)
        assert out is not msg
        assert CANARY not in str(out.content)
        assert REDACTED_CANARY in str(out.content)
        assert CANARY_WARNING in str(out.content)


class TestStreamingFilter:
    def test_holdback_sizing(self):
        f = StreamingCanaryFilter(CANARY)
        L = len(CANARY)
        assert f._holdback == L + (L - 1) * _MAX_GAP - 1

    def test_clean_passthrough_complete_after_flush(self):
        f = StreamingCanaryFilter(CANARY)
        text = "A" * 50
        emitted, detected = f.feed(text)
        assert not detected
        full = emitted + f.flush()
        assert full == text

    def test_contiguous_detected_and_redacted(self):
        f = StreamingCanaryFilter(CANARY)
        text = f"prefix {CANARY} suffix"
        emitted, detected = f.feed(text)
        assert detected
        full = emitted + f.flush()
        assert CANARY not in full
        assert REDACTED_CANARY in full
        assert "prefix" in full and "suffix" in full

    def test_split_at_every_boundary(self):
        """The canary split at EVERY position must never leak."""
        for k in range(len(CANARY) + 1):
            f = StreamingCanaryFilter(CANARY)
            a, d1 = f.feed(f"xx {CANARY[:k]}")
            b, d2 = f.feed(CANARY[k:] + " yy")
            tail = f.flush()
            full = a + b + tail
            assert CANARY not in full, f"leak at split {k}: {full!r}"
            assert REDACTED_CANARY in full, f"no redaction at split {k}: {full!r}"
            assert (d1 or d2), f"no detection at split {k}"
            # No pre-flush emission may contain a completing fragment:
            # the last 2+ chars of an emission joined with the next feed
            # must never reconstruct (checked implicitly by CANARY not in
            # full; emissions are in order).

    def test_spaced_detected(self):
        f = StreamingCanaryFilter(CANARY)
        emitted, detected = f.feed(f"got {_spaced(CANARY)}!")
        full = emitted + f.flush()
        assert detected
        assert REDACTED_CANARY in full
        assert not _canary_pattern(CANARY).search(full)

    def test_partial_prefix_held_back(self):
        f = StreamingCanaryFilter(CANARY)
        # Feed clean filler then a partial canary prefix at the very end
        emitted, _ = f.feed("B" * 60 + CANARY[:6])
        # Emission must not end with the partial prefix...
        assert not emitted.endswith(CANARY[:6])
        # ...and the prefix must come out on flush (uncorrupted).
        assert f.flush().endswith(CANARY[:6])

    def test_flush_warning_rendered_once_then_idempotent(self):
        f = StreamingCanaryFilter(CANARY)
        _, detected = f.feed(CANARY)
        assert detected
        f.mark_warning()
        tail = f.flush()
        assert REDACTED_CANARY in tail and CANARY_WARNING in tail
        assert f.flush() == ""  # second flush: nothing, no double warning

    def test_flush_warning_when_buffer_empty(self):
        f = StreamingCanaryFilter(CANARY)
        # Feed long clean filler so the canary is fully emitted, then flush empties buffer
        f.feed(CANARY + " " + "z" * 300)
        f.mark_warning()
        first = f.flush()
        assert CANARY_WARNING in first
        assert f.flush() == ""

    def test_empty_canary_passthrough(self):
        f = StreamingCanaryFilter("")
        assert f.feed(CANARY) == (CANARY, False)
        assert f.flush() == ""

    def test_empty_feed_passthrough(self):
        f = StreamingCanaryFilter(CANARY)
        assert f.feed("") == ("", False)
