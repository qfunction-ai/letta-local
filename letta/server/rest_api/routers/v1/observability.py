"""API endpoints for the observability dashboard.

Read-only endpoints: aggregation overview and tool call listing.
Used by the Delta observability page. All queries are org-scoped.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from letta.server.rest_api.dependencies import HeaderParams, get_headers, get_letta_server
from letta.server.server import SyncServer

router = APIRouter(prefix="/observability", tags=["observability"])


class ObservabilityOverview(BaseModel):
    total_runs: int = Field(0, description="Total runs in the time range")
    completed_runs: int = Field(0, description="Runs with status 'completed'")
    failed_runs: int = Field(0, description="Runs with status 'failed'")
    success_rate: float = Field(0.0, description="Completed / total (0.0 if no runs)")
    avg_step_ms: Optional[float] = Field(None, description="Average step duration in milliseconds")
    total_prompt_tokens: int = Field(0, description="Sum of prompt tokens across all steps")
    total_completion_tokens: int = Field(0, description="Sum of completion tokens across all steps")
    total_tool_calls: int = Field(0, description="Total tool call records in the time range")
    total_security_events: int = Field(0, description="Total security events in the time range")


@router.get(
    "/overview",
    response_model=ObservabilityOverview,
    operation_id="get_observability_overview",
)
async def get_observability_overview(
    since: Optional[datetime] = Query(None, description="Only include data after this timestamp"),
    until: Optional[datetime] = Query(None, description="Only include data before this timestamp"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Return aggregated observability stats for a time range.

    Four separate queries in one session: runs, step metrics,
    tool calls, and security events. All scoped to the actor's
    organization.
    """
    from sqlalchemy import func, select, case

    from letta.orm.run import Run
    from letta.orm.step import Step
    from letta.orm.step_metrics import StepMetrics
    from letta.orm.tool_call import ToolCall
    from letta.orm.security_event import SecurityEvent
    from letta.schemas.enums import RunStatus
    from letta.server.db import db_registry

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    org_id = actor.organization_id

    async with db_registry.async_session() as session:
        # 1. Run counts
        run_stmt = select(
            func.count(Run.id),
            func.count(case((Run.status == RunStatus.completed, 1))),
            func.count(case((Run.status == RunStatus.failed, 1))),
        )
        run_stmt = run_stmt.where(Run.organization_id == org_id)
        if since:
            run_stmt = run_stmt.where(Run.created_at >= since)
        if until:
            run_stmt = run_stmt.where(Run.created_at <= until)
        if agent_id:
            run_stmt = run_stmt.where(Run.agent_id == agent_id)

        run_result = await session.execute(run_stmt)
        total_runs, completed_runs, failed_runs = run_result.one()

        # 2. Step metrics (avg duration, token totals)
        metrics_stmt = select(
            func.avg(StepMetrics.step_ns),
            func.coalesce(func.sum(Step.prompt_tokens), 0),
            func.coalesce(func.sum(Step.completion_tokens), 0),
        ).join(Step, StepMetrics.id == Step.id)

        metrics_stmt = metrics_stmt.where(Step.organization_id == org_id)
        if since:
            metrics_stmt = metrics_stmt.where(Step.created_at >= since)
        if until:
            metrics_stmt = metrics_stmt.where(Step.created_at <= until)
        if agent_id:
            metrics_stmt = metrics_stmt.where(Step.agent_id == agent_id)

        metrics_result = await session.execute(metrics_stmt)
        avg_step_ns, total_prompt_tokens, total_completion_tokens = metrics_result.one()

        # 3. Tool call count
        tc_stmt = select(func.count(ToolCall.id))
        tc_stmt = tc_stmt.where(ToolCall.organization_id == org_id)
        if since:
            tc_stmt = tc_stmt.where(ToolCall.created_at >= since)
        if until:
            tc_stmt = tc_stmt.where(ToolCall.created_at <= until)
        if agent_id:
            tc_stmt = tc_stmt.where(ToolCall.agent_id == agent_id)

        tc_result = await session.execute(tc_stmt)
        total_tool_calls = tc_result.scalar_one()

        # 4. Security event count
        se_stmt = select(func.count(SecurityEvent.id))
        se_stmt = se_stmt.where(SecurityEvent.organization_id == org_id)
        if since:
            se_stmt = se_stmt.where(SecurityEvent.created_at >= since)
        if until:
            se_stmt = se_stmt.where(SecurityEvent.created_at <= until)
        if agent_id:
            se_stmt = se_stmt.where(SecurityEvent.agent_id == agent_id)

        se_result = await session.execute(se_stmt)
        total_security_events = se_result.scalar_one()

    # Convert ns → ms
    avg_step_ms = None
    if avg_step_ns is not None:
        avg_step_ms = round(avg_step_ns / 1_000_000, 1)

    success_rate = 0.0
    if total_runs > 0:
        success_rate = round(completed_runs / total_runs, 3)

    return ObservabilityOverview(
        total_runs=total_runs,
        completed_runs=completed_runs,
        failed_runs=failed_runs,
        success_rate=success_rate,
        avg_step_ms=avg_step_ms,
        total_prompt_tokens=total_prompt_tokens or 0,
        total_completion_tokens=total_completion_tokens or 0,
        total_tool_calls=total_tool_calls,
        total_security_events=total_security_events,
    )


class ToolCallResponse(BaseModel):
    id: str = Field(..., description="Tool call ID")
    step_id: str = Field(..., description="Parent step ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    tool_name: str = Field(..., description="Tool name")
    tool_args: Optional[dict] = Field(None, description="Arguments passed to the tool")
    tool_result: Optional[str] = Field(None, description="Result returned (truncated)")
    duration_ms: Optional[float] = Field(None, description="Execution duration in milliseconds")
    success: bool = Field(True, description="Whether execution succeeded")
    error: Optional[str] = Field(None, description="Error message if failed")
    created_at: Optional[datetime] = Field(None, description="Timestamp")


class ToolCallListResponse(BaseModel):
    tool_calls: List[ToolCallResponse] = Field(..., description="List of tool calls")
    count: int = Field(..., description="Number of tool calls returned")


@router.get(
    "/tool-calls",
    response_model=ToolCallListResponse,
    operation_id="list_observability_tool_calls",
)
async def list_tool_calls(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    tool_name: Optional[str] = Query(None, description="Filter by tool name"),
    success: Optional[bool] = Query(None, description="Filter by success/failure"),
    since: Optional[datetime] = Query(None, description="Only return tool calls after this timestamp"),
    until: Optional[datetime] = Query(None, description="Only return tool calls before this timestamp"),
    limit: int = Query(100, description="Maximum number of tool calls to return", ge=1, le=1000),
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """List tool call records from the observability store.

    Org-scoped, read-only. Ordered by created_at descending.
    """
    from sqlalchemy import desc, select

    from letta.orm.tool_call import ToolCall
    from letta.server.db import db_registry

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    org_id = actor.organization_id

    async with db_registry.async_session() as session:
        stmt = select(ToolCall).order_by(desc(ToolCall.created_at)).limit(limit)
        stmt = stmt.where(ToolCall.organization_id == org_id)

        if agent_id:
            stmt = stmt.where(ToolCall.agent_id == agent_id)
        if tool_name:
            stmt = stmt.where(ToolCall.tool_name == tool_name)
        if success is not None:
            stmt = stmt.where(ToolCall.success == success)
        if since:
            stmt = stmt.where(ToolCall.created_at >= since)
        if until:
            stmt = stmt.where(ToolCall.created_at <= until)

        result = await session.execute(stmt)
        tool_calls = result.scalars().all()

    return ToolCallListResponse(
        tool_calls=[
            ToolCallResponse(
                id=tc.id,
                step_id=tc.step_id,
                agent_id=tc.agent_id,
                tool_name=tc.tool_name,
                tool_args=tc.tool_args,
                tool_result=tc.tool_result[:500] if tc.tool_result else None,
                duration_ms=round(tc.duration_ns / 1_000_000, 1) if tc.duration_ns else None,
                success=tc.success,
                error=tc.error,
                created_at=tc.created_at,
            )
            for tc in tool_calls
        ],
        count=len(tool_calls),
    )
