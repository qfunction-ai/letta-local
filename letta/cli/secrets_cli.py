"""v0.16.40 (E-1): `letta secrets reencrypt` — re-encrypt plaintext secrets.

One-off admin command for installs that ran without LETTA_ENCRYPTION_KEY and
later set one. Without it, legacy rows stay plaintext forever: the write path
reuses stored values, and nothing migrates the columns.

Classes (dry run):
  unmarked-encrypted  decrypts with the current key — to be MARKED
  plaintext           decrypt fails, not base64-looking — to be ENCRYPTED and marked
  ambiguous           decrypt fails, looks like base64 — legacy high-entropy
                      plaintext OR ciphertext from a different key; blocks
                      --apply unless --treat-ambiguous-as-plaintext + --yes

`--apply` rewrites plaintext AND unmarked-encrypted rows (one transaction per
table), re-reads each row to confirm round-trip before committing, and is
idempotent. Identifies rows by table, column, and row ID only — no value or
fragment is ever printed or logged.

Run inside the container: docker exec <container> letta secrets reencrypt
"""

import asyncio
from enum import Enum

import typer

from letta.helpers.crypto_utils import ENCRYPTION_MARKER, CryptoUtils
from letta.log import get_logger
from letta.server.db import db_registry

logger = get_logger(__name__)

secrets_app = typer.Typer(help="Admin commands for stored secrets (E-1).")

# Table -> encrypted columns. v0.16.40 E-1 inventory (verified against letta/orm).
SECRET_TABLES: dict[str, list[tuple[str, str]]] = {
    # table name -> [(ORM attribute, column name)]
    "agent_environment_variables": [("value_enc", "value_enc")],
    "sandbox_environment_variables": [("value_enc", "value_enc")],
    "providers": [("api_key_enc", "api_key_enc"), ("access_key_enc", "access_key_enc")],
    "mcp_server": [("token_enc", "token_enc"), ("custom_headers_enc", "custom_headers_enc")],
    "mcp_oauth": [
        ("authorization_code_enc", "authorization_code_enc"),
        ("access_token_enc", "access_token_enc"),
        ("refresh_token_enc", "refresh_token_enc"),
        ("client_secret_enc", "client_secret_enc"),
    ],
}


class _RowClass(str, Enum):
    UNMARKED_ENCRYPTED = "unmarked-encrypted"
    PLAINTEXT = "plaintext"
    AMBIGUOUS = "ambiguous"


def _classify(value: str) -> _RowClass:
    """Classify a stored _enc value WITHOUT printing it."""
    if value.startswith(ENCRYPTION_MARKER):
        # Already marked: not our problem (decrypt already succeeded at write
        # time; a marked value that no longer decrypts is a wrong-key error,
        # surfaced by reads, not by this command).
        return _RowClass.UNMARKED_ENCRYPTED  # marked rows are counted separately as done
    try:
        CryptoUtils.decrypt(value)
        return _RowClass.UNMARKED_ENCRYPTED
    except ValueError as e:
        if "No encryption key configured" in str(e):
            raise typer.BadParameter(
                "No encryption key configured (LETTA_ENCRYPTION_KEY). The command requires one."
            )
        # Decrypt failed: plaintext if it does not look like base64 ciphertext
        if not CryptoUtils.is_encrypted(value):
            return _RowClass.PLAINTEXT
        return _RowClass.AMBIGUOUS


async def _scan_tables():
    """Yield (table, column, row_id, value) for every non-null _enc value."""
    from sqlalchemy import text

    async with db_registry.async_session() as session:
        for table, cols in SECRET_TABLES.items():
            for attr, col in cols:
                result = await session.execute(
                    text(f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
                )
                for row_id, value in result.all():
                    yield table, col, row_id, value


async def _dry_run() -> dict:
    """Classify all rows. Returns counts + ambiguous row identifiers (IDs only)."""
    counts = {c: 0 for c in _RowClass}
    marked = 0
    ambiguous_rows = []
    async for table, col, row_id, value in _scan_tables():
        if value.startswith(ENCRYPTION_MARKER):
            marked += 1
            continue
        cls = _classify(value)
        counts[cls] += 1
        if cls == _RowClass.AMBIGUOUS:
            ambiguous_rows.append(f"{table}.{col} id={row_id}")
    return {"counts": counts, "marked": marked, "ambiguous_rows": ambiguous_rows}


async def _apply(treat_ambiguous: bool) -> bool:
    """Re-encrypt plaintext + unmarked-encrypted rows. One transaction per
    table; each row re-read and round-trip-confirmed before commit. Returns
    True if any row changed."""
    from sqlalchemy import text

    changed_any = False
    # collect work per table first (classification is read-only)
    work: dict[str, list[tuple[str, any, str, _RowClass]]] = {}
    async for table, col, row_id, value in _scan_tables():
        if value.startswith(ENCRYPTION_MARKER):
            continue
        cls = _classify(value)
        if cls == _RowClass.AMBIGUOUS and not treat_ambiguous:
            continue
        work.setdefault(table, []).append((col, row_id, value, cls))

    for table, rows in work.items():
        async with db_registry.async_session() as session:
            async with session.begin():
                for col, row_id, value, cls in rows:
                    if cls == _RowClass.PLAINTEXT or (cls == _RowClass.AMBIGUOUS and treat_ambiguous):
                        # re-encrypt from the stored plaintext (it IS the plaintext)
                        new_value = CryptoUtils.encrypt(value)
                    else:  # unmarked-encrypted: re-mark via decrypt->encrypt
                        new_value = CryptoUtils.encrypt(CryptoUtils.decrypt(value))
                    await session.execute(
                        text(f"UPDATE {table} SET {col} = :v WHERE id = :id"), {"v": new_value, "id": row_id}
                    )
            # round-trip confirmation (post-commit read)
            async with db_registry.async_session() as session:
                result = await session.execute(
                    text(f"SELECT {col} FROM {table} WHERE id = :id"), {"id": row_id}
                )
                stored = result.scalar_one_or_none()
                if stored is None or not stored.startswith(ENCRYPTION_MARKER):
                    typer.echo(f"::error::round-trip confirmation failed for {table}.{col} id={row_id}")
                    raise typer.Exit(1)
                re_plain = CryptoUtils.decrypt(stored)
                if re_plain != value:
                    typer.echo(f"::error::round-trip mismatch for {table}.{col} id={row_id}")
                    raise typer.Exit(1)
            changed_any = True
    return changed_any


@secrets_app.command(name="reencrypt")
def reencrypt(
    apply: bool = typer.Option(False, "--apply", help="Apply changes (default: dry run)."),
    yes: bool = typer.Option(False, "--yes", help="Confirmation flag for --apply."),
    treat_ambiguous: bool = typer.Option(
        False,
        "--treat-ambiguous-as-plaintext",
        help="Declare that no other encryption key has EVER been used on this database: "
        "ambiguous rows are treated as legacy plaintext and encrypted.",
    ),
) -> None:
    """Re-encrypt plaintext secrets stored before an encryption key was set.

    Dry run by default: reports per-class counts and the row IDs (never
    values) of ambiguous rows. --apply re-encrypts; ambiguous rows block
    unless --treat-ambiguous-as-plaintext + --yes.
    """
    # requires a key
    from letta.settings import settings

    if not getattr(settings, "encryption_key", None):
        raise typer.BadParameter(
            "No encryption key configured (LETTA_ENCRYPTION_KEY). The command requires one — "
            "re-encrypting requires knowing the destination key."
        )

    if apply and not yes:
        raise typer.BadParameter("--apply requires --yes (the downgrade warning applies).")

    if apply:
        typer.echo(
            "WARNING: after --apply, stored secrets carry the enc:v1: marker. Do NOT "
            "downgrade below v0.16.40 without restoring a database backup taken before "
            "this run — older versions would read marked ciphertext as plaintext."
        )

    result = asyncio.run(_dry_run())
    counts = result["counts"]
    typer.echo(
        f"scan: {result['marked']} already-marked | "
        f"{counts[_RowClass.UNMARKED_ENCRYPTED]} unmarked-encrypted (to mark) | "
        f"{counts[_RowClass.PLAINTEXT]} plaintext (to encrypt) | "
        f"{counts[_RowClass.AMBIGUOUS]} ambiguous"
    )
    for ident in result["ambiguous_rows"]:
        typer.echo(f"  ambiguous: {ident}")

    if not apply:
        if counts[_RowClass.PLAINTEXT] or counts[_RowClass.UNMARKED_ENCRYPTED] or counts[_RowClass.AMBIGUOUS]:
            typer.echo("dry run — re-run with --apply --yes to change anything")
        else:
            typer.echo("nothing to do")
        return

    if result["ambiguous_rows"] and not treat_ambiguous:
        typer.echo(
            "::error::ambiguous rows block --apply: re-enter those secrets through the API "
            "(the v0.16.40 write path encrypts them), or pass --treat-ambiguous-as-plaintext "
            "--yes if no other key has EVER been used on this database."
        )
        raise typer.Exit(1)

    if treat_ambiguous:
        typer.echo(
            "Statement required: --treat-ambiguous-as-plaintext declares NO OTHER encryption "
            "key has ever been used on this database. Ambiguous rows will be encrypted as "
            "legacy plaintext. If another key was ever used, those rows are that key's "
            "ciphertext and will be CORRUPTED by this run."
        )

    changed = asyncio.run(_apply(treat_ambiguous=treat_ambiguous))
    typer.echo("apply complete" + ("" if changed else " (no changes — idempotent)"))


def report_unmarked_secret_rows() -> None:
    """v0.16.40 (E-1): startup WARNING counting unmarked secret rows.

    Unmarked = legacy plaintext, ambiguous values, or pre-v0.16.40 ciphertext —
    anything not carrying the enc:v1: marker. Counted with plain SQL LIKE
    (NO decryption, no PBKDF2), gated on a configured key, report-only.
    Never logs values.
    """
    from letta.settings import settings

    if not getattr(settings, "encryption_key", None):
        return
    import asyncio as _aio

    async def _count() -> int:
        from sqlalchemy import text

        total = 0
        async with db_registry.async_session() as session:
            for table, cols in SECRET_TABLES.items():
                for _attr, col in cols:
                    r = await session.execute(
                        text(f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND {col} NOT LIKE :m"),
                        {"m": ENCRYPTION_MARKER + "%"},
                    )
                    total += r.scalar_one()
        return total

    try:
        try:
            loop = _aio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Called from async context (lifespan): schedule on this loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                total = ex.submit(_aio.run, _count()).result()
        else:
            total = _aio.run(_count())
    except Exception:
        # Report-only: any failure (no DB yet, migration pending) is silent.
        return
    if total > 0:
        logger.warning(
            "%d stored secret value(s) are not yet marked-ciphertext (legacy plaintext or "
            "pre-v0.16.40 format). Run `letta secrets reencrypt` (dry run) to inspect. "
            "See README: Turning on encryption on an existing install.",
            total,
        )
