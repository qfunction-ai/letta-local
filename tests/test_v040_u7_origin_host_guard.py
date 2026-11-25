"""v0.16.40 U-7: Origin/Host guard witnesses.

Audit finding (live-probed on the pre-fix tree): foreign-Origin requests
executed 200 (CORS middleware is browser-advisory); foreign Host headers
(DNS-rebinding signature) executed unchecked. The guard rejects both —
Host enforcement scoped to NAMED binds (wildcard binds are the operator's
declared exposure via port publishing).
"""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from letta.security.origin_host_guard import OriginHostGuardMiddleware


async def _ep(request):
    return JSONResponse({"ok": True})


def _client(bind, allowed=("http://localhost:3000",), extra_env=None, monkeypatch=None):
    """NOTE: Starlette instantiates middleware LAZILY (first request), so env
    overrides must LIVE for the whole test — pass monkeypatch and the env is
    restored at test end."""
    import os

    if extra_env:
        if monkeypatch is not None:
            monkeypatch.setenv("LETTA_ALLOWED_HOSTS", extra_env)
        else:
            os.environ["LETTA_ALLOWED_HOSTS"] = extra_env
    app = Starlette(routes=[Route("/x", _ep)])
    app.add_middleware(OriginHostGuardMiddleware, allowed_origins=list(allowed), bind_host=bind)
    return TestClient(app)


class TestOriginGuard:
    def test_foreign_origin_rejected(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Origin": "http://evil.example"}).status_code == 403

    def test_allowed_origin_passes(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Origin": "http://localhost:3000", "Host": "localhost:8284"}).status_code == 200

    def test_no_origin_passes(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Host": "localhost:8283"}).status_code == 200

    def test_origin_guard_active_on_wildcard_bind(self):
        # Origin enforcement is NOT scoped to named binds — it applies always
        c = _client("0.0.0.0")
        assert c.get("/x", headers={"Origin": "http://evil.example", "Host": "192.168.1.5:8284"}).status_code == 403


class TestHostGuard:
    def test_foreign_host_rejected_on_named_bind(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Host": "evil.example"}).status_code == 403

    def test_localhost_host_with_port_passes(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Host": "localhost:8284"}).status_code == 200

    def test_ipv6_loopback_host_passes(self):
        c = _client("localhost")
        assert c.get("/x", headers={"Host": "[::1]:8283"}).status_code == 200

    def test_wildcard_bind_host_advisory(self):
        # 0.0.0.0 = port publishing is the operator's declared exposure
        c = _client("0.0.0.0")
        assert c.get("/x", headers={"Host": "192.168.1.5:8284"}).status_code == 200

    def test_operator_extended_hosts(self, monkeypatch):
        c = _client("localhost", extra_env="letta.internal.example, second.name", monkeypatch=monkeypatch)
        assert c.get("/x", headers={"Host": "letta.internal.example:8283"}).status_code == 200
        assert c.get("/x", headers={"Host": "second.name"}).status_code == 200

    def test_unextended_host_still_rejected(self, monkeypatch):
        c = _client("localhost", extra_env="letta.internal.example", monkeypatch=monkeypatch)
        assert c.get("/x", headers={"Host": "other.example"}).status_code == 403


from letta.security.origin_host_guard import _host_only


class TestHostParsing:
    def test_plain(self):
        assert _host_only("localhost") == "localhost"

    def test_port(self):
        assert _host_only("localhost:8283") == "localhost"

    def test_ipv6_bracketed(self):
        assert _host_only("[::1]:8283") == "[::1]"
        assert _host_only("[::1]") == "[::1]"
