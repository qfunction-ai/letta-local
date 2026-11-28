"""I29-2 (behavior, per rule 2): the fail-closed path must be tested by
RUNNING the wrapper main() with the probe mocked — not by grepping source.
- probe True + network config -> ruleset carries net rights, execvp reached
- probe False + network config -> SystemExit(1) + LandlockNetworkUnsupportedError
  on stderr, BEFORE landlock_restrict_self and before execvp
- probe False + NON-network config -> runs (restrict + execvp reached)
"""

import json
import sys
from unittest.mock import patch

import pytest

import letta.bin.letta_landlock_wrapper as W
import letta.services.tool_sandbox._landlock_ctypes as LC


def _drive(config: dict, net_supported: bool, monkeypatch, capsys):
    # macOS has no Landlock: force the ABI probe to 4 and stub the two
    # syscall-boundary functions (prctl dlsym fails on macOS) so the
    # net-support DECISION logic is what the test exercises.
    monkeypatch.setattr(LC, "detect_landlock_abi", lambda: 4)
    monkeypatch.setattr(LC, "prctl_set_no_new_privs", lambda: None)
    monkeypatch.setattr(W, "_probe_net_rules", lambda *a, **k: net_supported)
    calls = {"restrict": 0, "net_rights": 0, "execvp": None}

    monkeypatch.setattr(LC, "landlock_restrict_self", lambda fd: calls.__setitem__("restrict", calls["restrict"] + 1))

    def fake_ruleset(handled_fs, handled_net, scope):
        calls["net_rights"] = handled_net
        return 7

    monkeypatch.setattr(LC, "landlock_create_ruleset", fake_ruleset)
    monkeypatch.setattr(LC, "landlock_add_path_rule", lambda *a, **k: None)
    monkeypatch.setattr(LC, "landlock_add_net_rule", lambda *a, **k: None)
    monkeypatch.setattr(W, "apply_seccomp", lambda cfg: None)
    # the mocked ruleset fd is not a real fd — os.close(7) at teardown hits EBADF
    monkeypatch.setattr(W.os, "close", lambda fd: None)

    def fake_execvp(path, args):
        calls["execvp"] = (path, args)

    monkeypatch.setattr(W.os, "execvp", fake_execvp)

    script = "/tmp/i29_tool_script.py"
    argv = [
        "letta_landlock_wrapper.py",
        "--config", json.dumps(config),
        "--", sys.executable, "-P", script,
    ]
    monkeypatch.setattr(sys, "argv", argv)

    code = None
    try:
        W.main()
    except SystemExit as e:
        code = e.code
    captured = capsys.readouterr()
    return code, calls, captured


NET_TOOL = {"allow_tcp_connect": True, "allow_tcp_bind": False,
            "allowed_read_paths": ["/app"], "allowed_write_paths": ["/tmp"],
            "exec_dir": "/tmp", "blocked_syscalls": []}


class TestFailClosedBehavior:
    def test_net_supported_runs_with_net_rights(self, monkeypatch, capsys, tmp_path):
        code, calls, _ = _drive(NET_TOOL, net_supported=True, monkeypatch=monkeypatch, capsys=capsys)
        assert code is None, "must not exit"
        assert calls["net_rights"] != 0, "ruleset must carry net rights"
        assert calls["restrict"] == 1
        assert calls["execvp"] is not None, "tool must be exec'd"

    def test_net_unsupported_network_tool_refuses(self, monkeypatch, capsys):
        code, calls, captured = _drive(NET_TOOL, net_supported=False, monkeypatch=monkeypatch, capsys=capsys)
        assert code == 1, "must exit(1)"
        assert calls["restrict"] == 0, "must refuse BEFORE restrict_self"
        assert calls["execvp"] is None, "tool must NOT be exec'd"
        assert "LandlockNetworkUnsupportedError" in captured.err

    def test_net_unsupported_non_network_still_runs(self, monkeypatch, capsys):
        cfg = dict(NET_TOOL)
        cfg["allow_tcp_connect"] = False
        cfg["allow_tcp_bind"] = False
        code, calls, _ = _drive(cfg, net_supported=False, monkeypatch=monkeypatch, capsys=capsys)
        assert code is None, "non-network tool must still run"
        assert calls["restrict"] == 1
        assert calls["execvp"] is not None
