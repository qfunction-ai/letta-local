"""B-9 witnesses: agent create/modify must reject env vars carrying server credentials.

RED on baseline (v0.16.38): create_agent and modify_agent had zero validation of
tool_exec_environment_variables / secrets. Fixed tree: 400, matching the import paths
and the v0.16.38 consumer notice's promise.
"""

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import letta.security.server_credentials as sc
from letta.server.rest_api.routers.v1.agents import create_agent, modify_agent, CreateAgentRequest
from letta.schemas.agent import UpdateAgent
from fastapi import HTTPException

PASSWORD = "b09-test-server-password-xyz"


@pytest.fixture
def registered_password(monkeypatch):
    monkeypatch.setenv("LETTA_SERVER_PASSWORD", PASSWORD)
    sc.resolve_server_password()
    yield PASSWORD
    sc._server_password = None


def _stub_server():
    server = MagicMock()
    server.user_manager.get_actor_or_default_async = AsyncMock(return_value=types.SimpleNamespace(id="u1"))
    server.create_agent_async = AsyncMock(return_value="created-ok")
    server.update_agent_async = AsyncMock(return_value="updated-ok")
    return server


HEADERS = types.SimpleNamespace(actor_id=None)


async def _create(env_field: str, value: dict):
    kwargs = {env_field: value}
    agent = CreateAgentRequest.model_construct(**kwargs)
    return await create_agent(agent=agent, server=_stub_server(), headers=HEADERS)


async def _modify(env_field: str, value: dict):
    update = UpdateAgent.model_construct(**{env_field: value})
    return await modify_agent(agent_id="agent-00000000-0000-0000-0000-000000000000", update_agent=update, server=_stub_server(), headers=HEADERS)


@pytest.mark.asyncio
async def test_create_rejects_password_env_var(registered_password):
    """(a) create_agent with a password-valued tool_exec_environment_variables -> 400."""
    with pytest.raises(HTTPException) as exc:
        await _create("tool_exec_environment_variables", {"MY_VAR": PASSWORD})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_modify_rejects_password_env_var(registered_password):
    """(b) modify_agent with a password-valued tool_exec_environment_variables -> 400."""
    with pytest.raises(HTTPException) as exc:
        await _modify("tool_exec_environment_variables", {"MY_VAR": PASSWORD})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_benign_env_var_passes_create(registered_password):
    """(c) benign env var still passes create (no false positive)."""
    result = await _create("tool_exec_environment_variables", {"MY_VAR": "totally-benign-value"})
    assert result == "created-ok"


@pytest.mark.asyncio
async def test_benign_env_var_passes_modify(registered_password):
    """(c) benign env var still passes modify."""
    result = await _modify("tool_exec_environment_variables", {"MY_VAR": "totally-benign-value"})
    assert result == "updated-ok"


@pytest.mark.asyncio
async def test_create_rejects_password_in_secrets_field(registered_password):
    """The current (non-deprecated) secrets field is the same channel — must be covered too."""
    with pytest.raises(HTTPException) as exc:
        await _create("secrets", {"API_TOKEN": PASSWORD})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_modify_rejects_password_in_secrets_field(registered_password):
    with pytest.raises(HTTPException) as exc:
        await _modify("secrets", {"API_TOKEN": PASSWORD})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_benign_secrets_pass_create(registered_password):
    result = await _create("secrets", {"API_TOKEN": "benign-token"})
    assert result == "created-ok"
