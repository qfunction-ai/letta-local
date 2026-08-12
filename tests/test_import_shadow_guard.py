"""Guard against import alias shadowing bugs.

Catches the pattern where a module-level `import X as Y` is shadowed
by a function-level `import Z as Y` — Python treats Y as local for the
entire function, breaking any reference to the module-level import that
appears before the local assignment.

The original bug: letta_agent_v3.py had `from letta.agents import
agent_hardening as _ah` at module level and `from letta.security import
audit_helpers as _ah` as a local import inside stream(). Python treated
_ah as local for all of stream(), so the module-level _ah.init_run_hardening()
call at line 511 raised UnboundLocalError because the local assignment
at line 658 hadn't executed yet.
"""
import ast
from pathlib import Path


def _module_level_aliases(tree: ast.Module) -> dict[str, str]:
    """Return {alias_name: module_path} for module-level imports."""
    aliases = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _function_level_aliases(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Return {alias_name: module_path} for local imports in a function."""
    aliases = {}
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.asname:
                    aliases[alias.asname] = child.module or ""
        elif isinstance(child, ast.Import):
            for alias in child.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def test_no_ah_shadow_in_v3():
    """No function in letta_agent_v3.py may have a local `import ... as _ah`.

    V3 has `from letta.agents import agent_hardening as _ah` at module level.
    A local `import ... as _ah` inside any function shadows it, causing
    UnboundLocalError on any _ah reference before the local assignment.
    Use `_ah_audit` (already imported at module level) for audit_helpers.
    """
    path = Path("letta/agents/letta_agent_v3.py")
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _function_level_aliases(node)
        assert "_ah" not in local, (
            f"{node.name} (line {node.lineno}) has local `import ... as _ah` "
            f"which shadows the module-level `agent_hardening as _ah`. "
            f"Use `_ah_audit` for audit_helpers calls."
        )


def test_no_cross_module_alias_shadow():
    """No function in letta/ may shadow a module-level import alias with a
    different module via a local import.

    This catches the general class of bug: a module-level `from A import X as Y`
    gets shadowed by a function-level `from B import Z as Y`. Python treats Y
    as local for the entire function, breaking references to the module-level
    import that appear before the local assignment.

    Re-importing the same module under the same alias (e.g. lazy imports to
    avoid circular dependencies) is harmless and excluded from the check.
    """
    letta_dir = Path("letta")
    for pyfile in letta_dir.rglob("*.py"):
        tree = ast.parse(pyfile.read_text())
        module_aliases = _module_level_aliases(tree)
        if not module_aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = _function_level_aliases(node)
            for alias_name, local_module in local.items():
                if alias_name not in module_aliases:
                    continue  # not a shadow
                module_module = module_aliases[alias_name]
                if local_module == module_module:
                    continue  # same module re-import — harmless
                raise AssertionError(
                    f"{pyfile}:{node.name} (line {node.lineno}) shadows "
                    f"module-level `{alias_name}` (from {module_module}) "
                    f"with local import from {local_module}. "
                    f"Use a different alias name."
                )
