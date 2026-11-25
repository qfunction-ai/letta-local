"""v0.16.40 R-6: password file permissions.

The write path (write_text then chmod) had a umask-mode window (typically
0644); a pre-existing loose file was reused unchecked; two writers existed.
Fix: 0600 atomic create (temp + os.replace) in the SINGLE writer
(server_credentials), read-side warn+tighten, app.py delegates.
"""

import os
import pathlib
from unittest.mock import patch

import pytest

import letta.security.server_credentials as sc


@pytest.fixture
def pw_path(tmp_path):
    return tmp_path / "server_password"


@pytest.fixture(autouse=True)
def hostile_umask():
    old = os.umask(0o022)  # would give 0644 via write_text
    yield
    os.umask(old)


class TestPasswordFile:
    def test_create_is_0600_immediately(self, pw_path):
        with patch.object(sc, "_password_file_path", return_value=pw_path):
            sc._write_password_file("pw")
        assert pw_path.stat().st_mode & 0o777 == 0o600

    def test_no_temp_residue(self, pw_path):
        with patch.object(sc, "_password_file_path", return_value=pw_path):
            sc._write_password_file("pw")
        assert list(pw_path.parent.glob("server_password*")) == [pw_path]

    def test_overwrite_keeps_0600_and_value(self, pw_path):
        with patch.object(sc, "_password_file_path", return_value=pw_path):
            sc._write_password_file("one")
            sc._write_password_file("two")
        assert pw_path.read_text() == "two"
        assert pw_path.stat().st_mode & 0o777 == 0o600

    def test_read_tightens_loose_file(self, pw_path, caplog):
        with patch.object(sc, "_password_file_path", return_value=pw_path):
            sc._write_password_file("secret")
            os.chmod(pw_path, 0o644)
            value = sc._read_password_file()
        assert value == "secret"
        assert pw_path.stat().st_mode & 0o777 == 0o600
        assert any("loose permissions" in r.message for r in caplog.records if r.name.startswith("letta"))

    def test_read_missing_returns_none(self, pw_path):
        with patch.object(sc, "_password_file_path", return_value=pw_path):
            assert sc._read_password_file() is None

    def test_single_writer_app_delegates(self):
        """app.py's secure-mode block must not write the file itself — the
        writer call and the reporting are the only things it does."""
        import inspect

        import letta.server.rest_api.app as app_mod

        src = inspect.getsource(app_mod)
        # the old direct-write pattern is gone
        assert "password_file.write_text" not in src
        assert "password_file.chmod" not in src
        # it delegates
        assert "_write_password_file(random_password)" in src
