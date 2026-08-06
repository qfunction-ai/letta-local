"""Tests for tool loop detection in PolicyChecker."""

import pytest

from letta.security.policy import (
    LoopDetectionConfig,
    PolicyChecker,
    ToolCallPolicy,
)


class TestLoopDetection:
    """Unit tests for the loop detection feature in PolicyChecker."""

    def _make_checker(self, window=5, threshold=3):
        """Create a PolicyChecker with loop detection enabled."""
        policy = ToolCallPolicy(
            loop_detection=LoopDetectionConfig(enabled=True, window=window, threshold=threshold),
        )
        return PolicyChecker(policy)

    def _check_and_record(self, checker, tool_name, args):
        """Check policy and record the call if allowed."""
        ctx = {"tool_name": tool_name, "tool_args": args}
        decision = checker.check(tool_name, eval_context=ctx)
        if decision.allowed:
            checker.record_call(tool_name, args)
        return decision

    def test_loop_detected(self):
        """Same tool + same args called threshold times -> DENY."""
        checker = self._make_checker(window=5, threshold=3)
        args = {"query": "same query"}
        # First two calls: allowed
        d1 = self._check_and_record(checker, "web_search", args)
        d2 = self._check_and_record(checker, "web_search", args)
        assert d1.allowed is True
        assert d2.allowed is True
        # Third call: denied (count is 2 >= threshold-1=2)
        d3 = checker.check("web_search", eval_context={"tool_name": "web_search", "tool_args": args})
        assert d3.allowed is False
        assert d3.matched_rule == "loop_detection"

    def test_no_loop_with_different_args(self):
        """Same tool + different args -> ALLOW."""
        checker = self._make_checker(window=5, threshold=3)
        self._check_and_record(checker, "web_search", {"query": "query1"})
        self._check_and_record(checker, "web_search", {"query": "query2"})
        d3 = self._check_and_record(checker, "web_search", {"query": "query3"})
        assert d3.allowed is True

    def test_different_tools_same_args(self):
        """Same args but different tool names -> ALLOW."""
        checker = self._make_checker(window=5, threshold=3)
        args = {"path": "/etc/passwd"}
        self._check_and_record(checker, "file_read", args)
        self._check_and_record(checker, "file_write", args)
        d3 = self._check_and_record(checker, "file_read", args)
        # file_read with same args appeared twice in window (count=1 after first record_call)
        # Third call: count is 1, threshold-1=2, so 1 < 2 -> allowed
        assert d3.allowed is True

    def test_window_expiry(self):
        """Old calls fall out of window -> ALLOW."""
        checker = self._make_checker(window=3, threshold=3)
        args = {"query": "same"}
        # Fill window with 2 calls (window=3, so 2 entries after record)
        self._check_and_record(checker, "web_search", args)
        self._check_and_record(checker, "web_search", args)
        # Add a different call to push old ones out (window=3)
        self._check_and_record(checker, "web_search", {"query": "different"})
        # Add another different call so the original args fall out of the window
        self._check_and_record(checker, "web_search", {"query": "different2"})
        # Now the original args should have fallen out of the window (only 3 entries kept)
        d = self._check_and_record(checker, "web_search", args)
        assert d.allowed is True

    def test_disabled_by_default(self):
        """No loop_detection config -> no DENY, no tracking."""
        checker = PolicyChecker(ToolCallPolicy())
        args = {"query": "same"}
        for _ in range(10):
            d = self._check_and_record(checker, "web_search", args)
            assert d.allowed is True

    def test_rate_limit_coexists(self):
        """max_calls_per_tool and loop detection coexist."""
        policy = ToolCallPolicy(
            max_calls_per_tool={"web_search": 10},
            loop_detection=LoopDetectionConfig(enabled=True, window=5, threshold=3),
        )
        checker = PolicyChecker(policy)
        args = {"query": "same"}
        # First two calls: allowed by both rate limit and loop detection
        self._check_and_record(checker, "web_search", args)
        self._check_and_record(checker, "web_search", args)
        # Third call: denied by loop detection (rate limit not hit yet)
        d3 = checker.check("web_search", eval_context={"tool_name": "web_search", "tool_args": args})
        assert d3.allowed is False
        assert d3.matched_rule == "loop_detection"

    def test_reset_clears_window(self):
        """After reset_call_counts, previous calls don't count."""
        checker = self._make_checker(window=5, threshold=3)
        args = {"query": "same"}
        self._check_and_record(checker, "web_search", args)
        self._check_and_record(checker, "web_search", args)
        # Reset
        checker.reset_call_counts()
        # Same call should be allowed again
        d = self._check_and_record(checker, "web_search", args)
        assert d.allowed is True
