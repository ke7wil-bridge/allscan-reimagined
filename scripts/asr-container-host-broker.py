#!/usr/bin/env python3
"""Narrow root broker for bridge provisioning from an unprivileged web tier."""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")
HELPER = HERE / "asr-bridge-setup-helper.py"
MAX_REQUEST = 256 * 1024
MAX_RESPONSE = 1024 * 1024
COMMANDS = frozenset({
    "m17-plan", "m17-install", "p25-plan", "p25-install",
    "nxdn-plan", "nxdn-install", "ysf-plan", "ysf-install",
    "dmr-plan", "dmr-install",
    "net-bridge-plan", "net-bridge-install",
})
PROGRAMS = {
    "managed-dmr": "/usr/local/sbin/allscan-reimagined-managed-dmr-net-control",
    "ysf": "/usr/local/sbin/allscan-reimagined-ysf-bridge-control",
    "p25": "/usr/local/sbin/allscan-reimagined-p25-bridge-control",
    "nxdn": "/usr/local/sbin/allscan-reimagined-nxdn-bridge-control",
    "m17": "/usr/local/sbin/allscan-reimagined-m17-bridge-control",
    "net-mode": "/usr/local/sbin/allscan-reimagined-net-bridge-mode-control",
}
DOCKER_HOME = "/var/cache/allscan-reimagined/docker-home"
DOCKER_CONFIG = "/var/cache/allscan-reimagined/docker-config"
BUILDX_CONFIG = "/var/cache/allscan-reimagined/buildx"


class BrokerError(RuntimeError):
    pass


def child_environment(profile_path: Path, profile) -> dict[str, str]:
    """Return the complete, non-inherited environment for privileged helpers."""
    return {
        "PATH": f"{HERE / 'container-host-bin'}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        # Docker's buildx plugin normally falls back to $HOME/.docker/buildx.
        # Pin all three locations because this environment intentionally
        # replaces (rather than extends) the systemd service environment.
        "HOME": DOCKER_HOME,
        "DOCKER_CONFIG": DOCKER_CONFIG,
        "BUILDX_CONFIG": BUILDX_CONFIG,
        "ASR_CONTAINER_PROVISIONING_PROFILE": str(profile_path),
        "ASR_URF_DOCKER_NETWORK_MODE": f"container:{profile.asterisk_container}",
        "ASR_URF_DOCKER_HOST_BASE": str(profile.runtime_host_root),
    }


def load_backend():
    import importlib.util
    path = HERE / "asr-provisioning-backend.py"
    spec = importlib.util.spec_from_file_location("asr_provisioning_backend_broker", path)
    if not spec or not spec.loader:
        raise BrokerError("provisioning backend module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def peer_uid(fd: int = 0) -> int:
    try:
        sock = socket.socket(fileno=os.dup(fd))
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid
    except OSError as exc:
        test_uid = os.environ.get("ASR_BROKER_TEST_PEER_UID")
        if test_uid and os.environ.get("ASR_BROKER_TESTING") == "1":
            return int(test_uid)
        raise BrokerError("broker request is not a Unix socket") from exc


def read_request(stream=sys.stdin.buffer) -> dict:
    raw = stream.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        raise BrokerError("broker request exceeds size limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise BrokerError("broker request is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BrokerError("broker request has an invalid shape")
    if set(value) == {"schema", "command", "payload"}:
        if value.get("schema") != 1 or value.get("command") not in COMMANDS:
            raise BrokerError("broker command is not allowed")
        if not isinstance(value.get("payload"), dict):
            raise BrokerError("broker payload must be an object")
        value["kind"] = "setup"
    elif set(value) == {"schema", "program", "args"}:
        if value.get("schema") != 1 or value.get("program") not in PROGRAMS:
            raise BrokerError("broker control program is not allowed")
        if not isinstance(value.get("args"), list) or not all(isinstance(item, str) for item in value["args"]):
            raise BrokerError("broker control arguments are invalid")
        validate_control(value["program"], value["args"])
        value["kind"] = "control"
    else:
        raise BrokerError("broker request has an invalid shape")
    return value


def validate_control(program: str, args: list[str]) -> None:
    import re
    bid = lambda value: re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", value) is not None
    user = lambda value: re.fullmatch(r"[A-Za-z0-9_.@+-]{1,80}", value) is not None
    number = lambda value, maximum: value.isdigit() and 1 <= int(value) <= maximum
    valid = False
    if program == "managed-dmr":
        valid = ((len(args) == 4 and args[:1] == ["--bridge"] and bid(args[1])
                  and args[2] == "--connect" and number(args[3], 16777215) and args[3] != "4000")
                 or (len(args) == 3 and args[:1] == ["--bridge"] and bid(args[1])
                     and args[2] in {"--disconnect", "--status"}))
    elif program == "ysf":
        valid = ((len(args) == 5 and args[0] == "--connect" and bid(args[1])
                  and re.fullmatch(r"[0-9]{5}", args[2]) and args[3] == "--user" and user(args[4]))
                 or (len(args) == 4 and args[0] == "--disconnect" and bid(args[1])
                     and args[2] == "--user" and user(args[3]))
                 or (len(args) == 2 and args[0] == "--catalog-status" and bid(args[1])))
    elif program in {"p25", "nxdn"}:
        destination_ok = lambda value: (number(value, 65534) and int(value) >= 11
                                        and int(value) not in ({20, 9999, 10999} if program == "p25" else {20, 9999}))
        valid = ((len(args) == 5 and args[0] == "connect" and bid(args[1])
                  and destination_ok(args[2]) and args[3] == "--user" and user(args[4]))
                 or (len(args) == 4 and args[0] == "disconnect" and bid(args[1])
                     and args[2] == "--user" and user(args[3]))
                 or (len(args) == 2 and args[0] == "status" and bid(args[1])))
    elif program == "m17":
        valid = ((len(args) == 3 and args[0] == "--bridge" and bid(args[1]) and args[2] == "status")
                 or (len(args) == 5 and args[0] == "--bridge" and bid(args[1])
                     and args[2] == "--user" and user(args[3]) and args[4] == "disconnect")
                 or (len(args) == 9 and args[0] == "--bridge" and bid(args[1])
                     and args[2] == "--user" and user(args[3]) and args[4] == "connect"
                     and args[5] == "--reflector" and re.fullmatch(r"M17-[A-Z0-9]{3}", args[6])
                     and args[7] == "--module" and re.fullmatch(r"[A-Z]", args[8])))
    elif program == "net-mode":
        valid = len(args) == 2 and args[0] == "--mode" and args[1] in {"dmr", "ysf", "p25", "nxdn", "m17"}
    if not valid:
        raise BrokerError("broker control arguments are not allowed")


def execute(request: dict, profile_path: Path = PROFILE) -> dict:
    backend = load_backend()
    profile = backend.load_profile(profile_path)
    if profile is None:
        raise BrokerError("container provisioning profile is unavailable")
    if peer_uid() != profile.client_uid:
        raise BrokerError("broker peer is not authorized")
    backend.validate_active_profile(profile)
    executable = HELPER if request["kind"] == "setup" else Path(PROGRAMS[request["program"]])
    if not executable.is_file() or executable.is_symlink():
        raise BrokerError("host bridge helper is unavailable")
    info = executable.stat()
    parent = executable.parent.stat()
    if (info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022
            or parent.st_uid != 0 or parent.st_mode & 0o022):
        raise BrokerError("host bridge helper permissions are unsafe")
    environment = child_environment(profile_path, profile)
    argv = ([str(executable), request["command"]] if request["kind"] == "setup"
            else [str(executable), *request["args"]])
    input_data = (json.dumps(request["payload"], separators=(",", ":"))
                  if request["kind"] == "setup" else None)
    result = subprocess.run(
        argv, input=input_data,
        capture_output=True, text=True, env=environment, check=False, timeout=1200,
    )
    if len(result.stdout) + len(result.stderr) > MAX_RESPONSE:
        raise BrokerError("host helper response exceeds size limit")
    if (request["kind"] == "setup" and request["command"].endswith("-install")
            and result.returncode == 0):
        backend.refresh_watch_snapshots(profile)
    return {"schema": 1, "exitCode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=PROFILE)
    args = parser.parse_args()
    try:
        response = execute(read_request(), args.profile)
    except (BrokerError, OSError, ValueError) as exc:
        response = {"schema": 1, "exitCode": 1, "stdout": "",
                    "stderr": json.dumps({"ok": False, "error": str(exc)}) + "\n"}
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
