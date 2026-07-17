import hashlib
import json
import os
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator

from letta.constants import LETTA_TOOL_EXECUTION_DIR, MODAL_DEFAULT_TIMEOUT
from letta.schemas.agent import AgentState
from letta.schemas.enums import PrimitiveType, SandboxType
from letta.schemas.letta_base import LettaBase, OrmMetadataBase
from letta.schemas.pip_requirement import PipRequirement
from letta.settings import tool_settings

# Sandbox Config


class SandboxRunResult(BaseModel):
    func_return: Optional[Any] = Field(None, description="The function return object")
    agent_state: Optional[AgentState] = Field(None, description="The agent state")
    stdout: Optional[List[str]] = Field(None, description="Captured stdout (e.g. prints, logs) from the function invocation")
    stderr: Optional[List[str]] = Field(None, description="Captured stderr from the function invocation")
    status: Literal["success", "error"] = Field(..., description="The status of the tool execution and return object")
    sandbox_config_fingerprint: str = Field(None, description="The fingerprint of the config for the sandbox")


class LocalSandboxConfig(BaseModel):
    sandbox_dir: Optional[str] = Field(None, description="Directory for the sandbox environment.")
    use_venv: bool = Field(False, description="Whether or not to use the venv, or run directly in the same run loop.")
    venv_name: str = Field(
        "venv",
        description="The name for the venv in the sandbox directory. We first search for an existing venv with this name, otherwise, we make it from the requirements.txt.",
    )
    pip_requirements: List[PipRequirement] = Field(
        default_factory=list,
        description="List of pip packages to install with mandatory name and optional version following semantic versioning. This only is considered when use_venv is True.",
    )

    @property
    def type(self) -> "SandboxType":
        return SandboxType.LOCAL

    @model_validator(mode="before")
    @classmethod
    def set_default_sandbox_dir(cls, data):
        # If `data` is not a dict (e.g., it's another Pydantic model), just return it
        if not isinstance(data, dict):
            return data

        if data.get("sandbox_dir") is None:
            if tool_settings.tool_exec_dir:
                data["sandbox_dir"] = tool_settings.tool_exec_dir
            else:
                data["sandbox_dir"] = LETTA_TOOL_EXECUTION_DIR

        return data


class E2BSandboxConfig(BaseModel):
    timeout: int = Field(5 * 60, description="Time limit for the sandbox (in seconds).")
    template: Optional[str] = Field(None, description="The E2B template id (docker image).")
    pip_requirements: Optional[List[str]] = Field(None, description="A list of pip packages to install on the E2B Sandbox")

    @property
    def type(self) -> "SandboxType":
        return SandboxType.E2B

    @model_validator(mode="before")
    @classmethod
    def set_default_template(cls, data: dict):
        """
        Assign a default template value if the template field is not provided.
        """
        # If `data` is not a dict (e.g., it's another Pydantic model), just return it
        if not isinstance(data, dict):
            return data

        if data.get("template") is None:
            data["template"] = tool_settings.e2b_sandbox_template_id
        return data


class ModalSandboxConfig(BaseModel):
    timeout: int = Field(MODAL_DEFAULT_TIMEOUT, description="Time limit for the sandbox (in seconds).")
    pip_requirements: list[str] | None = Field(None, description="A list of pip packages to install in the Modal sandbox")
    npm_requirements: list[str] | None = Field(None, description="A list of npm packages to install in the Modal sandbox")
    language: Literal["python", "typescript"] = "python"

    @property
    def type(self) -> "SandboxType":
        return SandboxType.MODAL


class LandlockSandboxConfig(BaseModel):
    """Configuration for Landlock + seccomp-BPF sandbox.

    Provides kernel-level filesystem and network isolation
    for tool execution subprocesses. Works inside Docker
    Desktop containers with zero extra flags.

    CRITICAL: The wrapper handles ALL Landlock access rights for the
    detected ABI version. Any right NOT included in handled_access_fs
    is ALLOWED by default. The wrapper ensures no right is silently
    allowed by omission.
    """
    # Filesystem access
    allowed_read_paths: List[str] = Field(
        default_factory=lambda: ["/usr", "/lib", "/lib64", "/etc", "/app", "/extra-packages", "/data/config"],
        description="Paths allowed for read access (recursively). Includes /app for the Python venv and /extra-packages for pip-sidecar packages. /data/config for Delta's read-only config volume.",
    )
    allowed_write_paths: List[str] = Field(
        default_factory=lambda: [],  # Set dynamically from tool_exec_dir
        description="Paths allowed for write access (recursively).",
    )
    allowed_execute_paths: List[str] = Field(
        default_factory=lambda: ["/usr/bin", "/usr/local/bin", "/lib", "/app/.venv/bin"],
        description="Paths allowed for execution (recursively). /lib must be included for the ELF dynamic linker (ld-linux-aarch64.so.1 on ARM, ld-linux-x86-64.so.2 on x86_64). /app/.venv/bin for the Python venv executable.",
    )

    # Network access (ABI v4+, kernel 6.7+)
    allow_tcp_connect: bool = Field(
        False,
        description="Allow outbound TCP connections. Default: deny. Must be explicitly enabled for tools that call external APIs. Requires Landlock ABI >= 4. NOTE: also controls seccomp network syscall filtering — if False, network syscalls are blocked even when Landlock net rules are unavailable.",
    )
    allow_tcp_bind: bool = Field(
        False,
        description="Allow TCP bind (listen). Default: deny. Requires Landlock ABI >= 4.",
    )

    # Syscall filtering (via libseccomp)
    blocked_syscalls: List[str] = Field(
        default_factory=lambda: [
            "ptrace", "mount", "umount2", "chroot",
            "pivot_root", "reboot", "swapon", "swapoff",
            "init_module", "finit_module", "delete_module",
            "kexec_load", "kexec_file_load",
        ],
        description="Additional syscalls to block via seccomp-BPF.",
    )
    block_fork: bool = Field(
        True,
        description="Block fork/clone/clone3/vfork after sandbox setup to prevent resource exhaustion.",
    )

    # Subprocess settings (inherited from LocalSandboxConfig)
    sandbox_dir: Optional[str] = Field(None, description="Directory for the sandbox environment. NOT /tmp (64MB size limit in Delta).")
    use_venv: bool = Field(False)
    venv_name: str = Field("venv")
    pip_requirements: List[PipRequirement] = Field(default_factory=list)
    timeout: int = Field(180, description="Per-tool execution timeout in seconds.")

    @property
    def type(self) -> "SandboxType":
        return SandboxType.LANDLOCK

    @model_validator(mode="before")
    @classmethod
    def set_defaults_from_env(cls, data):
        if not isinstance(data, dict):
            return data
        # Sandbox dir
        if data.get("sandbox_dir") is None:
            if tool_settings.tool_exec_dir:
                data["sandbox_dir"] = tool_settings.tool_exec_dir
            else:
                data["sandbox_dir"] = LETTA_TOOL_EXECUTION_DIR
        # Network access: deny-by-default, but allow opt-in via env var.
        # This lets deployments (e.g. Delta) enable TCP for tools that call
        # external APIs without changing the secure default for all users.
        if "allow_tcp_connect" not in data:
            env_val = os.environ.get("LETTA_LANDLOCK_ALLOW_TCP_CONNECT", "").lower()
            if env_val in ("1", "true", "yes"):
                data["allow_tcp_connect"] = True
        return data


class SandboxConfigBase(OrmMetadataBase):
    __id_prefix__ = PrimitiveType.SANDBOX_CONFIG.value


class SandboxConfig(SandboxConfigBase):
    id: str = SandboxConfigBase.generate_id_field()
    type: SandboxType = Field(None, description="The type of sandbox.")
    organization_id: Optional[str] = Field(None, description="The unique identifier of the organization associated with the sandbox.")
    config: Dict = Field(default_factory=lambda: {}, description="The JSON sandbox settings data.")

    def get_e2b_config(self) -> E2BSandboxConfig:
        config_dict = self.config.copy()
        config_dict["template"] = tool_settings.e2b_sandbox_template_id
        return E2BSandboxConfig(**config_dict)

    def get_local_config(self) -> LocalSandboxConfig:
        return LocalSandboxConfig(**self.config)

    def get_modal_config(self) -> ModalSandboxConfig:
        return ModalSandboxConfig(**self.config)

    def get_landlock_config(self) -> LandlockSandboxConfig:
        return LandlockSandboxConfig(**self.config)

    def fingerprint(self) -> str:
        # Only take into account type, org_id, and the config items
        # Canonicalize input data into JSON with sorted keys
        hash_input = json.dumps(
            {
                "type": self.type.value,
                "organization_id": self.organization_id,
                "config": self.config,
            },
            sort_keys=True,  # Ensure stable ordering
            separators=(",", ":"),  # Minimize serialization differences
        )

        # Compute SHA-256 hash
        hash_digest = hashlib.sha256(hash_input.encode("utf-8")).digest()

        # Convert the digest to an integer for compatibility with Python's hash requirements
        return str(int.from_bytes(hash_digest, byteorder="big"))


class SandboxConfigCreate(LettaBase):
    config: Union[LocalSandboxConfig, E2BSandboxConfig, ModalSandboxConfig, LandlockSandboxConfig] = Field(..., description="The configuration for the sandbox.")


class SandboxConfigUpdate(LettaBase):
    """Pydantic model for updating SandboxConfig fields."""

    config: Union[LocalSandboxConfig, E2BSandboxConfig, ModalSandboxConfig, LandlockSandboxConfig] = Field(
        None, description="The JSON configuration data for the sandbox."
    )
