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
    # v0.16.34: action on flagged tool output (see init_security read)
    agent.tool_output_action = "warn"
    # v0.16.35: degenerate-generation watchdog (default off)
    agent.reasoning_watchdog = False


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
    # v0.16.34: action on flagged tool output. "warn" (default) appends
    # the [SECURITY WARNING] and passes content through (today's
    # behavior); "strip" replaces content with a minimal stub; "error"
    # returns an error-status result naming the detection. In
    # strip/error, flagged text never enters the model's context.
    # v0.16.35: reasoning-generation watchdog — terminates degenerate
    # reasoning streams (consecutive-identical-token collapse). Opt-in.
    agent.reasoning_watchdog = metadata.get("reasoning_watchdog", "off") == "on"

    _action = metadata.get("tool_output_action", "warn")
    if _action not in ("warn", "strip", "error"):
        import logging as _logging

        _logging.getLogger(__name__).warning(
            f"Unknown tool_output_action {(_action)!r}, falling back to 'warn'"
        )
        _action = "warn"
    agent.tool_output_action = _action
    agent.tool_arg_validation_enabled = cv_enabled


# ---------------------------------------------------------------------------
# Per-step loading — called at the start of each step/stream
# ---------------------------------------------------------------------------


def _inject_default_secret_rule(agent) -> None:
    """Inject a default CONTAINS_SECRET audit rule if the policy doesn't have one.

    v0.16.37 (review L-5): renamed from "secret-in-memory-write" to
    "secret-in-tool-args" — the rule's condition is on tool_args, NOT on
    tool category, so the old name lied about scope. The rule audits ANY
    tool call whose arguments contain a secret. It does NOT block — AUDIT
    is log-and-continue in the engine, so it never shadows deny rules
    below it. The model-facing warning text is gated to memory-write tools
    (P-7); the event fires for every tool. Strict deployments can change
    the action to DENY or REQUIRE_APPROVAL.
    """
    from letta.security.policy import PolicyAction, PolicyCondition, PolicyOperator, PolicyRule

    # Check if a contains_secret rule already exists
    if hasattr(agent, "policy_checker") and agent.policy_checker.policy:
        for rule in agent.policy_checker.policy.rules:
            if rule.condition.operator == PolicyOperator.CONTAINS_SECRET:
                return  # already has one

    rule = PolicyRule(
        name="secret-in-tool-args",
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

    Canary opt-out (LETTA_CANARY_ENABLED=false): returns before any
    arming — no block creation, no value loading, and the fail-closed
    except path below (which arms a fresh in-memory canary on failure)
    is unreachable. Pre-existing __canary__ blocks remain as inert
    system-prompt text; all consumers (output_filter, streaming filter
    gate, CanaryChecker.check) are already inert on an unset value.
    """
    # MUST precede the try/except: the except path generates and arms a
    # fresh canary (fail-closed), so a guard inside the try would not
    # fully disable the subsystem.
    from letta.settings import settings

    if not settings.canary_enabled:
        return

    from letta.security.canary import CanaryChecker

    try:
        # BaseAgent passes agent_state separately; BaseAgentV2 uses self.agent_state
        agent_state = getattr(agent, "agent_state", None)
        if agent_state is None:
            # V1 path: agent_state is not stored on self, caller must set it
            # This shouldn't happen in practice — V1 always has agent_state
            # available at the call site
            return

        # v0.16.37 (review P-15): prefer the read_only canary when more than
        # one __canary__ block exists (pre-attach-guard data, or a payload with
        # two canary blocks). The framework's canary is read_only by
        # construction; a writable impostor is never preferred for arming.
        canary_block = None
        for block in agent_state.memory.blocks:
            if block.label == CanaryChecker.CANARY_BLOCK_LABEL:
                if getattr(block, "read_only", False):
                    canary_block = block
                    break
                if canary_block is None:
                    canary_block = block  # fallback: first canary, upgraded below

        if canary_block and canary_block.value:
            agent.canary_checker.update_canary(canary_block.value)
            # v0.16.33: __canary__ is framework-managed by construction — no
            # legitimate consumer reason for writability. Consumers that pass
            # canary blocks through the CreateAgent payload get read_only=False
            # (schema default), silently disarming BlockGuard. Upgrade at load
            # time catches every creation path regardless of what was sent.
            if not getattr(canary_block, "read_only", False):
                canary_block.read_only = True  # in-memory (arms THIS request's guards)
                try:
                    await _persist_canary_read_only(canary_block)
                except Exception as e:
                    agent.logger.warning(f"Failed to persist canary read_only upgrade (in-memory upgrade stands): {e}")
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


async def _persist_canary_read_only(block) -> None:
    """Persist the read_only upgrade for a __canary__ block (short session).

    Flush-inside-async-with — the established _create_canary_block pattern
    (context exit commits; the lazy-mint path's persistence proves it).
    Failure is caught by the caller: the in-memory upgrade stands (arms the
    tool-executor guard layer for the request); the DB write is for
    persistence only.
    """
    from letta.orm.block import Block as BlockModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    async with db_registry.async_session() as session:
        stmt = select(BlockModel).where(BlockModel.id == block.id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is not None and not row.read_only:
            row.read_only = True
            await session.flush()


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
            # v0.16.33: same read_only upgrade on this path (session already open)
            if not existing.read_only:
                existing.read_only = True
                await session.flush()
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


async def check_policy(
    agent,
    tool_name: str,
    tool_args: dict | None = None,
    step_id: str | None = None,
    run_id: str | None = None,
    waive_approval: bool = False,
) -> "PolicyDecision":
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
    decision = agent.policy_checker.check(tool_name, eval_context=eval_context, waive_approval=waive_approval)
    # Record the call for rate limiting if allowed
    if decision.allowed:
        agent.policy_checker.record_call(tool_name, tool_args)

    # v0.16.37 (review L-5): AUDIT is log-and-continue in the engine, so audit
    # matches arrive as decision.audit_entries REGARDLESS of the terminal
    # action (allow, deny, require_approval, or default). Log one event per
    # collected entry — a secret-bearing call that a deny rule then blocks
    # produces both the SECRET_DETECTED event and the denial.
    if decision.audit_entries:
        from letta.security import audit_helpers as _ah
        from letta.security.secret_scanner import SecretPatternChecker
        from letta.security.content_validator import ContentValidator
        tool_args = tool_args or {}

        # Determine the label for the audit event
        label = "unknown"
        for value in tool_args.values():
            if isinstance(value, str):
                result = SecretPatternChecker.check(value)
                if result is not None:
                    label = result
                    break

        # Determine the injection label for the audit event
        injection_label = "unknown"
        for value in tool_args.values():
            if isinstance(value, str):
                result = ContentValidator.check(value)
                if result is not None:
                    injection_label = result
                    break

        for entry in decision.audit_entries:
            matched_rule = entry.get("matched_rule") or "unnamed_audit_rule"
            operator = entry.get("operator", "")
            if operator == "contains_secret":
                await _ah.log_secret_detected(
                    agent.audit_logger, agent.agent_id, agent.actor,
                    tool_name, label, step_id, run_id,
                    matched_rule=matched_rule,
                )
            elif operator == "contains_injection":
                await _ah.log_injection_detected(
                    agent.audit_logger, agent.agent_id, agent.actor,
                    tool_name, injection_label, step_id, run_id,
                    matched_rule=matched_rule,
                )
            else:
                # Custom AUDIT rule — log the match itself
                await _ah.log_policy_audit_matched(
                    agent.audit_logger, agent.agent_id, agent.actor,
                    tool_name, matched_rule, step_id, run_id,
                )

        # P-7 (review): the model-facing warning text is appended ONLY for
        # memory-write tools. The event fires for every tool; the warning
        # fires where it changes behavior. Otherwise every UUID-bearing call
        # pollutes the model's context with warnings.
        from letta.security.audit import MEMORY_WRITE_TOOLS

        if tool_name in MEMORY_WRITE_TOOLS:
            decision.audit_warning = "\n".join(
                entry.get("reason") or f"Matched rule '{entry.get('matched_rule')}'"
                for entry in decision.audit_entries
            )
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
# v0.16.37 (A8, P-14): replacement for blocked tool-call arguments at the
# storage redaction point. MUST be a valid JSON object STRING: the persisted
# tool_use message is replayed to the provider on the next request, and
# providers reject non-JSON arguments (rig catch: Ollama 400
# "invalid tool call arguments" killed the turn after a blocked call).
BLOCKED_ARGS_REDACTION_MARKER = '{"redacted": "Tool call arguments withheld by security policy."}'


