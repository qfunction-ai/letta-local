"""
v0.16.40 (U-7): Origin/Host header guard.

Audit finding: CORSMiddleware only controls what BROWSERS can read (ACAO
headers). A request with a foreign Origin and a valid password still EXECUTES
(200) — CORS is advisory, not a server-side access control. DNS-rebinding
attacks exploit exactly this: the rebound Host matches the origin, so the
browser treats it as same-origin and CORS never applies. Foreign Host headers
(requests that did not come through any name the operator serves) are the
rebinding signature and executed unchecked.

This middleware REJECTS (403):
- requests carrying an Origin not on the CORS allowlist (before routing);
- requests whose Host header names neither a localhost name, the configured
  bind host, nor an operator-extended entry (LETTA_ALLOWED_HOSTS, comma list).

Non-browser clients sending no Origin/Host or allowed ones are unaffected.
Operability note: Host checking must tolerate port suffixes (docker port
publishing rewrites Host to the published port).
"""

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import time

from letta.log import get_logger

logger = get_logger(__name__)

_DEFAULT_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _host_only(host_header: str) -> str:
    """Strip the port from a Host header (IPv6-aware)."""
    if host_header.startswith("["):  # [::1]:8283
        end = host_header.find("]")
        return host_header[: end + 1]
    if ":" in host_header:
        return host_header.rsplit(":", 1)[0]
    return host_header


class OriginHostGuardMiddleware(BaseHTTPMiddleware):
    """Server-enforced Origin + Host validation (v0.16.40 U-7).

    Host enforcement applies when the server binds a NAMED host (loopback —
    the local-dev default). A wildcard bind (0.0.0.0, the container default)
    is the operator's declared network exposure: port publishing makes Host
    names arbitrary, so foreign hosts are logged but allowed there."""

    def __init__(self, app, allowed_origins, bind_host: str = "localhost"):
        super().__init__(app)
        self.allowed_origins = set(allowed_origins)
        extra_hosts = os.getenv("LETTA_ALLOWED_HOSTS", "")
        self.allowed_hosts = set(_DEFAULT_LOCAL_HOSTS) | {bind_host.lower()}
        if extra_hosts:
            self.allowed_hosts |= {h.strip().lower() for h in extra_hosts.split(",") if h.strip()}
        # wildcard bind: Host checking is advisory (log only)
        self.host_enforced = bind_host.strip().lower() not in ("0.0.0.0", "*", "::")
        self._last_foreign_host_log = 0.0  # rate limit for the advisory log
        # v0.16.40.1 (T-2): the middleware is constructed at app import
        # time; `letta server --host` (recorded by start_server into the
        # environment) may arrive AFTER construction. Re-read HOST once on
        # first dispatch and cache — a non-loopback or wildcard env HOST
        # flips enforcement off, matching what the constructor would have
        # done had it seen the flag.
        self._bind_host_resolved = False
        self._constructor_bind_host = bind_host

    async def dispatch(self, request: Request, call_next):
        self._resolve_bind_host()
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            logger.warning(
                "Rejected request with disallowed Origin header (U-7 Origin/Host guard). "
                "Extend ACCEPTABLE_ORIGINS if this client is yours."
            )
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        host = request.headers.get("host")
        if host is not None and self.host_enforced:
            host_name = _host_only(host.strip().lower())
            if host_name not in self.allowed_hosts and host.strip().lower() not in self.allowed_hosts:
                logger.warning(
                    "Rejected request with unexpected Host header (U-7 Origin/Host guard, "
                    "DNS-rebinding signature). Extend LETTA_ALLOWED_HOSTS if this name is yours."
                )
                return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        elif host is not None and not self.host_enforced:
            # v0.16.40.1 (review K-1): wildcard binds allow foreign Hosts by
            # design, but the class docstring says "logged but allowed" — make
            # that true. Rate-limited so a hostile client can't flood the log.
            host_name = _host_only(host.strip().lower())
            if host_name not in self.allowed_hosts and host.strip().lower() not in self.allowed_hosts:
                now = time.monotonic()
                if now - self._last_foreign_host_log > 60.0:
                    self._last_foreign_host_log = now
                    logger.warning(
                        "Advisory (allowed, wildcard bind): request with foreign Host header "
                        f"{host!r} (U-7 Origin/Host guard). Extend LETTA_ALLOWED_HOSTS to silence."
                    )
        return await call_next(request)

    def _resolve_bind_host(self) -> None:
        """Resolve the effective bind host once, at first request (T-2)."""
        if self._bind_host_resolved:
            return
        self._bind_host_resolved = True
        env_host = os.getenv("HOST")
        # Empty/whitespace HOST is UNSET for our purposes: test teardowns and
        # monkeypatch restores can leave HOST="" in the process env, and a
        # stale empty value must never recompute enforcement away from the
        # constructor's bind (suite pollution caught this — u6+u7 order).
        if not env_host or not env_host.strip():
            return
        if env_host.strip().lower() == self._constructor_bind_host.strip().lower():
            return
        # start_server recorded a --host that differs from what the
        # constructor saw: recompute enforcement + allowed hosts from it.
        env_host = env_host.strip()
        self.host_enforced = env_host.lower() not in ("0.0.0.0", "*", "::")
        self.allowed_hosts = set(_DEFAULT_LOCAL_HOSTS) | {env_host.lower()}
        extra_hosts = os.getenv("LETTA_ALLOWED_HOSTS", "")
        if extra_hosts:
            self.allowed_hosts |= {h.strip().lower() for h in extra_hosts.split(",") if h.strip()}
