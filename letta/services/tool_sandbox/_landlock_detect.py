"""Runtime detection of Landlock ABI version with explicit fallback behavior.

This module is imported by settings.py to determine sandbox_type.
It must not import anything heavy — only the ctypes module which
itself only imports stdlib.
"""

import platform

from functools import lru_cache


def detect_landlock_abi() -> int:
    """Detect the Landlock ABI version available on this kernel.

    Returns:
        0 if Landlock is not available (ENOSYS or not Linux)
        1-6+ for the supported ABI version

    ABI version capabilities:
        ABI >= 4: full feature set including allow_tcp_connect
        ABI >= 2: filesystem only, no network. If allow_tcp_connect=True,
                  log warning and fall back to LOCAL.
        ABI >= 1: basic filesystem restrictions only
        ABI < 1 or ENOSYS: Landlock not available. Fall back to LOCAL
                  with a loud warning (not a quiet log line).
    """
    if platform.system() != "Linux":
        return 0
    try:
        from letta.services.tool_sandbox._landlock_ctypes import detect_landlock_abi as _detect
        return _detect()
    except Exception:
        return 0


@lru_cache(maxsize=1)
def detect_sandbox_health() -> dict:
    """Detect Landlock ABI and whether network rules are ACTUALLY enforced.

    v0.16.37 (review P-8, L-8): the two signals are different and both are
    needed for honest health reporting:
      - landlock_abi: the kernel's reported ABI version.
      - landlock_net_rules_enforced: whether a probe ruleset actually accepts
        NET rules. On Docker Desktop VMs the ABI reports >= 4 but net rules
        don't apply — deployers must be able to SEE that gap.

    Cached (lru_cache maxsize=1): kernel capabilities don't change while the
    process runs. Returns None for both on non-Linux / detection failure.
    """
    if platform.system() != "Linux":
        return {"landlock_abi": 0, "landlock_net_rules_enforced": None}
    try:
        from letta.services.tool_sandbox._landlock_ctypes import (
            NET_BIND_TCP,
            NET_CONNECT_TCP,
            detect_landlock_abi as _detect,
            landlock_add_net_rule,
            landlock_create_ruleset,
        )

        abi = _detect()
        if abi < 1:
            return {"landlock_abi": 0, "landlock_net_rules_enforced": None}

        net_enforced = None
        if abi >= 4:
            # Mirror the wrapper's probe: can the kernel accept NET rules?
            test_fd = landlock_create_ruleset(0, NET_BIND_TCP | NET_CONNECT_TCP, 0)
            try:
                landlock_add_net_rule(test_fd, NET_CONNECT_TCP)
                net_enforced = True
            except OSError:
                net_enforced = False
            finally:
                import os

                os.close(test_fd)

        return {"landlock_abi": abi, "landlock_net_rules_enforced": net_enforced}
    except Exception:
        return {"landlock_abi": 0, "landlock_net_rules_enforced": None}
