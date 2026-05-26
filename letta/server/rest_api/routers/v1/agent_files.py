"""API endpoints for agent file workspace access.

Lets humans download files that agents wrote via the file_write tool.
The files live in per-agent directories on the server filesystem.
"""

import mimetypes
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from letta.functions.function_sets.file_persistence import _agent_file_dir, _validate_path
from letta.server.rest_api.dependencies import HeaderParams, get_headers, get_letta_server
from letta.server.server import SyncServer

router = APIRouter(prefix="/agents", tags=["agent-files"])


class AgentFileInfo(BaseModel):
    name: str = Field(..., description="Relative file path within the agent's workspace")
    size: int = Field(..., description="File size in bytes")
    modified_at: str = Field(..., description="Last modification time (ISO 8601)")


class AgentFileListResponse(BaseModel):
    agent_id: str
    files: List[AgentFileInfo]
    total_size: int = Field(..., description="Total size of all files in bytes")


@router.get(
    "/{agent_id}/files",
    response_model=AgentFileListResponse,
    operation_id="list_agent_files",
)
async def list_agent_files(
    agent_id: str,
    prefix: Optional[str] = None,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """List files in an agent's workspace."""
    # Verify the agent exists and the user has access
    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    # Build a minimal agent_state-like object for _agent_file_dir
    class _AgentStateRef:
        def __init__(self, aid):
            self.id = aid

    base_dir = _agent_file_dir(_AgentStateRef(agent_id))

    if prefix:
        try:
            search_dir = _validate_path(base_dir, prefix)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        search_dir = base_dir

    if not search_dir.exists():
        return AgentFileListResponse(agent_id=agent_id, files=[], total_size=0)

    files = []
    total_size = 0
    for f in sorted(search_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(base_dir))
        stat = f.stat()
        from datetime import datetime, timezone
        files.append(AgentFileInfo(
            name=rel,
            size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        ))
        total_size += stat.st_size

    return AgentFileListResponse(agent_id=agent_id, files=files, total_size=total_size)


@router.get(
    "/{agent_id}/files/{path:path}",
    operation_id="get_agent_file",
)
async def get_agent_file(
    agent_id: str,
    path: str,
    server: SyncServer = Depends(get_letta_server),
    headers: HeaderParams = Depends(get_headers),
):
    """Download a file from an agent's workspace."""
    actor = await server.user_manager.get_actor_or_default_async(actor_id=headers.actor_id)
    try:
        await server.agent_manager.get_agent_by_id_async(agent_id=agent_id, actor=actor)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")

    class _AgentStateRef:
        def __init__(self, aid):
            self.id = aid

    base_dir = _agent_file_dir(_AgentStateRef(agent_id))

    try:
        file_path = _validate_path(base_dir, path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Determine content type from extension
    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"

    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        filename=file_path.name,
    )
