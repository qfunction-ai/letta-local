"""v0.16.39 B-8 witnesses: the generated password survives restarts.

RED-first: all persistence assertions FAIL on the 0.16.38 baseline
(resolve_server_password generates fresh every call, never reads the file).
"""

import os
import stat

from letta.settings import settings
from letta.security import server_credentials


class TestB8PasswordPersistence:
    def test_two_resolves_same_password(self, tmp_path, monkeypatch):
        """Two consecutive starts (same letta_dir) -> SAME password."""
        monkeypatch.delenv("LETTA_SERVER_PASSWORD", raising=False)
        monkeypatch.setattr(settings, "letta_dir", tmp_path)
        pw1 = server_credentials.resolve_server_password()
        # simulate a restart: module state is re-resolved
        pw2 = server_credentials.resolve_server_password()
        assert pw1 == pw2, f"password regenerated across restart: {pw1[:4]}... != {pw2[:4]}..."

    def test_env_var_wins_over_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LETTA_SERVER_PASSWORD", "env-set-password")
        monkeypatch.setattr(settings, "letta_dir", tmp_path)
        assert server_credentials.resolve_server_password() == "env-set-password"

    def test_empty_file_regenerates(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LETTA_SERVER_PASSWORD", raising=False)
        monkeypatch.setattr(settings, "letta_dir", tmp_path)
        (tmp_path / "server_password").write_text("")  # empty file must not be reused
        pw = server_credentials.resolve_server_password()
        assert pw, "empty password resolved"
        assert (tmp_path / "server_password").read_text().strip() == pw, "empty file not overwritten with the new password"

    def test_file_mode_0600(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LETTA_SERVER_PASSWORD", raising=False)
        monkeypatch.setattr(settings, "letta_dir", tmp_path)
        server_credentials.resolve_server_password()
        f = tmp_path / "server_password"
        assert f.exists(), "password file not written"
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode == 0o600, f"password file mode {oct(mode)} != 0600"

    def test_existing_nonempty_file_reused(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LETTA_SERVER_PASSWORD", raising=False)
        monkeypatch.setattr(settings, "letta_dir", tmp_path)
        (tmp_path / "server_password").write_text("pre-existing-password")
        assert server_credentials.resolve_server_password() == "pre-existing-password"
