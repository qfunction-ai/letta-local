"""B-7 witness: spawn-side sandbox env pins BLAS thread pools to 1.

The numpy/OpenBLAS hang (RC-8): custom tools importing numpy/pandas wedge
under the sandbox's seccomp clone restriction because OpenBLAS starts
thread pools at import time. Single-threaded BLAS (OPENBLAS_NUM_THREADS=1,
OMP_NUM_THREADS=1) in the spawn-side env is the mitigation — it must be
set in BOTH sandbox executors (Landlock and the local fallback), in the
same block as PYTHONWARNINGS, NOT in the inherited-env allowlist.

AST-based guard (repo source-guard pattern, cf. test_import_shadow_guard):
assert the exec_env.update({...}) dict in each sandbox file contains both
keys. RED on 0.16.39-pre (keys absent), GREEN after the fix.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = [
    REPO / "letta/services/tool_sandbox/landlock_sandbox.py",
    REPO / "letta/services/tool_sandbox/local_sandbox.py",
]
REQUIRED_KEYS = {"OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"}


def _exec_env_update_keys(path: Path) -> set[str]:
    """Keys of the dict literal in the single `exec_env.update({...})` call."""
    tree = ast.parse(path.read_text())
    keys = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "exec_env"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    for k in arg.keys:
                        if isinstance(k, ast.Constant):
                            keys.add(k.value)
    return keys


def test_landlock_spawn_env_pins_blas_threads():
    keys = _exec_env_update_keys(FILES[0])
    assert keys, "no exec_env.update dict found in landlock_sandbox.py"
    missing = REQUIRED_KEYS - keys
    assert not missing, f"landlock_sandbox spawn env missing BLAS pinning: {missing}"


def test_local_spawn_env_pins_blas_threads():
    keys = _exec_env_update_keys(FILES[1])
    assert keys, "no exec_env.update dict found in local_sandbox.py"
    missing = REQUIRED_KEYS - keys
    assert not missing, f"local_sandbox spawn env missing BLAS pinning: {missing}"
