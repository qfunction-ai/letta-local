"""Tests for letta.security.canary — CanaryChecker and canary value generation."""

import json
import pytest
from letta.security.canary import CanaryChecker


class TestCanaryChecker:
    def test_no_canary_value_returns_false(self):
        checker = CanaryChecker()
        assert checker.check({"message": "hello"}) is False

    def test_none_canary_value_returns_false(self):
        checker = CanaryChecker(canary_value=None)
        assert checker.check({"message": "hello"}) is False

    def test_detects_canary_in_argument(self):
        canary = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"
        checker = CanaryChecker(canary_value=canary)
        assert checker.check({"message": f"my secret is {canary}"}) is True

    def test_detects_canary_in_nested_argument(self):
        canary = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"
        checker = CanaryChecker(canary_value=canary)
        nested_args = {
            "label": "persona",
            "old_content": "some text",
            "new_content": f"leaked: {canary}",
        }
        assert checker.check(nested_args) is True

    def test_no_false_positive_on_normal_args(self):
        canary = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"
        checker = CanaryChecker(canary_value=canary)
        assert checker.check({"message": "hello world"}) is False
        assert checker.check({"label": "persona", "content": "I am an AI"}) is False

    def test_no_false_positive_on_partial_canary(self):
        """Partial canary string should not trigger detection."""
        canary = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"
        checker = CanaryChecker(canary_value=canary)
        # Just the prefix
        assert checker.check({"message": "I saw a CANARY- in the zoo"}) is False
        # Partial UUID
        assert checker.check({"message": "CANARY-a7f3"}) is False

    def test_empty_args(self):
        canary = "CANARY-a7f3b2c1-d4e5-6789-abcd-ef0123456789"
        checker = CanaryChecker(canary_value=canary)
        assert checker.check({}) is False

    def test_update_canary(self):
        checker = CanaryChecker()
        assert checker.check({"message": "test"}) is False

        canary = "CANARY-12345678-1234-1234-1234-123456789012"
        checker.update_canary(canary)
        assert checker.check({"message": canary}) is True

    def test_update_canary_to_none(self):
        canary = "CANARY-12345678-1234-1234-1234-123456789012"
        checker = CanaryChecker(canary_value=canary)
        assert checker.check({"message": canary}) is True

        checker.update_canary(None)
        assert checker.check({"message": canary}) is False


class TestCanaryGeneration:
    def test_generate_canary_value(self):
        value = CanaryChecker.generate_canary_value()
        assert value.startswith("CANARY-")
        # UUID format after prefix
        uuid_part = value[len("CANARY-"):]
        assert len(uuid_part) == 36  # standard UUID format

    def test_generate_unique_values(self):
        v1 = CanaryChecker.generate_canary_value()
        v2 = CanaryChecker.generate_canary_value()
        assert v1 != v2

    def test_canary_block_label(self):
        assert CanaryChecker.CANARY_BLOCK_LABEL == "__canary__"

    def test_canary_prefix(self):
        assert CanaryChecker.CANARY_PREFIX == "CANARY-"
