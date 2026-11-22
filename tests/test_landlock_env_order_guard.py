"""Source guard: exec_env["HOME"] assignment must FOLLOW exec_env construction.

v0.16.38 rig-caught bug (same class as v0.16.22's _ah shadow): the HOME/TMPDIR
per-exec assignment was anchored after exec_dir creation, but exec_env is
BUILT later in the flow — UnboundLocalError killed every sandboxed tool run.
Presence guards don't catch ordering bugs; this asserts the ORDER.
"""

import ast


def test_home_tmpdir_assigned_after_exec_env_built():
    src = open("letta/services/tool_sandbox/landlock_sandbox.py").read()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # find the function that assigns exec_env["HOME"] / ["TMPDIR"]
        assigns = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Assign)
            and isinstance(n.targets[0], ast.Subscript)
            and getattr(n.targets[0].value, "id", "") == "exec_env"
            and getattr(n.targets[0].slice, "value", "") in ("HOME", "TMPDIR")
        ]
        if not assigns:
            continue
        # the same function must also build exec_env (exec_env = ...)
        builds = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "exec_env"
        ]
        assert builds, "exec_env['HOME'] assigned in a function that never builds exec_env"
        for a in assigns:
            assert a.lineno > max(b.lineno for b in builds), (
                f"exec_env['HOME'/'TMPDIR'] at line {a.lineno} assigned BEFORE exec_env is built "
                f"(last build at line {max(b.lineno for b in builds)}) — UnboundLocalError at runtime"
            )
        return

    raise AssertionError("no exec_env['HOME'] assignment found in landlock_sandbox.py — per-exec HOME/TMPDIR (v0.16.38 L-1) is missing")
