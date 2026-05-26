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
        print("Landlock not available (ABI < 1), running unsandboxed", file=sys.stderr)
        return

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

    handled_net = 0
    if abi >= 4:
        handled_net = NET_BIND_TCP | NET_CONNECT_TCP

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

        # Add network rules if ABI >= 4 and network is allowed
        if abi >= 4:
            if config.get("allow_tcp_connect"):
                landlock_add_net_rule(ruleset_fd, NET_CONNECT_TCP)
            if config.get("allow_tcp_bind"):
                landlock_add_net_rule(ruleset_fd, NET_BIND_TCP)
        elif config.get("allow_tcp_connect") or config.get("allow_tcp_bind"):
            print("WARNING: Network access requested but Landlock ABI < 4. "
                  "Network restrictions not available.", file=sys.stderr)

        # Apply the sandbox — IRREVERSIBLE
        landlock_restrict_self(ruleset_fd)
    finally:
        os.close(ruleset_fd)


def apply_seccomp(config):
    """Apply seccomp-BPF syscall filter via libseccomp."""
    from letta.services.tool_sandbox._landlock_ctypes import apply_seccomp_filter

    blocked = config.get("blocked_syscalls", [])
    block_fork = config.get("block_fork", True)
    allow_network = config.get("allow_tcp_connect", False)
    apply_seccomp_filter(blocked, block_fork=block_fork, allow_network=allow_network)


if __name__ == "__main__":
    main()
