import os
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("letta")
except PackageNotFoundError:
    # Fallback for development installations
    __version__ = "0.16.38"

if os.environ.get("LETTA_VERSION"):
    __version__ = os.environ["LETTA_VERSION"]

# Import sqlite_functions early to ensure event handlers are registered (only for SQLite)
# This is only needed for the server, not for client usage.
# v0.16.38 (L-1): also skip in sandboxed tool children. The child env no longer
# carries LETTA_PG_URI (credential scrub, R-1), so a child would otherwise take
# the SQLITE branch and pull in the full ORM (numpy via helpers.converters) —
# numpy's OpenBLAS thread pool wedges under the sandbox seccomp filter
# (clone blocked -> spin), killing every agent_state-carrying tool call.
# Children register DB event handlers for nobody.
try:
    from letta.settings import DatabaseChoice, settings

    if settings.database_engine == DatabaseChoice.SQLITE and os.environ.get("LETTA_SANDBOXED_TOOL_EXECUTION") != "1":
        from letta.orm import sqlite_functions  # noqa: F401
except ImportError:
    # If sqlite_vec is not installed, it's fine for client usage
    pass

# # imports for easier access
from letta.schemas.agent import AgentState as AgentState
from letta.schemas.block import Block as Block
from letta.schemas.embedding_config import EmbeddingConfig as EmbeddingConfig
from letta.schemas.enums import JobStatus as JobStatus
from letta.schemas.file import FileMetadata as FileMetadata
from letta.schemas.job import Job as Job
from letta.schemas.letta_message import LettaErrorMessage as LettaErrorMessage, LettaMessage as LettaMessage, LettaPing as LettaPing
from letta.schemas.letta_stop_reason import LettaStopReason as LettaStopReason
from letta.schemas.llm_config import LLMConfig as LLMConfig
from letta.schemas.memory import (
    ArchivalMemorySummary as ArchivalMemorySummary,
    BasicBlockMemory as BasicBlockMemory,
    ChatMemory as ChatMemory,
    Memory as Memory,
    RecallMemorySummary as RecallMemorySummary,
)
from letta.schemas.message import Message as Message
from letta.schemas.organization import Organization as Organization
from letta.schemas.passage import Passage as Passage
from letta.schemas.source import Source as Source
from letta.schemas.tool import Tool as Tool
from letta.schemas.usage import LettaUsageStatistics as LettaUsageStatistics
from letta.schemas.user import User as User
