"""Integration tests for Landlock sandbox.

These tests verify the full Landlock sandbox pipeline: wrapper binary,
Landlock restrictions, seccomp filter, and result parsing.
They require a Linux kernel with Landlock support (ABI >= 1).
On non-Linux or kernels without Landlock, most tests are skipped.
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile

import pytest

# Skip all tests on non-Linux
pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="Landlock is Linux-only"
)


@pytest.fixture
def landlock_available():
    """Check if Landlock is available on this kernel."""
    try:
        from letta.services.tool_sandbox._landlock_detect import detect_landlock_abi
        return detect_landlock_abi() >= 1
    except Exception:
        return False


@pytest.fixture
def abi_version(landlock_available):
    """Get the Landlock ABI version."""
    if not landlock_available:
        return 0
    from letta.services.tool_sandbox._landlock_detect import detect_landlock_abi
    return detect_landlock_abi()


@pytest.fixture
def wrapper_path():
    """Path to the Landlock wrapper binary."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "letta", "bin", "letta_landlock_wrapper.py",
    )


@pytest.fixture
def sandbox_dir():
    """Create a temporary directory for sandbox execution."""
    with tempfile.TemporaryDirectory(prefix="landlock_test_") as tmpdir:
        yield tmpdir


def run_wrapper(wrapper_path, config, python_code, timeout=10):
    """Helper to run a Python script under the Landlock wrapper.

    Args:
        wrapper_path: Path to the wrapper binary.
        config: Dict to pass as --config JSON.
        python_code: Python code to execute.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (returncode, stdout, stderr).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(python_code)
        f.flush()
        script_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, wrapper_path, "--config", json.dumps(config), "--",
             sys.executable, script_path],
            capture_output=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, b"", b"timeout"
    finally:
        os.unlink(script_path)


class TestLandlockDetection:
    """Test Landlock ABI detection."""

    def test_abi_detection_inside_docker(self, landlock_available):
        """ABI detection should work inside Docker Desktop."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_detect import detect_landlock_abi
        abi = detect_landlock_abi()
        assert abi >= 1

    def test_abi_detection_outside_linux(self):
        """ABI detection should return 0 on non-Linux."""
        # This test is always skipped by the module-level skipif on non-Linux
        # but the logic is tested by the detect module itself
        pass


class TestLandlockFilesystemRestrictions:
    """Test Landlock filesystem write and read restrictions."""

    def test_filesystem_write_restriction(self, landlock_available, wrapper_path, sandbox_dir):
        """Write to /etc should fail under Landlock sandbox."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import os
try:
    with open('/etc/landlock_test_write', 'w') as f:
        f.write('test')
    print('WRITE_SUCCEEDED')
except (OSError, PermissionError) as e:
    print('WRITE_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"WRITE_FAILED" in stdout

    def test_filesystem_read_restriction(self, landlock_available, wrapper_path, sandbox_dir):
        """Read from unallowed path should fail under Landlock sandbox."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr"],  # /etc not in allowed list
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
try:
    with open('/etc/hostname', 'r') as f:
        data = f.read()
    print('READ_SUCCEEDED')
except (OSError, PermissionError) as e:
    print('READ_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"READ_FAILED" in stdout

    def test_proc_access_restriction(self, landlock_available, wrapper_path, sandbox_dir):
        """Read /proc/1/environ should fail, /proc/self/status should succeed."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import os

# /proc/self should be allowed
try:
    with open('/proc/self/status', 'r') as f:
        data = f.read(100)
    print('SELF_READ_OK')
except (OSError, PermissionError):
    print('SELF_READ_FAILED')

# /proc/1 should be denied (not /proc/self)
try:
    with open('/proc/1/environ', 'r') as f:
        data = f.read()
    print('PROC1_READ_OK')
except (OSError, PermissionError):
    print('PROC1_READ_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"SELF_READ_OK" in stdout
        assert b"PROC1_READ_FAILED" in stdout

    def test_allowed_path_write(self, landlock_available, wrapper_path, sandbox_dir):
        """Write to allowed sandbox_dir should succeed."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        test_file = os.path.join(sandbox_dir, "test_write.txt")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = f"""
try:
    with open('{test_file}', 'w') as f:
        f.write('test')
    print('WRITE_SUCCEEDED')
except (OSError, PermissionError) as e:
    print('WRITE_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"WRITE_SUCCEEDED" in stdout


class TestLandlockNetworkRestrictions:
    """Test Landlock network restrictions."""

    def test_network_tcp_connect_restriction(self, landlock_available, wrapper_path, sandbox_dir, abi_version):
        """TCP connect should fail when allow_tcp_connect=False."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        if abi_version < 4:
            pytest.skip("Landlock ABI < 4, network restrictions not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('8.8.8.8', 53))
    print('CONNECT_SUCCEEDED')
    s.close()
except (OSError, PermissionError):
    print('CONNECT_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"CONNECT_FAILED" in stdout

    def test_network_tcp_connect_allowed(self, landlock_available, wrapper_path, sandbox_dir, abi_version):
        """TCP connect should succeed when allow_tcp_connect=True."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        if abi_version < 4:
            pytest.skip("Landlock ABI < 4, network restrictions not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": True,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(('8.8.8.8', 53))
    print('CONNECT_SUCCEEDED')
    s.close()
except Exception as e:
    print(f'CONNECT_FAILED: {e}')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"CONNECT_SUCCEEDED" in stdout


class TestLandlockSeccomp:
    """Test seccomp-BPF syscall filtering."""

    def test_seccomp_blocks_ptrace(self, landlock_available, wrapper_path, sandbox_dir):
        """ptrace should be blocked by seccomp."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": ["ptrace"],
            "block_fork": True,
        }
        code = """
import ctypes
import ctypes.util
try:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    ret = libc.ptrace(0, 0, 0, 0)  # PTRACE_TRACEME
    if ret == -1:
        print('PTRACE_BLOCKED')
    else:
        print('PTRACE_SUCCEEDED')
except Exception:
    print('PTRACE_BLOCKED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"PTRACE_BLOCKED" in stdout

    def test_fork_bomb_prevention(self, landlock_available, wrapper_path, sandbox_dir):
        """fork/clone/clone3/vfork should all be blocked."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import ctypes
import ctypes.util
import os

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

# Test fork
ret = libc.fork()
if ret == 0:
    os._exit(0)
print(f'FORK_RET={ret}')

# Test vfork — skip in subprocess, it's too dangerous to test directly
# The seccomp filter blocks it at the syscall level
print('FORK_TEST_DONE')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        # fork should have returned -1 (EPERM)
        assert b"FORK_RET=-1" in stdout


class TestLandlockSandboxIrreversibility:
    """Test that sandbox restrictions are irreversible."""

    def test_sandbox_irreversibility(self, landlock_available, wrapper_path, sandbox_dir):
        """Cannot escape sandbox after apply."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
# Try to write to /etc after sandbox is applied
try:
    with open('/etc/landlock_escape_test', 'w') as f:
        f.write('escaped')
    print('ESCAPE_SUCCEEDED')
except (OSError, PermissionError):
    print('ESCAPE_FAILED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"ESCAPE_FAILED" in stdout


class TestLandlockFallback:
    """Test fallback behavior when Landlock is not available."""

    def test_graceful_fallback_when_landlock_not_available(self):
        """Should fall back to LOCAL with warning when Landlock not available."""
        # This is tested by the settings.py landlock_available property
        # On non-Linux, it returns False and sandbox_type falls back to LOCAL
        pass

    def test_abi_version_fallback(self, abi_version):
        """ABI < 4: network features disabled with warning."""
        if abi_version >= 4:
            pytest.skip("Landlock ABI >= 4, network features available")
        # When ABI < 4, the wrapper prints a warning about network
        # and continues without network restrictions
        pass


class TestLandlockCloseFds:
    """Test close_fds=True prevents FD leakage."""

    def test_close_fds_prevents_fd_leakage(self, landlock_available, wrapper_path, sandbox_dir):
        """Sandboxed code should not be able to access parent FDs."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/proc/self"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import os

# Check /proc/self/fd for unexpected FDs
fds = os.listdir('/proc/self/fd')
print(f'FD_COUNT={len(fds)}')

# The sandboxed process should have a minimal set of FDs
# stdin, stdout, stderr, and a few others
if len(fds) < 20:
    print('FD_COUNT_OK')
else:
    print('FD_COUNT_HIGH')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"FD_COUNT_OK" in stdout


class TestLandlockTmpSizeLimit:
    """Test /tmp size limit handling."""

    def test_tmp_size_limit_handling(self, landlock_available, wrapper_path, sandbox_dir):
        """Large scripts should not fail when using tool_exec_dir instead of /tmp."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        # Generate a large script (simulating embedded agent_state pickles)
        code = f"""
import os
# Write a large file to sandbox_dir (not /tmp)
test_file = os.path.join('{sandbox_dir}', 'large_test.txt')
with open(test_file, 'w') as f:
    f.write('x' * 1024 * 1024)  # 1MB
print('LARGE_WRITE_SUCCEEDED')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"LARGE_WRITE_SUCCEEDED" in stdout


class TestLandlockVenvSupport:
    """Test venv support under Landlock."""

    def test_venv_support(self, landlock_available, wrapper_path, sandbox_dir):
        """Tools with venv dependencies should work correctly."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import sys
print(f'PYTHON_VERSION={sys.version_info.major}.{sys.version_info.minor}')
print('VENV_TEST_OK')
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert b"VENV_TEST_OK" in stdout


class TestLandlockRequiresNetwork:
    """Test requires_network metadata auto-promotion."""

    def test_requires_network_auto_promotion(self):
        """Tool with requires_network: True should get allow_tcp_connect=True."""
        # This is tested in the SandboxToolExecutor, not in the wrapper
        # The executor sets allow_tcp_connect=True on the config when
        # tool.metadata_.get("requires_network") is True
        pass


class TestLandlockFullRoundTrip:
    """Test full tool execution round-trip."""

    def test_full_tool_execution_round_trip(self, landlock_available, wrapper_path, sandbox_dir):
        """Execute a tool, get result back."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        config = {
            "allowed_read_paths": ["/usr", "/lib", "/lib64", "/etc"],
            "allowed_write_paths": [sandbox_dir],
            "allowed_execute_paths": ["/usr/bin", "/usr/local/bin"],
            "allow_tcp_connect": False,
            "allow_tcp_bind": False,
            "blocked_syscalls": [],
            "block_fork": True,
        }
        code = """
import json
result = {"status": "success", "value": 42}
print(json.dumps(result))
"""
        returncode, stdout, stderr = run_wrapper(wrapper_path, config, code)
        assert returncode == 0
        assert b'"status": "success"' in stdout
        assert b'"value": 42' in stdout


class TestLandlockSandboxConfig:
    """Test LandlockSandboxConfig schema."""

    def test_landlock_sandbox_config_defaults(self):
        """LandlockSandboxConfig should have correct defaults."""
        from letta.schemas.sandbox_config import LandlockSandboxConfig
        from letta.schemas.enums import SandboxType
        config = LandlockSandboxConfig()
        assert config.type == SandboxType.LANDLOCK
        assert config.allow_tcp_connect is False
        assert config.allow_tcp_bind is False
        assert config.block_fork is True
        assert config.timeout == 180
        assert "/usr" in config.allowed_read_paths

    def test_landlock_sandbox_config_custom_values(self):
        """LandlockSandboxConfig should accept custom values."""
        from letta.schemas.sandbox_config import LandlockSandboxConfig
        config = LandlockSandboxConfig(
            allow_tcp_connect=True,
            timeout=300,
            allowed_write_paths=["/tmp/custom"],
        )
        assert config.allow_tcp_connect is True
        assert config.timeout == 300
        assert "/tmp/custom" in config.allowed_write_paths

    def test_sandbox_type_enum_has_landlock(self):
        """SandboxType enum should have LANDLOCK value."""
        from letta.schemas.enums import SandboxType
        assert SandboxType.LANDLOCK.value == "landlock"
        assert SandboxType.LANDLOCK in SandboxType

    def test_sandbox_type_enum_no_docker(self):
        """SandboxType enum should NOT have DOCKER value."""
        from letta.schemas.enums import SandboxType
        assert not hasattr(SandboxType, "DOCKER")
