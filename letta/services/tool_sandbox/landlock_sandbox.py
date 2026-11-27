"""Landlock + seccomp-BPF sandbox for Letta tool execution.

Architecturally similar to AsyncToolSandboxLocal (subprocess + stdout markers)
but with kernel-level restrictions applied by a wrapper script before the
tool code executes. The restrictions are IRREVERSIBLE once applied.

The wrapper is launched as a separate process via asyncio.create_subprocess_exec()
with close_fds=True. No fork from the multi-threaded parent, no preexec_fn,
no deadlock risk.
"""

import asyncio
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
from typing import Any, Dict, Optional

from pydantic.config import JsonDict

from letta.log import get_logger
from letta.otel.tracing import log_event, trace_method
from letta.schemas.agent import AgentState
from letta.schemas.enums import SandboxType
from letta.schemas.sandbox_config import SandboxConfig
from letta.schemas.tool import Tool
from letta.schemas.tool_execution_result import ToolExecutionResult
from letta.services.helpers.tool_execution_helper import (
    build_pip_subprocess_env,
    create_venv_for_local_sandbox,
    find_python_executable,
    install_pip_requirements_for_sandbox,
)
from letta.services.helpers.tool_parser_helper import parse_stdout_best_effort
from letta.services.tool_sandbox.base import AsyncToolSandboxBase
from letta.utils import get_friendly_error_msg, parse_stderr_error_msg, safe_create_task

logger = get_logger(__name__)

# v0.16.37 (review Low-3): the sandbox passes -P, which Python < 3.11
# rejects (fail-closed). A base-image downgrade would otherwise surface as
# opaque per-tool failures. Log the interpreter version once per distinct
# interpreter path so the cause is visible in the first log lines.
_LOGGED_INTERPRETERS: set = set()


def _log_interpreter_version_once(python_executable: str) -> None:
    key = os.path.realpath(python_executable)
    if key in _LOGGED_INTERPRETERS:
        return
    _LOGGED_INTERPRETERS.add(key)
    try:
        version_str = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        same_process = os.path.realpath(sys.executable) == key
        logger.info(
            f"Landlock sandbox interpreter: {python_executable} "
            f"(python {version_str}{'' if same_process else ' — venv/specified interpreter, base version unverified'})"
        )
        if same_process and (sys.version_info.major, sys.version_info.minor) < (3, 11):
            logger.error(
                f"Interpreter {python_executable} is Python {version_str} (< 3.11): "
                "sandboxed tools will fail (-P flag unsupported). Upgrade the base image."
            )
    except Exception:
        _LOGGED_INTERPRETERS.discard(key)  # never let logging break execution


class AsyncToolSandboxLandlock(AsyncToolSandboxBase):
    # W-1b: process-lifetime cache for the G-4 traversal probe (PASS only).
    _traversal_probe_passed: bool = False

    """Landlock sandbox backend for tool execution.

    Uses a wrapper script (letta_landlock_wrapper.py) that applies Landlock
    filesystem/network restrictions and a seccomp-BPF syscall filter before
    exec'ing the tool script. The wrapper is launched as a separate process
    to avoid fork-in-thread issues.
    """

    def __init__(
        self,
        tool_name: str,
        args: JsonDict,
        user,
        tool_id: str,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        force_recreate_venv=False,
        tool_object: Optional[Tool] = None,
        sandbox_config: Optional[SandboxConfig] = None,
        sandbox_env_vars: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            tool_name,
            args,
            user,
            tool_id=tool_id,
            agent_id=agent_id,
            project_id=project_id,
            tool_object=tool_object,
            sandbox_config=sandbox_config,
            sandbox_env_vars=sandbox_env_vars,
        )
        self.force_recreate_venv = force_recreate_venv

    @trace_method
    async def run(
        self,
        agent_state: Optional[AgentState] = None,
        additional_env_vars: Optional[Dict] = None,
    ) -> ToolExecutionResult:
        """Run the tool in a Landlock sandbox.

        Launches the wrapper script as a separate subprocess. The wrapper
        applies Landlock + seccomp restrictions, then execs the tool script.
        """
        if self.provided_sandbox_config:
            sbx_config = self.provided_sandbox_config
        else:
            sbx_config = await self.sandbox_config_manager.get_or_create_default_sandbox_config_async(
                sandbox_type=SandboxType.LANDLOCK, actor=self.user
            )
        landlock_config = sbx_config.get_landlock_config()

        # Prepare environment variables
        env = await self._gather_env_vars(agent_state, additional_env_vars, sbx_config.id, is_local=True)

        # Make sure sandbox directory exists
        sandbox_dir = os.path.expanduser(landlock_config.sandbox_dir)
        if not await asyncio.to_thread(lambda: os.path.exists(sandbox_dir) and os.path.isdir(sandbox_dir)):
            await asyncio.to_thread(os.makedirs, sandbox_dir)

        # If using a virtual environment, ensure it's prepared
        use_venv = landlock_config.use_venv
        venv_preparation_task = None
        if use_venv:
            venv_path = str(os.path.join(sandbox_dir, landlock_config.venv_name))
            venv_preparation_task = safe_create_task(
                self._prepare_venv(landlock_config, venv_path, env), label="prepare_venv"
            )

        # v0.16.37 (review L-4/P-4/P-11): per-execution isolation. Every
        # execution used to share one sandbox_dir — write-granted, cwd, and
        # sys.path[0] via the script location. One injection writing json.py
        # there hijacked every later tool run, for every agent, permanently;
        # concurrent runs could read each other's scripts (which carry
        # pickled agent state + decrypted secrets). Now: each execution gets
        # its own mkdtemp dir (0700), the shared sandbox_dir drops out of
        # BOTH read and write grants, and the interpreter runs with -P so
        # the script dir never lands on sys.path.
        code = await self.generate_execution_script(agent_state=agent_state, wrap_print_with_markers=True)

        exec_dir = await asyncio.to_thread(tempfile.mkdtemp, dir=sandbox_dir, prefix="exec-")
        # v0.16.41 (interim-4 G-4, interim-5 note): the sandbox uid must be
        # able to REACH the exec dir (every parent traversable by uid 1000).
        # On a pre-.41 /root/.letta volume, /root is mode 700 — the
        # subprocess would die opaquely at exec. Fail HERE with the named
        # error instead. Cached per process: /root's mode doesn't change
        # while the server runs, and the probe spawns setpriv+cat — once
        # is enough (one process spawn per SERVER lifetime, not per call).
        # W-1b: CLASS-level cache — a new AsyncToolSandboxLandlock is built
        # per tool call (sandbox_tool_executor), so an instance flag would
        # re-run the probe every call. Only the PASS is cached; a failure
        # keeps raising the named error every time.
        if not AsyncToolSandboxLandlock._traversal_probe_passed:
            await self._traversal_probe(exec_dir)
            AsyncToolSandboxLandlock._traversal_probe_passed = True
        # v0.16.41 (S-2/U-5): the exec dir is the sandbox subprocess's
        # HOME/TMPDIR — it must be owned by the sandbox uid. A failure here
        # is a REFUSAL, not a warning: the subprocess would run as uid 1000
        # unable to write its own working dir.
        import pwd as _pwd

        try:
            _pw = _pwd.getpwnam("letta")
            await asyncio.to_thread(os.chown, exec_dir, _pw.pw_uid, _pw.pw_gid)
        except KeyError as exc:
            raise RuntimeError(
                "Sandbox uid unavailable (exec dir chown): the 'letta' user does not "
                "exist. Sandboxed tool execution requires the separate sandbox uid "
                "(S-2/U-5); refusing to run tools with a root-owned exec dir."
            ) from exc
        except PermissionError as exc:
            raise RuntimeError(
                f"Cannot chown exec dir {exec_dir} to the sandbox uid (running "
                "unprivileged?). Refusing to run the tool with an unwritable exec dir."
            ) from exc

        async def write_temp_file(dir, content):
            def _write():
                with tempfile.NamedTemporaryFile(mode="w", dir=dir, suffix=".py", delete=False) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    return temp_file.name

            return await asyncio.to_thread(_write)

        temp_file_path = await write_temp_file(exec_dir, code)

        try:
            # Wait for venv preparation if started
            if venv_preparation_task:
                await venv_preparation_task

            # Determine the python executable and environment for the subprocess
            exec_env = env.copy()
            if use_venv:
                venv_path = str(os.path.join(sandbox_dir, landlock_config.venv_name))
                python_executable = find_python_executable(landlock_config)
                _log_interpreter_version_once(python_executable)
                exec_env["VIRTUAL_ENV"] = venv_path
                exec_env["PATH"] = os.path.join(venv_path, "bin") + ":" + exec_env["PATH"]
            else:
                python_executable = sys.executable
                _log_interpreter_version_once(python_executable)
                if "PYTHONPATH" in os.environ:
                    exec_env["PYTHONPATH"] = os.environ["PYTHONPATH"]

            # Handle unwanted terminal behavior
            exec_env.update(
                {
                    "PYTHONWARNINGS": "ignore",
                    "NO_COLOR": "1",
                    "TERM": "dumb",
                    "PYTHONUNBUFFERED": "1",
                    # v0.16.39 (B-7, RC-8): numpy/pandas imports wedge under
                    # the seccomp clone restriction because OpenBLAS starts
                    # thread pools at import time. Single-threaded BLAS
                    # avoids the pool spawn entirely.
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    # The sandboxed child must not configure file logging:
                    # letta.log's import-time dictConfig opens the logfile,
                    # which Landlock denies (writes outside the exec dir) —
                    # killing `import letta` before any tool code runs.
                    "LETTA_SANDBOXED_TOOL_EXECUTION": "1",
                }
            )

            # v0.16.38 (L-1, review W2 ruling): HOME and TMPDIR point at the
            # per-execution directory instead of the server's — libraries
            # that write ~/.cache or temp files get a writable location
            # inside the grant, and the tool never learns the server's home
            # path.
            exec_env["HOME"] = exec_dir
            exec_env["TMPDIR"] = exec_dir


            # Build wrapper config
            # Compute staging directory for file persistence (agent files)
            staging_write_paths = []
            agent_read_paths = []
            if self.agent_id:
                try:
                    from letta.settings import file_persistence_settings, settings
                    base = file_persistence_settings.agent_files_dir or os.path.join(str(settings.letta_dir), "agent_files")
                except Exception:
                    base = os.path.expanduser("~/.letta/agent_files")
                staging_dir = os.path.join(base, self.agent_id, ".staging")
                # Ensure staging dir exists so Landlock can grant write access
                os.makedirs(staging_dir, exist_ok=True)
                # v0.16.41 (S-2/U-5): the sandbox uid writes here; the
                # promotion (server-side, root) still reads it after the
                # run. Chown failure = refusal (same rule as exec_dir).
                import pwd as _pwd

                try:
                    _pw = _pwd.getpwnam("letta")
                    os.chown(staging_dir, _pw.pw_uid, _pw.pw_gid)
                except KeyError as exc:
                    raise RuntimeError(
                        "Sandbox uid unavailable (staging chown): the 'letta' user "
                        "does not exist. Refusing (S-2/U-5)."
                    ) from exc
                except PermissionError as exc:
                    # Staging is optional (file-persistence tools only): a
                    # permission failure means no staging WRITE grant, but
                    # the tool can still run without file persistence. Log
                    # loudly and continue WITHOUT granting staging.
                    logger.warning(
                        f"Cannot chown staging dir {staging_dir} to the sandbox uid; "
                        "file-persistence staging will be unavailable for this agent"
                    )
                staging_write_paths = [staging_dir] if os.stat(staging_dir).st_uid == _pw.pw_uid else []
                # file_read/file_list need READ access to the agent's promoted
                # files dir (without this, Path.exists() swallows the Landlock
                # EACCES as False and the tools silently return empty/missing)
                agent_read_paths = [os.path.join(base, self.agent_id)]

            wrapper_config = {
                # P-11: read/write grants go to THIS execution's temp dir
                # only — the shared sandbox_dir is no longer granted at all,
                # so concurrent executions cannot read each other's scripts.
                # The venv gets read+execute on its own path (not the sandbox
                # root), so tool code can't plant modules in site-packages.
                "allowed_read_paths": (
                    landlock_config.allowed_read_paths
                    + [exec_dir]
                    + agent_read_paths
                    + ([venv_path] if use_venv else [])
                ),
                "allowed_write_paths": (
                    landlock_config.allowed_write_paths
                    + [exec_dir]
                    + staging_write_paths
                ),
                "allowed_execute_paths": (
                    landlock_config.allowed_execute_paths
                    + ([venv_path] if use_venv else [])
                ),
                "allow_tcp_connect": landlock_config.allow_tcp_connect,
                "allow_tcp_bind": landlock_config.allow_tcp_bind,
                "blocked_syscalls": landlock_config.blocked_syscalls,
                "block_fork": landlock_config.block_fork,
            }

            # Find wrapper binary
            wrapper_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "bin", "letta_landlock_wrapper.py",
            )
            wrapper_path = os.path.normpath(wrapper_path)

            # Execute via wrapper
            return await self._execute_tool_subprocess(
                sbx_config=sbx_config,
                python_executable=python_executable,
                wrapper_path=wrapper_path,
                wrapper_config=wrapper_config,
                temp_file_path=temp_file_path,
                env=exec_env,
                cwd=exec_dir,
                timeout=landlock_config.timeout,
            )

        except Exception as e:
            logger.exception(f"Executing tool {self.tool_name} in Landlock sandbox has an unexpected error: {e}")
            logger.debug(f"Auto-generated code for debugging:\n\n{code}")
            raise e
        finally:
            from letta.settings import settings

            if not settings.debug:
                await asyncio.to_thread(os.remove, temp_file_path)
                await asyncio.to_thread(shutil.rmtree, exec_dir, True)

    async def _traversal_probe(self, exec_dir: str) -> None:
        """G-4 (W-1 fixed): prove the sandbox uid can traverse to (and read
        from) the exec dir. A named RuntimeError (with the migration
        instruction) on failure; silent pass on success. setpriv absence
        (macOS dev) also passes — the container always has it.

        W-1: the original `await asyncio.to_thread(open, _probe, "w").close()`
        was `await (coroutine.close())` — the coroutine was closed without
        running (TypeError: NoneType can't be awaited), the probe file was
        never created, and the finally's os.remove raised FileNotFoundError
        from OUTSIDE the except clauses. Reproduced and fixed with a lambda
        (the whole open/close runs INSIDE the thread)."""
        import contextlib
        import pwd as _pwd
        import subprocess as _sp

        try:
            _pw = _pwd.getpwnam("letta")
        except KeyError:
            return  # U-5's named refusal fires later in the exec path
        _probe = os.path.join(exec_dir, ".uid_probe")
        try:
            await asyncio.to_thread(lambda: open(_probe, "w").close())
            await asyncio.to_thread(os.chown, _probe, _pw.pw_uid, _pw.pw_gid)
            _r = await asyncio.to_thread(
                _sp.run,
                ["setpriv", "--reuid", str(_pw.pw_uid), "--regid", str(_pw.pw_gid),
                 "--clear-groups", "cat", _probe],
                capture_output=True,
            )
            if _r.returncode != 0:
                raise PermissionError(_r.stderr.decode()[:200] if _r.stderr else "traversal failed")
        except FileNotFoundError:
            return  # setpriv absent (macOS dev)
        except PermissionError as exc:
            raise RuntimeError(
                "Sandbox exec directory is unreachable by the sandbox uid (an old "
                "/root/.letta volume mount? /root is mode 700 — the sandbox uid cannot "
                "traverse it). Move the volume to /data/letta per the startup banner; "
                f"tool execution is disabled until then. Detail: {exc}"
            ) from exc
        finally:
            # W-1: tolerant removal — if the probe file was never created
            # (the binding bug, or an early failure), don't raise from the
            # finally and mask the real error.
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(os.remove, _probe)

    async def _prepare_venv(self, landlock_config, venv_path: str, env: Dict[str, str]):
        """Prepare virtual environment asynchronously (in a background thread).

        B-12 (R-1 fix, Part B review): pip/venv subprocesses NEVER get the tool
        execution env — it carries agent secrets, and it lacks HOME (pip would
        resolve ~ to the server user's home). build_pip_subprocess_env() is the
        allowlist env: no secrets, fresh temp HOME, PIP_* passthrough.
        """
        pip_env = build_pip_subprocess_env()
        if self.force_recreate_venv or not await asyncio.to_thread(os.path.isdir, venv_path):
            sandbox_dir = os.path.expanduser(landlock_config.sandbox_dir)
            log_event(name="start create_venv_for_landlock_sandbox", attributes={"venv_path": venv_path})
            await asyncio.to_thread(
                create_venv_for_local_sandbox,
                sandbox_dir_path=sandbox_dir,
                venv_path=venv_path,
                env=pip_env,
                force_recreate=self.force_recreate_venv,
            )
            log_event(name="finish create_venv_for_landlock_sandbox")

        if landlock_config.pip_requirements or (self.tool and self.tool.pip_requirements):
            log_event(
                name="start install_pip_requirements_for_landlock_sandbox",
                attributes={"landlock_config": landlock_config.model_dump_json()},
            )
            await asyncio.to_thread(
                install_pip_requirements_for_sandbox,
                landlock_config,
                upgrade=True,
                user_install_if_no_venv=False,
                env=pip_env,
                tool=self.tool,
            )
            log_event(
                name="finish install_pip_requirements_for_landlock_sandbox",
                attributes={"landlock_config": landlock_config.model_dump_json()},
            )

    async def _execute_tool_subprocess(
        self,
        sbx_config,
        python_executable: str,
        wrapper_path: str,
        wrapper_config: dict,
        temp_file_path: str,
        env: Dict[str, str],
        cwd: str,
        timeout: int,
    ) -> ToolExecutionResult:
        """Execute user code in a Landlock sandbox subprocess.

        Launches the wrapper script which applies Landlock + seccomp
        restrictions, then execs the tool script. Uses close_fds=True
        to prevent FD leakage to the sandboxed code.
        """
        stdout_text = ""
        try:
            log_event(name="start landlock subprocess")

            # Launch wrapper via asyncio.create_subprocess_exec with close_fds=True
            # The wrapper applies Landlock + seccomp, then execs the tool script.
            #
            # v0.16.41 (S-2, F-5 + U-1/U-2/U-5): the sandbox subprocess runs
            # as the non-root `letta` uid (1000) via user=/group=/extra_groups
            # — applied in C after fork (thread-safe; preexec_fn ran Python
            # between fork and exec, unsafe with the parent's asyncio threads
            # — U-2). extra_groups=[] also drops the server's supplementary
            # groups (root's gid-0 membership kept 640 root:root secrets
            # group-readable — U-1). The server stays root (it owns the
            # Postgres socket dir, the venv, the data). The sandboxed code
            # cannot: read the server's /proc environ, reach the datastore
            # socket dirs, or chmod/chown/truncate server-owned files.
            #
            # U-5: REFUSE, don't fall back. If the image lacks the `letta`
            # user (or we cannot determine the drop), the tool call fails
            # with a named error — never silently runs as root.
            import pwd

            sandbox_uid = None
            sandbox_gid = None
            try:
                pw = pwd.getpwnam("letta")
                sandbox_uid, sandbox_gid = pw.pw_uid, pw.pw_gid
            except KeyError:
                raise RuntimeError(
                    "Sandbox uid unavailable: the 'letta' user does not exist in this "
                    "environment. Sandboxed tool execution requires the separate sandbox "
                    "uid (S-2/U-5); refusing to run tools as the server uid. Use the "
                    "published container image, or create the user (uid 1000) first."
                )
            if os.getuid() == sandbox_uid:
                # Already unprivileged (e.g. the whole server runs as uid 1000):
                # no drop needed, but supplementary groups still get cleared.
                pass

            process = await asyncio.create_subprocess_exec(
                python_executable,
                wrapper_path,
                "--config", json.dumps(wrapper_config),
                "--",
                # -P (Python 3.11+): the script's directory is NOT prepended
                # to sys.path. Combined with the per-exec temp dir this
                # closes the module-hijack hole (L-4): a tool writing json.py
                # next to its script can no longer shadow the real module for
                # itself or anyone else. Pre-3.11 interpreters reject the
                # flag and fail loudly (fail-closed, consistent with the
                # wrapper's ABI gate). Container Python is 3.11 (Dockerfile:
                # uv --python 3.11). NOTE: interpreter flags go AFTER the
                # executable — the wrapper's argv layout is
                # `-- <python> <script> [args...]`; a flag in the executable
                # slot is exec'd as a path (v0.16.37 rig catch: every
                # sandboxed tool died with FileNotFoundError).
                python_executable,
                "-P",
                temp_file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                close_fds=True,  # CRITICAL: prevent FD leakage
                user=sandbox_uid,
                group=sandbox_gid,
                extra_groups=[],  # U-1: no supplementary groups (esp. gid 0)
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # Terminate the process on timeout
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        process.kill()

                raise TimeoutError(
                    f"Executing tool {self.tool_name} timed out after {timeout} seconds."
                )

            stderr = stderr_bytes.decode("utf-8") if stderr_bytes else ""
            log_event(name="finish landlock subprocess")

            # Parse markers to isolate the function result (same protocol as Local sandbox)
            func_result_bytes, stdout_text = self.parse_out_function_results_markers(stdout_bytes)
            func_return, agent_state = parse_stdout_best_effort(func_result_bytes)

            if process.returncode != 0 and func_return is None:
                exception_name, msg = parse_stderr_error_msg(stderr)
                func_return = get_friendly_error_msg(
                    function_name=self.tool_name,
                    exception_name=exception_name,
                    exception_message=msg,
                )

            return ToolExecutionResult(
                func_return=func_return,
                agent_state=agent_state,
                stdout=[stdout_text] if stdout_text else [],
                stderr=[stderr] if stderr else [],
                status="success" if process.returncode == 0 else "error",
                sandbox_config_fingerprint=sbx_config.fingerprint(),
            )

        except (TimeoutError, Exception) as e:
            if isinstance(e, TimeoutError):
                raise e

            logger.exception(f"Landlock subprocess execution for tool {self.tool_name} encountered an error: {e}")
            func_return = get_friendly_error_msg(
                function_name=self.tool_name,
                exception_name=type(e).__name__,
                exception_message=str(e),
            )
            return ToolExecutionResult(
                func_return=func_return,
                agent_state=None,
                stdout=[stdout_text],
                stderr=[str(e)],
                status="error",
                sandbox_config_fingerprint=sbx_config.fingerprint(),
            )

    def parse_out_function_results_markers(self, data: bytes) -> tuple[bytes, str]:
        """Parse the function results out of the stdout using special markers.

        Same protocol as AsyncToolSandboxLocal.
        """
        pos = data.find(self.LOCAL_SANDBOX_RESULT_START_MARKER)
        if pos < 0:
            return b"", data.decode("utf-8") if data else ""

        DATA_LENGTH_INDICATOR = 4
        CHECKSUM_LENGTH = 32
        pos_start = pos + len(self.LOCAL_SANDBOX_RESULT_START_MARKER)
        checksum_start = pos_start + DATA_LENGTH_INDICATOR
        message_start = checksum_start + CHECKSUM_LENGTH

        message_len = struct.unpack(">I", data[pos_start:checksum_start])[0]
        checksum = data[checksum_start:message_start]
        message_data = data[message_start : message_start + message_len]
        actual_checksum = hashlib.md5(message_data).hexdigest().encode("ascii")
        if actual_checksum == checksum:
            remainder = data[:pos] + data[message_start + message_len :]
            return message_data, (remainder.decode("utf-8") if remainder else "")
        raise Exception("Function ran, but output is corrupted.")
