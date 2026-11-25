"""v0.16.40 review V-1 witness: _apply round-trip per row, in-transaction.

The original defect: the confirmation loop covered only the LAST row per
table (loop-leftover variables), compared decrypted plaintext against the
stored CIPHERTEXT for unmarked-encrypted rows (failing on always-had-a-key
installs — Delta's state), and ran after the commit. This test builds that
exact state: three rows in one table — plaintext, unmarked-encrypted,
plaintext — and applies.

Uses a real (aiosqlite) database via a patched db_registry.
"""

import asyncio

import pytest

import letta.cli.secrets_cli as secrets_cli
from letta.cli.secrets_cli import _apply
from letta.helpers.crypto_utils import ENCRYPTION_MARKER, CryptoUtils
from letta.settings import settings


@pytest.fixture
def sqlite_reg(monkeypatch, tmp_path):
    """File-backed sqlite with check_same_thread=False, shaped as db_registry."""
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v1.db",
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class _FakeRegistry:
        @staticmethod
        def async_session():
            import contextlib

            @contextlib.asynccontextmanager
            async def _ctx():
                async with factory() as s:
                    try:
                        yield s
                        await s.commit()  # match the real registry's commit-on-exit
                    except Exception:
                        await s.rollback()
                        raise

            return _ctx()

    monkeypatch.setattr(secrets_cli, "db_registry", _FakeRegistry)

    async def init():
        async with engine.begin() as conn:
            # column sets mirroring SECRET_TABLES
            schema = {
                "agent_environment_variables": ["value_enc"],
                "sandbox_environment_variables": ["value_enc"],
                "providers": ["api_key_enc", "access_key_enc"],
                "mcp_server": ["token_enc", "custom_headers_enc"],
                "mcp_oauth": [
                    "authorization_code_enc",
                    "access_token_enc",
                    "refresh_token_enc",
                    "client_secret_enc",
                ],
            }
            for table, cols in schema.items():
                col_defs = ", ".join(["id TEXT PRIMARY KEY"] + [f"{c} TEXT" for c in cols])
                await conn.execute(text(f"CREATE TABLE {table} ({col_defs})"))

    asyncio.run(init())
    return _FakeRegistry


async def _insert(reg, rows):
    from sqlalchemy import text

    async with reg.async_session() as s:
        for rid, v in rows:
            await s.execute(
                text("INSERT INTO agent_environment_variables (id, value_enc) VALUES (:id, :v)"),
                {"id": rid, "v": v},
            )


async def _read_all(reg):
    from sqlalchemy import text

    async with reg.async_session() as s:
        r = await s.execute(text("SELECT id, value_enc FROM agent_environment_variables ORDER BY id"))
        return r.all()


class TestApplyRoundTrip:
    def test_three_rows_mixed_classes_all_marked(self, sqlite_reg, monkeypatch):
        """V-1: plaintext, unmarked-encrypted, plaintext — order chosen so the
        LAST row is plaintext (the old code only confirmed the last row and
        compared against the original value, which happened to work for
        plaintext). The unmarked-encrypted MIDDLE row is the one the old code
        broke on."""
        monkeypatch.setattr(settings, "encryption_key", "v1-test-key-aaaaaaaaaaaaaaaaaaaa")

        # row B is REAL unmarked ciphertext (a v0.16.39-style write)
        unmarked = CryptoUtils.encrypt("enc-legacy-value")
        unmarked_bare = unmarked[len(ENCRYPTION_MARKER):]  # pre-.40 writes carry no marker

        asyncio.run(_insert(sqlite_reg, [("row-a", "plain-value"), ("row-b", unmarked_bare), ("row-c", "plain-2")]))

        changed = asyncio.run(_apply(treat_ambiguous=False))
        assert changed is True

        rows = asyncio.run(_read_all(sqlite_reg))
        assert len(rows) == 3
        for rid, v in rows:
            assert v.startswith(ENCRYPTION_MARKER), f"{rid} not marked: {v[:20]}"
        # each row round-trips to its OWN plaintext
        assert CryptoUtils.decrypt(rows[0][1]) == "plain-value"
        assert CryptoUtils.decrypt(rows[1][1]) == "enc-legacy-value"
        assert CryptoUtils.decrypt(rows[2][1]) == "plain-2"

    def test_idempotent_second_apply(self, sqlite_reg, monkeypatch):
        monkeypatch.setattr(settings, "encryption_key", "v1-test-key-aaaaaaaaaaaaaaaaaaaa")
        asyncio.run(_insert(sqlite_reg, [("r1", "x")]))
        asyncio.run(_apply(treat_ambiguous=False))
        before = asyncio.run(_read_all(sqlite_reg))
        changed = asyncio.run(_apply(treat_ambiguous=False))
        after = asyncio.run(_read_all(sqlite_reg))
        assert changed is False  # all marked -> no work
        assert before == after
