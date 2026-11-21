"""v0.16.37 (review L-6): the Landlock wrapper FAILS CLOSED.

The old behavior: ABI < 1 → print a warning and run tool code UNSANDBOXED.
A kernel without Landlock meant every sandboxed tool silently escaped its
sandbox. Now: non-zero exit (loud tool failure through the normal error
path) unless the operator explicitly sets LETTA_ALLOW_UNSANDBOXED=true.
"""

import os
import sys
import types
from unittest import mock

import pytest


def _fake_ctypes_module(abi: int):
    """A fake _landlock_ctypes module so apply_landlock runs without a kernel."""
    m = types.ModuleType("letta.services.tool_sandbox._landlock_ctypes")
    m.detect_landlock_abi = lambda: abi
    m.prctl_set_no_new_privs = lambda: None
    # real fd: apply_landlock closes it via os.close
    m.landlock_create_ruleset = lambda handled, net, scope: os.open(os.devnull, os.O_RDONLY)
    m.landlock_add_path_rule = lambda fd, path, rights: None
    m.landlock_add_net_rule = lambda fd, right: None
    m.landlock_restrict_self = lambda fd: None
    m.ABI_V1_FS_RIGHTS = 0
    m.FS_READ_FILE = m.FS_READ_DIR = m.FS_WRITE_FILE = m.FS_MAKE_REG = 1
    m.FS_REMOVE_FILE = m.FS_REMOVE_DIR = m.FS_EXECUTE = m.FS_MAKE_DIR = 2
    m.FS_REFER = m.FS_TRUNCATE = m.FS_IOCTL = 4
    m.NET_BIND_TCP = m.NET_CONNECT_TCP = 8
    m.SCOPE_ABSTRACT_UNIX_SOCKET = m.SCOPE_SIGNAL = 16
    m.apply_seccomp_filter = lambda *a, **k: None
    return m


def _run_apply_landlock(abi: int, config: dict):
    from letta.bin.letta_landlock_wrapper import apply_landlock

    fake = _fake_ctypes_module(abi)
    with mock.patch.dict(sys.modules, {"letta.services.tool_sandbox._landlock_ctypes": fake}):
        apply_landlock(config)


class TestWrapperFailsClosed:
    def test_abi_zero_no_flag_exits_nonzero(self, monkeypatch):
        monkeypatch.delenv("LETTA_ALLOW_UNSANDBOXED", raising=False)
        with pytest.raises(SystemExit) as exc:
            _run_apply_landlock(0, {"allowed_read_paths": []})
        assert exc.value.code == 1

    def test_abi_zero_with_flag_proceeds_unsandboxed(self, monkeypatch, capsys):
        monkeypatch.setenv("LETTA_ALLOW_UNSANDBOXED", "true")
        _run_apply_landlock(0, {"allowed_read_paths": []})  # must NOT raise
        assert "explicit operator opt-in" in capsys.readouterr().err

    def test_abi_zero_flag_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LETTA_ALLOW_UNSANDBOXED", "TRUE")
        _run_apply_landlock(0, {"allowed_read_paths": []})  # must NOT raise

    def test_abi_one_proceeds_normally(self, monkeypatch):
        monkeypatch.delenv("LETTA_ALLOW_UNSANDBOXED", raising=False)
        _run_apply_landlock(1, {"allowed_read_paths": [], "allowed_write_paths": []})
        # no SystemExit — the fail-closed branch is ABI<1 only


class TestSandboxHealthReporting:
    def test_detect_sandbox_health_shape(self):
        from letta.services.tool_sandbox._landlock_detect import detect_sandbox_health

        result = detect_sandbox_health()
        assert set(result.keys()) == {"landlock_abi", "landlock_net_rules_enforced"}
        # on non-Linux dev machines: abi 0, net None
        assert result["landlock_abi"] == 0
        assert result["landlock_net_rules_enforced"] is None

    def test_health_schema_has_honest_fields(self):
        from letta.schemas.health import Health

        h = Health(version="test", status="ok")
        assert h.landlock_abi is None
        assert h.landlock_net_rules_enforced is None
        assert h.audit_write_failures is None

    def test_audit_write_failure_counter_increments(self):
        import letta.security.audit as audit_mod
        from letta.security.audit import get_audit_write_failures

        before = get_audit_write_failures()
        broken_logger = mock.MagicMock()
        broken_logger.log = mock.AsyncMock(side_effect=RuntimeError("db down"))

        async def _call():
            await audit_mod.audit_log(
                broken_logger, "agent-1", None, "tool_executed", {}, None, None, "test",
            )

        import asyncio

        asyncio.run(_call())  # must not raise (fail-open)
        assert get_audit_write_failures() == before + 1
