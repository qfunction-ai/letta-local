#!/usr/bin/env python3
"""Landlock sandbox wrapper for Letta tool execution.

Usage: python3 letta_landlock_wrapper.py --config <json> -- <python> <script> [args...]

This script applies Landlock filesystem/network restrictions and a seccomp-BPF
syscall filter, then execs the tool execution script. It is launched as a
separate process by AsyncToolSandboxLandlock to avoid fork-in-thread issues.

CRITICAL SECURITY DESIGN:
    Landlock's default-allow model means any access right NOT included in
    the handled_access_fs bitmask is ALLOWED. This wrapper handles ALL rights
    available for the detected ABI version, then allows specific rights on
    specific paths. Any right NOT granted by a path rule is DENIED.
"""

import json
import os
import sys


def main():
    # Parse args: --config <json> -- <python> <script> [args...]
    try:
        separator = sys.argv.index("--")
    except ValueError:
        print("Usage: letta_landlock_wrapper.py --config <json> -- <python> <script> [args...]", file=sys.stderr)
        sys.exit(1)

    config_json = sys.argv[2]  # after --config
    exec_args = sys.argv[separator + 1:]  # [python_executable, script_path, ...]

    if not exec_args:
        print("Error: no execution arguments after --", file=sys.stderr)
        sys.exit(1)

    config = json.loads(config_json)

    # Apply sandbox restrictions
    apply_landlock(config)
    apply_seccomp(config)

    # Replace this process with the tool script
    os.execvp(exec_args[0], exec_args)


def _probe_net_rules(abi, NET_BIND_TCP, NET_CONNECT_TCP, landlock_create_ruleset, landlock_add_net_rule, config):
    """Probe whether the kernel actually supports Landlock network rules.

    Some kernels report ABI >= 4 but the landlock_add_rule syscall for
    LANDLOCK_RULE_NET_PORT fails (e.g. Docker Desktop VMs, restricted
    containers). Returns True if net rules work, False if they don't.
    """
    if abi < 4:
        return False

    # Create a throwaway ruleset to test network rule support
    test_fd = landlock_create_ruleset(0, NET_BIND_TCP | NET_CONNECT_TCP, 0)
    try:
        if config.get("allow_tcp_connect"):
            landlock_add_net_rule(test_fd, NET_CONNECT_TCP)
        if config.get("allow_tcp_bind"):
            landlock_add_net_rule(test_fd, NET_BIND_TCP)
        return True
    except OSError:
        return False
    finally:
        os.close(test_fd)


def apply_landlock(config):
    """Apply Landlock filesystem and network restrictions.

    Handles ALL access rights for the detected ABI version to prevent
    the default-allow security hole. Any right not explicitly allowed
    via a path rule is denied.
    """
    from letta.services.tool_sandbox._landlock_ctypes import (
        prctl_set_no_new_privs,
        landlock_create_ruleset,
        landlock_add_path_rule,
        landlock_add_net_rule,
        landlock_restrict_self,
        detect_landlock_abi,
        # Access right constants
        ABI_V1_FS_RIGHTS,
        FS_READ_FILE, FS_READ_DIR, FS_WRITE_FILE, FS_MAKE_REG,
        FS_REMOVE_FILE, FS_REMOVE_DIR, FS_EXECUTE, FS_MAKE_DIR,
        FS_REFER, FS_TRUNCATE, FS_IOCTL,
        NET_BIND_TCP, NET_CONNECT_TCP,
        SCOPE_ABSTRACT_UNIX_SOCKET, SCOPE_SIGNAL,
    )

    abi = detect_landlock_abi()
    if abi < 1:
        # v0.16.37 (review L-6): FAIL CLOSED. The old behavior silently ran
        # tool code unsandboxed — a kernel without Landlock meant every
        # sandboxed tool escaped its sandbox with no signal. Now the tool
        # fails loudly through the normal tool-error path unless the
        # operator explicitly opts in with LETTA_ALLOW_UNSANDBOXED=true
        # (v0.16.31 flag pattern).
        if os.environ.get("LETTA_ALLOW_UNSANDBOXED", "").lower() == "true":
            print(
                "Landlock not available (ABI < 1); LETTA_ALLOW_UNSANDBOXED=true set, "
                "running unsandboxed by explicit operator opt-in",
                file=sys.stderr,
            )
            return
        print(
            "Landlock not available (ABI < 1) and LETTA_ALLOW_UNSANDBOXED is not set. "
            "Refusing to run tool code unsandboxed. "
            "Set LETTA_ALLOW_UNSANDBOXED=true to explicitly opt in to unsandboxed execution.",
            file=sys.stderr,
        )
        sys.exit(1)

    # CRITICAL: Handle ALL access rights for the detected ABI version.
    # Landlock's design: any access right NOT included in handled_access_fs
    # is ALLOWED by default. If you only handle WRITE_FILE and MAKE_REG but
    # don't handle TRUNCATE (ABI v3+), the sandboxed code can truncate any
    # file on the system. We must handle every right available at this ABI
    # version, then allow specific rights on specific paths via rules.
    handled_fs = ABI_V1_FS_RIGHTS
    if abi >= 2:
        handled_fs |= FS_REFER
    if abi >= 3:
        handled_fs |= FS_TRUNCATE
    if abi >= 5:
        handled_fs |= FS_IOCTL

    # Probe whether the kernel actually supports network rules.
    # If it doesn't, we MUST NOT include NET rights in handled_net —
    # otherwise Landlock would deny ALL network access (handled but no
    # rules allow it), breaking any tool that makes HTTP calls.
    net_supported = _probe_net_rules(
        abi, NET_BIND_TCP, NET_CONNECT_TCP,
        landlock_create_ruleset, landlock_add_net_rule, config,
    )

    handled_net = 0
    if net_supported:
        handled_net = NET_BIND_TCP | NET_CONNECT_TCP
    elif abi >= 4 and (config.get("allow_tcp_connect") or config.get("allow_tcp_bind")):
        # I28-2 (Ryan decision): FAIL CLOSED for network-enabled tools. A
        # kernel that reports ABI >= 4 but rejects Landlock net-port rules
        # cannot enforce the network boundary this tool was configured with.
        # Degrading to unrestricted networking would silently strip the
        # sandbox's network policy. Refuse with a named, actionable error.
        print(
            "ERROR: network-enabled tool refused: this kernel reports Landlock "
            "ABI >= 4 but rejects Landlock network rules (net-port add_rule "
            "fails), so the network boundary cannot be enforced. Tool config "
            "requests allow_tcp_connect="
            f"{bool(config.get('allow_tcp_connect'))} allow_tcp_bind="
            f"{bool(config.get('allow_tcp_bind'))}. "
            "Resolution: run the container on a kernel with Landlock ABI >= 4 "
            "network-rule support (Linux 6.7+ with CONFIG_SECURITY_LANDLOCK "
            "network patches, e.g. mainline kernels; Docker Desktop's linuxkit "
            "kernel does NOT qualify). Alternatively mark the tool as NOT "
            "requires_network if it truly needs no network.",
            file=sys.stderr,
        )
        # Parser-compatible tail: parse_stderr_error_msg matches
        # "SomeError: message" on the last stderr lines; end with a line it
        # can extract so the operator sees the refusal in the tool_return
        # instead of an empty error.
        print(
            "LandlockNetworkUnsupportedError: kernel cannot enforce Landlock "
            "network rules; refusing network-enabled tool (fail-closed). "
            "Upgrade the host kernel to one with Landlock net-rule support "
            "(Linux 6.7+ mainline; linuxkit/Docker Desktop kernels do not "
            "qualify).",
            file=sys.stderr,
        )
        sys.exit(1)

    scope = 0
    if abi >= 6:
        scope = SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL

    # Create ruleset
    prctl_set_no_new_privs()
    ruleset_fd = landlock_create_ruleset(handled_fs, handled_net, scope)

    try:
        # Add path rules for allowed read paths
        for path in config.get("allowed_read_paths", []):
            try:
                landlock_add_path_rule(ruleset_fd, path, FS_READ_FILE | FS_READ_DIR)
            except (FileNotFoundError, OSError) as e:
                print(f"Skipping non-existent path {path}: {e}", file=sys.stderr)

        # Add path rules for allowed write paths
        for path in config.get("allowed_write_paths", []):
            try:
                landlock_add_path_rule(ruleset_fd, path,
                    FS_READ_FILE | FS_READ_DIR | FS_WRITE_FILE | FS_MAKE_REG |
                    FS_REMOVE_FILE | FS_REMOVE_DIR | FS_MAKE_DIR)
            except (FileNotFoundError, OSError) as e:
                print(f"Skipping non-existent path {path}: {e}", file=sys.stderr)

        # Add path rules for allowed execute paths
        for path in config.get("allowed_execute_paths", []):
            try:
                landlock_add_path_rule(ruleset_fd, path, FS_EXECUTE | FS_READ_FILE | FS_READ_DIR)
            except (FileNotFoundError, OSError) as e:
                print(f"Skipping non-existent path {path}: {e}", file=sys.stderr)

        # Add /proc/self (read only, no /proc broadly)
        try:
            landlock_add_path_rule(ruleset_fd, "/proc/self", FS_READ_FILE | FS_READ_DIR)
        except (FileNotFoundError, OSError):
            pass  # /proc/self should always exist, but be safe

        # Add network rules if kernel actually supports them
        if net_supported:
            if config.get("allow_tcp_connect"):
                landlock_add_net_rule(ruleset_fd, NET_CONNECT_TCP)
            if config.get("allow_tcp_bind"):
                landlock_add_net_rule(ruleset_fd, NET_BIND_TCP)

        # Apply the sandbox — IRREVERSIBLE
        landlock_restrict_self(ruleset_fd)
    finally:
        os.close(ruleset_fd)


def apply_seccomp(config):
    """Apply seccomp-BPF syscall filter via libseccomp."""
    from letta.services.tool_sandbox._landlock_ctypes import apply_seccomp_filter

    # v0.16.41 (interim-4 G-1): the MANDATORY set is always applied on top
    # of any configured list — io_uring creates/connects sockets in-kernel,
    # invisible to socket()-based seccomp rules. A config cannot remove it.
    blocked = config.get("blocked_syscalls", [])
    MANDATORY_BLOCKED = [
        "ptrace", "mount", "umount2", "chroot",
        "pivot_root", "reboot", "swapon", "swapoff",
        "init_module", "finit_module", "delete_module",
        "kexec_load", "kexec_file_load",
        "io_uring_setup", "io_uring_enter", "io_uring_register",
    ]
    for _name in MANDATORY_BLOCKED:
        if _name not in blocked:
            blocked.append(_name)
    block_fork = config.get("block_fork", True)
    allow_network = config.get("allow_tcp_connect", False)
    apply_seccomp_filter(blocked, block_fork=block_fork, allow_network=allow_network)


if __name__ == "__main__":
    main()
