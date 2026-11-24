"""B-1 guard: no raw environment copies or mutations in tool/sandbox/security services.

The v0.16.38 L-1 allowlist replaced os.environ.copy() in the sandbox spawn
path. v0.16.39 B-1 deletes the last legacy copy (tool_execution_sandbox.py)
and this guard keeps it deleted: any os.environ.copy(), dict(os.environ), or
os.environ[...] = mutation under the guarded directories fails the suite,
unless the line carries a `sandbox-env-exempt:` annotation (with a reason).

RED on 05a7d0af5: tool_execution_sandbox.py:116,132 contained os.environ.copy().
"""
import ast
import re
from pathlib import Path

LETTA_ROOT = Path(__file__).resolve().parent.parent / "letta"

GUARDED_DIRS = [
    LETTA_ROOT / "services" / "tool_sandbox",
    LETTA_ROOT / "services" / "tool_executor",
    LETTA_ROOT / "services" / "tool_manager.py",
    LETTA_ROOT / "security",
]

EXEMPT_MARKER = "sandbox-env-exempt"

COPY_PATTERNS = ("os.environ.copy()", "dict(os.environ)")
MUTATE_RE = re.compile(r"os\.environ\[.+\]\s*=")


def _iter_py_files():
    for target in GUARDED_DIRS:
        if target.is_file():
            yield target
        else:
            for p in target.rglob("*.py"):
                yield p


def _line_has_exempt(source: str, lineno: int) -> bool:
    lines = source.split("\n")
    # annotation may sit on the line itself or the line above
    for idx in (lineno - 1, lineno):
        if 0 <= idx - 1 < len(lines) and EXEMPT_MARKER in lines[idx - 1]:
            return True
    return False


def test_no_raw_env_copies_or_mutations_in_tool_services():
    violations = []
    for path in _iter_py_files():
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                rendered = ast.dump(node)
                # os.environ.copy()
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "copy"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "environ"
                ):
                    if not _line_has_exempt(source, node.lineno):
                        violations.append(f"{path.relative_to(LETTA_ROOT)}:{node.lineno} os.environ.copy()")
                # dict(os.environ)
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "dict"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Attribute)
                    and node.args[0].attr == "environ"
                ):
                    if not _line_has_exempt(source, node.lineno):
                        violations.append(f"{path.relative_to(LETTA_ROOT)}:{node.lineno} dict(os.environ)")
            # os.environ[key] = value
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                    ):
                        if not _line_has_exempt(source, node.lineno):
                            violations.append(f"{path.relative_to(LETTA_ROOT)}:{node.lineno} os.environ[...] =")
    assert not violations, "Raw environment copies/mutations in guarded services:\n" + "\n".join(violations)


def test_legacy_tool_execution_sandbox_deleted():
    assert not (LETTA_ROOT / "services" / "tool_executor" / "tool_execution_sandbox.py").exists()
    assert not (LETTA_ROOT / "agent.py").exists()
    assert not (LETTA_ROOT / "server" / "rest_api" / "routers" / "openai").exists()
