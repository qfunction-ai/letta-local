"""I28-2 (Ryan decision): network-enabled tools FAIL CLOSED when the kernel
can't enforce Landlock network rules — the wrapper exits 1 with a named
LandlockNetworkUnsupportedError instead of degrading to unrestricted."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

WRAPPER = Path(__file__).resolve().parents[1] / "letta" / "bin" / "letta_landlock_wrapper.py"


class TestFailClosedNetworkTools:
    def _probe_patch(self):
        """Force the net-support probe to fail (kernel rejects net rules)."""
        import letta.bin.letta_landlock_wrapper as w
        return patch.object(w, "_probe_net_rules", return_value=False)

    def test_source_contains_fail_closed(self):
        src = WRAPPER.read_text()
        assert "sys.exit(1)" in src
        assert "LandlockNetworkUnsupportedError" in src
        assert "fail-closed" in src.lower()
        # The old silent-degradation warning must be gone
        assert '"WARNING: Landlock ABI >= 4 but kernel rejected network rules. "\n              "Network restrictions not enforced."' not in src

    def test_no_silent_degradation_path(self):
        """The net-unsupported + network-tool combination must NOT fall
        through to ruleset creation — it must exit before that."""
        src = WRAPPER.read_text()
        between = src.split("elif abi >= 4 and (config.get(\"allow_tcp_connect\")")[1]
        before_scope = between.split("    scope = 0")[0]
        assert "sys.exit(1)" in before_scope, "refusal must exit before ruleset creation"

    def test_refusal_message_parser_compatible(self):
        """The server's parse_stderr_error_msg must be able to extract the
        refusal (SomeError: message shape on the last lines)."""
        src = WRAPPER.read_text()
        assert "LandlockNetworkUnsupportedError: " in src
