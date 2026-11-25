"""
Server credential handling — v0.16.38 L-3/L-1 closure (review R-1).

The server password, the Postgres URI, and the encryption key must never
reach a sandboxed tool environment. The env allowlist (base.py) stops them
arriving through os.environ; this module scrubs them from EVERY channel
after layering (DB global/provided/agent/runtime vars), because an operator
who puts the server password into a sandbox env var reopens L-3 through the
"sanctioned" path. Containment matching (v0.16.39 B-10): a value is dropped
if it CONTAINS any secret, raw or URL-encoded — URLs are the common hiding
place (http://u:<password>@host).

Never log the values. Log key names only.
"""

import logging
import os
import secrets as _secrets
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Registered once at app startup (resolve_server_password). Empty until then
# (e.g. unit tests importing this module without a server) — scrub is a no-op.
_server_password: str | None = None


def resolve_server_password() -> str:
    """LETTA_SERVER_PASSWORD if set, else the persisted password file, else a
    fresh generation (persisted best-effort so it survives restarts — v0.16.39 B-8).

    Precedence: env var wins over the file; the file wins over generation; an
    empty/absent file triggers generation. The file is written mode 0600.
    Registered module-level.
    """
    global _server_password
    env_pw = os.getenv("LETTA_SERVER_PASSWORD")
    if env_pw:
        _server_password = env_pw
        return _server_password
    file_pw = _read_password_file()
    if file_pw:
        _server_password = file_pw
        return _server_password
    _server_password = _secrets.token_urlsafe(16)
    _write_password_file(_server_password)
    return _server_password


def _password_file_path() -> Path | None:
    try:
        from letta.settings import settings

        return Path(settings.letta_dir) / "server_password"
    except Exception:
        # Settings unavailable (early import, exotic test env): no file persistence.
        return None


def _read_password_file() -> str | None:
    """Read the persisted password, warning + tightening if the file's
    permissions are looser than 0600 (v0.16.40 R-6: a pre-existing loose file
    is not silently reused)."""
    path = _password_file_path()
    if path is None:
        return None
    try:
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            logger.warning(
                "server_password file has loose permissions (%o); tightening to 0600. "
                "Check who created it.",
                mode,
            )
            try:
                path.chmod(0o600)
            except OSError:
                pass
        value = path.read_text().strip()
        return value or None
    except OSError:
        return None


def _write_password_file(value: str) -> None:
    """Best-effort persistence, mode 0600. Unwritable dir: password still works
    for this process (app.py's secure-mode block logs the guidance).

    v0.16.40 (R-6): the file is created via a 0600 temp file + atomic rename —
    there is no window where the password exists on disk with umask-derived
    permissions (write_text-then-chmod had a 0644-shaped window). This is the
    SINGLE writer: app.py's secure-mode block delegates here rather than
    writing its own copy.
    """
    path = _password_file_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, value.encode())
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:
        # Best-effort: password still works for this process.
        try:
            path.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass


def get_server_password() -> str | None:
    return _server_password


def check_encryption_at_startup(secure: bool) -> None:
    """v0.16.39 (B-4): fail loudly when secrets would be stored in plaintext.

    - LETTA_REQUIRE_ENCRYPTION=true with no LETTA_ENCRYPTION_KEY: refuse to
      start (RuntimeError), regardless of secure mode — an explicit operator
      demand is honored even in insecure mode.
    - Secure mode with no key: one startup WARNING (per-write fallback still
      happens in Secret.from_plaintext with its own warning).
    - With a key configured: quiet.
    """
    try:
        from letta.settings import settings

        key = getattr(settings, "encryption_key", None)
        require = getattr(settings, "require_encryption", False)
    except Exception:
        # Settings unavailable (exotic import context): skip the startup check.
        return
    if key:
        return
    if require:
        raise RuntimeError(
            "LETTA_REQUIRE_ENCRYPTION is set but no LETTA_ENCRYPTION_KEY is configured. "
            "The server refuses to start: secrets would be stored in plaintext. "
            "Set LETTA_ENCRYPTION_KEY (recommended: a 32+ character random string)."
        )
    if secure:
        logger.warning(
            "Secure mode is ON but no encryption key is configured — secrets "
            "(API keys, credentials) will be stored in PLAINTEXT in the database. "
            "Set LETTA_ENCRYPTION_KEY, or set LETTA_REQUIRE_ENCRYPTION=true to "
            "make the server refuse to start without it."
        )


# v0.16.40 (R-5): containment (contains) matching only for secrets at least
# this long. Shorter secrets substring-match benign values (a 4-char operator
# password scrubs PATH via 'bin'; the compose-default DB password 'letta'
# matches 'letta' anywhere). Below the floor: exact match only + a startup
# WARNING that the password is short.
CONTAINMENT_MIN_SECRET_LENGTH = 12


def get_sensitive_env_values() -> set[str]:
    """Values that must never appear in a sandboxed tool environment.

    v0.16.40 (R-5): includes the Postgres PASSWORD COMPONENT of the URI (a
    value carrying just the DB password previously passed — only the whole
    URI matched). Secrets shorter than CONTAINMENT_MIN_SECRET_LENGTH are
    exact-match-only downstream.
    """
    values: set[str] = set()
    if _server_password:
        values.add(_server_password)
    try:
        from letta.settings import settings

        if settings.pg_uri:
            values.add(settings.pg_uri)
            # R-5: the PG password component (password=... in the URI) is its
            # own secret — a value carrying ONLY it must not pass.
            try:
                from urllib.parse import urlparse, unquote

                pw = urlparse(settings.pg_uri).password
                if pw:
                    values.add(unquote(pw))
            except Exception:
                pass
        if getattr(settings, "encryption_key", None):
            values.add(str(settings.encryption_key))
    except Exception:
        # Settings unavailable (early import, exotic test env): password-only scrub.
        pass
    return values


def check_insecure_bind_host(host: str | None) -> None:
    """v0.16.40 (U-6): insecure mode is only for loopback. Raises RuntimeError
    (naming the fix) when insecure mode would bind a non-loopback host."""
    effective = (host or os.getenv("HOST") or "localhost").strip()
    if effective not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError(
            f"Insecure mode (LETTA_SERVER_SECURE=false) refuses to bind non-loopback host '{effective}'. "
            "Set LETTA_SERVER_PASSWORD (and remove LETTA_SERVER_SECURE=false), or bind to 127.0.0.1."
        )


def warn_on_short_secrets() -> None:
    """v0.16.40 (R-5): one WARNING per start when a configured secret is too
    short for containment matching (exact-match-only protection)."""
    for s in get_sensitive_env_values():
        if len(s) < CONTAINMENT_MIN_SECRET_LENGTH:
            logger.warning(
                "A server secret is only %d characters — below the containment-match "
                "floor (%d). Protection for it is exact-match only. Use a longer secret "
                "(32+ recommended).",
                len(s),
                CONTAINMENT_MIN_SECRET_LENGTH,
            )
            # warn once per short secret, not once per call site
            break


def scrub_sensitive_env(env: dict[str, str | None]) -> dict[str, str | None]:
    """Drop variables whose VALUE carries a server credential (contains it, raw
    or URL-encoded — v0.16.39 B-10). Logs key names, never values."""
    sensitive = get_sensitive_env_values()
    if not sensitive:
        return env
    scrubbed = {}
    for key, value in env.items():
        if value is not None and _value_carries_secret(str(value), sensitive):
            logger.warning(
                f"Environment variable '{key}' matched a server credential — scrubbed from tool environment. "
                "The server password must never be given to tools (v0.16.38 L-1/R-1; "
                "containment matching v0.16.39 B-10)."
            )
            continue
        scrubbed[key] = value
    return scrubbed


def _value_carries_secret(value: str, sensitive: set[str]) -> bool:
    """True if value carries any secret.

    v0.16.40 (R-5): secrets at least CONTAINMENT_MIN_SECRET_LENGTH long match
    by CONTAINMENT (raw or URL-encoded) — the v0.16.39 B-10 behavior. Shorter
    secrets match EXACT-ONLY: a 4-char password must not scrub PATH because
    it appears as a substring of 'bin'.
    """
    for s in sensitive:
        if len(s) >= CONTAINMENT_MIN_SECRET_LENGTH:
            if s in value:
                return True
            encoded = quote(s, safe="")
            if encoded != s and encoded in value:
                return True
        else:
            if value == s:
                return True
    return False


def validate_env_var_value(key: str, value) -> None:
    """Raise ValueError if an env-var write carries a server credential — the
    password, the Postgres URI, or the encryption key, contained raw or
    URL-encoded (v0.16.39 B-10; parity with scrub_sensitive_env). API layer
    wraps as 400."""
    if value is None:
        return
    sensitive = get_sensitive_env_values()
    if sensitive and _value_carries_secret(str(value), sensitive):
        raise ValueError(
            f"Environment variable '{key}' may not carry a server credential "
            "(server password, database URI, or encryption key). "
            "Tools that need API access need their own scoped credential (v0.16.39 design item)."
        )


def reject_sensitive_env_vars(env_vars: dict | None) -> None:
    """Raise ValueError if any value in a dict of env vars carries a server password (API layer wraps as 400)."""
    if not env_vars:
        return
    for key, value in env_vars.items():
        validate_env_var_value(key, value)
