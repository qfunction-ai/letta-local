"""W-1 (interim-6): the G-4 traversal probe must actually RUN, pass on a
traversable dir, and raise the named error on a mode-700 parent.

The original `await asyncio.to_thread(open, _probe, "w").close()` bound as
`await (coroutine.close())` — the coroutine was closed without running,
TypeError on None, the probe file never existed, and the finally's
os.remove FileNotFoundError REPLACED the real error (raised from finally,
unmatched by any except). Verified by reproduction before the fix.
"""

import os
import pwd
import shutil
import stat
import tempfile
from unittest.mock import patch

import pytest

from letta.services.tool_sandbox.landlock_sandbox import AsyncToolSandboxLandlock


def _current_pw():
    return pwd.getpwuid(os.getuid())


@pytest.mark.asyncio
async def test_probe_positive_control_traversable():
    """A traversable exec dir (normal temp dir): the probe passes and
    creates-and-removes the probe file — i.e., it RUNS. X-nit: setpriv
    must be present, else the 'pass' only proves the file step ran."""
    if not shutil.which("setpriv"):
        pytest.skip("setpriv absent — a pass here would not prove the setpriv step ran")
    with tempfile.TemporaryDirectory() as d:
        probe_path = os.path.join(d, ".uid_probe")
        pw = _current_pw()
        with patch("pwd.getpwnam", return_value=pw):
            await AsyncToolSandboxLandlock._traversal_probe(
                AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock), d
            )
        # the probe file was created and cleaned up (the fix's lambda form)
        assert not os.path.exists(probe_path)


@pytest.mark.asyncio
async def test_probe_canary_mode700_parent():
    """A dir under a mode-700 parent owned by ANOTHER uid: the probe must
    raise the NAMED RuntimeError (the /root volume case). Requires root
    (we chmod a parent 700 and traverse as a different uid via setpriv);
    skipped when not root (the canary can't be constructed)."""
    if os.getuid() != 0:
        pytest.skip("canary requires root (construct a foreign-uid 700 parent)")
    base = tempfile.mkdtemp()
    try:
        blocked = os.path.join(base, "blocked")
        os.mkdir(blocked)
        os.chmod(blocked, stat.S_IRWXU)  # 700
        # owner stays root; the sandbox uid (or nobody) cannot traverse
        pw = pwd.getpwnam("nobody")
        with patch("pwd.getpwnam", return_value=pw):
            with pytest.raises(RuntimeError, match="unreachable by the sandbox uid"):
                await AsyncToolSandboxLandlock._traversal_probe(
                    AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock), blocked
                )
    finally:
        os.chmod(base, 0o755)
        import shutil

        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.asyncio
async def test_probe_class_cache():
    """X-2 (interim-7): the REAL gate (_ensure_traversal), two SEPARATE
    instances, _traversal_probe patched to count calls — one call total.
    (The previous test copied the call-site if into the test, the C-2
    anti-pattern: it would pass with the cache deleted.)"""
    calls = []

    async def fake_probe(exec_dir):
        calls.append(exec_dir)

    AsyncToolSandboxLandlock._traversal_probe_passed = False
    try:
        with patch.object(AsyncToolSandboxLandlock, "_traversal_probe", side_effect=fake_probe):
            a = AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock)
            b = AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock)
            await a._ensure_traversal("/tmp/probe-dir-1")
            await b._ensure_traversal("/tmp/probe-dir-2")  # separate instance!
        assert len(calls) == 1, (
            f"expected exactly ONE probe call (cached pass); got {len(calls)} — "
            "an instance-level flag or missing cache would re-run per call"
        )
    finally:
        AsyncToolSandboxLandlock._traversal_probe_passed = False


@pytest.mark.asyncio
async def test_probe_failure_not_cached():
    """X-2 canary: a FAILING probe is never cached — every call re-runs
    and re-raises (the operator sees the actionable error on every tool
    call, not once per server lifetime)."""
    attempts = []

    async def failing_probe(exec_dir):
        attempts.append(exec_dir)
        raise RuntimeError("Sandbox exec directory is unreachable by the sandbox uid (canary)")

    AsyncToolSandboxLandlock._traversal_probe_passed = False
    try:
        with patch.object(AsyncToolSandboxLandlock, "_traversal_probe", side_effect=failing_probe):
            a = AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock)
            b = AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock)
            for sbx in (a, b):
                with pytest.raises(RuntimeError, match="unreachable"):
                    await sbx._ensure_traversal("/tmp/probe-dir")
        assert len(attempts) == 2, "a failing probe must NOT be cached — both calls re-ran"
    finally:
        AsyncToolSandboxLandlock._traversal_probe_passed = False
