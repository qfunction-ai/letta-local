"""Agent security lifecycle — init, load, and check functions.

Owns all security state management so that HIGH-activity shared agent
files (V1, V2, V3) don't store fork-local methods. The agent files
import this module and call its functions via delegation — one import
line, zero fork-local methods.

This follows the same pattern as agent_hardening.py and
step_recorder_integration.py: fork logic in a new module, shared
files get one import + one-liner calls.

Usage in agent files:
    from letta.security import agent_security as _sec
    _sec.init_agent_attributes(self)          # BaseAgent.__init__
    _sec.init_security(self)                  # BaseAgentV2._initialize_state
    await _sec.load_tool_call_policy(self)     # step start
    await _sec.load_canary(self)               # step start
    decision = _sec.check_policy(self, name, args, step_id, run_id)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from letta.log import get_logger

if TYPE_CHECKING:
    from letta.agents.base_agent import BaseAgent
    from letta.agents.base_agent_v2 import BaseAgentV2
    from letta.agents.token_budget import TokenBudget
    from letta.security.policy import PolicyDecision

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Initialization — called once per agent construction
# ---------------------------------------------------------------------------


def init_agent_attributes(agent: "BaseAgent") -> None:
    """Initialize all fork-local attributes on a BaseAgent instance.

    Called from BaseAgent.__init__. Sets up observability recorders,
    security checkers, and hardening objects. Replaces ~30 lines of
    individual imports and assignments.
    """
    from letta.observability.agent_step_recorder import AgentStepRecorder
    from letta.observability.tool_call_recorder import ToolCallRecorder
    from letta.security.audit import AuditLogger
    from letta.security.canary import CanaryChecker
    from letta.security.policy import PolicyChecker
    from letta.agents.token_budget import TokenBudget
    from letta.agents.circuit_breaker import AgentCircuitBreaker

    # Observability
    agent.recorder = AgentStepRecorder()
    agent.tool_call_recorder = ToolCallRecorder()

    # Security
    agent.audit_logger = AuditLogger()
    agent.policy_checker = PolicyChecker()
    agent.canary_checker = CanaryChecker()

    # Hardening
    agent.token_budget = TokenBudget()
    agent.circuit_breaker = AgentCircuitBreaker()

    # Tool validation (default off — opt-in)
    agent.tool_output_validation_enabled = False
    agent.tool_arg_validation_enabled = False


def init_security(agent: "BaseAgentV2") -> None:
    """Initialize security objects on a BaseAgentV2 instance.

    Called from _initialize_state() in V2/V3 subclasses. Replaces
    the old _initialize_security() method that was defined on
    BaseAgentV2.
    """
    from letta.security.audit import AuditLogger
    from letta.security.canary import CanaryChecker
    from letta.security.policy import PolicyChecker
    from letta.observability.tool_call_recorder import ToolCallRecorder

    agent.audit_logger = AuditLogger()
    agent.policy_checker = PolicyChecker()
    agent.canary_checker = CanaryChecker()
    agent.tool_call_recorder = ToolCallRecorder()

    # Read content validation flag from agent metadata.
    # Epsilon sends enable_content_validation inside the metadata dict
    # (top-level field would be dropped by Pydantic extra="ignore").
    metadata = {}
    if hasattr(agent, "agent_state") and agent.agent_state:
        metadata = getattr(agent.agent_state, "metadata", {}) or {}
    cv_enabled = metadata.get("enable_content_validation", False)
    agent.tool_output_validation_enabled = cv_enabled
    agent.tool_arg_validation_enabled = cv_enabled


# ---------------------------------------------------------------------------
# Per-step loading — called at the start of each step/stream
# ---------------------------------------------------------------------------


def _inject_default_secret_rule(agent) -> None:
    """Inject a default CONTAINS_SECRET audit rule if the policy doesn't have one.

    The default rule audits memory-write tool calls that contain secrets.
    It does NOT block — it logs the event and appends a warning to the
    tool result so the LLM can inform the user. Strict deployments can
    change the action to DENY or REQUIRE_APPROVAL.
    """
    from letta.security.policy import PolicyAction, PolicyCondition, PolicyOperator, PolicyRule

    # Check if a contains_secret rule already exists
    if hasattr(agent, "policy_checker") and agent.policy_checker.policy:
        for rule in agent.policy_checker.policy.rules:
            if rule.condition.operator == PolicyOperator.CONTAINS_SECRET:
                return  # already has one

    rule = PolicyRule(
        name="secret-in-memory-write",
        condition=PolicyCondition(
            field="tool_args",
            operator=PolicyOperator.CONTAINS_SECRET,
            value=True,
        ),
        action=PolicyAction.AUDIT,
        priority=90,
        message="Memory write contains what appears to be a secret. "
                "Consider storing credentials in the Credentials page instead.",
    )
    if hasattr(agent, "policy_checker"):
        agent.policy_checker.add_rule(rule)


async def load_tool_call_policy(agent) -> None:
    """Load the per-agent tool call policy from the DB.

    Called at the start of each step. Fails closed (deny all)
    if the load fails.

    Works with both BaseAgent and BaseAgentV2 — reads agent_id
    and actor from the agent object.
    """
    from letta.security.policy import ToolCallPolicy
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    try:
        async with db_registry.async_session() as session:
            stmt = select(ToolCallPolicyModel).where(
                ToolCallPolicyModel.agent_id == agent.agent_id,
                ToolCallPolicyModel.organization_id == agent.actor.organization_id,
            )
            result = await session.execute(stmt)
            policy_model = result.scalar_one_or_none()
            if policy_model and policy_model.policy:
                agent.policy_checker.update_policy(ToolCallPolicy(**policy_model.policy))
            else:
                agent.policy_checker.update_policy(ToolCallPolicy())
            # Inject default secret-detection rule if not present
            _inject_default_secret_rule(agent)
    except Exception as e:
        agent.logger.error(f"Failed to load tool call policy, denying all tools (fail-closed): {e}")
        agent.policy_checker.deny_all = True


async def load_canary(agent) -> None:
    """Load the canary value from the __canary__ memory block.

    Lazy creation: if the canary block doesn't exist, create it
    with a random value AND persist it to the DB. The canary is
    in place before any tool calls happen because step
    initialization runs before the LLM is called.

    Works with both BaseAgent (pass agent_state) and BaseAgentV2
    (uses self.agent_state).
    """
    from letta.security.canary import CanaryChecker

    try:
        # BaseAgent passes agent_state separately; BaseAgentV2 uses self.agent_state
        agent_state = getattr(agent, "agent_state", None)
        if agent_state is None:
            # V1 path: agent_state is not stored on self, caller must set it
            # This shouldn't happen in practice — V1 always has agent_state
            # available at the call site
            return

        canary_block = None
        for block in agent_state.memory.blocks:
            if block.label == CanaryChecker.CANARY_BLOCK_LABEL:
                canary_block = block
                break

        if canary_block and canary_block.value:
            agent.canary_checker.update_canary(canary_block.value)
        else:
            # Lazy creation: create the canary block in DB and in-memory
            canary_value = CanaryChecker.generate_canary_value()
            await _create_canary_block(agent, agent_state, canary_value)
            agent.canary_checker.update_canary(canary_value)
    except Exception as e:
        agent.logger.error(f"Failed to load/create canary (fail-closed): {e}")
        # Keep the last known canary if we have one; otherwise generate
        # a fresh in-memory canary so the check still works (it just
        # won't match the system prompt canary, which is the best we
        # can do without DB access).
        if not agent.canary_checker.canary_value:
            agent.canary_checker.update_canary(CanaryChecker.generate_canary_value())


async def _create_canary_block(agent, agent_state, canary_value: str) -> None:
    """Create and persist the __canary__ memory block in the DB.

    Also updates the in-memory agent_state so the canary appears
    in the system prompt on the next refresh.

    Internal to this module — not called directly by agent files.
    """
    from uuid import uuid4
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
            BlockModel.agents.any(id=agent.agent_id),
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Block exists in DB but wasn't in agent_state — load its value
            agent.canary_checker.update_canary(existing.value)
            # Add to in-memory state if not already there
            if not any(b.label == CanaryChecker.CANARY_BLOCK_LABEL for b in agent_state.memory.blocks):
                pydantic_block = existing.to_pydantic()
                agent_state.memory.blocks.append(pydantic_block)
            return

        # Create new block in DB
        org_id = agent.actor.organization_id if agent.actor else None
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
        agent_model = await session.get(AgentModel, agent.agent_id)
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
        agent_state.memory.blocks.append(pydantic_block)


# ---------------------------------------------------------------------------
# Policy checking — called per tool call
# ---------------------------------------------------------------------------


async def check_policy(agent, tool_name: str, tool_args: dict | None = None, step_id: str | None = None, run_id: str | None = None) -> "PolicyDecision":
    """Check a tool call against the security policy with full context.

    Wraps ``agent.policy_checker.check()`` with the evaluation context
    so that agent files call this one-liner instead of building the
    context dict themselves.

    Replaces the duplicate _check_policy() methods on BaseAgent and
    BaseAgentV2, and the inline policy check in LettaAgent._step().
    """
    from letta.security.policy import PolicyAction, PolicyDecision

    # Tool argument validation (opt-in, before policy check)
    if getattr(agent, "tool_arg_validation_enabled", False):
        from letta.security.tool_arg_validator import validate_tool_args
        tool_schema = None
        agent_state = getattr(agent, "agent_state", None)
        if agent_state is not None:
            target_tool = next((t for t in agent_state.tools if t.name == tool_name), None)
            if target_tool is not None:
                tool_schema = target_tool.args_json_schema or (
                    target_tool.json_schema.get("parameters") if target_tool.json_schema else None
                )
        validation_error = validate_tool_args(tool_name, tool_args or {}, tool_schema)
        if validation_error is not None:
            from letta.security import audit_helpers as _ah
            await _ah.log_tool_denied(
                agent.audit_logger, agent.agent_id, agent.actor,
                tool_name, validation_error, step_id, run_id,
                matched_rule="argument_validation_failed",
            )
            return PolicyDecision(
                allowed=False,
                action="deny",
                matched_rule="argument_validation_failed",
                reason=validation_error,
                audit_entry={
                    "tool_name": tool_name,
                    "matched_rule": "argument_validation_failed",
                    "action": "deny",
                    "reason": validation_error,
                },
            )

    eval_context = {
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "tool_call_count": agent.policy_checker.get_call_count(tool_name),
        "actor_id": agent.actor.id if agent.actor else None,
        "agent_id": agent.agent_id,
    }
    decision = agent.policy_checker.check(tool_name, eval_context=eval_context)
    # Record the call for rate limiting if allowed
    if decision.allowed:
        agent.policy_checker.record_call(tool_name, tool_args)
    # Handle AUDIT action: log event + set warning for caller to append to tool result
    if decision.action == PolicyAction.AUDIT:
        from letta.security import audit_helpers as _ah
        from letta.security.secret_scanner import SecretPatternChecker
        from letta.security.content_validator import ContentValidator
        # Determine the label for the audit event
        tool_args = tool_args or {}
        label = "unknown"
        for value in tool_args.values():
            if isinstance(value, str):
                result = SecretPatternChecker.check(value)
                if result is not None:
                    label = result
                    break
        await _ah.log_secret_detected(
            agent.audit_logger, agent.agent_id, agent.actor,
            tool_name, label, step_id, run_id,
        )
        # Also check for injection patterns
        for value in tool_args.values():
            if isinstance(value, str):
                injection_label = ContentValidator.check(value)
                if injection_label is not None:
                    await _ah.log_injection_detected(
                        agent.audit_logger, agent.agent_id, agent.actor,
                        tool_name, injection_label, step_id, run_id,
                    )
                    break
        decision.audit_warning = decision.reason
    return decision


# ---------------------------------------------------------------------------
# Token budget creation — called per step
# ---------------------------------------------------------------------------


def create_token_budget(agent_state) -> "TokenBudget":
    """Create a TokenBudget from agent metadata.

    Pure function — reads from agent_state.metadata and
    agent_state.llm_config.context_window. No self needed.

    Budget settings are stored in agent.metadata, not in LLMConfig
    (which is HIGH-activity upstream). Budgets are resource management,
    not security — they stay separate from the policy engine.

    Metadata keys:
    - token_budget_run: int | None — max cumulative tokens per run
    - token_budget_step: int | None — max tokens per single step
    - token_budget_context_ratio: float — fraction of context_window
      to allow (default 0.7, matching common vLLM --gpu-memory-utilization)
    """
    from letta.agents.token_budget import TokenBudget

    metadata = getattr(agent_state, "metadata", None) or {}
    return TokenBudget(
        max_run_tokens=metadata.get("token_budget_run"),
        max_step_tokens=metadata.get("token_budget_step"),
        context_window_limit=agent_state.llm_config.context_window,
        context_window_ratio=metadata.get("token_budget_context_ratio", 0.7),
    )


# ---------------------------------------------------------------------------
# Circuit breaker — called per error
# ---------------------------------------------------------------------------


def handle_circuit_breaker_error(agent, error_type: str) -> Optional[str]:
    """Record an error with the circuit breaker and return the recovery action.

    Wraps ``agent.circuit_breaker.record_error()`` so that agent
    subclasses call this one-liner instead of interacting with the
    circuit breaker directly. Keeps the HIGH-activity agent file
    diffs minimal.

    Returns:
        ``"auto_compact"`` if the threshold is exceeded, ``None`` otherwise.
    """
    return agent.circuit_breaker.record_error(error_type)
