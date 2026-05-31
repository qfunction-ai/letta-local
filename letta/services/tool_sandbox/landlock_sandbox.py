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
    create_venv_for_local_sandbox,
    find_python_executable,
    install_pip_requirements_for_sandbox,
)
from letta.services.helpers.tool_parser_helper import parse_stdout_best_effort
from letta.services.tool_sandbox.base import AsyncToolSandboxBase
from letta.utils import get_friendly_error_msg, parse_stderr_error_msg, safe_create_task

logger = get_logger(__name__)


class AsyncToolSandboxLandlock(AsyncToolSandboxBase):
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

        # Generate and write execution script (always with markers)
        code = await self.generate_execution_script(agent_state=agent_state, wrap_print_with_markers=True)

        async def write_temp_file(dir, content):
            def _write():
                with tempfile.NamedTemporaryFile(mode="w", dir=dir, suffix=".py", delete=False) as temp_file:
                    temp_file.write(content)
                    temp_file.flush()
                    return temp_file.name

            return await asyncio.to_thread(_write)

        temp_file_path = await write_temp_file(sandbox_dir, code)

        try:
            # Wait for venv preparation if started
            if venv_preparation_task:
                await venv_preparation_task

            # Determine the python executable and environment for the subprocess
            exec_env = env.copy()
            if use_venv:
                venv_path = str(os.path.join(sandbox_dir, landlock_config.venv_name))
                python_executable = find_python_executable(landlock_config)
                exec_env["VIRTUAL_ENV"] = venv_path
                exec_env["PATH"] = os.path.join(venv_path, "bin") + ":" + exec_env["PATH"]
            else:
                python_executable = sys.executable
                if "PYTHONPATH" in os.environ:
                    exec_env["PYTHONPATH"] = os.environ["PYTHONPATH"]

            # Handle unwanted terminal behavior
            exec_env.update(
                {
                    "PYTHONWARNINGS": "ignore",
                    "NO_COLOR": "1",
                    "TERM": "dumb",
                    "PYTHONUNBUFFERED": "1",
                }
            )

            # Build wrapper config
            # Compute staging directory for file persistence (agent files)
            staging_write_paths = []
            if self.agent_id:
                try:
                    from letta.settings import file_persistence_settings, settings
                    base = file_persistence_settings.agent_files_dir or os.path.join(str(settings.letta_dir), "agent_files")
                except Exception:
                    base = os.path.expanduser("~/.letta/agent_files")
                staging_dir = os.path.join(base, self.agent_id, ".staging")
                # Ensure staging dir exists so Landlock can grant write access
                os.makedirs(staging_dir, exist_ok=True)
                staging_write_paths = [staging_dir]

            wrapper_config = {
                "allowed_read_paths": landlock_config.allowed_read_paths + [sandbox_dir],
                "allowed_write_paths": landlock_config.allowed_write_paths + [sandbox_dir] + staging_write_paths,
                "allowed_execute_paths": landlock_config.allowed_execute_paths,
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
                cwd=sandbox_dir,
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

    async def _prepare_venv(self, landlock_config, venv_path: str, env: Dict[str, str]):
        """Prepare virtual environment asynchronously (in a background thread)."""
        if self.force_recreate_venv or not await asyncio.to_thread(os.path.isdir, venv_path):
            sandbox_dir = os.path.expanduser(landlock_config.sandbox_dir)
            log_event(name="start create_venv_for_landlock_sandbox", attributes={"venv_path": venv_path})
            await asyncio.to_thread(
                create_venv_for_local_sandbox,
                sandbox_dir_path=sandbox_dir,
                venv_path=venv_path,
                env=env,
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
                env=env,
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
            # The wrapper applies Landlock + seccomp, then execs the tool script
            process = await asyncio.create_subprocess_exec(
                python_executable,
                wrapper_path,
                "--config", json.dumps(wrapper_config),
                "--",
                python_executable, temp_file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                close_fds=True,  # CRITICAL: prevent FD leakage
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
