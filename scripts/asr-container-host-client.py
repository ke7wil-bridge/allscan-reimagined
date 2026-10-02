#!/usr/bin/env python3
"""Unprivileged client for the host provisioning broker."""
from __future__ import annotations

import json
import os
import socket
import sys

SOCKET = "/run/allscan-reimagined-host/bridge-setup.sock"
COMMANDS = {
    "m17-plan", "m17-install", "p25-plan", "p25-install",
    "nxdn-plan", "nxdn-install", "ysf-plan", "ysf-install",
    "dmr-plan", "dmr-install",
}
MAX_REQUEST = 256 * 1024
MAX_RESPONSE = 1024 * 1024


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(json.dumps({"ok": False, "error": "unsupported bridge setup action"}), file=sys.stderr)
        return 2
    payload = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(payload) > MAX_REQUEST:
        print(json.dumps({"ok": False, "error": "request exceeds size limit"}), file=sys.stderr)
        return 2
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, ValueError):
        print(json.dumps({"ok": False, "error": "request is not valid JSON"}), file=sys.stderr)
        return 2
    request = json.dumps({"schema": 1, "command": sys.argv[1], "payload": parsed},
                         separators=(",", ":")).encode()
    path = os.environ.get("ASR_CONTAINER_HOST_SOCKET", SOCKET)
    if path != SOCKET and os.environ.get("ASR_BROKER_TESTING") != "1":
        print(json.dumps({"ok": False, "error": "invalid broker socket"}), file=sys.stderr)
        return 2
    try:
        if os.geteuid() == 0:
            uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
            if not uid.isdigit() or not gid.isdigit() or not 1 <= int(uid) <= 65535 or not 1 <= int(gid) <= 65535:
                raise RuntimeError("root client lacks a validated invoking UID")
            os.setgroups([]); os.setgid(int(gid)); os.setuid(int(uid))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1200)
            connection.connect(path)
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
            chunks, size = [], 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise RuntimeError("broker response exceeds size limit")
                chunks.append(chunk)
        response = json.loads(b"".join(chunks))
        if not isinstance(response, dict) or set(response) != {"schema", "exitCode", "stdout", "stderr"}:
            raise RuntimeError("broker returned an invalid response")
        success = response["exitCode"] == 0
        output = response["stdout"] if success else (response["stderr"] or response["stdout"])
        target = sys.stdout if success else sys.stderr
        target.write(output)
        return int(response["exitCode"])
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": f"host provisioning unavailable: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
