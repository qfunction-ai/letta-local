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

    async def dispatch(self, request: Request, call_next):
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
        return await call_next(request)
