"""B-12 witnesses: pip/venv subprocess env narrowing + wheel-only installs.

RED on 05a7d0af5: build_pip_subprocess_env did not exist; the three call
sites passed os.environ.copy(); pip_cmd had no --only-binary flag; a local
sdist installed successfully (build code ran as the server user).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from letta.schemas.pip_requirement import PipRequirement
from letta.schemas.sandbox_config import LocalSandboxConfig
from letta.services.helpers import tool_execution_helper as teh


SECRETS = {
    "LETTA_SERVER_PASSWORD": "super-secret-pw-123",
    "LETTA_PG_URI": "postgresql://u:secretpw@localhost/db",
    "LETTA_ENCRYPTION_KEY": "enc-key-abc",
}


def test_build_pip_env_excludes_secrets(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    env = teh.build_pip_subprocess_env()
    for k in SECRETS:
        assert k not in env, f"{k} leaked into pip subprocess env"
    assert "PATH" in env
    assert env["HOME"].startswith(tempfile.gettempdir()), "HOME must be a fresh temp dir"


def test_build_pip_env_passes_pip_config(monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://mirror.example.com/simple")
    env = teh.build_pip_subprocess_env()
    assert env["PIP_INDEX_URL"] == "https://mirror.example.com/simple"


def _fake_venv(sandbox_dir: str):
    """Create a venv-shaped dir whose python is this interpreter (uv pythons
    SIGABRT on ensurepip, so with_pip=True is unusable in tests)."""
    bin_dir = os.path.join(sandbox_dir, "venv", "bin")
    os.makedirs(bin_dir)
    os.symlink(sys.executable, os.path.join(bin_dir, "python3"))


def test_pip_cmd_is_wheel_only(monkeypatch, tmp_path):
    # drive install_pip_requirements_for_sandbox with a throwaway venv
    sandbox_dir = str(tmp_path)
    _fake_venv(sandbox_dir)
    configs = LocalSandboxConfig(
        sandbox_dir=sandbox_dir,
        use_venv=True,
        venv_name="venv",
        pip_requirements=[PipRequirement(name="six")],
    )
    captured = {}
    def fake_run_subprocess(command, env=None, fail_msg=""):
        captured["cmd"] = command
    monkeypatch.setattr(teh, "run_subprocess", fake_run_subprocess)
    monkeypatch.setattr(teh, "ensure_pip_is_up_to_date", lambda *a, **k: None)
    teh.install_pip_requirements_for_sandbox(local_configs=configs, env=teh.build_pip_subprocess_env())
    assert "--only-binary=:all:" in captured["cmd"], "pip must refuse sdist installs"


def _build_marker_sdist(base: Path) -> Path:
    """Build a tiny sdist whose setup.py writes a marker file at build time."""
    pkg = base / "marker-pkg"
    pkg.mkdir()
    (pkg / "setup.py").write_text(
        "from setuptools import setup\n"
        "import os\n"
        f"open({str(base / 'marker.txt')!r}, 'w').write('build code ran')\n"
        "setup(name='marker-pkg', version='0.0.1', packages=[])\n"
    )
    dist = base / "dist"
    subprocess.run(
        [sys.executable, "setup.py", "sdist", "--dist-dir", str(dist)],
        cwd=pkg, check=True, capture_output=True,
    )
    sdist = next(dist.glob("*.tar.gz"))
    return sdist


@pytest.mark.slow
def test_sdist_build_code_refused(tmp_path):
    """End-to-end witness: an sdist whose build code writes a marker file
    must NOT be installed (wheel-only), and the marker must not exist."""
    sdist = _build_marker_sdist(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.unlink(missing_ok=True)  # the sdist BUILD writes the marker; only pip's install-time build may re-create it
    sandbox_dir = str(tmp_path / "sbx")
    os.makedirs(sandbox_dir)
    _fake_venv(sandbox_dir)
    configs = LocalSandboxConfig(
        sandbox_dir=sandbox_dir,
        use_venv=True,
        venv_name="venv",
        pip_requirements=[PipRequirement(name=str(sdist))],
    )
    with pytest.raises(Exception):
        teh.install_pip_requirements_for_sandbox(local_configs=configs, env=teh.build_pip_subprocess_env())
    assert not marker.exists(), "sdist build code ran during install — wheel-only violated"


# ---------------------------------------------------------------------------
# R-1 (Part B review): the HOT PATH — _prepare_venv — must use the pip
# allowlist env, not the tool-execution env. The original B-12 wiring covered
# three operator-triggered call sites; the path every pip_requirements tool
# actually takes is _prepare_venv in both sandboxes. These witnesses drive it.
# ---------------------------------------------------------------------------

POISON_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "LETTA_SERVER_PASSWORD": "super-secret-pw-123",
    "LETTA_PG_URI": "postgresql://u:secretpw@localhost/db",
    "MY_AGENT_SECRET_TOKEN": "agent-tool-secret-xyz",
}


class _CapturedSubprocess:
    """Records every (cmd, env) pair instead of running anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args[0] if args else kwargs.get("command"), kwargs.get("env")))
        if args and args[0] and args[0][0] == "python3":
            # venv.create path — no-op
            return None
        return subprocess.CompletedProcess(args[0] if args else [], 0, "", "")


@pytest.mark.asyncio
async def test_prepare_venv_hot_path_uses_pip_allowlist_env(monkeypatch, tmp_path):
    """Drive LocalSandbox._prepare_venv with a POISONED tool env: no secret may
    reach any pip/venv subprocess, HOME must be a fresh temp dir, and the
    install command must be wheel-only."""
    from letta.services.tool_sandbox.local_sandbox import AsyncToolSandboxLocal

    captured = _CapturedSubprocess()
    monkeypatch.setattr(teh.subprocess, "run", captured)
    monkeypatch.setattr(teh, "run_subprocess", captured)
    # create_venv_for_local_sandbox checks os.path.isdir(venv) — make it exist
    venv_path = tmp_path / "venv"
    venv_path.mkdir()
    (venv_path / "bin").mkdir()
    # find_python_executable requires the binary to exist
    (venv_path / "bin" / "python3").write_text("#!/bin/sh\n")
    (venv_path / "bin" / "pip").write_text("#!/bin/sh\n")

    sandbox = AsyncToolSandboxLocal.__new__(AsyncToolSandboxLocal)
    sandbox.force_recreate_venv = False
    sandbox.tool = None

    configs = LocalSandboxConfig(
        sandbox_dir=str(tmp_path),
        venv_name="venv",
        use_venv=True,
        pip_requirements=[PipRequirement(name="numpy")],
    )

    await sandbox._prepare_venv(configs, str(venv_path), dict(POISON_ENV))

    assert captured.calls, "expected pip/venv subprocess calls"
    for cmd, env in captured.calls:
        assert env is not None
        for k, v in POISON_ENV.items():
            if k == "PATH":
                continue
            assert env.get(k) != v, f"secret {k} leaked to subprocess env"
            assert v not in (env.get(k) or ""), f"secret value of {k} leaked"
        assert env.get("HOME", "").startswith(tempfile.gettempdir()), (
            "pip subprocess HOME must be a fresh temp dir, not the server user's home"
        )
    installs = [cmd for cmd, _ in captured.calls if cmd and "pip" in cmd and "install" in cmd]
    assert installs, "expected a pip install call"
    for cmd in installs:
        assert any("--only-binary=:all:" == part for c in cmd for part in (c if isinstance(c, list) else [c])), (
            "hot-path pip install must be wheel-only"
        )


def test_source_guard_prepare_venv_never_passes_tool_env_to_pip():
    """AST guard (R-1): both _prepare_venv bodies must route pip/venv calls
    through build_pip_subprocess_env, never the raw tool env parameter."""
    import ast

    for mod_path, cls_name in [
        ("letta/services/tool_sandbox/local_sandbox.py", "AsyncToolSandboxLocal"),
        ("letta/services/tool_sandbox/landlock_sandbox.py", "AsyncToolSandboxLandlock"),
    ]:
        src = Path(__file__).resolve().parents[1] / mod_path
        tree = ast.parse(src.read_text())
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_prepare_venv":
                found = True
                body_src = ast.unparse(node)
                assert "build_pip_subprocess_env()" in body_src, (
                    f"{mod_path}: _prepare_venv must build the pip allowlist env"
                )
                # no call inside _prepare_venv may pass env=env (the raw param)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        for kw in sub.keywords:
                            if kw.arg == "env" and isinstance(kw.value, ast.Name) and kw.value.id == "env":
                                raise AssertionError(
                                    f"{mod_path}: _prepare_venv passes the raw tool env to a subprocess call"
                                )
        assert found, f"{mod_path}: _prepare_venv not found"


# ---------------------------------------------------------------------------
# T-2 (Part B re-check): build_pip_subprocess_env() used to mkdtemp() per
# call with no cleanup — one orphaned temp dir per sandboxed tool run once
# R-1 routed the hot path through it. The pip home is now process-lifetime.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prepare_venv_hot_path_does_not_leak_pip_homes(monkeypatch, tmp_path):
    """Drive _prepare_venv TWICE with the poisoned env: the number of
    letta-pip-home-* directories must grow by at most one (process-lifetime
    home), not one per run."""
    from letta.services.tool_sandbox.local_sandbox import AsyncToolSandboxLocal

    captured = _CapturedSubprocess()
    monkeypatch.setattr(teh.subprocess, "run", captured)
    monkeypatch.setattr(teh, "run_subprocess", captured)
    venv_path = tmp_path / "venv"
    venv_path.mkdir()
    (venv_path / "bin").mkdir()
    (venv_path / "bin" / "python3").write_text("#!/bin/sh\n")
    (venv_path / "bin" / "pip").write_text("#!/bin/sh\n")

    sandbox = AsyncToolSandboxLocal.__new__(AsyncToolSandboxLocal)
    sandbox.force_recreate_venv = False
    sandbox.tool = None
    configs = LocalSandboxConfig(
        sandbox_dir=str(tmp_path),
        venv_name="venv",
        use_venv=True,
        pip_requirements=[PipRequirement(name="numpy")],
    )

    import glob
    import stat as stat_mod

    before = glob.glob(os.path.join(tempfile.gettempdir(), "letta-pip-home-*"))
    await sandbox._prepare_venv(configs, str(venv_path), dict(POISON_ENV))
    await sandbox._prepare_venv(configs, str(venv_path), dict(POISON_ENV))
    after = glob.glob(os.path.join(tempfile.gettempdir(), "letta-pip-home-*"))
    assert len(after) - len(before) <= 1, (
        f"pip home leaked: {len(before)} dirs before two runs, {len(after)} after"
    )
    if after:
        mode = stat_mod.S_IMODE(os.stat(after[0]).st_mode)
        assert mode == 0o700, f"pip home must be 0700, got {oct(mode)}"
        assert os.path.isdir(os.path.join(after[0], ".cache")), "pip home must carry a reusable cache dir"
