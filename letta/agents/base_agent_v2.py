from abc import ABC, abstractmethod
from uuid import uuid4

from typing import TYPE_CHECKING, AsyncGenerator

from letta.constants import DEFAULT_MAX_STEPS
from letta.log import get_logger
from letta.schemas.agent import AgentState
from letta.schemas.enums import MessageStreamStatus
from letta.schemas.letta_message import LegacyLettaMessage, LettaMessage, MessageType
from letta.schemas.letta_response import LettaResponse
from letta.schemas.message import MessageCreate
from letta.schemas.user import User

if TYPE_CHECKING:
    from letta.schemas.letta_request import ClientSkillSchema, ClientToolSchema
    from letta.schemas.provider_trace import BillingContext


class BaseAgentV2(ABC):
    """
    Abstract base class for the main agent execution loop for letta agents, handling
    message management, llm api request, tool execution, and context tracking.
    """

    def __init__(self, agent_state: AgentState, actor: User):
        self.agent_state = agent_state
        self.actor = actor
        self.logger = get_logger(agent_state.id)
        self.conversation_id: str | None = None

    def _initialize_security(self):
        """Initialize security objects. Called by subclass _initialize_state()."""
        from letta.security.audit import AuditLogger
        from letta.security.canary import CanaryChecker
        from letta.security.policy import PolicyChecker
        from letta.observability.tool_call_recorder import ToolCallRecorder

        self.audit_logger = AuditLogger()
        self.policy_checker = PolicyChecker()
        self.canary_checker = CanaryChecker()
        self.tool_call_recorder = ToolCallRecorder()

    async def _load_tool_call_policy(self) -> None:
        """Load the per-agent tool call policy from the DB.

        Called at the start of each step. Fails closed (deny all)
        if the load fails.
        """
        from letta.security.policy import ToolCallPolicy
        from letta.orm.tool_call_policy import ToolCallPolicyModel
        from letta.server.db import db_registry
        from sqlalchemy import select

        try:
            async with db_registry.async_session() as session:
                stmt = select(ToolCallPolicyModel).where(
                    ToolCallPolicyModel.agent_id == self.agent_id,
                    ToolCallPolicyModel.organization_id == self.actor.organization_id,
                )
                result = await session.execute(stmt)
                policy_model = result.scalar_one_or_none()
                if policy_model and policy_model.policy:
                    self.policy_checker.update_policy(ToolCallPolicy(**policy_model.policy))
                else:
                    self.policy_checker.update_policy(ToolCallPolicy())
        except Exception as e:
            self.logger.error(f"Failed to load tool call policy, denying all tools (fail-closed): {e}")
            self.policy_checker.deny_all = True

    async def _load_canary(self) -> None:
        """Load the canary value from the __canary__ memory block.

        Lazy creation: if the canary block doesn't exist, create it
        with a random value AND persist it to the DB. The canary is
        in place before any tool calls happen because step
        initialization runs before the LLM is called.
        """
        from letta.security.canary import CanaryChecker

        try:
            canary_block = None
            for block in self.agent_state.memory.blocks:
                if block.label == CanaryChecker.CANARY_BLOCK_LABEL:
                    canary_block = block
                    break

            if canary_block and canary_block.value:
                self.canary_checker.update_canary(canary_block.value)
            else:
                # Lazy creation: create the canary block in DB and in-memory
                canary_value = CanaryChecker.generate_canary_value()
                await self._create_canary_block(canary_value)
                self.canary_checker.update_canary(canary_value)
        except Exception as e:
            self.logger.error(f"Failed to load/create canary (fail-closed): {e}")
            # Keep the last known canary if we have one; otherwise generate
            # a fresh in-memory canary so the check still works (it just
            # won't match the system prompt canary, which is the best we
            # can do without DB access).
            if not self.canary_checker.canary_value:
                self.canary_checker.update_canary(CanaryChecker.generate_canary_value())

    async def _create_canary_block(self, canary_value: str) -> None:
        """Create and persist the __canary__ memory block in the DB.

        Also updates the in-memory agent_state so the canary appears
        in the system prompt on the next refresh.
        """
        from letta.security.canary import CanaryChecker
        from letta.orm.block import Block as BlockModel
        from letta.server.db import db_registry
        from letta.schemas.block import Block
        from sqlalchemy import select

        async with db_registry.async_session() as session:
            # Check if the block already exists in DB (race safety)
            stmt = select(BlockModel).where(
                BlockModel.label == CanaryChecker.CANARY_BLOCK_LABEL,
            ).join(
                BlockModel.agents
            ).where(
                BlockModel.agents.any(id=self.agent_id),
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Block exists in DB but wasn't in agent_state — load its value
                self.canary_checker.update_canary(existing.value)
                # Add to in-memory state if not already there
                if not any(b.label == CanaryChecker.CANARY_BLOCK_LABEL for b in self.agent_state.memory.blocks):
                    pydantic_block = existing.to_pydantic()
                    self.agent_state.memory.blocks.append(pydantic_block)
                return

            # Create new block in DB
            org_id = self.actor.organization_id if self.actor else None
            canary_block = BlockModel(
                id=f"block-{uuid4()}",
                organization_id=org_id,
                label=CanaryChecker.CANARY_BLOCK_LABEL,
                value=canary_value,
                read_only=True,
                description=CanaryChecker.CANARY_BLOCK_DESCRIPTION,
                limit=500,
            )
            session.add(canary_block)

            # Link block to agent via blocks_agents join table
            from letta.orm.agent import Agent as AgentModel
            agent_model = await session.get(AgentModel, self.agent_id)
            if agent_model:
                agent_model.core_memory.append(canary_block)

            await session.flush()

            # Add to in-memory agent_state
            pydantic_block = Block(
                id=canary_block.id,
                label=CanaryChecker.CANARY_BLOCK_LABEL,
                value=canary_value,
                read_only=True,
                description=CanaryChecker.CANARY_BLOCK_DESCRIPTION,
            )
            self.agent_state.memory.blocks.append(pydantic_block)

    @property
    def agent_id(self) -> str:
        """Return the agent ID for backward compatibility with code expecting self.agent_id."""
        return self.agent_state.id

    @abstractmethod
    async def build_request(
        self,
        input_messages: list[MessageCreate],
        client_skills: list["ClientSkillSchema"] | None = None,
        client_tools: list["ClientToolSchema"] | None = None,
        conversation_id: str | None = None,
        override_system: str | None = None,
    ) -> dict:
        """
        Execute the agent loop in dry_run mode, returning just the generated request
        payload sent to the underlying llm provider.
        """
        raise NotImplementedError

    @abstractmethod
    async def step(
        self,
        input_messages: list[MessageCreate],
        max_steps: int = DEFAULT_MAX_STEPS,
        run_id: str | None = None,
        use_assistant_message: bool = True,
        include_return_message_types: list[MessageType] | None = None,
        request_start_timestamp_ns: int | None = None,
        client_tools: list["ClientToolSchema"] | None = None,
        client_skills: list["ClientSkillSchema"] | None = None,
        override_system: str | None = None,
        include_compaction_messages: bool = False,  # Not used in V2, but accepted for API compatibility
        billing_context: "BillingContext | None" = None,
    ) -> LettaResponse:
        """
        Execute the agent loop in blocking mode, returning all messages at once.

        Args:
            client_tools: Optional list of client-side tools. When called, execution pauses
                for client to provide tool returns.
            include_compaction_messages: Not used in V2, but accepted for API compatibility.
        """
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self,
        input_messages: list[MessageCreate],
        max_steps: int = DEFAULT_MAX_STEPS,
        stream_tokens: bool = False,
        run_id: str | None = None,
        use_assistant_message: bool = True,
        include_return_message_types: list[MessageType] | None = None,
        request_start_timestamp_ns: int | None = None,
        conversation_id: str | None = None,
        client_tools: list["ClientToolSchema"] | None = None,
        client_skills: list["ClientSkillSchema"] | None = None,
        override_system: str | None = None,
        include_compaction_messages: bool = False,  # Not used in V2, but accepted for API compatibility
        billing_context: "BillingContext | None" = None,
        openai_responses_websocket: bool = False,
    ) -> AsyncGenerator[LettaMessage | LegacyLettaMessage | MessageStreamStatus, None]:
        """
        Execute the agent loop in streaming mode, yielding chunks as they become available.
        If stream_tokens is True, individual tokens are streamed as they arrive from the LLM,
        providing the lowest latency experience, otherwise each complete step (reasoning +
        tool call + tool return) is yielded as it completes.

        Args:
            client_tools: Optional list of client-side tools. When called, execution pauses
                for client to provide tool returns.
            include_compaction_messages: Not used in V2, but accepted for API compatibility.
            openai_responses_websocket: If True, use WebSocket transport for OpenAI Responses API.
        """
        raise NotImplementedError
