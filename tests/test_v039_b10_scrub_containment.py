"""v0.16.39 B-10 witnesses: scrub/validate catch values that CONTAIN secrets,
including URL-encoded forms.

RED-first: containment and URL-encoded assertions FAIL on the 0.16.38 baseline
(exact-match only; validate checks only the password).
"""

from urllib.parse import quote

import pytest

from letta.settings import settings
from letta.security import server_credentials as cred


PW = "s3cret-Passw0rd-xyz"


@pytest.fixture
def registered_password(monkeypatch):
    monkeypatch.setattr(cred, "_server_password", PW)
    yield PW
    monkeypatch.setattr(cred, "_server_password", None)


class TestB10ScrubContainment:
    def test_scrub_drops_value_containing_password(self, registered_password):
        env = {"CONN": f"http://u:{PW}@host/db", "OK": "harmless"}
        out = cred.scrub_sensitive_env(env)
        assert "CONN" not in out, "value containing the password passed the scrub"
        assert out.get("OK") == "harmless"

    def test_scrub_drops_url_encoded_password(self, registered_password):
        encoded = quote(PW, safe="")
        env = {"URL": f"http://u:{encoded}@host"}
        out = cred.scrub_sensitive_env(env)
        assert "URL" not in out, "URL-encoded password passed the scrub"

    def test_scrub_exact_match_still_dropped(self, registered_password):
        out = cred.scrub_sensitive_env({"LEAK": PW})
        assert "LEAK" not in out

    def test_scrub_drops_pg_uri_containing(self, monkeypatch, registered_password):
        monkeypatch.setattr(settings, "pg_uri", "postgres://user:pw@localhost:5432/db")
        env = {"NOTE": "connect via postgres://user:pw@localhost:5432/db ok"}
        out = cred.scrub_sensitive_env(env)
        assert "NOTE" not in out, "value containing pg_uri passed the scrub"

    def test_scrub_none_values_preserved(self, registered_password):
        out = cred.scrub_sensitive_env({"A": None, "B": "fine"})
        assert out["A"] is None and out["B"] == "fine"


class TestB10ValidateContainment:
    def test_validate_rejects_password_containing(self, registered_password):
        with pytest.raises(ValueError):
            cred.validate_env_var_value("V", f"http://u:{PW}@host")

    def test_validate_rejects_url_encoded_password(self, registered_password):
        with pytest.raises(ValueError):
            cred.validate_env_var_value("V", f"prefix-{quote(PW, safe='')}-suffix")

    def test_validate_rejects_pg_uri_containing(self, monkeypatch, registered_password):
        monkeypatch.setattr(settings, "pg_uri", "postgres://user:pw@localhost:5432/db")
        with pytest.raises(ValueError):
            cred.validate_env_var_value("V", "see postgres://user:pw@localhost:5432/db")

    def test_validate_rejects_encryption_key_containing(self, monkeypatch, registered_password):
        monkeypatch.setattr(settings, "encryption_key", "enc-key-abc123")
        with pytest.raises(ValueError):
            cred.validate_env_var_value("V", "key=enc-key-abc123;")

    def test_validate_exact_password_still_rejected(self, registered_password):
        with pytest.raises(ValueError):
            cred.validate_env_var_value("V", PW)

    def test_validate_harmless_value_passes(self, registered_password):
        cred.validate_env_var_value("V", "totally-harmless-value")
