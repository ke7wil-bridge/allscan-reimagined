#!/usr/bin/env python3
"""Validate and dispatch ASR Plus AllStar connect/disconnect requests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


HUBS = {"kentucky": "674982", "phoenix": "641894", "georgia": "674980"}
QUEUE_ROOT = Path("/var/lib/asrplus/queue")
AUDIT_PATH = Path("/var/log/asrplus-control.jsonl")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def audit(payload: dict) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    os.chmod(AUDIT_PATH, 0o640)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read(16384))
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        hub = str(request.get("hub", "")).lower()
        action = str(request.get("action", "")).lower()
        node = str(request.get("node", ""))
        permanent = request.get("permanent") is True
        actor = re.sub(r"[^A-Za-z0-9_.@-]", "", str(request.get("actor", "")))[:80]
        if hub not in HUBS or action not in {"connect", "disconnect"}:
            raise ValueError("invalid hub or action")
        if not re.fullmatch(r"[0-9]{3,10}", node) or node == HUBS[hub]:
            raise ValueError("invalid remote node")
        command_id = uuid.uuid4().hex
        command = {
            "id": command_id, "utc": utc_now(), "hub": hub, "localNode": HUBS[hub],
            "action": action, "node": node, "permanent": permanent, "actor": actor,
        }
        if hub == "kentucky":
            ilink = "13" if action == "connect" and permanent else "3" if action == "connect" else "11"
            result = subprocess.run(
                ["/usr/sbin/asterisk", "-rx", f"rpt cmd {HUBS[hub]} ilink {ilink} {node}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            command.update({"result": "complete" if result.returncode == 0 else "failed", "exitCode": result.returncode})
            audit(command)
            if result.returncode != 0:
                raise RuntimeError("Asterisk rejected the command")
            response = {"ok": True, "queued": False, "id": command_id, "message": f"{action.title()} command sent on Kentucky."}
        else:
            queue = QUEUE_ROOT / hub
            queue.mkdir(parents=True, exist_ok=True)
            if len(list(queue.glob("*.json"))) + len(list(queue.glob("*.inflight"))) >= 100:
                raise RuntimeError("remote command queue is full")
            atomic_json(queue / f"{command_id}.json", command)
            audit(command | {"result": "queued"})
            response = {"ok": True, "queued": True, "id": command_id, "message": f"{action.title()} command queued for {hub.title()}."}
        print(json.dumps(response))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:200]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
