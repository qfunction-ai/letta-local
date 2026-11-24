"""v0.16.39 B-4 witnesses: fail loudly when secrets would be stored in plaintext.

RED-first protocol: (a) and (b) must FAIL on the 0.16.38 baseline (the startup
helper does not exist); (c) is a regression guard (green on baseline by design —
encryption already works when a key is configured).
"""

import logging

import pytest

from letta.settings import settings
from letta.schemas.secret import Secret
from letta.helpers.crypto_utils import CryptoUtils


def _import_helper():
    from letta.security import server_credentials

    return server_credentials.check_encryption_at_startup


class TestB4EncryptionStartup:
    def test_secure_mode_no_key_warns(self, monkeypatch, caplog):
        """(a) secure mode ON + no encryption key -> startup WARNING."""
        check = _import_helper()
        monkeypatch.setattr(settings, "encryption_key", None)
        monkeypatch.setattr(settings, "require_encryption", False)
        with caplog.at_level(logging.WARNING, logger="letta.security.server_credentials"):
            check(secure=True)
        assert any(
            "encryption key" in rec.message.lower() and "secure mode" in rec.message.lower()
            for rec in caplog.records
        ), f"expected startup WARNING about missing encryption key under secure mode, got: {[r.message for r in caplog.records]}"

    def test_no_warning_with_key(self, monkeypatch, caplog):
        check = _import_helper()
        monkeypatch.setattr(settings, "encryption_key", "some-key-value")
        with caplog.at_level(logging.WARNING, logger="letta.security.server_credentials"):
            check(secure=True)
        assert not any("encryption key" in rec.message.lower() for rec in caplog.records)

    def test_require_encryption_no_key_refuses_to_start(self, monkeypatch):
        """(b) LETTA_REQUIRE_ENCRYPTION=true + no key -> RuntimeError."""
        check = _import_helper()
        monkeypatch.setattr(settings, "encryption_key", None)
        monkeypatch.setattr(settings, "require_encryption", True)
        with pytest.raises(RuntimeError, match="(?i)encryption"):
            check(secure=True)

    def test_require_encryption_with_key_starts(self, monkeypatch):
        check = _import_helper()
        monkeypatch.setattr(settings, "encryption_key", "some-key-value")
        monkeypatch.setattr(settings, "require_encryption", True)
        check(secure=True)  # must not raise

    def test_require_encryption_refuses_even_insecure_mode(self, monkeypatch):
        """require_encryption is an operator demand — honored regardless of secure mode."""
        check = _import_helper()
        monkeypatch.setattr(settings, "encryption_key", None)
        monkeypatch.setattr(settings, "require_encryption", True)
        with pytest.raises(RuntimeError, match="(?i)encryption"):
            check(secure=False)

    def test_with_key_secret_stored_encrypted(self, monkeypatch):
        """(c) guard: with a key, from_plaintext stores ciphertext, decrypts back."""
        monkeypatch.setattr(settings, "encryption_key", "v039-test-master-key")
        secret = Secret.from_plaintext("my-plain-secret-value")
        assert secret.encrypted_value != "my-plain-secret-value", "raw plaintext stored in _enc column"
        assert not secret.encrypted_value.startswith("my-plain-secret-value")
        assert CryptoUtils.decrypt(secret.encrypted_value) == "my-plain-secret-value"
