"""I29-1: the landlock_net_port_attr struct must match the kernel layout
(allowed_access FIRST, port SECOND) — the reversed pack made every net
rule EINVAL, degrading the sandbox to net-unrestricted (I28-1), which
I28-2's fail-closed then turned into "all network tools refused
everywhere"."""

import struct
from unittest.mock import patch

import pytest


class TestNetPortAttrPacking:
    def test_pack_order_matches_kernel(self):
        """The packed bytes must be allowed_access || port (kernel UAPI
        order), NOT port || allowed_access."""
        from letta.services.tool_sandbox import _landlock_ctypes as L

        captured = {}

        def fake_syscall(num, fd, rule_type, attr, flags):
            captured["attr"] = attr
            captured["rule_type"] = rule_type
            return 0

        with patch.object(L, "_get_syscall_numbers", return_value=(0, 999, 0)), \
             patch.object(L._libc, "syscall", side_effect=fake_syscall):
            L.landlock_add_net_rule(ruleset_fd=7, allowed_access=0x20, port=443)

        expected = struct.pack("QQ", 0x20, 443)  # allowed_access, port
        assert captured["attr"] == expected, (
            f"packed bytes wrong: got {captured['attr'].hex()}, "
            f"want {expected.hex()} (allowed_access first, port second)"
        )
        assert captured["rule_type"] == 2  # LANDLOCK_RULE_NET_PORT

    def test_pack_order_canary_reversed_bytes_fail(self):
        """Canary: the OLD (reversed) packing produces different bytes —
        this test fails if someone re-reverses the pack order."""
        from letta.services.tool_sandbox import _landlock_ctypes as L

        captured = {}

        def fake_syscall(num, fd, rule_type, attr, flags):
            captured["attr"] = attr
            return 0

        with patch.object(L, "_get_syscall_numbers", return_value=(0, 999, 0)), \
             patch.object(L._libc, "syscall", side_effect=fake_syscall):
            L.landlock_add_net_rule(ruleset_fd=7, allowed_access=0x20, port=443)

        reversed_bytes = struct.pack("QQ", 443, 0x20)  # the OLD wrong order
        assert captured["attr"] != reversed_bytes

    def test_port_zero_is_literal(self):
        """Port 0 packs as port 0 (a real port number in Landlock), not a
        wildcard marker."""
        from letta.services.tool_sandbox import _landlock_ctypes as L

        captured = {}

        def fake_syscall(num, fd, rule_type, attr, flags):
            captured["attr"] = attr
            return 0

        with patch.object(L, "_get_syscall_numbers", return_value=(0, 999, 0)), \
             patch.object(L._libc, "syscall", side_effect=fake_syscall):
            L.landlock_add_net_rule(ruleset_fd=7, allowed_access=0x40, port=0)

        expected = struct.pack("QQ", 0x40, 0)
        assert captured["attr"] == expected
