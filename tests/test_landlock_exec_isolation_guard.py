"""Source guards for v0.16.37 A7 (review L-4/P-4/P-11): per-execution isolation
in the Landlock sandbox runner.

The old shape: every execution shared one sandbox_dir — write-granted, cwd,
and sys.path[0] via the script location. One injected json.py = permanent
hijack of every later tool run, for every agent; concurrent runs could read
each other's scripts (pickled agent state + decrypted secrets).

The new shape (each assertion below pins one part):
    1. per-execution mkdtemp dir, script written there, cwd = that dir
    2. shared sandbox_dir ABSENT from read AND write grants
    3. per-exec dir present in read AND write grants
    4. venv granted read+execute on its own path (not the sandbox root)
    5. interpreter launched with -P (script dir never on sys.path)
    6. exec dir removed in finally
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "letta" / "services" / "tool_sandbox" / "landlock_sandbox.py"


def test_per_exec_dir_created_and_used():
    src = SRC.read_text()
    assert 'tempfile.mkdtemp, dir=sandbox_dir, prefix="exec-"' in src
    assert "temp_file_path = await write_temp_file(exec_dir, code)" in src
    assert "cwd=exec_dir" in src


def test_shared_sandbox_dir_absent_from_grants():
    tree = ast.parse(SRC.read_text())
    # find the wrapper_config dict literal
    config_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if "allowed_read_paths" in keys and "allowed_write_paths" in keys:
                config_node = node
    assert config_node is not None, "wrapper_config dict not found"

    read_val = ast.unparse(config_node.values[[i for i, k in enumerate(config_node.keys) if isinstance(k, ast.Constant) and k.value == "allowed_read_paths"][0]])
    write_val = ast.unparse(config_node.values[[i for i, k in enumerate(config_node.keys) if isinstance(k, ast.Constant) and k.value == "allowed_write_paths"][0]])

    # P-11: the shared sandbox_dir must not appear as a grant...
    assert "sandbox_dir" not in read_val.replace("sandbox_dir, prefix", ""), (
        f"sandbox_dir must not be granted read: {read_val}"
    )
    assert "sandbox_dir" not in write_val, f"sandbox_dir must not be granted write: {write_val}"
    # ...but the per-exec dir must be in both
    assert "exec_dir" in read_val
    assert "exec_dir" in write_val


def test_venv_granted_own_path():
    src = SRC.read_text()
    assert "([venv_path] if use_venv else [])" in src
    # read AND execute grants both carry the venv path
    assert src.count("([venv_path] if use_venv else [])") == 2


def test_interpreter_dash_p():
    src = SRC.read_text()
    assert '"-P",' in src, "tool interpreter must run with -P (script dir off sys.path)"


def test_exec_dir_removed_in_finally():
    src = SRC.read_text()
    assert "shutil.rmtree, exec_dir, True" in src
