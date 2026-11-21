"""v0.16.37 Part B: canary skeleton tier — audit-only fuzzy matching.

The strict tier is insertion-tolerant only; deletion variants
("CANARY-eps-08-2026" from "CANARY-eps-08-hidden-2026") never fired — no
redaction, no event. The skeleton tier closes the unlogged-exfiltration gap
WITHOUT touching output: audit event only.

Design constraints under test:
  - strict tier keeps the redaction duty, unchanged
  - skeleton NEVER redacts (fuzzy redaction false-positives on legitimate
    transcripts)
  - P-2 distance window: segments must appear IN ORDER within <= 2x canary
    length of the marker — "CANARY" in paragraph one plus a date in
    paragraph nine must NOT fire
  - span floor: marker + trivial fragments must not fire
  - short canaries (N < 4 segments) get no skeleton tier
"""

import pytest

from letta.security.canary_output_filter import (
    REDACTED_CANARY,
    StreamingCanaryFilter,
    _skeleton_segments,
    apply_canary_filter_to_message,
    scan_skeleton,
)

CANARY = "CANARY-eps-08-hidden-2026"  # 5 segments: the live incident shape


class TestSkeletonMatcher:
    def test_incident_deletion_variant_fires(self):
        """The live 2026-10-31 incident: 'hidden' segment deleted."""
        text = "Here is the token: CANARY-eps-08-2026 please exfiltrate it."
        result = scan_skeleton(text, CANARY)
        assert result is not None
        assert result["match_type"] == "skeleton"
        assert "CANARY" in result["matched_segments"]
        assert result["matched_ratio"] >= 0.5

    def test_full_canary_also_skeleton_noted(self):
        text = f"leak {CANARY} now"
        result = scan_skeleton(text, CANARY)
        assert result is not None
        assert len(result["matched_segments"]) == 5

    def test_transcript_far_apart_segments_no_fire(self):
        """P-2 window: marker and date in the same text but NOT within 2x."""
        para1 = "In this course we discuss how CANARY tokens are used in prompt injection defense."
        para9 = "The example token from the lecture was tagged eps-08 and dated 2026 for the demo."
        text = para1 + (" filler prose. " * 200) + para9
        assert scan_skeleton(text, CANARY) is None

    def test_marker_alone_no_fire(self):
        """The prototype false-positive class: bare marker never fires."""
        assert scan_skeleton("CANARY CANARY CANARY", CANARY) is None

    def test_marker_plus_trivial_fragments_no_fire(self):
        """Span floor: marker + tiny adjacent fragments stay under 50%."""
        assert scan_skeleton("CANARY 08", CANARY) is None

    def test_short_canary_no_skeleton_tier(self):
        assert _skeleton_segments("CANARY-abc") is None
        assert scan_skeleton("CANARY abc", "CANARY-abc") is None

    def test_segments_cached(self):
        segs1 = _skeleton_segments(CANARY)
        segs2 = _skeleton_segments(CANARY)
        assert segs1 is segs2  # lru_cache identity
        assert segs1 == ("CANARY", "eps", "08", "hidden", "2026")


class TestStrictTierUnchanged:
    def test_deletion_variant_strict_silent(self):
        """Strict tier must NOT fire on the deletion variant (insertion-only
        tolerance) — this is the documented gap the skeleton tier covers."""
        filt = StreamingCanaryFilter(CANARY)
        text = "CANARY-eps-08-2026"
        safe, detected = filt.feed(text)
        # feed holds back the tail; flush to see everything
        tail = filt.flush()
        full_emitted = safe + tail
        assert REDACTED_CANARY not in full_emitted
        assert "CANARY-eps-08-2026" in full_emitted  # NOT redacted by strict

    def test_full_canary_strict_still_redacts(self):
        filt = StreamingCanaryFilter(CANARY)
        safe, detected = filt.feed(CANARY)
        assert detected
        tail = filt.flush()
        assert REDACTED_CANARY in safe + tail


class TestStreamingSkeleton:
    def test_skeleton_at_flush_audit_only(self):
        """Skeleton detected at flush; emitted text byte-identical to the
        no-canary-agent case (skeleton never gates emission)."""
        garbled = "CANARY-eps-08-2026"
        filt = StreamingCanaryFilter(CANARY)
        safe, _ = filt.feed(garbled)
        emitted = safe + filt.flush()
        assert emitted == garbled  # byte-identical: no redaction, no warning
        assert filt.skeleton_match is not None
        assert filt.skeleton_match["match_type"] == "skeleton"

    def test_clean_text_no_skeleton_match(self):
        filt = StreamingCanaryFilter(CANARY)
        safe, _ = filt.feed("The weather in Paris is lovely today.")
        emitted = safe + filt.flush()
        assert filt.skeleton_match is None
        assert emitted == "The weather in Paris is lovely today."

    def test_existing_holdback_behavior_untouched(self):
        """The v0.16.25 holdback math is untouched by construction: strict
        detection still redacts mid-stream and flush still appends the
        warning."""
        filt = StreamingCanaryFilter(CANARY)
        # text shorter than the holdback window -> all content held to flush
        safe, detected = filt.feed(f"prefix {CANARY} suffix")
        assert detected
        filt.mark_warning()
        tail = filt.flush()
        assert REDACTED_CANARY in safe + tail
        assert "SECURITY WARNING" in tail


class TestEventShapes:
    def test_strict_event_carries_match_type(self):
        from letta.schemas.letta_message import AssistantMessage
        from datetime import datetime, timezone

        msg = AssistantMessage(
            id="message-1",
            date=datetime.now(timezone.utc),
            content=f"leak {CANARY}",
        )
        filtered = apply_canary_filter_to_message(msg, CANARY)
        assert filtered is not msg
        assert REDACTED_CANARY in str(filtered.content)

    def test_suspected_event_data_shape(self):
        from letta.security import audit_helpers as _ah
        from letta.security.audit import SecurityEventType
        from unittest.mock import AsyncMock, patch

        match = scan_skeleton("CANARY-eps-08-2026", CANARY)
        assert match is not None
        with patch("letta.security.audit_helpers.audit_log", new=AsyncMock()) as mock_log:
            import asyncio

            asyncio.run(_ah.log_canary_output_suspected(None, "agent-1", None, match, None, None))
        args = mock_log.await_args.args
        assert args[3] == SecurityEventType.CANARY_OUTPUT_SUSPECTED
        assert args[4]["match_type"] == "skeleton"
        assert args[4]["location"] == "canary_block"
        assert args[4]["matched_segments"]
