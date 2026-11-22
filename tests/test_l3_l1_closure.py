"""v0.16.38 L-3/L-1 closure tests (plan W2/W3/R-1, review REV 2)."""

import os

import pytest

from letta.security import server_credentials as sc


@pytest.fixture
def registered_password(monkeypatch):
    """Register a known server password, restore module state after."""
    monkeypatch.setenv("LETTA_SERVER_PASSWORD", "test-server-password-123")
    sc.resolve_server_password()
    yield "test-server-password-123"
    sc._server_password = None


class _FakeSandboxManager:
    async def get_sandbox_env_vars_as_dict_async(self, sandbox_config_id, actor, limit=None):
        return {}


class _FakeExecutor:
    sandbox_config_manager = _FakeSandboxManager()
    provided_sandbox_env_vars = None
    agent_id = "agent-test"
    project_id = None
    tool_id = "tool-test"
    user = None


async def _gather(monkeypatch, os_env: dict, **kwargs):
    from letta.services.tool_sandbox.base import AsyncToolSandboxBase

    for k, v in os_env.items():
        monkeypatch.setenv(k, v)
    return await AsyncToolSandboxBase._gather_env_vars(
        _FakeExecutor(), None, kwargs.pop("additional_env_vars", None), "sbx-test", True
    )


class TestEnvAllowlist:
    async def test_server_password_never_inherited(self, monkeypatch, registered_password):
        env = await _gather(monkeypatch, {"LETTA_SERVER_PASSWORD": registered_password, "PATH": "/usr/bin"})
        assert "LETTA_SERVER_PASSWORD" not in env
        assert env["PATH"] == "/usr/bin"

    async def test_allowlist_excludes_unlisted_os_vars(self, monkeypatch, registered_password):
        env = await _gather(monkeypatch, {"SOME_RANDOM_VAR": "x", "AWS_SECRET_ACCESS_KEY": "y", "PATH": "/usr/bin"})
        assert "SOME_RANDOM_VAR" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env

    async def test_injected_letta_vars_present(self, monkeypatch, registered_password):
        env = await _gather(monkeypatch, {})
        assert env["LETTA_AGENT_ID"] == "agent-test"
        assert env["LETTA_TOOL_ID"] == "tool-test"
        assert env["LETTA_STAGING_DIR"].endswith(".staging")


class TestValueScrub:
    async def test_password_valued_runtime_var_scrubbed(self, monkeypatch, registered_password):
        env = await _gather(
            monkeypatch,
            {},
            additional_env_vars={"MY_TOOL_CRED": registered_password, "MY_TOOL_OK": "fine"},
        )
        assert "MY_TOOL_CRED" not in env
        assert env["MY_TOOL_OK"] == "fine"

    async def test_password_valued_provided_var_scrubbed(self, monkeypatch, registered_password):
        _FakeExecutor.provided_sandbox_env_vars = {"PROVIDED_CRED": registered_password}
        try:
            env = await _gather(monkeypatch, {})
            assert "PROVIDED_CRED" not in env
        finally:
            _FakeExecutor.provided_sandbox_env_vars = None


class TestApiValidation:
    def test_reject_password_valued_env_var(self, registered_password):
        with pytest.raises(ValueError, match="server password"):
            sc.validate_env_var_value("LETTA_API_KEY", registered_password)

    def test_reject_password_in_dict(self, registered_password):
        with pytest.raises(ValueError):
            sc.reject_sensitive_env_vars({"A": "ok", "B": registered_password})

    def test_accept_normal_values(self, registered_password):
        sc.validate_env_var_value("MY_VAR", "totally-fine")
        sc.reject_sensitive_env_vars({"A": "ok", "B": "also-ok"})

    def test_no_password_registered_is_noop(self):
        assert sc._server_password is None
        sc.validate_env_var_value("ANY", "any-value")  # must not raise


class TestPasswordMiddleware:
    def _make_middleware(self, password):
        from letta.server.rest_api.middleware.check_password import CheckPasswordMiddleware

        return CheckPasswordMiddleware(app=None, password=password)

    @pytest.mark.asyncio
    async def test_bearer_header_passes(self):
        mw = self._make_middleware("pw123")

        class R:
            url = type("U", (), {"path": "/v1/agents"})()
            headers = {"Authorization": "Bearer pw123"}

        async def call_next(request):
            return "passed"

        assert await mw.dispatch(R(), call_next) == "passed"

    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self):
        mw = self._make_middleware("pw123")

        class R:
            url = type("U", (), {"path": "/v1/agents"})()
            headers = {"Authorization": "Bearer wrong"}

        async def call_next(request):
            return "passed"

        response = await mw.dispatch(R(), call_next)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_bare_password_header_passes(self):
        mw = self._make_middleware("pw123")

        class R:
            url = type("U", (), {"path": "/v1/agents"})()
            headers = {"X-BARE-PASSWORD": "password pw123"}

        async def call_next(request):
            return "passed"

        assert await mw.dispatch(R(), call_next) == "passed"
