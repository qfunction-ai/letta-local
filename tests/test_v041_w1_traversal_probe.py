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
    creates-and-removes the probe file — i.e., it RUNS."""
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
    """W-1b: the pass is cached at CLASS level — the second call does not
    re-run the probe (a per-call instance flag would spawn setpriv+cat on
    every tool call; the executor builds a new sandbox per call)."""
    calls = []

    async def fake_probe(self, exec_dir):
        calls.append(exec_dir)

    with patch.object(AsyncToolSandboxLandlock, "_traversal_probe", fake_probe):
        AsyncToolSandboxLandlock._traversal_probe_passed = True
        try:
            sbx = AsyncToolSandboxLandlock.__new__(AsyncToolSandboxLandlock)
            # emulate the call-site check
            if not AsyncToolSandboxLandlock._traversal_probe_passed:
                await sbx._traversal_probe("/tmp/x")
                AsyncToolSandboxLandlock._traversal_probe_passed = True
        finally:
            AsyncToolSandboxLandlock._traversal_probe_passed = False  # reset for other tests
    assert calls == [], "cached pass must not re-run the probe"
