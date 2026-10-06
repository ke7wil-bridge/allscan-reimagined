#!/usr/bin/env python3
"""Constrained Asterisk CLI adapter and socket-activated host broker."""
from __future__ import annotations

import importlib.util
import json
import os
import pwd
import re
import socket
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOCKET = "/run/allscan-reimagined-bridge-rpc/asterisk.sock"
SERVICE_USER = "asr-bridge"
MAX_MESSAGE = 64 * 1024
INSTALLATIONS = Path("/var/lib/allscan-reimagined/bridge-setup/installations")


def backend():
    spec = importlib.util.spec_from_file_location("asr_backend_asterisk", HERE / "asr-provisioning-backend.py")
    if not spec or not spec.loader:
        raise RuntimeError("backend unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_command(argv: list[str]) -> str:
    if len(argv) != 2 or argv[0] != "-rx":
        raise RuntimeError("Asterisk operation is not allowed")
    command = argv[1]
    if not (re.fullmatch(r"rpt stats [0-9]{3,10}", command)
            or re.fullmatch(r"rpt lstats [0-9]{3,10}", command)
            or re.fullmatch(r"rpt show channels [0-9]{3,10}", command)
            or re.fullmatch(r"rpt cmd [0-9]{3,10} ilink (?:3|11) [0-9]{3,10}", command)
            or command == "core reload"
            or command == "module show like chan_usrp.so"):
        raise RuntimeError("Asterisk command is not allowed")
    return command


def peer_uid(fd: int = 0) -> int:
    connection = socket.socket(fileno=os.dup(fd))
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid
    finally:
        connection.close()


def run_host(command: str, *, allow_pending: bool = False) -> subprocess.CompletedProcess[str]:
    module = backend()
    profile = module.load_profile()
    if profile is None:
        raise RuntimeError("container profile unavailable")
    module.validate_active_profile(profile)
    authorize_command(command, profile, allow_pending=allow_pending)
    return subprocess.run(
        ["/usr/bin/docker", "exec", profile.asterisk_container,
         "/usr/sbin/asterisk", "-rx", command],
        capture_output=True, text=True, timeout=15, check=False,
    )


def safe_root_json(path: Path, allowed_uid: int = 0, allowed_gid: int | None = None) -> dict:
    info = path.lstat()
    allowed_group_write = (allowed_gid is not None and info.st_uid == 0
                           and info.st_gid == allowed_gid
                           and (info.st_mode & 0o777) == 0o664)
    unsafe_write_bits = bool(info.st_mode & 0o002) or (
        bool(info.st_mode & 0o020) and not allowed_group_write)
    if (path.is_symlink() or not path.is_file() or info.st_uid not in {0, allowed_uid}
            or info.st_nlink != 1 or unsafe_write_bits):
        raise RuntimeError("Asterisk authorization state is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Asterisk authorization state is invalid")
    return value


def authorize_command(command: str, profile, loader=safe_root_json,
                      installations: Path = INSTALLATIONS,
                      allow_pending: bool = False) -> None:
    if command in {"module show like chan_usrp.so", "core reload"}:
        return
    config = (loader(profile.asr_config / "config.json", profile.client_uid, profile.client_gid)
              if loader is safe_root_json else loader(profile.asr_config / "config.json"))
    main_node = str(config.get("node", ""))
    if not re.fullmatch(r"[0-9]{3,10}", main_node):
        raise RuntimeError("configured main node is invalid")
    owned: set[str] = set()
    pending: set[str] = set()
    bridges = config.get("bridges", [])
    if not isinstance(bridges, list):
        raise RuntimeError("configured bridges are invalid")
    active_net_mode = str(config.get("netBridgeMode", "")).lower()
    for bridge in bridges:
        if not isinstance(bridge, dict):
            continue
        bridge_id, node = str(bridge.get("id", "")), str(bridge.get("node", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id) or not re.fullmatch(r"[0-9]{3,10}", node):
            continue
        expected_target = f"/opt/allscan-reimagined-bridges/urf/{bridge_id}/tgif-run/net-target"
        card_type = str(bridge.get("cardType", ""))
        if card_type in {"ysf_net", "p25_net", "nxdn_net", "m17_net"}:
            pending.add(node)
        elif (card_type == "dmr_net"
                and bridge.get("backendMode") == "managed"
                and bridge.get("managedNetControl") is True
                and bridge.get("managedTargetFile") == expected_target):
            pending.add(node)
        record_path = installations / f"{bridge_id}.json"
        try:
            record = loader(record_path)
        except (KeyError, OSError, RuntimeError, ValueError):
            continue
        unified_owner = (node == "1999" and active_net_mode in {"dmr", "ysf", "p25", "nxdn", "m17"}
                         and card_type == f"{active_net_mode}_net")
        if (record.get("bridgeId") == bridge_id
                and (str(record.get("bridgeNode", "")) == node or unified_owner)):
            owned.add(node)
    status = re.fullmatch(r"rpt (?:stats|lstats|show channels) ([0-9]{3,10})", command)
    if status:
        if status.group(1) != main_node and not (allow_pending and status.group(1) in pending):
            raise RuntimeError("Asterisk status node is not authorized")
        return
    link = re.fullmatch(r"rpt cmd ([0-9]{3,10}) ilink (?:3|11) ([0-9]{3,10})", command)
    if not link or link.group(1) != main_node or link.group(2) not in owned:
        raise RuntimeError("Asterisk link pair is not authorized")


def broker_main() -> int:
    if os.geteuid() != 0:
        raise RuntimeError("Asterisk broker requires root")
    if peer_uid() != pwd.getpwnam(SERVICE_USER).pw_uid:
        raise RuntimeError("Asterisk broker peer is not authorized")
    raw = sys.stdin.buffer.read(MAX_MESSAGE + 1)
    if len(raw) > MAX_MESSAGE:
        raise RuntimeError("Asterisk broker request exceeds size limit")
    request = json.loads(raw)
    if not isinstance(request, dict) or set(request) != {"schema", "args"} or request.get("schema") != 1:
        raise RuntimeError("Asterisk broker request is invalid")
    args = request.get("args")
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise RuntimeError("Asterisk broker arguments are invalid")
    result = run_host(validate_command(args))
    if len(result.stdout.encode()) + len(result.stderr.encode()) > MAX_MESSAGE // 2:
        raise RuntimeError("Asterisk response exceeds size limit")
    sys.stdout.write(json.dumps({"schema": 1, "exitCode": result.returncode,
                                 "stdout": result.stdout, "stderr": result.stderr},
                                separators=(",", ":")) + "\n")
    return 0


def client_main(args: list[str]) -> int:
    validate_command(args)
    request = json.dumps({"schema": 1, "args": args}, separators=(",", ":")).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(20)
        connection.connect(SOCKET)
        connection.sendall(request)
        connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = connection.recv(8192)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MESSAGE:
                raise RuntimeError("Asterisk broker response exceeds size limit")
            chunks.append(chunk)
    response = json.loads(b"".join(chunks))
    if (not isinstance(response, dict)
            or set(response) != {"schema", "exitCode", "stdout", "stderr"}
            or response.get("schema") != 1
            or not isinstance(response.get("exitCode"), int)
            or not isinstance(response.get("stdout"), str)
            or not isinstance(response.get("stderr"), str)):
        raise RuntimeError("Asterisk broker response is invalid")
    sys.stdout.write(response["stdout"])
    sys.stderr.write(response["stderr"])
    return int(response["exitCode"])


def main() -> int:
    if sys.argv[1:] == ["--broker"]:
        try:
            return broker_main()
        except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError,
                subprocess.SubprocessError) as exc:
            sys.stdout.write(json.dumps({"schema": 1, "exitCode": 1,
                                         "stdout": "", "stderr": str(exc)},
                                        separators=(",", ":")) + "\n")
            return 0
    try:
        command = validate_command(sys.argv[1:])
        if os.geteuid() == 0:
            # This executable is the root-only adapter used by the constrained
            # provisioning broker.  Permit read-only status verification for
            # the exact managed DMR node already staged in root-owned config;
            # the unprivileged Asterisk socket retains the stricter policy.
            result = run_host(command, allow_pending=True)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        return client_main(sys.argv[1:])
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError,
            subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
