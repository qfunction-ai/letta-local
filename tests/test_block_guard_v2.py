"""Tests for the v0.16.37 read-only block guard layer (review P-1/P-6/P-15).

Covers enforce_read_only_mutation (lowest-common-layer guard), the canary
label reservation, and the load_canary arming preference. Manager-level
wiring is enforced by tests/test_block_write_source_guard.py; REST-level
refusals are covered by smoke checks 16-18.
"""

from types import SimpleNamespace

import pytest

from letta.security.block_guard import (
    check_read_only_block,
    enforce_canary_label_protection,
    enforce_read_only_mutation,
)
from letta.constants import READ_ONLY_BLOCK_EDIT_ERROR


def _block(read_only=True, label="persona", value="v", description="d"):
    return SimpleNamespace(read_only=read_only, label=label, value=value, description=description)


class TestEnforceReadOnlyMutation:
    def test_value_change_refused(self):
        with pytest.raises(ValueError, match=READ_ONLY_BLOCK_EDIT_ERROR):
            enforce_read_only_mutation(_block(), {"value": "new"})

    def test_label_change_refused(self):
        with pytest.raises(ValueError):
            enforce_read_only_mutation(_block(), {"label": "evil"})

    def test_description_change_refused(self):
        # P-6: description reaches the system prompt — not cosmetic
        with pytest.raises(ValueError):
            enforce_read_only_mutation(_block(), {"description": "injected instructions"})

    def test_read_only_flip_refused(self):
        with pytest.raises(ValueError):
            enforce_read_only_mutation(_block(), {"read_only": False})

    def test_read_only_true_to_true_allowed(self):
        enforce_read_only_mutation(_block(), {"read_only": True})  # no-op

    def test_no_op_same_values_allowed(self):
        b = _block()
        enforce_read_only_mutation(b, {"value": "v", "label": "persona", "description": "d"})

    def test_unrelated_fields_allowed(self):
        enforce_read_only_mutation(_block(), {"limit": 100, "metadata_": {}})

    def test_teardown_exempt(self):
        enforce_read_only_mutation(_block(), {"value": "anything"}, teardown=True)

    def test_writable_block_anything_goes(self):
        enforce_read_only_mutation(_block(read_only=False), {"value": "x", "read_only": False})

    def test_canary_value_rewrite_refused(self):
        # The original L-10 gap: even with label+flag guarded, a canary VALUE
        # rewrite is a full defeat
        canary = _block(label="__canary__", value="CANARY-abc123")
        with pytest.raises(ValueError):
            enforce_read_only_mutation(canary, {"value": "CANARY-known-to-attacker"})


class TestCanaryLabelReservation:
    def test_relabel_to_canary_refused(self):
        with pytest.raises(ValueError, match="reserved"):
            enforce_canary_label_protection(_block(read_only=False, label="persona"), {"label": "__canary__"})

    def test_existing_canary_keeps_label(self):
        enforce_canary_label_protection(_block(label="__canary__"), {"label": "__canary__"})

    def test_normal_label_change_fine(self):
        enforce_canary_label_protection(_block(label="persona"), {"label": "human"})


class TestOperationGuards:
    def test_delete_refused(self):
        with pytest.raises(ValueError):
            check_read_only_block(_block(), operation="delete")

    def test_detach_refused(self):
        with pytest.raises(ValueError):
            check_read_only_block(_block(), operation="detach")

    def test_undo_refused(self):
        with pytest.raises(ValueError):
            check_read_only_block(_block(), operation="undo")

    def test_redo_refused(self):
        with pytest.raises(ValueError):
            check_read_only_block(_block(), operation="redo")

    def test_writable_block_operations_fine(self):
        for op in ("delete", "detach", "undo", "redo"):
            check_read_only_block(_block(read_only=False), operation=op)


class TestLoadCanaryArmingPreference:
    """P-15 belt-and-braces: prefer the read_only canary when multiple exist."""

    @pytest.mark.asyncio
    async def test_read_only_canary_preferred_over_writable_impostor(self):
        from letta.security.agent_security import load_canary

        writable_impostor = SimpleNamespace(
            label="__canary__", value="CANARY-attacker-known", read_only=False,
            id="block-impostor", description="d",
        )
        real_canary = SimpleNamespace(
            label="__canary__", value="CANARY-real-value", read_only=True,
            id="block-real", description="d",
        )
        agent = SimpleNamespace(
            agent_state=SimpleNamespace(memory=SimpleNamespace(blocks=[writable_impostor, real_canary])),
            canary_checker=_FakeChecker(),
            logger=SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
        )
        await load_canary(agent)
        assert agent.canary_checker.canary_value == "CANARY-real-value"

    @pytest.mark.asyncio
    async def test_single_writable_canary_still_arms_and_upgrades(self):
        from letta.security.agent_security import load_canary

        canary = SimpleNamespace(
            label="__canary__", value="CANARY-solo", read_only=False,
            id="block-solo", description="d",
        )
        agent = SimpleNamespace(
            agent_state=SimpleNamespace(memory=SimpleMemory(blocks=[canary])),
            canary_checker=_FakeChecker(),
            logger=SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None),
        )
        await load_canary(agent)
        assert agent.canary_checker.canary_value == "CANARY-solo"
        assert canary.read_only is True  # upgraded in-memory


class _FakeChecker:
    def __init__(self):
        self.canary_value = None

    def update_canary(self, v):
        self.canary_value = v


class SimpleMemory:
    def __init__(self, blocks):
        self.blocks = blocks
