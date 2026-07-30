import importlib
import inspect
import os
import pickle
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from letta.constants import LETTA_FILE_PERSISTENCE_TOOL_MODULE_NAME, LETTA_MULTI_AGENT_TOOL_MODULE_NAME
from letta.functions.helpers import generate_model_from_args_json_schema
from letta.log import get_logger
from letta.otel.tracing import trace_method
from letta.schemas.agent import AgentState
from letta.schemas.enums import ToolSourceType, ToolType
from letta.schemas.sandbox_config import SandboxConfig
from letta.schemas.tool import Tool
from letta.schemas.tool_execution_result import ToolExecutionResult
from letta.services.helpers.tool_execution_helper import add_imports_and_pydantic_schemas_for_args
from letta.services.helpers.tool_parser_helper import convert_param_to_str_value, parse_function_arguments
from letta.services.sandbox_config_manager import SandboxConfigManager
from letta.services.tool_manager import ToolManager
from letta.types import JsonDict, JsonValue

logger = get_logger(__name__)


class AsyncToolSandboxBase(ABC):
    NAMESPACE = uuid.NAMESPACE_DNS
    LOCAL_SANDBOX_RESULT_START_MARKER = uuid.uuid5(NAMESPACE, "local-sandbox-result-start-marker").bytes
    LOCAL_SANDBOX_RESULT_VAR_NAME = "result_ZQqiequkcFwRwwGQMqkt"

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
        self.tool_name = tool_name
        self.args = args
        self.user = user
        self.agent_id = agent_id
        self.project_id = project_id
        self.tool_id = tool_id
        self.tool = tool_object

        # Store provided values or create manager to fetch them later
        self.provided_sandbox_config = sandbox_config
        self.provided_sandbox_env_vars = sandbox_env_vars

        # Only create the manager if we need to (lazy initialization)
        self._sandbox_config_manager = None

        self._initialized = False

    async def _init_async(self):
        """Must be called inside the run method before the sandbox can be used"""
        if not self._initialized:
            if not self.tool:
                self.tool = await ToolManager().get_tool_by_name_async(tool_name=self.tool_name, actor=self.user)

            # missing tool
            if self.tool is None:
                raise ValueError(
                    f"Agent attempted to invoke tool {self.tool_name} that does not exist for organization {self.user.organization_id}"
                )

            if not self.tool.source_code and self.tool.tool_type == ToolType.LETTA_MULTI_AGENT_CORE:
                module = importlib.import_module(LETTA_MULTI_AGENT_TOOL_MODULE_NAME)
                self.tool.source_code = inspect.getsource(module)
            elif not self.tool.source_code and self.tool.tool_type == ToolType.LETTA_FILE_PERSISTENCE_CORE:
                module = importlib.import_module(LETTA_FILE_PERSISTENCE_TOOL_MODULE_NAME)
                self.tool.source_code = inspect.getsource(module)

            # TypeScript tools do not support agent_state or agent_id injection as function params
            # (these are Python-only features). Instead, agent_id is exposed via LETTA_AGENT_ID env var.
            if self.is_typescript_tool():
                self.inject_agent_state = False
                self.inject_agent_id = False
            else:
                # Check for reserved keyword arguments (Python tools only)
                tool_arguments = parse_function_arguments(self.tool.source_code, self.tool.name)

                # TODO: deprecate this
                # Note: AgentState injection is a legacy feature for Python tools only.
                if "agent_state" in tool_arguments:
                    self.inject_agent_state = True
                else:
                    self.inject_agent_state = False

                self.inject_agent_id = "agent_id" in tool_arguments

            # Always inject Letta client (available as `client` variable in sandbox)
            # For Python: letta_client package
            # For TypeScript: @letta-ai/letta-client package
            self.inject_letta_client = True

            self.is_async_function = self._detect_async_function()
        self._initialized = True

    # Lazily initialize the manager only when needed
    @property
    def sandbox_config_manager(self):
        if self._sandbox_config_manager is None:
            self._sandbox_config_manager = SandboxConfigManager()
        return self._sandbox_config_manager

    @abstractmethod
    async def run(
        self,
        agent_state: Optional[AgentState] = None,
        additional_env_vars: Optional[Dict] = None,
    ) -> ToolExecutionResult:
        """
        Run the tool in a sandbox environment asynchronously.
        Must be implemented by subclasses.
        """
        raise NotImplementedError

    async def promote_staging_files(self, agent_state: Optional[AgentState] = None) -> None:
        """After tool execution, move validated files from .staging/ to agent files dir.

        Sandbox tools that write large output (e.g., query results) can write
        to LETTA_STAGING_DIR instead of returning everything in the tool result.
        This method validates the staged files (size limits, path safety) and
        moves them to the agent's persistent file directory.

        Called by sandbox_tool_executor.py after sandbox.run() returns,
        before the result is returned to the agent. This keeps the promotion
        sandbox-agnostic — any sandbox type that writes to .staging/ gets
        the same promotion.
        """
        if not self.agent_id:
            return

        # Compute paths
        try:
            from letta.settings import file_persistence_settings, settings
            base = file_persistence_settings.agent_files_dir or os.path.join(str(settings.letta_dir), "agent_files")
        except Exception:
            base = os.path.expanduser("~/.letta/agent_files")

        staging_dir = os.path.join(base, self.agent_id, ".staging")
        agent_files_dir = os.path.join(base, self.agent_id)

        if not os.path.isdir(staging_dir):
            return

        # Read size limits (same as file_write, but with higher per-file default)
        _DEFAULT_MAX_FILE_SIZE = 10_000_000  # 10MB (security logs can be large)
        _DEFAULT_MAX_TOTAL_SIZE = 50_000_000  # 50MB
        try:
            from letta.settings import file_persistence_settings as fps
            max_file_size = fps.max_file_size_bytes or _DEFAULT_MAX_FILE_SIZE
            max_total_size = fps.max_total_size_bytes or _DEFAULT_MAX_TOTAL_SIZE
        except Exception:
            max_file_size = _DEFAULT_MAX_FILE_SIZE
            max_total_size = _DEFAULT_MAX_TOTAL_SIZE

        # Compute current total size of agent files dir
        current_total = 0
        for dirpath, dirnames, filenames in os.walk(agent_files_dir):
            # Skip .staging when computing current total
            if ".staging" in dirnames:
                dirnames.remove(".staging")
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    current_total += os.path.getsize(fpath)
                except OSError:
                    pass

        promoted = 0
        rejected = 0

        for dirpath, dirnames, filenames in os.walk(staging_dir):
            for fname in filenames:
                src_path = os.path.join(dirpath, fname)

                # Compute relative path from staging_dir
                rel_path = os.path.relpath(src_path, staging_dir)

                # Validate path (no .., no absolute, no null bytes)
                if not rel_path or "\0" in rel_path or rel_path.startswith("/") or ".." in rel_path.split(os.sep):
                    logger.warning(f"Staging file rejected (invalid path): {rel_path}")
                    rejected += 1
                    continue

                # Validate resolved path doesn't escape agent files dir
                dst_path = os.path.join(agent_files_dir, rel_path)
                dst_resolved = os.path.realpath(dst_path)
                agent_resolved = os.path.realpath(agent_files_dir)
                if not dst_resolved.startswith(agent_resolved + os.sep) and dst_resolved != agent_resolved:
                    logger.warning(f"Staging file rejected (path escape): {rel_path}")
                    rejected += 1
                    continue

                # Validate per-file size
                try:
                    file_size = os.path.getsize(src_path)
                except OSError:
                    logger.warning(f"Staging file rejected (cannot stat): {rel_path}")
                    rejected += 1
                    continue

                if file_size > max_file_size:
                    logger.warning(
                        f"Staging file rejected (too large: {file_size} > {max_file_size}): {rel_path}"
                    )
                    rejected += 1
                    continue

                # Validate total size after adding
                if current_total + file_size > max_total_size:
                    logger.warning(
                        f"Staging file rejected (total would exceed {max_total_size}): {rel_path}"
                    )
                    rejected += 1
                    continue

                # Move file to agent files dir
                try:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    os.rename(src_path, dst_path)
                    current_total += file_size
                    promoted += 1
                except OSError as e:
                    logger.warning(f"Failed to promote staging file {rel_path}: {e}")
                    rejected += 1

        # Clean up staging dir (remove any remaining files)
        try:
            import shutil
            shutil.rmtree(staging_dir, ignore_errors=True)
        except Exception:
            pass

        if promoted or rejected:
            logger.info(
                f"Staging promotion for agent {self.agent_id}: "
                f"{promoted} promoted, {rejected} rejected"
            )

    def is_typescript_tool(self) -> bool:
        """Check if the tool is a TypeScript tool based on source_type."""
        if self.tool and self.tool.source_type:
            return self.tool.source_type == ToolSourceType.typescript or self.tool.source_type == "typescript"
        return False

    @trace_method
    async def generate_execution_script(self, agent_state: Optional[AgentState], wrap_print_with_markers: bool = False) -> str:
        """
        Generate code to run inside of execution sandbox. Serialize the agent state and arguments, call the tool,
        then base64-encode/pickle the result. Constructs the python file (or TypeScript for TS tools).
        """
        await self._init_async()

        # Route to TypeScript generator for TypeScript tools
        if self.is_typescript_tool():
            return await self._generate_typescript_execution_script(agent_state)
        future_import = False
        schema_code = None

        if self.tool.args_json_schema:
            # Add schema code if available
            schema_code = add_imports_and_pydantic_schemas_for_args(self.tool.args_json_schema)
            if "from __future__ import annotations" in schema_code:
                schema_code = schema_code.replace("from __future__ import annotations", "").lstrip()
                future_import = True

            # Initialize arguments
            args_schema = generate_model_from_args_json_schema(self.tool.args_json_schema)
            tool_args = f"args_object = {args_schema.__name__}(**{self.args})\n"
            for param in self.args:
                tool_args += f"{param} = args_object.{param}\n"
        else:
            tool_args = ""
            for param in self.args:
                tool_args += self.initialize_param(param, self.args[param])

        agent_state_pickle = pickle.dumps(agent_state) if self.inject_agent_state else None
        agent_id = agent_state.id if agent_state else None

        code = self._render_sandbox_code(
            future_import=future_import,
            inject_agent_state=self.inject_agent_state,
            inject_letta_client=self.inject_letta_client,
            inject_agent_id=self.inject_agent_id,
            schema_imports=schema_code or "",
            agent_state_pickle=agent_state_pickle,
            agent_id=agent_id,
            tool_args=tool_args,
            tool_source_code=self.tool.source_code,
            local_sandbox_result_var_name=self.LOCAL_SANDBOX_RESULT_VAR_NAME,
            invoke_function_call=self.invoke_function_call(),
            wrap_print_with_markers=wrap_print_with_markers,
            start_marker=self.LOCAL_SANDBOX_RESULT_START_MARKER,
            use_top_level_await=self.use_top_level_await(),
        )
        return code

    def _render_sandbox_code(
        self,
        *,
        future_import: bool,
        inject_agent_state: bool,
        inject_letta_client: bool,
        inject_agent_id: bool,
        schema_imports: str,
        agent_state_pickle: bytes | None,
        agent_id: str | None,
        tool_args: str,
        tool_source_code: str,
        local_sandbox_result_var_name: str,
        invoke_function_call: str,
        wrap_print_with_markers: bool,
        start_marker: bytes,
        use_top_level_await: bool,
    ) -> str:
        lines: list[str] = []
        if future_import:
            lines.append("from __future__ import annotations")
        lines.extend(
            [
                "from typing import *",
                "import pickle",
                "import json as _letta_json",
                "import sys",
                "import base64",
                "import struct",
                "import hashlib",
            ]
        )
        if self.is_async_function:
            lines.append("import asyncio")

        if inject_agent_state:
            lines.extend(["import letta", "from letta import *"])

        # Import Letta client if available (wrapped in try/except for sandboxes without letta_client installed)
        if inject_letta_client:
            lines.extend(
                [
                    "try:",
                    "    from letta_client import Letta",
                    "except ImportError:",
                    "    Letta = None",
                ]
            )

        if schema_imports:
            lines.append(schema_imports.rstrip())

        if agent_state_pickle is not None:
            lines.append(f"agent_state = pickle.loads({repr(agent_state_pickle)})")
        else:
            lines.append("agent_state = None")

        # Initialize Letta client if needed (client is always available as a variable, may be None)
        if inject_letta_client:
            lines.extend(
                [
                    "# Initialize Letta client for tool execution",
                    "import os",
                    "client = None",
                    "if Letta is not None and os.getenv('LETTA_API_KEY'):",
                    "    # Check letta_client version to use correct parameter name",
                    "    from packaging import version as pkg_version",
                    "    import letta_client as lc_module",
                    "    lc_version = pkg_version.parse(lc_module.__version__)",
                    "    if lc_version < pkg_version.parse('1.0.0'):",
                    "        client = Letta(",
                    "            token=os.getenv('LETTA_API_KEY')",
                    "        )",
                    "    else:",
                    "        client = Letta(",
                    "            api_key=os.getenv('LETTA_API_KEY')",
                    "        )",
                ]
            )

        # Set agent_id if needed
        if inject_agent_id:
            if agent_id:
                lines.append(f"agent_id = {repr(agent_id)}")
            else:
                lines.append("agent_id = None")

        if tool_args:
            lines.append(tool_args.rstrip())

        if tool_source_code:
            lines.append(tool_source_code.rstrip())

        if self.args:
            raw_args = ", ".join([f"{name!r}: {name}" for name in self.args])
            lines.extend(
                [
                    f"__letta_raw_args = {{{raw_args}}}",
                    "try:",
                    "    from letta.functions.ast_parsers import coerce_dict_args_by_annotations",
                    f"    __letta_func = {self.tool.name}",
                    "    __letta_annotations = getattr(__letta_func, '__annotations__', {})",
                    "    __letta_coerced_args = coerce_dict_args_by_annotations(",
                    "        __letta_raw_args,",
                    "        __letta_annotations,",
                    "        allow_unsafe_eval=True,",
                    "        extra_globals=__letta_func.__globals__,",
                    "    )",
                ]
            )
            for name in self.args:
                lines.append(f"    {name} = __letta_coerced_args.get({name!r}, {name})")
            lines.extend(["except Exception:", "    pass"])

        if not self.is_async_function:
            # sync variant
            lines.append(f"_function_result = {invoke_function_call}")
            lines.extend(
                [
                    "try:",
                    "    from pydantic import BaseModel, ConfigDict",
                    "    from typing import Any",
                    "",
                    "    class _TempResultWrapper(BaseModel):",
                    "        model_config = ConfigDict(arbitrary_types_allowed=True)",
                    "        result: Any",
                    "",
                    "    _wrapped = _TempResultWrapper(result=_function_result)",
                    "    _serialized_result = _wrapped.model_dump()['result']",
                    "except ImportError:",
                    '    print("Pydantic not available in sandbox environment, falling back to string conversion")',
                    "    _serialized_result = str(_function_result)",
                    "except Exception as e:",
                    '    print(f"Failed to serialize result with Pydantic wrapper: {e}")',
                    "    _serialized_result = str(_function_result)",
                    "",
                    "if agent_state is not None:",
                    "    try:",
                    "        _letta_agent_state_payload = agent_state.model_dump(mode='json')",
                    "    except Exception:",
                    "        _letta_agent_state_payload = None",
                    "else:",
                    "    _letta_agent_state_payload = None",
                    f"{local_sandbox_result_var_name} = {{",
                    '    "results": _serialized_result,',
                    '    "agent_state": _letta_agent_state_payload',
                    "}",
                    f"{local_sandbox_result_var_name}_pkl = _letta_json.dumps({local_sandbox_result_var_name}, default=str).encode('utf-8')",
                ]
            )
        else:
            # async variant
            lines.extend(
                [
                    "async def _async_wrapper():",
                    f"    _function_result = await {invoke_function_call}",
                    "    try:",
                    "        from pydantic import BaseModel, ConfigDict",
                    "        from typing import Any",
                    "",
                    "        class _TempResultWrapper(BaseModel):",
                    "            model_config = ConfigDict(arbitrary_types_allowed=True)",
                    "            result: Any",
                    "",
                    "        _wrapped = _TempResultWrapper(result=_function_result)",
                    "        _serialized_result = _wrapped.model_dump()['result']",
                    "    except ImportError:",
                    '        print("Pydantic not available in sandbox environment, falling back to string conversion")',
                    "        _serialized_result = str(_function_result)",
                    "    except Exception as e:",
                    '        print(f"Failed to serialize result with Pydantic wrapper: {e}")',
                    "        _serialized_result = str(_function_result)",
                    "",
                    "    if agent_state is not None:",
                    "        try:",
                    "            _letta_agent_state_payload = agent_state.model_dump(mode='json')",
                    "        except Exception:",
                    "            _letta_agent_state_payload = None",
                    "    else:",
                    "        _letta_agent_state_payload = None",
                    "    return {",
                    '        "results": _serialized_result,',
                    '        "agent_state": _letta_agent_state_payload',
                    "    }",
                ]
            )
            if use_top_level_await:
                lines.append(f"{local_sandbox_result_var_name} = await _async_wrapper()")
            else:
                lines.append(f"{local_sandbox_result_var_name} = asyncio.run(_async_wrapper())")
            lines.append(
                f"{local_sandbox_result_var_name}_pkl = _letta_json.dumps({local_sandbox_result_var_name}, default=str).encode('utf-8')"
            )

        if wrap_print_with_markers:
            lines.extend(
                [
                    f"data_checksum = hashlib.md5({local_sandbox_result_var_name}_pkl).hexdigest().encode('ascii')",
                    f"{local_sandbox_result_var_name}_msg = (",
                    f"  {repr(start_marker)} +",
                    f"  struct.pack('>I', len({local_sandbox_result_var_name}_pkl)) +",
                    "  data_checksum +",
                    f"  {local_sandbox_result_var_name}_pkl",
                    ")",
                    f"sys.stdout.buffer.write({local_sandbox_result_var_name}_msg)",
                    "sys.stdout.buffer.flush()",
                ]
            )
        else:
            lines.append(f"base64.b64encode({local_sandbox_result_var_name}_pkl).decode('utf-8')")

        return "\n".join(lines) + "\n"

    async def _generate_typescript_execution_script(self, agent_state: Optional[AgentState]) -> str:
        """
        Generate TypeScript code to run inside of execution sandbox.

        TypeScript tools:
        - Do NOT support agent_state injection (stateless)
        - agent_id is available via process.env.LETTA_AGENT_ID
        - Return JSON-serialized results instead of pickle
        - Require explicit json_schema (no docstring parsing)
        """
        from letta.services.tool_sandbox.typescript_generator import generate_typescript_execution_script

        return generate_typescript_execution_script(
            tool_name=self.tool.name,
            tool_source_code=self.tool.source_code,
            args=self.args,
            json_schema=self.tool.json_schema,
        )

    def initialize_param(self, name: str, raw_value: JsonValue) -> str:
        """
        Produce code for initializing a single parameter in the generated script.
        """
        params = self.tool.json_schema["parameters"]["properties"]
        spec = params.get(name)
        if spec is None:
            # Possibly an extra param like 'self' that we ignore
            return ""

        param_type = spec.get("type")
        if param_type is None and spec.get("parameters"):
            param_type = spec["parameters"].get("type")

        value = convert_param_to_str_value(param_type, raw_value)
        return f"{name} = {value}\n"

    def invoke_function_call(self) -> str:
        """
        Generate the function call code string with the appropriate arguments.
        """
        kwargs = []
        for name in self.args:
            if name in self.tool.json_schema["parameters"]["properties"]:
                kwargs.append(name)

        param_list = [f"{arg}={arg}" for arg in kwargs]

        # Add reserved keyword arguments
        if self.inject_agent_state:
            param_list.append("agent_state=agent_state")

        # Note: client is always available as a variable in the sandbox scope
        # Tools should access it directly rather than receiving it as a parameter

        if self.inject_agent_id:
            param_list.append("agent_id=agent_id")

        params = ", ".join(param_list)
        func_call_str = self.tool.name + "(" + params + ")"
        return func_call_str

    def _detect_async_function(self) -> bool:
        """
        Detect if the tool function is an async function by examining its source code.
        Uses AST parsing to reliably detect 'async def' declarations.
        """
        import ast

        try:
            tree = ast.parse(self.tool.source_code)

            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef) and node.name == self.tool.name:
                    return True
            return False
        except Exception:
            return False

    def use_top_level_await(self) -> bool:
        """
        Determine if this sandbox environment supports top-level await.
        Should be overridden by subclasses to return True for environments
        with running event loops (like E2B), False for local execution.
        """
        return False  # Default to False for local execution

    async def _gather_env_vars(
        self, agent_state: AgentState | None, additional_env_vars: dict[str, str] | None, sbx_id: str, is_local: bool
    ):
        """
        Gather environment variables with proper layering:
        1. OS environment (for local sandboxes only)
        2. Global sandbox env vars from DB (always included)
        3. Provided sandbox env vars (agent-scoped, override global on key collision)
        4. Agent state env vars
        5. Additional runtime env vars (highest priority)
        """
        env = os.environ.copy() if is_local else {}

        # Always fetch and include global sandbox env vars from DB
        global_env_vars = await self.sandbox_config_manager.get_sandbox_env_vars_as_dict_async(
            sandbox_config_id=sbx_id, actor=self.user, limit=None
        )
        env.update(global_env_vars)

        # Override with provided sandbox env vars
        if self.provided_sandbox_env_vars:
            env.update(self.provided_sandbox_env_vars)

        # TOOD: may be duplicative with provided sandbox env vars above
        # Override with agent state env vars
        if agent_state:
            env.update(agent_state.get_agent_env_vars_as_dict())

        # Override with additional runtime env vars (highest priority)
        if additional_env_vars:
            env.update(additional_env_vars)

        # Inject agent, project, and tool IDs as environment variables
        if self.agent_id:
            env["LETTA_AGENT_ID"] = self.agent_id
            # Compute agent files directory and staging directory
            try:
                from letta.settings import file_persistence_settings, settings
                base = file_persistence_settings.agent_files_dir or os.path.join(str(settings.letta_dir), "agent_files")
            except Exception:
                base = os.path.expanduser("~/.letta/agent_files")
            agent_files_dir = os.path.join(base, self.agent_id)
            env["LETTA_AGENT_FILES_DIR"] = agent_files_dir
            env["LETTA_STAGING_DIR"] = os.path.join(agent_files_dir, ".staging")
        if self.project_id:
            env["LETTA_PROJECT_ID"] = self.project_id
        env["LETTA_TOOL_ID"] = self.tool_id

        # Filter out None values to prevent subprocess errors
        env = {k: v for k, v in env.items() if v is not None}

        return env
