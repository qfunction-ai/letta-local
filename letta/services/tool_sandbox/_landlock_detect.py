"""Runtime detection of Landlock ABI version with explicit fallback behavior.

This module is imported by settings.py to determine sandbox_type.
It must not import anything heavy — only the ctypes module which
itself only imports stdlib.
"""

import platform


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
