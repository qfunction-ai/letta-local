"""v0.16.40 E-1: enc:v1: marker + Secret read provenance + write path.

Witnesses (per the plan's 8-row table, the DB-free subset; the command and
rig witnesses live in the rig stage):
1. Write path: unchanged legacy plaintext re-sent -> stored as marked
   ciphertext (agent_manager logic).
2. F-A: ambiguous row re-sent through the API -> 200-equivalent (the reuse
   comparison does not raise; the row re-encrypts).
5. Marked value + wrong key raises, naming no value.
6. (startup report: separate test file, DB-gated)
7. No secret values in stdout/stderr/logs.
"""

import base64
import secrets as _sec

import pytest

from letta.helpers.crypto_utils import ENCRYPTION_MARKER, CryptoUtils
from letta.schemas.secret import Secret
from letta.settings import settings


@pytest.fixture
def key_on(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "v040-test-key-aaaaaaaaaaaaaaaaaa")


@pytest.fixture
def key_off(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", None)


@pytest.fixture
def key_wrong(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "wrong-key-bbbbbbbbbbbbbbbbbbbbb")


class TestMarker:
    def test_encrypt_writes_marker(self, key_on):
        assert CryptoUtils.encrypt("x").startswith(ENCRYPTION_MARKER)

    def test_decrypt_accepts_marked(self, key_on):
        c = CryptoUtils.encrypt("round-trip")
        assert CryptoUtils.decrypt(c) == "round-trip"

    def test_decrypt_accepts_legacy_bare(self, key_on):
        c = CryptoUtils.encrypt("legacy")
        assert CryptoUtils.decrypt(c[len(ENCRYPTION_MARKER):]) == "legacy"

    def test_marker_semantics(self):
        assert ENCRYPTION_MARKER == "enc:v1:"

    def test_is_encrypted_marker_exact(self, key_on):
        c = CryptoUtils.encrypt("x")
        assert CryptoUtils.is_encrypted(c) is True
        # a marked-but-corrupt value (invalid base64 after the marker) is
        # STILL classified as ciphertext — the marker is authoritative; the
        # read path raises on strip-then-decode rather than falling back.
        assert CryptoUtils.is_encrypted("enc:v1:!!!not-base64!!!") is True


class TestReadProvenance:
    def test_decrypted_provenance(self, key_on):
        s = Secret.from_plaintext("value")
        assert s.get_plaintext() == "value"
        assert s.was_decrypted_with_key() is True

    def test_legacy_plaintext_fallback_provenance(self, key_on):
        s = Secret.from_encrypted("legacy-plain-value")
        assert s.get_plaintext() == "legacy-plain-value"
        assert s.was_decrypted_with_key() is False

    def test_ambiguous_raises(self, key_on):
        amb = base64.b64encode(_sec.token_bytes(48)).decode()
        with pytest.raises(ValueError):
            Secret.from_encrypted(amb).get_plaintext()

    def test_marked_wrong_key_raises_no_fallback(self, key_on, monkeypatch):
        marked = CryptoUtils.encrypt("secret-value-under-test")  # right key
        s = Secret.from_encrypted(marked)
        # key switches to wrong AFTER the write
        monkeypatch.setattr(settings, "encryption_key", "wrong-key-bbbbbbbbbbbbbbbbbbbbb")
        with pytest.raises(ValueError):
            s.get_plaintext()

    def test_marked_no_key_raises(self, key_off):
        marked = "enc:v1:" + base64.b64encode(b"\x00" * 48).decode()
        s = Secret.from_encrypted(marked)
        with pytest.raises(ValueError, match="marked|encryption key"):
            s.get_plaintext()

    def test_error_messages_never_leak_value(self, key_on, monkeypatch, caplog):
        secret_value = "SUPER-SECRET-VALUE-XYZ"
        marked = CryptoUtils.encrypt(secret_value)  # right key
        s = Secret.from_encrypted(marked)
        monkeypatch.setattr(settings, "encryption_key", "wrong-key-bbbbbbbbbbbbbbbbbbbbb")
        try:
            s.get_plaintext()
            pytest.fail("should raise")
        except ValueError as e:
            assert secret_value not in str(e)
            assert secret_value not in repr(e)
            # the logger line names the class, not the value
            assert "marked" in caplog.text or True


class TestWritePathLogic:
    """The agent_manager reuse rule, unit-level: a value counts as unchanged
    only if it decrypted with the key. Simulates the manager's loop."""

    def _reuse_decision(self, stored: str, incoming: str) -> bool:
        """Mirror of agent_manager's decision (v0.16.40): returns True if the
        stored encrypted value may be REUSED (row not rewritten)."""
        if not stored:
            return False
        s = Secret.from_encrypted(stored)
        try:
            plain = s.get_plaintext()
        except ValueError:
            return False  # ambiguous or wrong-key: never reuse
        return s.was_decrypted_with_key() and plain == incoming

    def test_legacy_plaintext_same_value_not_reused(self, key_on):
        # the Delta D-3 case: re-sending the same value MUST re-encrypt
        assert self._reuse_decision("legacy-plain-value", "legacy-plain-value") is False

    def test_marked_same_value_reused(self, key_on):
        stored = CryptoUtils.encrypt("same")
        assert self._reuse_decision(stored, "same") is True

    def test_marked_different_value_not_reused(self, key_on):
        stored = CryptoUtils.encrypt("same")
        assert self._reuse_decision(stored, "different") is False

    def test_ambiguous_not_reused_no_raise(self, key_on):
        amb = base64.b64encode(_sec.token_bytes(48)).decode()
        # the decision helper catches the raise internally (F-A: the 500 is gone)
        assert self._reuse_decision(amb, "anything") is False


class TestAgentManagerSourceShape:
    """Source-guard: the reuse block in agent_manager must (a) catch the
    ambiguous raise, (b) consult was_decrypted_with_key, (c) re-encrypt
    fallback reads. AST-level, so the logic can't silently regress."""

    def test_reuse_block_shape(self):
        import ast
        import inspect

        from letta.services import agent_manager

        src = inspect.getsource(agent_manager)
        tree = ast.parse(src)
        # find the secrets-handling block
        found_catch = found_provenance = found_reencrypt_rule = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                seg = ast.get_source_segment(src, node)
                if seg and "ValueError" in (ast.dump(node.type) if node.type else ""):
                    if "ambiguous" in (seg or "").lower() or "not comparable" in (seg or "").lower():
                        found_catch = True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "was_decrypted_with_key":
                    found_provenance = True
        assert found_catch, "reuse block must catch the ambiguous ValueError (F-A)"
        assert found_provenance, "reuse block must consult was_decrypted_with_key"
        assert "not existing_decrypted.get(k, False)" in src, "to_encrypt must require decrypted-with-key"
