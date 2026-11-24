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
