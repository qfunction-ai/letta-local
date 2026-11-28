"""I30-1: connect rules are per-allowed-port ([80, 443] default), bind is
denied entirely unless explicit bind ports are configured, and the probe
uses a real port. Canary: a port=0 rule call fails these tests."""

import sys
from unittest.mock import patch

import pytest

import letta.bin.letta_landlock_wrapper as W
import letta.services.tool_sandbox._landlock_ctypes as LC


def _drive(config: dict, monkeypatch):
    monkeypatch.setattr(LC, "detect_landlock_abi", lambda: 4)
    monkeypatch.setattr(LC, "prctl_set_no_new_privs", lambda: None)
    monkeypatch.setattr(W, "_probe_net_rules", lambda *a, **k: True)
    calls = {"net_rules": [], "restrict": 0}

    monkeypatch.setattr(LC, "landlock_restrict_self", lambda fd: calls.__setitem__("restrict", calls["restrict"] + 1))

    def fake_ruleset(handled_fs, handled_net, scope):
        return 7

    monkeypatch.setattr(LC, "landlock_create_ruleset", fake_ruleset)
    monkeypatch.setattr(LC, "landlock_add_path_rule", lambda *a, **k: None)

    def fake_add_net(fd, right, port=0):
        calls["net_rules"].append((right, port))

    monkeypatch.setattr(LC, "landlock_add_net_rule", fake_add_net)
    monkeypatch.setattr(W, "apply_seccomp", lambda cfg: None)
    monkeypatch.setattr(W.os, "close", lambda fd: None)

    def fake_execvp(path, args):
        pass

    monkeypatch.setattr(W.os, "execvp", fake_execvp)

    argv = ["w", "--config", __import__("json").dumps(config), "--", sys.executable, "-P", "/tmp/x.py"]
    monkeypatch.setattr(sys, "argv", argv)
    W.main()
    return calls


BASE = {"allow_tcp_connect": True, "allow_tcp_bind": False,
        "allowed_read_paths": ["/app"], "allowed_write_paths": ["/tmp"],
        "exec_dir": "/tmp", "blocked_syscalls": []}

NET_CONNECT = 1 << 1  # value from the wrapper's constants
NET_BIND = 1 << 0


class TestPerPortRules:
    def test_default_ports_80_443(self, monkeypatch):
        calls = _drive(dict(BASE), monkeypatch)
        ports = [p for (right, p) in calls["net_rules"] if right == 1 << 1 or "CONNECT" in str(right)]
        # assert exact rule pairs
        assert (2, 80) in calls["net_rules"], "must allow connect to 80"
        assert (2, 443) in calls["net_rules"], "must allow connect to 443"

    def test_no_port_zero_rule(self, monkeypatch):
        """Canary: the old port-less call (defaulting to port 0) must be gone."""
        calls = _drive(dict(BASE), monkeypatch)
        for right, port in calls["net_rules"]:
            assert port != 0, "a port=0 rule is the I30-1 bug — connect rules must name real ports"

    def test_bind_denied_without_explicit_ports(self, monkeypatch):
        cfg = dict(BASE)
        cfg["allow_tcp_bind"] = True  # requested, but no bind ports configured
        calls = _drive(cfg, monkeypatch)
        bind_rules = [r for r in calls["net_rules"] if r[0] == 1]
        assert bind_rules == [], "bind must be denied entirely (handled right, no rule)"

    def test_custom_connect_ports(self, monkeypatch):
        cfg = dict(BASE)
        cfg["allowed_tcp_connect_ports"] = [8443]
        calls = _drive(cfg, monkeypatch)
        assert (2, 8443) in calls["net_rules"]
        assert (2, 443) not in calls["net_rules"], "custom list replaces the default"
