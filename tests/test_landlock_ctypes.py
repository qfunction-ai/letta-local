"""Unit tests for Landlock ctypes bindings and seccomp filter.

These tests verify the low-level ctypes interface works correctly.
They require a Linux kernel with Landlock support (ABI >= 1).
On non-Linux or kernels without Landlock, most tests are skipped.
"""

import os
import platform
import pytest
import struct

# Skip all tests on non-Linux
pytestmark = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="Landlock is Linux-only"
)


@pytest.fixture
def landlock_available():
    """Check if Landlock is available on this kernel."""
    try:
        from letta.services.tool_sandbox._landlock_ctypes import detect_landlock_abi
        return detect_landlock_abi() >= 1
    except Exception:
        return False


@pytest.fixture
def abi_version(landlock_available):
    """Get the Landlock ABI version."""
    if not landlock_available:
        return 0
    from letta.services.tool_sandbox._landlock_ctypes import detect_landlock_abi
    return detect_landlock_abi()


class TestLandlockCtypes:
    """Tests for the Landlock ctypes bindings."""

    def test_prctl_set_no_new_privs_succeeds(self, landlock_available):
        """prctl(PR_SET_NO_NEW_PRIVS) should succeed."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import prctl_set_no_new_privs
        # This should not raise
        prctl_set_no_new_privs()

    def test_landlock_create_ruleset_returns_valid_fd(self, landlock_available):
        """landlock_create_ruleset should return a valid file descriptor."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import (
            landlock_create_ruleset, ABI_V1_FS_RIGHTS
        )
        fd = landlock_create_ruleset(ABI_V1_FS_RIGHTS)
        assert fd >= 0
        os.close(fd)

    def test_landlock_add_path_rule_succeeds_for_valid_path(self, landlock_available):
        """landlock_add_path_rule should succeed for a valid directory."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import (
            landlock_create_ruleset, landlock_add_path_rule,
            ABI_V1_FS_RIGHTS, FS_READ_FILE, FS_READ_DIR,
        )
        fd = landlock_create_ruleset(ABI_V1_FS_RIGHTS)
        try:
            landlock_add_path_rule(fd, "/usr", FS_READ_FILE | FS_READ_DIR)
        finally:
            os.close(fd)

    def test_landlock_add_path_rule_fails_for_nonexistent_path(self, landlock_available):
        """landlock_add_path_rule should raise OSError for a nonexistent path."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import (
            landlock_create_ruleset, landlock_add_path_rule,
            ABI_V1_FS_RIGHTS, FS_READ_FILE,
        )
        fd = landlock_create_ruleset(ABI_V1_FS_RIGHTS)
        try:
            with pytest.raises(OSError):
                landlock_add_path_rule(fd, "/nonexistent/path/that/does/not/exist", FS_READ_FILE)
        finally:
            os.close(fd)

    def test_landlock_restrict_self_succeeds(self, landlock_available):
        """landlock_restrict_self should succeed on a valid ruleset."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import (
            prctl_set_no_new_privs, landlock_create_ruleset,
            landlock_add_path_rule, landlock_restrict_self,
            ABI_V1_FS_RIGHTS, FS_READ_FILE, FS_READ_DIR,
        )
        prctl_set_no_new_privs()
        fd = landlock_create_ruleset(ABI_V1_FS_RIGHTS)
        try:
            landlock_add_path_rule(fd, "/usr", FS_READ_FILE | FS_READ_DIR)
            landlock_restrict_self(fd)
        finally:
            os.close(fd)

    def test_detect_landlock_abi_returns_correct_version(self, landlock_available):
        """detect_landlock_abi should return a positive integer on supported kernels."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import detect_landlock_abi
        abi = detect_landlock_abi()
        assert isinstance(abi, int)
        assert abi >= 1

    def test_architecture_detection_returns_correct_syscall_numbers(self):
        """_get_syscall_numbers should return a tuple of 3 integers."""
        from letta.services.tool_sandbox._landlock_ctypes import _get_syscall_numbers
        numbers = _get_syscall_numbers()
        assert len(numbers) == 3
        assert all(isinstance(n, int) for n in numbers)
        assert all(n > 0 for n in numbers)

    def test_libseccomp_load_succeeds(self, landlock_available):
        """libseccomp should be loadable."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import apply_seccomp_filter
        # This should not raise — libseccomp2 is present in Debian
        apply_seccomp_filter(blocked_syscalls=[], block_fork=False, allow_network=True)

    def test_seccomp_filter_blocks_fork(self, landlock_available):
        """Seccomp filter with block_fork=True should block fork/clone/clone3/vfork."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import apply_seccomp_filter
        import ctypes
        apply_seccomp_filter(blocked_syscalls=[], block_fork=True, allow_network=False)
        # After applying the filter, fork should fail with EPERM
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ret = libc.fork()
        # fork returns -1 on failure (seccomp blocks it)
        # If ret == 0, we're in the child — exit immediately
        if ret == 0:
            os._exit(0)
        # Parent: fork should have failed
        assert ret == -1

    def test_seccomp_filter_blocks_ptrace(self, landlock_available):
        """Seccomp filter should block ptrace."""
        if not landlock_available:
            pytest.skip("Landlock not available")
        from letta.services.tool_sandbox._landlock_ctypes import apply_seccomp_filter
        import ctypes
        apply_seccomp_filter(blocked_syscalls=["ptrace"], block_fork=False, allow_network=True)
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # ptrace(PTRACE_TRACEME, 0, NULL, NULL) should fail with EPERM
        ret = libc.ptrace(0, 0, 0, 0)  # PTRACE_TRACEME = 0
        assert ret == -1


class TestLandlockConstants:
    """Tests for Landlock constant definitions."""

    def test_abi_v1_fs_rights_includes_all_v1_rights(self):
        """ABI_V1_FS_RIGHTS should include all 13 ABI v1 filesystem rights."""
        from letta.services.tool_sandbox._landlock_ctypes import ABI_V1_FS_RIGHTS
        # ABI v1 has 13 rights (bits 0-12)
        expected = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | \
                   (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8) | (1 << 9) | \
                   (1 << 10) | (1 << 11) | (1 << 12)
        assert ABI_V1_FS_RIGHTS == expected

    def test_constants_are_distinct(self):
        """All Landlock constant aliases should be distinct."""
        from letta.services.tool_sandbox._landlock_ctypes import (
            FS_READ_FILE, FS_READ_DIR, FS_WRITE_FILE, FS_MAKE_REG,
            FS_REMOVE_FILE, FS_REMOVE_DIR, FS_EXECUTE, FS_MAKE_DIR,
            FS_MAKE_CHAR, FS_MAKE_SOCK, FS_MAKE_FIFO, FS_MAKE_BLOCK,
            FS_MAKE_SYM, FS_REFER, FS_TRUNCATE, FS_IOCTL,
        )
        values = [
            FS_READ_FILE, FS_READ_DIR, FS_WRITE_FILE, FS_MAKE_REG,
            FS_REMOVE_FILE, FS_REMOVE_DIR, FS_EXECUTE, FS_MAKE_DIR,
            FS_MAKE_CHAR, FS_MAKE_SOCK, FS_MAKE_FIFO, FS_MAKE_BLOCK,
            FS_MAKE_SYM, FS_REFER, FS_TRUNCATE, FS_IOCTL,
        ]
        assert len(set(values)) == len(values), "All constants should be distinct"
