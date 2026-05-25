import hashlib
import json
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


class DockerSandboxConfig(BaseModel):
    """Configuration for local Docker sandbox.

    Provides containerized tool execution with security defaults:
    network isolation, resource limits, non-root execution,
    read-only rootfs. Network access is opt-in via network_mode.
    """

    image: str = Field("letta-sandbox:latest", description="Docker image for the sandbox container.")
    user: str = Field("1001:1001", description="User and group ID for container execution (non-root).")
    network_mode: str = Field(
        "none",
        description="Docker network mode. 'none' = no network. 'bridge' = default Docker networking.",
    )
    read_only: bool = Field(True, description="Read-only root filesystem. /tmp is writable via tmpfs.")
    mem_limit: str = Field("512m", description="Memory limit per container.")
    cpu_count: float = Field(1.0, description="CPU count limit per container.")
    pids_limit: int = Field(100, description="Maximum number of processes per container.")
    tmpfs_size: str = Field("100m", description="Size of /tmp tmpfs mount.")
    timeout: int = Field(180, description="Per-command timeout in seconds.")
    orphan_ttl: int = Field(3600, description="Time after which orphaned containers are cleaned up (seconds).")
    pip_requirements: Optional[List[str]] = Field(None, description="A list of pip packages to install in the Docker sandbox")

    @property
    def type(self) -> "SandboxType":
        return SandboxType.DOCKER


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

    def get_docker_config(self) -> DockerSandboxConfig:
        return DockerSandboxConfig(**self.config)

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
    config: Union[LocalSandboxConfig, E2BSandboxConfig, ModalSandboxConfig, DockerSandboxConfig] = Field(..., description="The configuration for the sandbox.")


class SandboxConfigUpdate(LettaBase):
    """Pydantic model for updating SandboxConfig fields."""

    config: Union[LocalSandboxConfig, E2BSandboxConfig, ModalSandboxConfig, DockerSandboxConfig] = Field(
        None, description="The JSON configuration data for the sandbox."
    )
