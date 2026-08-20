"""Source guard for reset_messages_async soft-delete semantics.

The v0.16.22 bug: reset_messages returned 200 but left old messages
visible in GET /messages (which queries by agent_id, not by
agent.message_ids). The fix soft-deletes all non-system messages scoped
by agent_id.

This guard pins two properties of the fix:
  1. The delete is scoped by agent_id (NOT message_ids[1:]) —
     capture-ingested messages never enter agent.message_ids but DO
     appear in GET /messages, so a message_ids-scoped delete would
     leave them visible after reset.
  2. The system message is excluded from the delete.

The full end-to-end proof (capture -> reset -> GET count == 1) runs in
scripts/smoke/smoke_test.sh as check 5, against a real server.
"""
import ast
from pathlib import Path


def _find_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def test_reset_soft_delete_is_agent_scoped():
    """reset_messages_async must issue an is_deleted=True update scoped by
    agent_id and excluding system_message_id — not by message_ids[1:]."""
    path = Path("letta/services/agent_manager.py")
    source = path.read_text()
    tree = ast.parse(source)

    func = _find_function(tree, "reset_messages_async")
    assert func is not None, "reset_messages_async not found in agent_manager.py"

    for child in ast.walk(func):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "execute":
            code = ast.get_source_segment(source, child) or ""
            has_soft_delete = "is_deleted" in code and "True" in code
            has_agent_scope = "agent_id" in code
            excludes_system = "system_message_id" in code
            not_ids_scoped = ".in_(" not in code
            if has_soft_delete and has_agent_scope and excludes_system and not_ids_scoped:
                return  # guard satisfied

    raise AssertionError(
        "reset_messages_async must soft-delete by agent_id (excluding "
        "system_message_id) — found no matching session.execute() call. "
        "A message_ids[1:]-scoped delete leaves capture-ingested messages "
        "visible after reset (the v0.16.22 bug)."
    )


def test_step_child_fks_cascade():
    """FKs referencing steps.id must not block agent deletion.

    SQLAlchemy flushes child DELETEs before the parent agent row, so any
    step-child FK with NO ACTION blocks the steps DELETE while its rows
    are still live (the v0.16.23 bug: tool_calls, security_events).
    Complete expected inventory of steps.id FKs:
      messages  -> SET NULL, step_metrics -> CASCADE,
      tool_calls -> CASCADE, security_events -> CASCADE.
    """
    expectations = {
        "letta/orm/message.py": ("ForeignKey(\"steps.id\", ondelete=\"SET NULL\")", "message"),
        "letta/orm/step_metrics.py": ("ForeignKey(\"steps.id\", ondelete=\"CASCADE\")", "step_metrics"),
        "letta/orm/tool_call.py": ("ForeignKey(\"steps.id\", ondelete=\"CASCADE\")", "tool_calls"),
        "letta/orm/security_event.py": ("ForeignKey(\"steps.id\", ondelete=\"CASCADE\")", "security_events"),
    }
    for path, (expected, table) in expectations.items():
        source = Path(path).read_text()
        assert expected in source, (
            f"{table}.step_id FK must be {expected.split('ondelete=')[1].rstrip(')')} "
            f"— a NO ACTION step-child FK blocks agent deletion (v0.16.23 bug class)"
        )
