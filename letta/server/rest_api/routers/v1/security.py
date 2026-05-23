"""API endpoints for the security audit log.

Read-only access to the security_events table. No write endpoints —
events are only added through the AuditLogger. The audit log is
append-only.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from letta.server.rest_api.dependencies import HeaderParams, get_headers, get_letta_server
from letta.server.server import SyncServer

router = APIRouter(prefix="/security", tags=["security"])


class SecurityEventResponse(BaseModel):
    id: str = Field(..., description="Security event ID")
    agent_id: str = Field(..., description="Agent ID")
    organization_id: str = Field(..., description="Organization ID")
    step_id: Optional[str] = Field(None, description="Step ID (if applicable)")
    run_id: Optional[str] = Field(None, description="Run ID (if applicable)")
    event_type: str = Field(..., description="Event type (e.g. tool_executed, tool_denied)")
    event_data: Optional[dict] = Field(None, description="Event-type-specific data (JSON)")
    actor_id: Optional[str] = Field(None, description="Actor ID")
    created_at: Optional[datetime] = Field(None, description="Timestamp")


class SecurityEventListResponse(BaseModel):
    events: List[SecurityEventResponse] = Field(..., description="List of security events")
    count: int = Field(..., description="Number of events returned")


@router.get(
    "/events",
    response_model=SecurityEventListResponse,
    operation_id="list_security_events",
)
async def list_security_events(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    since: Optional[datetime] = Query(None, description="Only return events after this timestamp"),
    limit: int = Query(100, description="Maximum number of events to return", ge=1, le=1000),
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """List security events from the audit log.

    Append-only, no write endpoints. Filter by agent_id, event_type,
    and since (timestamp). Results are ordered by created_at descending.
    """
    from letta.orm.security_event import SecurityEvent
    from letta.server.db import db_registry
    from sqlalchemy import select, desc

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)

    async with db_registry.async_session() as session:
        stmt = select(SecurityEvent).order_by(desc(SecurityEvent.created_at)).limit(limit)

        if agent_id:
            stmt = stmt.where(SecurityEvent.agent_id == agent_id)
        if event_type:
            stmt = stmt.where(SecurityEvent.event_type == event_type)
        if since:
            stmt = stmt.where(SecurityEvent.created_at >= since)

        # Scope to the actor's organization
        stmt = stmt.where(SecurityEvent.organization_id == actor.organization_id)

        result = await session.execute(stmt)
        events = result.scalars().all()

    return SecurityEventListResponse(
        events=[
            SecurityEventResponse(
                id=e.id,
                agent_id=e.agent_id,
                organization_id=e.organization_id,
                step_id=e.step_id,
                run_id=e.run_id,
                event_type=e.event_type,
                event_data=e.event_data,
                actor_id=e.actor_id,
                created_at=e.created_at,
            )
            for e in events
        ],
        count=len(events),
    )


@router.get(
    "/events/{event_id}",
    response_model=SecurityEventResponse,
    operation_id="get_security_event",
)
async def get_security_event(
    event_id: str,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Get a single security event by ID."""
    from letta.orm.security_event import SecurityEvent
    from letta.server.db import db_registry
    from sqlalchemy import select

    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)

    async with db_registry.async_session() as session:
        stmt = select(SecurityEvent).where(
            SecurityEvent.id == event_id,
            SecurityEvent.organization_id == actor.organization_id,
        )
        result = await session.execute(stmt)
        event = result.scalar_one_or_none()

    if event is None:
        raise HTTPException(status_code=404, detail=f"Security event {event_id} not found")

    return SecurityEventResponse(
        id=event.id,
        agent_id=event.agent_id,
        organization_id=event.organization_id,
        step_id=event.step_id,
        run_id=event.run_id,
        event_type=event.event_type,
        event_data=event.event_data,
        actor_id=event.actor_id,
        created_at=event.created_at,
    )
