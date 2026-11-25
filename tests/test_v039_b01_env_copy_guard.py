"""B-1 guard: no raw environment copies or mutations in tool/sandbox/security services.

The v0.16.38 L-1 allowlist replaced os.environ.copy() in the sandbox spawn
path. v0.16.39 B-1 deletes the last legacy copy (tool_execution_sandbox.py)
and this guard keeps it deleted: any os.environ.copy(), dict(os.environ), or
os.environ[...] = mutation under the guarded directories fails the suite,
unless the line carries a `sandbox-env-exempt:` annotation (with a reason).

RED on 05a7d0af5: tool_execution_sandbox.py:116,132 contained os.environ.copy().

v0.16.40 (R-8) widening: letta/services/helpers/ joins the guarded
directories (R-1's raw-env pip call lived there); patterns add
{**os.environ}, os.environ.update/setdefault/pop, and subprocess /
asyncio.create_subprocess spawns with NO env= argument
(whole-environment inheritance) — the latter exempted for remote
executors with a `remote-executor-exempt:` annotation (E2B runs
server-side subprocesses by design; their env is not a tool env).
"""
import ast
import re
from pathlib import Path

LETTA_ROOT = Path(__file__).resolve().parent.parent / "letta"

GUARDED_DIRS = [
    LETTA_ROOT / "services" / "tool_sandbox",
    LETTA_ROOT / "services" / "tool_executor",
    LETTA_ROOT / "services" / "helpers",  # v0.16.40 R-8: R-1's pip env call lived here
    LETTA_ROOT / "services" / "tool_manager.py",
    LETTA_ROOT / "security",
]

EXEMPT_MARKER = "sandbox-env-exempt"
REMOTE_EXEMPT_MARKER = "remote-executor-exempt"  # v0.16.40 R-8: E2B and other remote executors

COPY_PATTERNS = ("os.environ.copy()", "dict(os.environ)")
MUTATE_RE = re.compile(r"os\.environ\[.+\]\s*=")
# v0.16.40 R-8 patterns
DICT_UNPACK_RE = re.compile(r"\{\*\*os\.environ\b")
ENVRION_METHODS_RE = re.compile(r"os\.environ\.(update|setdefault|pop)\s*\(")


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


# ---- v0.16.40 (R-8) widening ------------------------------------------------


def test_no_env_dict_unpack_or_method_mutations():
    """{**os.environ} and os.environ.update/setdefault/pop are raw whole-env
    materializations/mutations — same rule as copy()."""
    violations = []
    for path in _iter_py_files():
        source = path.read_text()
        for lineno, line in enumerate(source.split("\n"), 1):
            if DICT_UNPACK_RE.search(line) or ENVRION_METHODS_RE.search(line):
                if _line_has_exempt(source, lineno):
                    continue
                violations.append(f"{path.relative_to(LETTA_ROOT)}:{lineno} {line.strip()[:80]}")
    assert not violations, "Raw env unpack/method mutation in guarded services:\n" + "\n".join(violations)


def test_subprocess_spawns_pass_env():
    """subprocess / asyncio.create_subprocess calls in guarded dirs must pass
    env= explicitly (no whole-environment inheritance). Remote executors
    (E2B) are exempt with a `remote-executor-exempt:` annotation."""
    violations = []
    for path in _iter_py_files():
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            base = func.value
            mod = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else None)
            is_subprocess = mod == "subprocess" or (mod == "asyncio" and func.attr.startswith("create_subprocess"))
            if not is_subprocess:
                continue
            if any(kw.arg == "env" for kw in node.keywords):
                continue
            if _line_has_exempt(source, node.lineno) or _line_has_marker(source, node.lineno, REMOTE_EXEMPT_MARKER):
                continue
            violations.append(f"{path.relative_to(LETTA_ROOT)}:{node.lineno} {func.attr}(...) without env=")
    assert not violations, "Subprocess spawn without explicit env= in guarded services:\n" + "\n".join(violations)


def _line_has_marker(source: str, lineno: int, marker: str) -> bool:
    lines = source.split("\n")
    for idx in (lineno - 1, lineno):
        if 0 <= idx - 1 < len(lines) and marker in lines[idx - 1]:
            return True
    return False
