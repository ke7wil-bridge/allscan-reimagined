#!/usr/bin/env python3
"""Fixed-program client for container-host bridge controls."""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

SOCKET = "/run/allscan-reimagined-host/bridge-setup.sock"
CONTROL_TIMEOUT_SECONDS = 120
NAMES = {
    "allscan-reimagined-managed-dmr-net-control": "managed-dmr",
    "allscan-reimagined-ysf-bridge-control": "ysf",
    "allscan-reimagined-p25-bridge-control": "p25",
    "allscan-reimagined-nxdn-bridge-control": "nxdn",
    "allscan-reimagined-m17-bridge-control": "m17",
    "allscan-reimagined-net-bridge-mode-control": "net-mode",
}


def main() -> int:
    program = NAMES.get(Path(sys.argv[0]).name)
    if program is None:
        print('{"ok":false,"error":"unsupported host control"}', file=sys.stderr)
        return 2
    request = json.dumps({"schema": 1, "program": program, "args": sys.argv[1:]},
                         separators=(",", ":")).encode()
    path = os.environ.get("ASR_CONTAINER_HOST_SOCKET", SOCKET)
    if path != SOCKET and os.environ.get("ASR_BROKER_TESTING") != "1":
        print('{"ok":false,"error":"invalid broker socket"}', file=sys.stderr); return 2
    try:
        if os.geteuid() == 0:
            uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
            if not uid.isdigit() or not gid.isdigit() or not 1 <= int(uid) <= 65535 or not 1 <= int(gid) <= 65535:
                raise RuntimeError("root client lacks a validated invoking UID")
            os.setgroups([]); os.setgid(int(gid)); os.setuid(int(uid))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONTROL_TIMEOUT_SECONDS); connection.connect(path); connection.sendall(request); connection.shutdown(socket.SHUT_WR)
            response = b""
            while len(response) <= 1024 * 1024:
                chunk = connection.recv(65536)
                if not chunk: break
                response += chunk
                if len(response) > 1024 * 1024:
                    raise RuntimeError("broker response exceeds size limit")
        result = json.loads(response)
        if not isinstance(result, dict) or result.get("schema") != 1:
            raise RuntimeError("invalid broker response")
        success = result.get("exitCode") == 0
        target = sys.stdout if success else sys.stderr
        output = result.get("stdout") if success else (result.get("stderr") or result.get("stdout"))
        target.write(str(output or ""))
        return int(result.get("exitCode", 1))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": f"host control unavailable: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
