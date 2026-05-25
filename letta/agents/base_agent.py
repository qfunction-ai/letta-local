from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, List, Optional, Union

import openai

from letta.constants import DEFAULT_MAX_STEPS
from letta.helpers import ToolRulesSolver
from letta.helpers.datetime_helpers import get_utc_time
from letta.log import get_logger
from letta.prompts.prompt_generator import PromptGenerator
from letta.schemas.agent import AgentState
from letta.schemas.enums import MessageStreamStatus
from letta.schemas.letta_message import LegacyLettaMessage, LettaMessage
from letta.schemas.letta_message_content import TextContent
from letta.schemas.letta_response import LettaResponse
from letta.schemas.letta_stop_reason import LettaStopReason, StopReasonType
from letta.schemas.message import Message, MessageCreate, MessageUpdate
from letta.schemas.provider_trace import BillingContext
from letta.schemas.usage import LettaUsageStatistics
from letta.schemas.user import User
from letta.services.agent_manager import AgentManager
from letta.services.message_manager import MessageManager
from letta.services.passage_manager import PassageManager
from letta.utils import united_diff

logger = get_logger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for AI agents, handling message management, tool execution,
    and context tracking.
    """

    def __init__(
        self,
        agent_id: str,
        # TODO: Make required once client refactor hits
        openai_client: Optional[openai.AsyncClient],
        message_manager: MessageManager,
        agent_manager: AgentManager,
        actor: User,
    ):
        self.agent_id = agent_id
        self.openai_client = openai_client
        self.message_manager = message_manager
        self.agent_manager = agent_manager
        # TODO: Pass this in
        self.passage_manager = PassageManager()
        self.actor = actor
        self.logger = get_logger(agent_id)
        self.client_skills: list = []
        self.conversation_id: str | None = None

        # Observability: recorder receives step lifecycle events and
        # emits OTel data. No-op when tracing is disabled.
        from letta.observability.agent_step_recorder import AgentStepRecorder
        self.recorder = AgentStepRecorder()

        # Observability: tool_call_recorder persists per-tool-call records
        # to the DB. Separate from the OTel recorder so DB failures don't
        # affect event emission. Creates its own session per call.
        from letta.observability.tool_call_recorder import ToolCallRecorder
        self.tool_call_recorder = ToolCallRecorder()

        # Security: audit_logger writes security events to the
        # security_events table. Append-only timeline for incident
        # reconstruction. Creates its own session per call.
        from letta.security.audit import AuditLogger
        self.audit_logger = AuditLogger()

        # Security: policy_checker enforces per-agent tool call policies.
        # Loaded from tool_call_policies table at step start.
        from letta.security.policy import PolicyChecker
        self.policy_checker = PolicyChecker()

        # Security: canary_checker detects prompt exfiltration attempts.
        # Loaded from __canary__ memory block at step start.
        from letta.security.canary import CanaryChecker
        self.canary_checker = CanaryChecker()

        # Local model hardening: token_budget enforces per-run and
        # per-step token limits. Initialized from agent metadata at
        # step start. Prevents VRAM OOM on local inference.
        from letta.agents.token_budget import TokenBudget
        self.token_budget = TokenBudget()  # defaults (no limits) until step() sets it up

        # Local model hardening: circuit_breaker breaks consecutive
        # error death spirals. Tracks consecutive llm_api_error counts
        # and triggers auto-compaction when threshold is exceeded.
        from letta.agents.circuit_breaker import AgentCircuitBreaker
        self.circuit_breaker = AgentCircuitBreaker()

    @abstractmethod
    async def step(
        self,
        input_messages: List[MessageCreate],
        max_steps: int = DEFAULT_MAX_STEPS,
        run_id: Optional[str] = None,
        billing_context: "BillingContext | None" = None,
    ) -> LettaResponse:
        """
        Main execution loop for the agent.
        """
        raise NotImplementedError

    def _create_token_budget(self, agent_state) -> "TokenBudget":
        """Create a TokenBudget from agent metadata.

        Token budget settings are stored in agent.metadata, not in
        LLMConfig (which is HIGH-activity upstream). Budgets are
        resource management, not security — they stay separate
        from the policy engine.

        Metadata keys:
        - token_budget_run: int | None — max cumulative tokens per run
        - token_budget_step: int | None — max tokens per single step
        - token_budget_context_ratio: float — fraction of context_window
          to allow (default 0.7)
        """
        from letta.agents.token_budget import TokenBudget

        metadata = getattr(agent_state, "metadata", None) or {}
        return TokenBudget(
            max_run_tokens=metadata.get("token_budget_run"),
            max_step_tokens=metadata.get("token_budget_step"),
            context_window_limit=agent_state.llm_config.context_window,
            context_window_ratio=metadata.get("token_budget_context_ratio", 0.7),
        )

    @abstractmethod
    async def step_stream(
        self, input_messages: List[MessageCreate], max_steps: int = DEFAULT_MAX_STEPS
    ) -> AsyncGenerator[Union[LettaMessage, LegacyLettaMessage, MessageStreamStatus], None]:
        """
        Main streaming execution loop for the agent.
        """
        raise NotImplementedError

    def _handle_circuit_breaker_error(self, error_type: str) -> str | None:
        """Record an error with the circuit breaker and return the recovery action.

        Wraps ``self.circuit_breaker.record_error()`` so that agent
        subclasses call this one-liner instead of interacting with the
        circuit breaker directly. Keeps the HIGH-activity agent file
        diffs minimal.

        Returns:
            ``"auto_compact"`` if the threshold is exceeded, ``None`` otherwise.
        """
        return self.circuit_breaker.record_error(error_type)

    @staticmethod
    def pre_process_input_message(input_messages: List[MessageCreate]) -> Any:
        """
        Pre-process function to run on the input_message.
        """

        def get_content(message: MessageCreate) -> str:
            if isinstance(message.content, str):
                return message.content
            elif message.content and len(message.content) == 1 and isinstance(message.content[0], TextContent):
                return message.content[0].text
            else:
                return ""

        return [{"role": input_message.role.value, "content": get_content(input_message)} for input_message in input_messages]

    async def _rebuild_memory_async(
        self,
        in_context_messages: List[Message],
        agent_state: AgentState,
        tool_rules_solver: Optional[ToolRulesSolver] = None,
        num_messages: Optional[int] = None,  # storing these calculations is specific to the voice agent
        num_archival_memories: Optional[int] = None,
    ) -> List[Message]:
        """
        Async version of function above. For now before breaking up components, changes should be made in both places.
        """
        try:
            # [DB Call] loading blocks (modifies: agent_state.memory.blocks)
            agent_state = await self.agent_manager.refresh_memory_async(agent_state=agent_state, actor=self.actor)

            tool_constraint_block = None
            if tool_rules_solver is not None:
                tool_constraint_block = tool_rules_solver.compile_tool_rule_prompts()

            # compile archive tags if there's an attached archive
            from letta.services.archive_manager import ArchiveManager

            archive_manager = ArchiveManager()
            archive = await archive_manager.get_default_archive_for_agent_async(
                agent_id=agent_state.id,
                actor=self.actor,
            )

            if archive:
                archive_tags = await self.passage_manager.get_unique_tags_for_archive_async(
                    archive_id=archive.id,
                    actor=self.actor,
                )
            else:
                archive_tags = None

            # TODO: This is a pretty brittle pattern established all over our code, need to get rid of this
            curr_system_message = in_context_messages[0]
            curr_system_message_text = curr_system_message.content[0].text

            # generate memory string with current state for comparison
            curr_memory_str = agent_state.memory.compile(
                tool_usage_rules=tool_constraint_block,
                sources=agent_state.sources,
                max_files_open=agent_state.max_files_open,
                llm_config=agent_state.llm_config,
            )

            system_prompt_changed = agent_state.system not in curr_system_message_text
            memory_changed = curr_memory_str not in curr_system_message_text

            # Observability: record memory rebuild state
            self.recorder.on_memory_rebuilt(
                block_count=len(agent_state.memory.blocks) if agent_state.memory.blocks else 0,
                system_prompt_changed=system_prompt_changed,
                memory_changed=memory_changed,
            )

            if (not system_prompt_changed) and (not memory_changed):
                logger.debug(
                    f"Memory and sources haven't changed for agent id={agent_state.id} and actor=({self.actor.id}, {self.actor.name}), skipping system prompt rebuild"
                )
                return in_context_messages

            memory_edit_timestamp = get_utc_time()

            # size of messages and archival memories
            if num_messages is None:
                num_messages = await self.message_manager.size_async(actor=self.actor, agent_id=agent_state.id)
            if num_archival_memories is None:
                num_archival_memories = await self.passage_manager.agent_passage_size_async(actor=self.actor, agent_id=agent_state.id)

            new_system_message_str = PromptGenerator.get_system_message_from_compiled_memory(
                system_prompt=agent_state.system,
                memory_with_sources=curr_memory_str,
                agent_id=agent_state.id,
                conversation_id=self.conversation_id or "default",
                in_context_memory_last_edit=memory_edit_timestamp,
                timezone=agent_state.timezone,
                previous_message_count=num_messages - len(in_context_messages),
                archival_memory_size=num_archival_memories,
                archive_tags=archive_tags,
            )

            diff = united_diff(curr_system_message_text, new_system_message_str)
            if len(diff) > 0:
                logger.debug(f"Rebuilding system with new memory...\nDiff:\n{diff}")

                # [DB Call] Update Messages
                new_system_message = await self.message_manager.update_message_by_id_async(
                    curr_system_message.id,
                    message_update=MessageUpdate(content=new_system_message_str),
                    actor=self.actor,
                    project_id=agent_state.project_id,
                )
                return [new_system_message, *in_context_messages[1:]]

            else:
                return in_context_messages
        except:
            logger.exception(f"Failed to rebuild memory for agent id={agent_state.id} and actor=({self.actor.id}, {self.actor.name})")
            raise

    def get_finish_chunks_for_stream(self, usage: LettaUsageStatistics, stop_reason: Optional[LettaStopReason] = None):
        if stop_reason is None:
            stop_reason = LettaStopReason(stop_reason=StopReasonType.end_turn.value)
        return [
            stop_reason.model_dump_json(),
            usage.model_dump_json(),
            MessageStreamStatus.done.value,
        ]
