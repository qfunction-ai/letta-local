"""API endpoints for managing per-agent tool call policies.

Tool call policies are the security boundary between the LLM's tool
decisions and the server's execution. They define which tools are
denied, which require human approval, and which are allowed by default.

The policy is stored in a separate table (tool_call_policies), not on
the agent state, to avoid modifying shared schema files.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from letta.security.policy import ToolCallPolicy
from letta.server.rest_api.dependencies import HeaderParams, get_headers, get_letta_server
from letta.server.server import SyncServer

router = APIRouter(prefix="/agents", tags=["agent-policies"])


class ToolCallPolicyRequest(BaseModel):
    denied_tools: list[str] = Field(default_factory=list, description="Tools that are always denied.")
    approval_required_tools: list[str] = Field(default_factory=list, description="Tools that require human approval.")


class ToolCallPolicyResponse(BaseModel):
    agent_id: str = Field(..., description="Agent ID")
    denied_tools: list[str] = Field(default_factory=list, description="Denied tools")
    approval_required_tools: list[str] = Field(default_factory=list, description="Tools requiring approval")


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
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)

    # Verify the agent exists and the user has access
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(ToolCallPolicyModel.agent_id == agent_id)
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

    if policy_model and policy_model.policy:
        policy = ToolCallPolicy(**policy_model.policy)
    else:
        policy = ToolCallPolicy()  # default: allow all

    return ToolCallPolicyResponse(
        agent_id=agent_id,
        denied_tools=policy.denied_tools,
        approval_required_tools=policy.approval_required_tools,
    )


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
    """Update the tool call policy for an agent."""
    from letta.orm.tool_call_policy import ToolCallPolicyModel
    from letta.server.db import db_registry
    from sqlalchemy import select

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)

    # Verify the agent exists and the user has access
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    policy = ToolCallPolicy(
        denied_tools=request.denied_tools,
        approval_required_tools=request.approval_required_tools,
    )

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(ToolCallPolicyModel.agent_id == agent_id)
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

        if policy_model:
            policy_model.policy = policy.model_dump()
        else:
            policy_model = ToolCallPolicyModel(
                agent_id=agent_id,
                policy=policy.model_dump(),
            )
            session.add(policy_model)

        await session.flush()

    return ToolCallPolicyResponse(
        agent_id=agent_id,
        denied_tools=policy.denied_tools,
        approval_required_tools=policy.approval_required_tools,
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

    # Verify the agent exists and the user has access
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    async with db_registry.async_session() as session:
        stmt = select(ToolCallPolicyModel).where(ToolCallPolicyModel.agent_id == agent_id)
        result = await session.execute(stmt)
        policy_model = result.scalar_one_or_none()

        if policy_model:
            await session.delete(policy_model)
            await session.flush()

    return ToolCallPolicyResponse(
        agent_id=agent_id,
        denied_tools=[],
        approval_required_tools=[],
    )
