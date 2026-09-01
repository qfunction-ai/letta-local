"""Tests for the canary opt-out flag (v0.16.31, LETTA_CANARY_ENABLED).

Flag-off semantics: load_canary returns before any arming — no block
creation, no value loading, fail-closed fallback unreachable. Consumers
(output_filter, streaming gate, CanaryChecker.check) are already inert
on an unset value. Pre-existing __canary__ blocks remain as inert
system-prompt text (documented semantics, pinned by test 2).

Default is True — consumers that never set the flag are byte-identical
(functionally; pedantically one early `if`). The on-path is additionally
rig-proven end-to-end by smoke check 4.7.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from letta.security import agent_security as _sec
from letta.security.canary import CanaryChecker
from letta.settings import settings


def _agent(blocks):
    """Minimal agent stand-in for load_canary (SimpleNamespace precedent)."""
    return SimpleNamespace(
        agent_state=SimpleNamespace(memory=SimpleNamespace(blocks=list(blocks))),
        canary_checker=CanaryChecker(),
        agent_id="agent-test",
        actor=SimpleNamespace(organization_id="org-test"),
    )


def _canary_block(value="CANARY-11111111-2222-3333-4444-555555555555"):
    return SimpleNamespace(label="__canary__", value=value)


class TestFlagOff:
    def test_fresh_agent_no_arming_no_creation(self, monkeypatch):
        monkeypatch.setattr(settings, "canary_enabled", False, raising=False)
        agent = _agent(blocks=[])
        import asyncio

        asyncio.run(_sec.load_canary(agent))
        assert agent.canary_checker.canary_value is None
        # No lazy block creation — and no DB session was ever opened
        # (the early return precedes everything; nothing raised without
        # a configured DB, which is itself the proof).
        assert len(agent.agent_state.memory.blocks) == 0

    def test_preexisting_block_not_loaded_but_present(self, monkeypatch):
        monkeypatch.setattr(settings, "canary_enabled", False, raising=False)
        block = _canary_block()
        agent = _agent(blocks=[block])
        import asyncio

        asyncio.run(_sec.load_canary(agent))
        # Value NOT loaded — checker stays inert
        assert agent.canary_checker.canary_value is None
        # Block stays as inert prompt text (the documented semantics)
        assert "__canary__" in [b.label for b in agent.agent_state.memory.blocks]


class TestFlagOn:
    def test_existing_block_value_loaded_unchanged(self, monkeypatch):
        monkeypatch.setattr(settings, "canary_enabled", True, raising=False)
        value = "CANARY-aaaa1111-bbbb2222-cccc3333-dddd44444444"
        agent = _agent(blocks=[_canary_block(value)])
        import asyncio

        asyncio.run(_sec.load_canary(agent))
        assert agent.canary_checker.canary_value == value

    def test_default_is_true(self):
        # The no-behavior-change pin: consumers that never set the flag
        assert settings.canary_enabled is True


class TestSourceGuard:
    def test_guard_precedes_try(self):
        """The load-bearing placement: the check must appear BEFORE the
        try in load_canary — the except path arms a fresh canary
        (fail-closed), so a guard inside the try leaves it live."""
        src = Path("letta/security/agent_security.py").read_text()
        fn_start = src.find("async def load_canary")
        fn_body = src[fn_start:]
        guard_pos = fn_body.find("if not settings.canary_enabled:")
        try_pos = fn_body.find("try:")
        assert guard_pos != -1, "canary_enabled guard missing from load_canary"
        assert try_pos != -1
        assert guard_pos < try_pos, "guard MUST precede the try/except (fail-closed fallback)"
