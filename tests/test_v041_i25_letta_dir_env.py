import importlib
import os
import sys

import pytest


@pytest.fixture
def _letta_env(monkeypatch):
    monkeypatch.setenv("LETTA_DIR", "/tmp/letta-i25-unit")
    import sys
    for mod in list(sys.modules):
        if mod.startswith("letta"):
            del sys.modules[mod]
    yield
    for mod in list(sys.modules):
        if mod.startswith("letta"):
            del sys.modules[mod]


@pytest.fixture
def _no_letta_env(monkeypatch):
    monkeypatch.delenv("LETTA_DIR", raising=False)
    import sys
    for mod in list(sys.modules):
        if mod.startswith("letta"):
            del sys.modules[mod]
    yield
    for mod in list(sys.modules):
        if mod.startswith("letta"):
            del sys.modules[mod]


class TestLettaDirEnvHonor:
    """I25-2: LETTA_DIR env var must redirect every path the .41
    relocation touches."""

    def test_constants_honors_env(self, _letta_env):
        import letta.constants as C
        assert str(C.LETTA_DIR) == "/tmp/letta-i25-unit"

    def test_settings_letta_dir_honors_env(self, _letta_env):
        from letta.settings import Settings
        s = Settings()
        assert str(s.letta_dir) == "/tmp/letta-i25-unit"

    def test_settings_log_path_honors_env(self, _letta_env):
        from letta.settings import LogSettings
        ls = LogSettings()
        assert str(ls.letta_log_path).startswith("/tmp/letta-i25-unit")

    def test_config_file_honors_env(self, _letta_env):
        import letta.config_file as cf
        assert str(cf.DEFAULT_USER_CONFIG).startswith("/tmp/letta-i25-unit")

    def test_memfs_default_honors_env(self, _letta_env):
        import letta.services.memory_repo.memfs_client_base as m
        assert str(m.DEFAULT_LOCAL_PATH).startswith("/tmp/letta-i25-unit")

    def test_storage_local_no_expanduser(self, _letta_env):
        import letta.services.memory_repo.storage.local as sl
        src = open(sl.__file__).read()
        assert 'os.path.expanduser("~/.letta/memfs")' not in src
        assert "LETTA_DIR_CONST" in src

    def test_tool_sandbox_base_no_expanduser(self, _letta_env):
        import letta.services.tool_sandbox.base as b
        src = open(b.__file__).read()
        assert 'os.path.expanduser("~/.letta/agent_files")' not in src

    def test_landlock_sandbox_no_expanduser(self, _letta_env):
        import letta.services.tool_sandbox.landlock_sandbox as l
        src = open(l.__file__).read()
        assert 'os.path.expanduser("~/.letta/agent_files")' not in src

    def test_file_persistence_no_expanduser(self, _letta_env):
        import letta.functions.function_sets.file_persistence as fp
        src = open(fp.__file__).read()
        assert 'os.path.expanduser("~/.letta/agent_files")' not in src


class TestLettaDirFallback:
    """I25-2 canary: without the env var, paths fall back to ~/.letta."""

    def test_constants_fallback(self, _no_letta_env):
        import letta.constants as C
        home = os.path.expanduser("~")
        assert str(C.LETTA_DIR).startswith(home)

    def test_settings_fallback(self, _no_letta_env):
        from letta.settings import Settings
        s = Settings()
        home = os.path.expanduser("~")
        assert str(s.letta_dir).startswith(home)

    def test_settings_log_path_fallback(self, _no_letta_env):
        from letta.settings import LogSettings
        ls = LogSettings()
        home = os.path.expanduser("~")
        assert str(ls.letta_log_path).startswith(home)
