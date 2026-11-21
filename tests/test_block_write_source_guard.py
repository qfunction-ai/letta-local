"""Source guard: no block-field writes outside the guarded helper functions.

v0.16.37 (review P-16): the read_only enforcement in block_guard.py is only
as strong as its routing — every block-mutating function must run its change
set through enforce_read_only_mutation / check_read_only_block BEFORE
writing. This test scans ALL of letta/ (not just the managers) for:

  (a) setattr() calls on block-like variables
  (b) direct assignments to protected block fields (.value, .label,
      .read_only, .description) on block-like variables
  (c) bulk SQL against BlockModel (update/delete/insert(BlockModel))

and fails when any hit is outside the allowlist of guarded/legitimate
functions. Summarization, sleeptime, or any future path that writes block
fields directly breaks this test until it routes through the guard.

Known limitation (accepted): block-likeness is inferred from the target
variable's NAME (block, blk, b, db_block, matched_block, canary_block, ...)
matching the codebase's naming conventions. A new path using an
unconventional variable name could evade (a)/(b) — but (c) is exact, and
the manager-layer routes are name-stable. The allowlist is deliberately
small; additions require a justification comment here.
"""

import ast
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1] / "letta"

PROTECTED_FIELDS = {"value", "label", "read_only", "description"}

# Variable names the codebase uses for block objects (ORM or pydantic).
BLOCK_VAR_LEXICON = {
    "block", "blocks", "b", "blk", "db_block", "matched_block",
    "canary_block", "existing", "target_block", "pydantic_block",
    "block_model", "new_block", "created_block", "block_obj",
    "block_orm",
}

# (filename suffix, function name) pairs allowed to write block fields.
# Every entry must have a justification.
ALLOWED_WRITE_FUNCTIONS = {
    # Guarded update route: enforce_read_only_mutation runs before the loop
    ("services/block_manager.py", "update_block_async"),
    # Guarded bulk route: all updates validated before any applied
    ("services/block_manager.py", "bulk_update_block_values_async"),
    # Restore writes; ONLY called from undo/redo, both guarded at entry
    ("services/block_manager.py", "_move_block_to_sequence"),
    # Guarded agent-scoped route (raw setattr in its own session)
    ("services/agent_manager.py", "modify_block_by_label_async"),
    # Creation-time default description injection on not-yet-persisted blocks
    ("services/agent_manager.py", "create_agent_async"),
    # Protective-direction canary read_only upgrades (v0.16.33, P-15 arming)
    ("security/agent_security.py", "load_canary"),
    ("security/agent_security.py", "_persist_canary_read_only"),
    ("security/agent_security.py", "_create_canary_block"),
    # In-memory pydantic update with its OWN read_only check before writing
    ("schemas/memory.py", "update_block_value"),
    # Git->postgres sync, guarded by enforce_read_only_mutation (v0.16.37);
    # read-only blocks are excluded from the git namespace entirely
    ("services/block_manager_git.py", "_sync_block_to_postgres"),
    # Git enablement backfill relabels — read-only blocks are SKIPPED in the
    # loop (relabeling __canary__ to system/__canary__ would disarm it)
    ("services/block_manager_git.py", "enable_git_memory_for_agent"),
}

# The guard helper itself may inspect/compare but never writes; nothing to allow.
BULK_SQL_TARGETS = {"BlockModel"}


def _iter_python_files():
    for path in PKG_ROOT.rglob("*.py"):
        yield path


def _find_hits():
    """Return list of (file, lineno, kind, description) for unallowed writes.

    Uses a proper recursive visitor so the enclosing-function attribution is
    exact (ast.walk is BFS and cannot maintain a function stack).
    """
    hits = []

    def visit(node, rel, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        # setattr(x, ...) where x is block-like
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in BLOCK_VAR_LEXICON
        ):
            if (rel, fn) not in ALLOWED_WRITE_FUNCTIONS:
                hits.append((rel, node.lineno, "setattr", f"setattr({node.args[0].id}, ...) in {fn}"))
        # x.<field> = ... where x is block-like and field is protected
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Attribute)
            and node.targets[0].attr in PROTECTED_FIELDS
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id in BLOCK_VAR_LEXICON
        ):
            var = node.targets[0].value.id
            if (rel, fn) not in ALLOWED_WRITE_FUNCTIONS:
                hits.append((rel, node.lineno, "assign", f"{var}.{node.targets[0].attr} = ... in {fn}"))
        # Bulk SQL: update/delete/insert(BlockModel)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("update", "delete", "insert"):
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in BULK_SQL_TARGETS:
                hits.append((rel, node.lineno, "bulk_sql", f"{node.func.id}(BlockModel) in {fn}"))
        for child in ast.iter_child_nodes(node):
            visit(child, rel, fn)

    for path in _iter_python_files():
        rel = str(path.relative_to(PKG_ROOT))
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        visit(tree, rel, None)
    return hits


def test_no_block_field_writes_outside_guard():
    hits = _find_hits()
    assert hits == [], (
        "Block-field writes found outside the guarded helper functions. "
        "Each site must route through enforce_read_only_mutation / "
        "check_read_only_block (letta/security/block_guard.py) BEFORE writing, "
        "or be added to ALLOWED_WRITE_FUNCTIONS with a justification. "
        f"Unallowed writes: {hits}"
    )
