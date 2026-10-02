#!/usr/bin/env python3
"""Enter one validated Asterisk container network namespace and exec a unit."""
from __future__ import annotations

import argparse
import ctypes
import grp
import json
import os
import pwd
import re
import subprocess
import sys

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
CLONE_NEWNET = 0x40000000


def enter_network_namespace(descriptor: int) -> None:
    """Use libc for Debian/Python versions that do not expose os.setns."""
    if hasattr(os, "setns"):
        os.setns(descriptor, getattr(os, "CLONE_NEWNET", CLONE_NEWNET))
        return
    libc = ctypes.CDLL(None, use_errno=True)
    setns = getattr(libc, "setns", None)
    if setns is None:
        raise OSError("setns is unavailable on this host")
    setns.argtypes = (ctypes.c_int, ctypes.c_int)
    setns.restype = ctypes.c_int
    if setns(descriptor, CLONE_NEWNET) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--proc-root", default="/proc")
    parser.add_argument("--user", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("network namespace entry requires root")
    if not NAME.fullmatch(args.container):
        raise SystemExit("invalid container identity")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not os.path.isabs(command[0]):
        raise SystemExit("an absolute executable is required")
    result = subprocess.run(
        ["/usr/bin/docker", "inspect", "--format", "{{json .State}}", args.container],
        capture_output=True, text=True, check=False,
    )
    try:
        state = json.loads(result.stdout)
        pid = int(state["Pid"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit("Asterisk container state is unavailable") from exc
    if result.returncode or state.get("Running") is not True or pid < 2:
        raise SystemExit("Asterisk container is not running")
    proc_root = os.path.realpath(args.proc_root)
    if not os.path.isabs(proc_root) or not os.path.isfile(f"{proc_root}/self/status"):
        raise SystemExit("invalid host proc filesystem")
    descriptor = os.open(f"{proc_root}/{pid}/ns/net", os.O_RDONLY | os.O_CLOEXEC)
    try:
        verify = subprocess.run(
            ["/usr/bin/docker", "inspect", "--format", "{{json .State}}", args.container],
            capture_output=True, text=True, check=False,
        )
        try:
            verified_state = json.loads(verify.stdout)
        except ValueError as exc:
            raise SystemExit("Asterisk container changed during namespace entry") from exc
        if (verify.returncode or verified_state.get("Running") is not True
                or int(verified_state.get("Pid", 0)) != pid):
            raise SystemExit("Asterisk container changed during namespace entry")
        enter_network_namespace(descriptor)
    finally:
        os.close(descriptor)
    if args.user:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", args.user):
            raise SystemExit("invalid service account")
        account = pwd.getpwnam(args.user)
        os.initgroups(account.pw_name, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.execv(command[0], command)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
