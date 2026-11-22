"""Source guard: sandboxed tool children must NOT import sqlite_functions.

v0.16.38 rig-caught bug: the env allowlist (L-1) correctly dropped
LETTA_PG_URI from child env, which flipped the child's `import letta` into
the SQLITE branch of letta/__init__ — pulling in the full ORM (numpy via
helpers.converters). numpy's OpenBLAS thread pool wedges under the sandbox
seccomp filter (clone blocked -> spin), killing every agent_state-carrying
tool call with a hang or a ~60s KeyboardInterrupt. Children register DB
event handlers for nobody; the import is server-only by its own comment.
"""

import ast


def test_sqlite_functions_import_gated_on_sandbox_env():
    src = open("letta/__init__.py").read()
    tree = ast.parse(src)

    # find every `from letta.orm import sqlite_functions` in the file
    imports = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and n.module == "letta.orm"
        and any(a.name == "sqlite_functions" for a in n.names)
    ]
    assert imports, "sqlite_functions import vanished from letta/__init__.py — server needs it for SQLITE deployments"

    # the guard: the If node containing the import must ALSO test that
    # LETTA_SANDBOXED_TOOL_EXECUTION != "1"
    src_text = src
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body_src = ast.get_source_segment(src_text, node) or ""
            if "sqlite_functions" in body_src:
                assert "LETTA_SANDBOXED_TOOL_EXECUTION" in body_src, (
                    "sqlite_functions import is no longer gated on LETTA_SANDBOXED_TOOL_EXECUTION — "
                    "sandboxed children will re-enter the SQLITE branch and wedge on numpy/OpenBLAS "
                    "under the seccomp clone block (v0.16.38 L-1 regression)"
                )
                return

    raise AssertionError("no If node containing the sqlite_functions import found")
