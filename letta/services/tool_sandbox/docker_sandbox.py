"""Docker-based sandbox for tool execution.

Runs agent tool code in a Docker container with security defaults:
network isolation, resource limits, non-root execution, read-only
rootfs. Network access is opt-in via ``network_mode`` in the config.

Container lifecycle:
- One container per agent run (lazy-created on first tool call)
- Reused across tool calls within the same run
- Cleaned up on run exit, process exit, or orphan reaper

All docker-py calls are wrapped in ``asyncio.to_thread()`` since
docker-py is synchronous.
"""

from __future__ import annotations

import atexit
import hashlib
import io
import os
import tarfile
import time
from typing import Any, Dict, Optional

from letta.log import get_logger
from letta.otel.tracing import trace_method
from letta.schemas.agent import AgentState
from letta.schemas.enums import SandboxType
from letta.schemas.sandbox_config import DockerSandboxConfig, SandboxConfig
from letta.schemas.tool import Tool
from letta.schemas.tool_execution_result import ToolExecutionResult
from letta.services.helpers.tool_parser_helper import parse_stdout_best_effort
from letta.services.tool_sandbox.base import AsyncToolSandboxBase
from letta.settings import tool_settings
from letta.types import JsonDict
from letta.utils import get_friendly_error_msg, parse_stderr_error_msg

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level container cache (per run_id)
# ---------------------------------------------------------------------------

_container_cache: Dict[str, Any] = {}  # run_id -> docker.Container
_docker_client: Any = None  # cached DockerClient instance

# Maximum size of the execution script payload for put_archive.
# Prevents silently choking on tar + transfer overhead for very large inputs.
_PUT_ARCHIVE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _get_docker_client() -> Any:
    """Get or create the Docker client (cached)."""
    global _docker_client
    if _docker_client is None:
        import docker
        _docker_client = docker.from_env()
    return _docker_client


def _reap_orphan_containers() -> None:
    """Kill containers from previous runs that are no longer in the cache.

    Called on module initialization. Lists containers with the label
    ``letta-sandbox=1`` and removes any whose run_id isn't in the
    active ``_container_cache``.
    """
    try:
        client = _get_docker_client()
        orphans = client.containers.list(
            filters={"label": "letta-sandbox=1", "status": "running"},
            all=False,
        )
        for c in orphans:
            c_name = c.name  # e.g. "letta-sandbox-<agent_id>-<run_id>"
            # Check if this container's run_id is in our cache
            in_cache = any(c_name.endswith(run_id[:8]) for run_id in _container_cache)
            if not in_cache:
                logger.info("Reaping orphan container: %s", c_name)
                try:
                    c.stop(timeout=5)
                except Exception:
                    pass
                try:
                    c.remove(force=True)
                except Exception:
                    pass
    except Exception as e:
        logger.debug("Orphan reaper failed (Docker may not be available): %s", e)


def _cleanup_all_containers() -> None:
    """Stop and remove all containers in the cache. Called at process exit."""
    for run_id, container in list(_container_cache.items()):
        try:
            container.stop(timeout=5)
            container.remove(force=True)
        except Exception:
            pass
    _container_cache.clear()


# Register cleanup on process exit
atexit.register(_cleanup_all_containers)


# ---------------------------------------------------------------------------
# Docker sandbox implementation
# ---------------------------------------------------------------------------


class AsyncToolSandboxDocker(AsyncToolSandboxBase):
    """Docker-based tool sandbox.

    Runs tool code in a Docker container with security defaults:
    - ``network_mode="none"`` (no network by default)
    - ``user="1001:1001"`` (non-root)
    - ``read_only=True`` (read-only rootfs)
    - ``cap_drop=["ALL"]`` + ``no-new-privileges``
    - ``mem_limit="512m"``, ``pids_limit=100``, ``cpu_count=1.0``

    Container lifecycle is per-run: one container is created on the
    first tool call and reused for subsequent calls within the same
    run. The container is stopped and removed when the run ends.
    """

    def __init__(
        self,
        tool_name: str,
        args: JsonDict,
        user,
        tool_id: str,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
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
        self._run_id: Optional[str] = None

    @trace_method
    async def run(
        self,
        agent_state: Optional[AgentState] = None,
        additional_env_vars: Optional[Dict] = None,
    ) -> ToolExecutionResult:
        """Run the tool in a Docker container."""
        import docker as _docker

        await self._init_async()

        # Fetch sandbox config
        if self.provided_sandbox_config:
            sbx_config = self.provided_sandbox_config
        else:
            sbx_config = await self.sandbox_config_manager.get_or_create_default_sandbox_config_async(
                sandbox_type=SandboxType.DOCKER, actor=self.user
            )
        docker_config = sbx_config.get_docker_config()

        # Determine run_id for container lifecycle
        self._run_id = os.environ.get("LETTA_RUN_ID", self.agent_id or "unknown")

        # Gather environment variables
        env = await self._gather_env_vars(agent_state, additional_env_vars, sbx_config.id, is_local=False)

        # Get or create container for this run
        container = await self._get_or_create_container(docker_config, env)

        # Generate the execution script
        code = await self.generate_execution_script(agent_state=agent_state, wrap_print_with_markers=True)

        # Check payload size
        code_bytes = code.encode("utf-8")
        if len(code_bytes) > _PUT_ARCHIVE_MAX_BYTES:
            raise ValueError(
                f"Execution script payload ({len(code_bytes)} bytes) exceeds "
                f"put_archive size limit ({_PUT_ARCHIVE_MAX_BYTES} bytes). "
                f"Reduce tool input size or increase _PUT_ARCHIVE_MAX_BYTES."
            )

        # Copy script into the container
        await asyncio.to_thread(self._put_script, container, code_bytes)

        # Execute the script
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                self._exec_script(container, docker_config),
                timeout=docker_config.timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Executing tool {self.tool_name} in Docker timed out after {docker_config.timeout} seconds."
            )

        # Parse results
        func_result_bytes, stdout_text = self.parse_out_function_results_markers(stdout_bytes)
        func_return, agent_state_result = parse_stdout_best_effort(func_result_bytes)

        stderr = stderr_bytes.decode("utf-8") if stderr_bytes else ""

        if func_return is None and stderr:
            exception_name, msg = parse_stderr_error_msg(stderr)
            func_return = get_friendly_error_msg(
                function_name=self.tool_name,
                exception_name=exception_name,
                exception_message=msg,
            )

        return ToolExecutionResult(
            func_return=func_return,
            agent_state=agent_state_result,
            stdout=[stdout_text] if stdout_text else [],
            stderr=[stderr] if stderr else [],
            status="success" if func_return is not None and not stderr else "error",
            sandbox_config_fingerprint=sbx_config.fingerprint(),
        )

    async def _get_or_create_container(self, config: DockerSandboxConfig, env: Dict[str, str]) -> Any:
        """Get existing container for this run, or create a new one."""
        global _container_cache

        run_id = self._run_id
        if run_id in _container_cache:
            container = _container_cache[run_id]
            try:
                await asyncio.to_thread(container.reload)
                if container.status == "running":
                    return container
            except Exception:
                # Container died, remove from cache
                del _container_cache[run_id]

        # Create new container
        container = await asyncio.to_thread(
            self._create_container, config, env
        )
        _container_cache[run_id] = container
        return container

    def _create_container(self, config: DockerSandboxConfig, env: Dict[str, str]) -> Any:
        """Create a new Docker container for the sandbox."""
        client = _get_docker_client()

        container_name = f"letta-sandbox-{(self.agent_id or 'noxid')[:12]}-{self._run_id[:8]}"

        try:
            # Remove existing container with the same name (from a previous crash)
            existing = client.containers.get(container_name)
            existing.remove(force=True)
        except Exception:
            pass

        container = client.containers.run(
            image=config.image,
            command="sleep infinity",  # keep container alive for the session
            detach=True,
            name=container_name,
            user=config.user,
            network_mode=config.network_mode,
            read_only=config.read_only,
            mem_limit=config.mem_limit,
            memswap_limit=config.mem_limit,  # no swap beyond mem_limit
            cpu_count=config.cpu_count,
            pids_limit=config.pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            tmpfs={"/tmp": f"size={config.tmpfs_size},exec"},
            environment=env,
            labels={"letta-sandbox": "1", "letta-run-id": self._run_id},
            auto_remove=False,
        )

        logger.info("Created Docker sandbox container: %s (image=%s, network=%s)", container_name, config.image, config.network_mode)

        # Install pip requirements if provided
        if config.pip_requirements:
            pip_install_cmd = f"pip install --no-cache-dir {' '.join(config.pip_requirements)}"
            logger.info("Installing pip requirements in container %s: %s", container_name, config.pip_requirements)
            exit_code, output = container.exec_run(
                cmd=["/bin/bash", "-c", pip_install_cmd],
                workdir="/tmp",
                demux=True,
            )
            if exit_code != 0:
                stderr = output[1].decode("utf-8") if output[1] else ""
                logger.warning("pip install failed in container %s: %s", container_name, stderr)

        return container

    def _put_script(self, container: Any, code_bytes: bytes) -> None:
        """Copy the execution script into the container via tar archive."""
        script_name = "letta_tool_exec.py"

        # Create a tar archive in memory
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=script_name)
            info.size = len(code_bytes)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(code_bytes))
        tar_bytes = tar_buffer.getvalue()

        # Copy into container at /tmp/
        container.put_archive("/tmp/", tar_bytes)

    async def _exec_script(self, container: Any, config: DockerSandboxConfig) -> tuple[bytes, bytes]:
        """Execute the script inside the container and return (stdout, stderr)."""
        import sys

        python_executable = "python3"  # container has python3 via the Dockerfile

        def _run():
            exit_code, output = container.exec_run(
                cmd=[python_executable, "/tmp/letta_tool_exec.py"],
                workdir="/tmp",
                demux=True,
            )
            stdout = output[0] if output[0] else b""
            stderr = output[1] if output[1] else b""
            return stdout, stderr

        return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Import asyncio at module level for the class methods
# ---------------------------------------------------------------------------
import asyncio

# Run orphan reaper on module import
try:
    _reap_orphan_containers()
except Exception:
    pass  # Docker may not be available
