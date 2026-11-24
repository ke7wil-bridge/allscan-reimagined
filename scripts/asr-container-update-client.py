#!/usr/bin/env python3
"""Fixed-operation client for the root container update broker."""
from __future__ import annotations

import json
import os
import re
import socket
import sys

SOCKET = "/run/allscan-reimagined-host/bridge-setup.sock"
OPERATIONS = {
    "--check-json": "check", "--preflight-json": "preflight",
    "--queue-update": "queue", "--recover-json": "recover",
    "--status-json": "status", "--diagnostic-json": "diagnostic",
}
MAX_RESPONSE = 1024 * 1024
JOB_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")


def fail(message: str, code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": message}))
    return code


def main() -> int:
    if not sys.argv[1:] or sys.argv[1] not in OPERATIONS:
        return fail("Invalid updater operation", 2)
    operation = OPERATIONS[sys.argv[1]]
    needs_job = operation in {"status", "diagnostic"}
    if len(sys.argv) != (3 if needs_job else 2):
        return fail("Invalid updater operation", 2)
    request: dict[str, object] = {"schema": 1, "update": operation}
    if needs_job:
        if JOB_RE.fullmatch(sys.argv[2]) is None:
            return fail("Invalid update job ID", 2)
        request["jobId"] = sys.argv[2]
    path = os.environ.get("ASR_CONTAINER_HOST_SOCKET", SOCKET)
    if path != SOCKET and os.environ.get("ASR_BROKER_TESTING") != "1":
        return fail("Invalid broker socket")
    try:
        if os.geteuid() == 0:
            uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
            if not uid.isdigit() or not gid.isdigit() or not 1 <= int(uid) <= 65535 or not 1 <= int(gid) <= 65535:
                raise RuntimeError("root client lacks a validated invoking UID")
            os.setgroups([])
            os.setgid(int(gid))
            os.setuid(int(uid))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(30)
            connection.connect(path)
            connection.sendall(json.dumps(request, separators=(",", ":")).encode())
            connection.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            size = 0
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
        output = response["stdout"] if response["exitCode"] == 0 else (response["stderr"] or response["stdout"])
        # The API consumes JSON from stdout even for a rejected operation.
        sys.stdout.write(output)
        return int(response["exitCode"])
    except (OSError, ValueError, RuntimeError) as exc:
        return fail(f"Container updater unavailable: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
