"""v0.16.40 R-5: containment-scrub secret length floor + PG password component.

Short secrets substring-match benign values (4-char password scrubs PATH via
'bin'; compose-default DB password 'letta' matches 'letta' anywhere). Fix:
containment matching only for secrets >= 12 chars; below that exact-match
only + startup WARNING. Plus: the PG password COMPONENT joins the sensitive
set (was whole-URI only).
"""

from unittest.mock import patch

import pytest

import letta.security.server_credentials as sc


class TestLengthFloor:
    def test_long_secret_containment(self):
        # 32-char secret: containment matching (a value carrying it is dropped)
        secret = "a" * 32
        assert sc._value_carries_secret(f"prefix-{secret}-suffix", {secret})
        assert sc._value_carries_secret(f"Bearer {secret}", {secret})

    def test_long_secret_urlencoded_containment(self):
        secret = "p@ss word+chars/" + "x" * 20
        from urllib.parse import quote

        assert sc._value_carries_secret(f"http://u:{quote(secret, safe='')}@host", {secret})

    def test_short_secret_exact_only(self):
        short = "bin4"
        # carried as substring: NOT dropped (the PATH/bin false positive class)
        assert not sc._value_carries_secret("/usr/local/bin:/usr/bin", {short})
        assert not sc._value_carries_secret("letta-is-a-word", {"letta"})
        # exact: dropped
        assert sc._value_carries_secret("bin4", {short})

    def test_floor_boundary_12(self):
        at_floor = "b" * 12
        below = "b" * 11
        assert sc._value_carries_secret(f"x{at_floor}x", {at_floor})  # containment
        assert not sc._value_carries_secret(f"x{below}x", {below})  # exact-only

    def test_short_startup_warning_fires(self, caplog):
        with patch.object(sc, "get_sensitive_env_values", return_value={"short"}):
            with patch.object(sc.logger, "warning") as w:
                sc.warn_on_short_secrets()
                w.assert_called_once()

    def test_no_warning_when_all_long(self):
        with patch.object(sc, "get_sensitive_env_values", return_value={"long-enough-secret-value"}):
            with patch.object(sc.logger, "warning") as w:
                sc.warn_on_short_secrets()
                w.assert_not_called()


class TestPgPasswordComponent:
    def test_pg_password_component_in_sensitive_set(self):
        # a value carrying ONLY the DB password is caught when the component
        # is in the sensitive set (R-5: previously only the whole URI matched)
        assert sc._value_carries_secret("just-the-db-password: S3cretPassw0rd", {"S3cretPassw0rd"})

    def test_urlencoded_pg_password_caught(self):
        from urllib.parse import quote

        pw = "p@ss:word//chars" + "longer" * 2
        assert sc._value_carries_secret(f"val {quote(pw, safe='')}", {pw})

    def test_get_sensitive_env_values_pg_component(self, monkeypatch):
        import importlib

        settings_mod = importlib.import_module("letta.settings")
        uri = "postgresql://letta:S3cretPassw0rd@localhost:5432/letta"
        monkeypatch.setattr(settings_mod, "settings", type("S", (), {"pg_uri": uri, "encryption_key": None})())
        # clear + restore the module-level password
        old = sc._server_password
        sc._server_password = None
        try:
            values = sc.get_sensitive_env_values()
        finally:
            sc._server_password = old
        assert uri in values
        assert "S3cretPassw0rd" in values
