"""
Server credential handling — v0.16.38 L-3/L-1 closure (review R-1).

The server password, the Postgres URI, and the encryption key must never
reach a sandboxed tool environment. The env allowlist (base.py) stops them
arriving through os.environ; this module scrubs them from EVERY channel
after layering (DB global/provided/agent/runtime vars), because an operator
who puts the server password into a sandbox env var reopens L-3 through the
"sanctioned" path. Exact-match per review ruling; containment matching is
a future hardening item, not this release.

Never log the values. Log key names only.
"""

import logging
import os
import secrets as _secrets

logger = logging.getLogger(__name__)

# Registered once at app startup (resolve_server_password). Empty until then
# (e.g. unit tests importing this module without a server) — scrub is a no-op.
_server_password: str | None = None


def resolve_server_password() -> str:
    """LETTA_SERVER_PASSWORD if set, else a generated value. Registered module-level."""
    global _server_password
    _server_password = os.getenv("LETTA_SERVER_PASSWORD") or _secrets.token_urlsafe(16)
    return _server_password


def get_server_password() -> str | None:
    return _server_password


def get_sensitive_env_values() -> set[str]:
    """Values that must never appear in a sandboxed tool environment (exact match)."""
    values: set[str] = set()
    if _server_password:
        values.add(_server_password)
    try:
        from letta.settings import settings

        if settings.pg_uri:
            values.add(settings.pg_uri)
        if getattr(settings, "encryption_key", None):
            values.add(str(settings.encryption_key))
    except Exception:
        # Settings unavailable (early import, exotic test env): password-only scrub.
        pass
    return values


def scrub_sensitive_env(env: dict[str, str | None]) -> dict[str, str | None]:
    """Drop variables whose VALUE equals a server credential. Logs key names, never values."""
    sensitive = get_sensitive_env_values()
    if not sensitive:
        return env
    scrubbed = {}
    for key, value in env.items():
        if value is not None and value in sensitive:
            logger.warning(
                f"Environment variable '{key}' matched a server credential — scrubbed from tool environment. "
                "The server password must never be given to tools (v0.16.38 L-1/R-1)."
            )
            continue
        scrubbed[key] = value
    return scrubbed


def validate_env_var_value(key: str, value) -> None:
    """Raise ValueError if an env-var write carries a server credential (API layer wraps as 400)."""
    if value is None:
        return
    if _server_password and value == _server_password:
        raise ValueError(
            f"Environment variable '{key}' may not carry the server password. "
            "Tools that need API access need their own scoped credential (v0.16.39 design item)."
        )


def reject_sensitive_env_vars(env_vars: dict | None) -> None:
    """Raise ValueError if any value in a dict of env vars carries a server password (API layer wraps as 400)."""
    if not env_vars:
        return
    for key, value in env_vars.items():
        validate_env_var_value(key, value)
