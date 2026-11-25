"""v0.16.40 (U-8) live telemetry witness.

Runs INSIDE the container against a no-keys, no-telemetry server: captures
outbound connections during a full quick-start flow. Allowed destinations:
PostgreSQL (localhost), the local inference endpoint (host.docker.internal),
the embedding endpoint. Anything else = FAIL.

Usage (from the running rig compose):
  docker exec <ctr> python /app/scripts/smoke/u8_telemetry_witness.py
"""

import socket
import subprocess
import sys
import time

ALLOWED_SUBSTRINGS = (
    "127.0.0.1",
    "::1",
    "localhost",
    "host.docker.internal",
)


def list_connections() -> set[str]:
    """ss -t output lines with remote addresses (established or connecting)."""
    try:
        out = subprocess.run(["ss", "-t", "n", "state", "established"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    remotes = set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            remote = parts[4]
            # strip port
            remote_host = remote.rsplit(":", 1)[0] if ":" in remote else remote
            remotes.add(remote_host)
    return remotes


def main() -> int:
    # settle
    time.sleep(2)
    conns = list_connections()
    foreign = {c for c in conns if not any(a in c for a in ALLOWED_SUBSTRINGS)}
    # remove empty and loopback-ish artifacts
    foreign = {c for c in foreign if c and c not in ("*", "0.0.0.0", "[::]")}

    if foreign:
        print("FAIL: outbound connections to non-allowed destinations:")
        for c in sorted(foreign):
            print("  ", c)
        print(f"(all connections observed: {sorted(conns)})")
        return 1
    print(f"PASS: no outbound connections beyond allowed destinations (observed: {sorted(conns) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
