"""Landlock and seccomp-BPF ctypes bindings for kernel-level sandboxing.

Pure Python module wrapping Landlock syscalls via ctypes and the seccomp
filter via libseccomp.so.2. No C extension, no compilation required.

libseccomp2 is already present in the Docker image (python:3.11-slim ->
Debian -> libseccomp2). No new dependency.

CRITICAL: When creating a Landlock ruleset, you MUST handle ALL access
rights available for the detected ABI version. Any right NOT included in
the handled_access_fs bitmask is ALLOWED by default. See the
LandlockSandboxConfig docstring for the full list per ABI version.
"""

import ctypes
import ctypes.util
import os
import struct

# --- Landlock constants (ABI v6) ---

PR_SET_NO_NEW_PRIVS = 38
LANDLOCK_CREATE_RULESET_VERSION = 1 << 0

# Landlock rule types
LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_RULE_NET_PORT = 2

# Filesystem access rights (ABI v1)
LANDLOCK_ACCESS_FS_EXECUTE      = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE   = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE    = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR     = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR   = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE  = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR    = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR     = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG     = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK    = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO    = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK   = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM     = 1 << 12
# ABI v2+
LANDLOCK_ACCESS_FS_REFER        = 1 << 13
# ABI v3+
LANDLOCK_ACCESS_FS_TRUNCATE     = 1 << 14
# ABI v5+
LANDLOCK_ACCESS_FS_IOCTL        = 1 << 15

# Network access rights (ABI v4+)
LANDLOCK_ACCESS_NET_BIND_TCP    = 1 << 0
LANDLOCK_ACCESS_NET_CONNECT_TCP = 1 << 1

# IPC scoping (ABI v6)
LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
LANDLOCK_SCOPE_SIGNAL               = 1 << 1

# Short aliases for readability
FS_READ_FILE   = LANDLOCK_ACCESS_FS_READ_FILE
FS_READ_DIR    = LANDLOCK_ACCESS_FS_READ_DIR
FS_WRITE_FILE  = LANDLOCK_ACCESS_FS_WRITE_FILE
FS_MAKE_REG    = LANDLOCK_ACCESS_FS_MAKE_REG
FS_REMOVE_FILE = LANDLOCK_ACCESS_FS_REMOVE_FILE
FS_EXECUTE     = LANDLOCK_ACCESS_FS_EXECUTE
FS_MAKE_DIR    = LANDLOCK_ACCESS_FS_MAKE_DIR
FS_MAKE_CHAR   = LANDLOCK_ACCESS_FS_MAKE_CHAR
FS_MAKE_SOCK   = LANDLOCK_ACCESS_FS_MAKE_SOCK
FS_MAKE_FIFO   = LANDLOCK_ACCESS_FS_MAKE_FIFO
FS_MAKE_BLOCK  = LANDLOCK_ACCESS_FS_MAKE_BLOCK
FS_MAKE_SYM    = LANDLOCK_ACCESS_FS_MAKE_SYM
FS_REMOVE_DIR  = LANDLOCK_ACCESS_FS_REMOVE_DIR
FS_REFER       = LANDLOCK_ACCESS_FS_REFER
FS_TRUNCATE    = LANDLOCK_ACCESS_FS_TRUNCATE
FS_IOCTL       = LANDLOCK_ACCESS_FS_IOCTL
NET_BIND_TCP    = LANDLOCK_ACCESS_NET_BIND_TCP
NET_CONNECT_TCP = LANDLOCK_ACCESS_NET_CONNECT_TCP
SCOPE_ABSTRACT_UNIX_SOCKET = LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET
SCOPE_SIGNAL = LANDLOCK_SCOPE_SIGNAL

# All ABI v1 filesystem rights (for building handled_access_fs)
ABI_V1_FS_RIGHTS = (
    FS_READ_FILE | FS_READ_DIR | FS_WRITE_FILE | FS_MAKE_REG |
    FS_REMOVE_FILE | FS_REMOVE_DIR | FS_EXECUTE | FS_MAKE_DIR |
    FS_MAKE_CHAR | FS_MAKE_SOCK | FS_MAKE_FIFO | FS_MAKE_BLOCK |
    FS_MAKE_SYM
)

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _get_syscall_numbers():
    """Detect architecture and return correct Landlock syscall numbers.

    Returns:
        Tuple of (landlock_create_ruleset, landlock_add_rule, landlock_restrict_self)

    Raises:
        RuntimeError: If the architecture is not supported.
    """
    machine = os.uname().machine
    if machine == "x86_64":
        return 445, 446, 447
    elif machine in ("aarch64", "arm64"):
        return 444, 445, 446
    else:
        raise RuntimeError(f"Unsupported architecture for Landlock: {machine}")


def prctl_set_no_new_privs():
    """Set PR_SET_NO_NEW_PRIVS to prevent privilege escalation.

    Required before Landlock. Once set, the process and its descendants
    cannot gain privileges via setuid/setgid binaries.
    """
    ret = _libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if ret != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")


def detect_landlock_abi() -> int:
    """Detect the Landlock ABI version available on this kernel.

    Returns:
        0 if Landlock is not available (ENOSYS or not Linux)
        1-6+ for the supported ABI version

    The return value from landlock_create_ruleset with
    LANDLOCK_CREATE_RULESET_VERSION IS the ABI version number.
    """
    try:
        SYS_create, _, _ = _get_syscall_numbers()
        result = _libc.syscall(SYS_create, None, 0, LANDLOCK_CREATE_RULESET_VERSION)
        if result >= 0:
            return result  # The return value IS the ABI version
        return 0
    except Exception:
        return 0


def landlock_create_ruleset(handled_access_fs, handled_access_net=0, scope=0):
    """Create a Landlock ruleset and return its file descriptor.

    Args:
        handled_access_fs: Bitmask of ALL filesystem access rights to handle.
            Rights NOT in this bitmask are ALLOWED by default, so you MUST
            include all rights available for the detected ABI version.
        handled_access_net: Bitmask of network access rights (ABI v4+).
            0 means no network rights are handled (network is unrestricted).
        scope: Bitmask of IPC scoping (ABI v6+).

    Returns:
        File descriptor for the new ruleset.
    """
    SYS_create, _, _ = _get_syscall_numbers()
    # struct landlock_ruleset_attr {
    #   __u64 handled_access_fs;
    #   __u64 handled_access_net;
    #   __u64 scoped;
    # }
    attr = struct.pack("QQQ", handled_access_fs, handled_access_net, scope)
    fd = _libc.syscall(SYS_create, attr, len(attr), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    return fd


def landlock_add_path_rule(ruleset_fd, path, allowed_access):
    """Add a path-beneath rule to the ruleset.

    Args:
        ruleset_fd: File descriptor from landlock_create_ruleset.
        path: Directory path to allow access to (recursively).
        allowed_access: Bitmask of access rights to allow on this path.

    Raises:
        OSError: If the rule cannot be added (e.g., path doesn't exist).
    """
    _, SYS_add, _ = _get_syscall_numbers()
    dir_fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    # struct landlock_path_beneath_attr {
    #   __u64 allowed_access;
    #   __s32 parent_fd;
    #   __s32 reserved;
    # }
    attr = struct.pack("Qii", allowed_access, dir_fd, 0)
    ret = _libc.syscall(SYS_add, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH, attr, 0)
    os.close(dir_fd)
    if ret != 0:
        raise OSError(ctypes.get_errno(), f"landlock_add_rule failed for {path}")


def landlock_add_net_rule(ruleset_fd, allowed_access, port=0):
    """Add a net port rule to the ruleset (ABI v4+).

    Args:
        ruleset_fd: File descriptor from landlock_create_ruleset.
        allowed_access: Bitmask of network access rights (NET_BIND_TCP or NET_CONNECT_TCP).
        port: Port number (0 means any port).

    Raises:
        OSError: If the rule cannot be added.
    """
    _, SYS_add, _ = _get_syscall_numbers()
    # struct landlock_net_port_attr {
    #   __u64 port;
    #   __u64 allowed_access;
    # }
    attr = struct.pack("QQ", port, allowed_access)
    ret = _libc.syscall(SYS_add, ruleset_fd, LANDLOCK_RULE_NET_PORT, attr, 0)
    if ret != 0:
        raise OSError(ctypes.get_errno(), "landlock_add_rule (net) failed")


def landlock_restrict_self(ruleset_fd):
    """Apply the Landlock ruleset to the current process.

    Once applied, the restrictions are IRREVERSIBLE — the process and all
    its descendants cannot relax them.
    """
    _, _, SYS_restrict = _get_syscall_numbers()
    ret = _libc.syscall(SYS_restrict, ruleset_fd, 0)
    if ret != 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")


def apply_seccomp_filter(blocked_syscalls, block_fork=True, allow_network=False):
    """Install a seccomp filter via libseccomp.so.2.

    Uses libseccomp's high-level API instead of raw BPF construction.
    libseccomp2 is already present in the Docker image (python:3.11-slim
    -> Debian -> libseccomp2). No new dependency.

    CRITICAL: argtypes/restype must be set on all libseccomp functions.
    On aarch64, ctypes default argument conversion mangles the arguments
    (e.g., passing a Python string where an int is expected), making the
    seccomp filter silently non-functional. We also resolve syscall names
    to numbers via seccomp_syscall_resolve_name because seccomp_rule_add
    expects an int syscall number, not a string. On aarch64, some syscalls
    (e.g., fork) don't exist as discrete syscall numbers — resolve returns
    a negative value, and we skip those.

    Args:
        blocked_syscalls: List of syscall names to block (e.g., ["ptrace", "mount"]).
        block_fork: If True, block fork/clone/clone3/vfork.
        allow_network: If True, allow socket/connect/send/recv syscalls.
    """
    try:
        libseccomp = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    except OSError:
        libseccomp = ctypes.CDLL(ctypes.util.find_library("seccomp"), use_errno=True)

    # Set argtypes/restype — REQUIRED on aarch64 where default conversion
    # mangles arguments. Without these, seccomp_rule_add silently fails
    # and the filter does nothing.
    libseccomp.seccomp_init.restype = ctypes.c_void_p
    libseccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    libseccomp.seccomp_rule_add.restype = ctypes.c_int
    libseccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    libseccomp.seccomp_load.restype = ctypes.c_int
    libseccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    libseccomp.seccomp_release.restype = None
    libseccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    libseccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    libseccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]

    # SCMP_ACT_ALLOW = 0x7fff0000 (default: allow all, then block specific)
    SCMP_ACT_ALLOW = 0x7FFF0000
    SCMP_ACT_ERRNO = 0x00050000  # ERRNO(EPERM=13)

    ctx = libseccomp.seccomp_init(SCMP_ACT_ALLOW)
    if not ctx:
        raise OSError(ctypes.get_errno(), "seccomp_init failed")

    def _block_syscall(ctx, name):
        """Resolve a syscall name to its number and add a blocking rule.

        On aarch64, some syscalls (e.g., fork) don't exist as discrete
        entries — seccomp_syscall_resolve_name returns __NR_SCMP_ERROR
        (a negative number). We skip those rather than failing.
        """
        nr = libseccomp.seccomp_syscall_resolve_name(name.encode("ascii"))
        if nr < 0:
            return  # Syscall doesn't exist on this arch (e.g., fork on aarch64)
        ret = libseccomp.seccomp_rule_add(ctx, SCMP_ACT_ERRNO | 13, nr, 0)
        if ret != 0:
            raise OSError(ctypes.get_errno(), f"seccomp_rule_add failed for {name} (nr={nr})")

    try:
        # Block fork/clone/clone3/vfork
        if block_fork:
            for name in ("fork", "clone", "clone3", "vfork"):
                _block_syscall(ctx, name)

        # Block additional syscalls
        for name in blocked_syscalls:
            _block_syscall(ctx, name)

        # If network is NOT allowed, block socket/connect/etc.
        if not allow_network:
            for name in ("socket", "connect", "bind", "listen",
                         "accept", "sendto", "recvfrom"):
                _block_syscall(ctx, name)

        # Load the filter
        ret = libseccomp.seccomp_load(ctx)
        if ret != 0:
            raise OSError(ctypes.get_errno(), "seccomp_load failed")
    finally:
        libseccomp.seccomp_release(ctx)
