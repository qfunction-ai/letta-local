"""API endpoints for managing per-agent tool call policies.

Tool call policies are the security boundary between the LLM's tool
decisions and the server's execution. They define which tools are
denied, which require human approval, and which are allowed by default.

The policy is stored in a separate table (tool_call_policies), not on
the agent state, to avoid modifying shared schema files.

Full API exposure: all 5 ToolCallPolicy fields are available through
the API (denied_tools, approval_required_tools, rules,
max_calls_per_tool, defaults). The PUT endpoint replaces the entire
policy. The PATCH endpoint merges partial updates. The evaluate
endpoint dry-runs a tool call against the current policy.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from letta.security.policy import (
    PolicyAction,
    PolicyCondition,
    PolicyDefaults,
    PolicyRule,
    ToolCallPolicy,
)
from letta.server.rest_api.dependencies import HeaderParams, get_headers, get_letta_server
from letta.server.server import SyncServer

router = APIRouter(prefix="/agents", tags=["agent-policies"])


# ---------------------------------------------------------------------------
# API schemas — mirror the policy types but exclude cached regex fields
# ---------------------------------------------------------------------------


class PolicyConditionRequest(BaseModel):
    """API schema for a policy condition."""
    field: str = Field(description="Dot-path: 'tool_name', 'tool_args.query', 'tool_call_count'")
    operator: str = Field(description="Comparison operator: eq, ne, gt, lt, gte, lte, in, not_in, matches, contains")
    value: Any = Field(description="Value to compare against")


class PolicyRuleRequest(BaseModel):
    """API schema for a policy rule.

    Excludes the cached _compiled_pattern and _compiled_condition_pattern
    fields from the internal PolicyRule model.
    """
    name: str = Field(description="Human-readable rule name")
    condition: PolicyConditionRequest = Field(description="When this rule fires")
    action: str = Field(description="Action: allow, deny, require_approval, audit")
    priority: int = Field(default=0, description="Higher priority rules override lower ones")
    message: Optional[str] = Field(default=None, description="Human-readable explanation")
    pattern: Optional[str] = Field(default=None, description="Regex pattern for action params (Agent OS compatible)")


class PolicyDefaultsRequest(BaseModel):
    """API schema for default policy settings."""
    action: str = Field(default="allow", description="Default action when no rule matches")
    max_tool_calls: Optional[int] = Field(default=None, description="Global per-run tool call limit")
    max_tokens: Optional[int] = Field(default=None, description="Token limit (future)")
    timeout_seconds: Optional[int] = Field(default=None, description="Timeout limit (future)")


class ToolCallPolicyRequest(BaseModel):
    """Request body for creating or replacing a tool call policy."""
    denied_tools: list[str] = Field(default_factory=list, description="Tools that are always denied.")
    approval_required_tools: list[str] = Field(default_factory=list, description="Tools that require human approval.")
    rules: list[PolicyRuleRequest] = Field(default_factory=list, description="Ordered list of policy rules.")
    max_calls_per_tool: dict[str, int] = Field(default_factory=dict, description="Per-tool per-run call limit.")
    defaults: Optional[PolicyDefaultsRequest] = Field(default=None, description="Default policy settings.")


class ToolCallPolicyPatchRequest(BaseModel):
    """Request body for partially updating a tool call policy.

    All fields are Optional. Only fields that are explicitly set (not None)
    will be merged into the existing policy. To clear a field, use PUT
    with the full policy instead of PATCH.
    """
    denied_tools: Optional[list[str]] = Field(default=None, description="Tools that are always denied. None = no change.")
    approval_required_tools: Optional[list[str]] = Field(default=None, description="Tools that require human approval. None = no change.")
    rules: Optional[list[PolicyRuleRequest]] = Field(default=None, description="Policy rules. None = no change.")
    max_calls_per_tool: Optional[dict[str, int]] = Field(default=None, description="Per-tool per-run call limit. None = no change.")
    defaults: Optional[PolicyDefaultsRequest] = Field(default=None, description="Default policy settings. None = no change.")


class EvaluateRequest(BaseModel):
    """Request body for the policy evaluate endpoint."""
    tool_name: str = Field(..., description="Name of the tool to evaluate")
    tool_args: Optional[dict] = Field(default=None, description="Tool arguments (as JSON dict)")


class PolicyConditionResponse(BaseModel):
    """API response schema for a policy condition."""
    field: str
    operator: str
    value: Any


class PolicyRuleResponse(BaseModel):
    """API response schema for a policy rule.

    Excludes the cached _compiled_pattern and _compiled_condition_pattern
    fields from the internal PolicyRule model.
    """
    name: str
    condition: PolicyConditionResponse
    action: str
    priority: int = 0
    message: Optional[str] = None
    pattern: Optional[str] = None


class PolicyDefaultsResponse(BaseModel):
    """API response schema for default policy settings."""
    action: str = "allow"
    max_tool_calls: Optional[int] = None
    max_tokens: Optional[int] = None
    timeout_seconds: Optional[int] = None


class ToolCallPolicyResponse(BaseModel):
    """Response body for tool call policy endpoints."""
    agent_id: str = Field(..., description="Agent ID")
    denied_tools: list[str] = Field(default_factory=list, description="Denied tools")
    approval_required_tools: list[str] = Field(default_factory=list, description="Tools requiring approval")
    rules: list[PolicyRuleResponse] = Field(default_factory=list, description="Policy rules")
    max_calls_per_tool: dict[str, int] = Field(default_factory=dict, description="Per-tool per-run call limit")
    defaults: Optional[PolicyDefaultsResponse] = Field(default=None, description="Default policy settings")


class PolicyDecisionResponse(BaseModel):
    """Response body for the policy evaluate endpoint."""
    allowed: bool = Field(..., description="Whether the tool call would be allowed")
    action: str = Field(..., description="The action that would be taken")
    matched_rule: Optional[str] = Field(default=None, description="Name of the matched rule, if any")
    reason: str = Field(..., description="Why this decision would be made")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_policy_condition(req: PolicyConditionRequest) -> PolicyCondition:
    """Convert API condition to internal PolicyCondition."""
    return PolicyCondition(
        field=req.field,
        operator=req.operator,
        value=req.value,
    )


def _to_policy_rule(req: PolicyRuleRequest) -> PolicyRule:
    """Convert API rule to internal PolicyRule.

    Validates regex patterns before construction to give clear API errors.
    """
    from letta.security.policy import validate_regex_pattern

    # Validate regex patterns at the API boundary
    if req.pattern is not None:
        validate_regex_pattern(req.pattern)
    if req.condition.operator == "matches" and isinstance(req.condition.value, str):
        validate_regex_pattern(req.condition.value)

    return PolicyRule(
        name=req.name,
        condition=_to_policy_condition(req.condition),
        action=PolicyAction(req.action),
        priority=req.priority,
        message=req.message,
        pattern=req.pattern,
    )


def _to_policy_defaults(req: PolicyDefaultsRequest) -> PolicyDefaults:
    """Convert API defaults to internal PolicyDefaults."""
    return PolicyDefaults(
        action=PolicyAction(req.action),
        max_tool_calls=req.max_tool_calls,
        max_tokens=req.max_tokens,
        timeout_seconds=req.timeout_seconds,
    )


def _to_policy(request: ToolCallPolicyRequest) -> ToolCallPolicy:
    """Convert a full API request to an internal ToolCallPolicy."""
    return ToolCallPolicy(
        denied_tools=request.denied_tools,
        approval_required_tools=request.approval_required_tools,
        rules=[_to_policy_rule(r) for r in request.rules],
        max_calls_per_tool=request.max_calls_per_tool,
        defaults=_to_policy_defaults(request.defaults) if request.defaults else None,
    )


def _to_response(agent_id: str, policy: ToolCallPolicy) -> ToolCallPolicyResponse:
    """Convert an internal ToolCallPolicy to an API response."""
    defaults_resp = None
    if policy.defaults is not None:
        defaults_resp = PolicyDefaultsResponse(
            action=policy.defaults.action.value,
            max_tool_calls=policy.defaults.max_tool_calls,
            max_tokens=policy.defaults.max_tokens,
            timeout_seconds=policy.defaults.timeout_seconds,
        )

    return ToolCallPolicyResponse(
        agent_id=agent_id,
        denied_tools=policy.denied_tools,
        approval_required_tools=policy.approval_required_tools,
        rules=[
            PolicyRuleResponse(
                name=r.name,
                condition=PolicyConditionResponse(
                    field=r.condition.field,
                    operator=r.condition.operator.value,
                    value=r.condition.value,
                ),
                action=r.action.value,
                priority=r.priority,
                message=r.message,
                pattern=r.pattern,
            )
            for r in policy.rules
        ],
        max_calls_per_tool=policy.max_calls_per_tool,
        defaults=defaults_resp,
    )


async def _load_policy(agent_id: str, actor) -> ToolCallPolicy:
    """Load the policy for an agent from the DB. Returns default if none exists."""
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    _org_id = actor.organization_id

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(
            ToolCallPolicyModel.agent_id == agent_id,
            ToolCallPolicyModel.organization_id == _org_id,
        )
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

    if policy_model and policy_model.policy:
        return ToolCallPolicy(**policy_model.policy)
    return ToolCallPolicy()  # default: allow all


async def _save_policy(agent_id: str, actor, policy: ToolCallPolicy) -> None:
    """Save a policy to the DB. Creates or updates."""
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    _org_id = actor.organization_id
    policy_dict = policy.model_dump()

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(
            ToolCallPolicyModel.agent_id == agent_id,
            ToolCallPolicyModel.organization_id == _org_id,
        )
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

        if policy_model:
            policy_model.policy = policy_dict
        else:
            policy_model = ToolCallPolicyModel(
                agent_id=agent_id,
                organization_id=_org_id,
                policy=policy_dict,
            )
            session.add(policy_model)

        await session.flush()


async def _verify_agent_access(agent_id: str, server: SyncServer, actor) -> None:
    """Verify the agent exists and the user has access. Raises 404 if not."""
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{agent_id}/policy",
    response_model=ToolCallPolicyResponse,
    operation_id="get_tool_call_policy",
)
async def get_tool_call_policy(
    agent_id: str,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Get the tool call policy for an agent."""
    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    await _verify_agent_access(agent_id, server, actor)
    policy = await _load_policy(agent_id, actor)
    return _to_response(agent_id, policy)


@router.put(
    "/{agent_id}/policy",
    response_model=ToolCallPolicyResponse,
    operation_id="update_tool_call_policy",
)
async def update_tool_call_policy(
    agent_id: str,
    request: ToolCallPolicyRequest,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Replace the tool call policy for an agent.

    This replaces the entire policy. To update individual fields without
    sending the full policy, use PATCH instead.
    """
    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    await _verify_agent_access(agent_id, server, actor)
    policy = _to_policy(request)
    await _save_policy(agent_id, actor, policy)
    return _to_response(agent_id, policy)


@router.patch(
    "/{agent_id}/policy",
    response_model=ToolCallPolicyResponse,
    operation_id="patch_tool_call_policy",
)
async def patch_tool_call_policy(
    agent_id: str,
    request: ToolCallPolicyPatchRequest,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Partially update the tool call policy for an agent.

    Merges the request fields into the existing policy. Only fields
    that are explicitly set (not None) are applied. To clear a field,
    use PUT with the full policy instead.
    """
    from letta.security.policy import PolicyChecker

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    await _verify_agent_access(agent_id, server, actor)

    # Load existing policy
    existing = await _load_policy(agent_id, actor)

    # Merge: only overwrite fields that are explicitly set (not None)
    if request.denied_tools is not None:
        existing.denied_tools = request.denied_tools
    if request.approval_required_tools is not None:
        existing.approval_required_tools = request.approval_required_tools
    if request.rules is not None:
        existing.rules = [_to_policy_rule(r) for r in request.rules]
    if request.max_calls_per_tool is not None:
        existing.max_calls_per_tool = request.max_calls_per_tool
    if request.defaults is not None:
        existing.defaults = _to_policy_defaults(request.defaults)

    await _save_policy(agent_id, actor, existing)
    return _to_response(agent_id, existing)


@router.post(
    "/{agent_id}/policy/evaluate",
    response_model=PolicyDecisionResponse,
    operation_id="evaluate_tool_call_policy",
)
async def evaluate_tool_call_policy(
    agent_id: str,
    request: EvaluateRequest,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Dry-run: evaluate a hypothetical tool call against the current policy.

    Takes a JSON body with tool_name and optional tool_args. Returns
    the decision (allowed, action, matched_rule, reason) without
    actually executing the tool call or recording it in the audit log.
    """
    from letta.security.policy import PolicyChecker

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    await _verify_agent_access(agent_id, server, actor)

    policy = await _load_policy(agent_id, actor)
    checker = PolicyChecker(policy)

    eval_context = {
        "tool_name": request.tool_name,
        "tool_args": request.tool_args or {},
        "tool_call_count": 0,  # dry-run: no prior calls
        "actor_id": actor.id if actor else None,
        "agent_id": agent_id,
    }

    decision = checker.check(request.tool_name, eval_context=eval_context)

    return PolicyDecisionResponse(
        allowed=decision.allowed,
        action=decision.action,
        matched_rule=decision.matched_rule,
        reason=decision.reason,
    )


@router.delete(
    "/{agent_id}/policy",
    response_model=ToolCallPolicyResponse,
    operation_id="delete_tool_call_policy",
)
async def delete_tool_call_policy(
    agent_id: str,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Delete the tool call policy for an agent (resets to allow all)."""
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    await _verify_agent_access(agent_id, server, actor)

    _org_id = actor.organization_id

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(
            ToolCallPolicyModel.agent_id == agent_id,
            ToolCallPolicyModel.organization_id == _org_id,
        )
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

        if policy_model:
            await session.delete(policy_model)
            await session.flush()

    # Return the default policy with all 5 fields
    return _to_response(agent_id, ToolCallPolicy())
