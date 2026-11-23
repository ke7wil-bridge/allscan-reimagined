#!/usr/bin/env python3
"""Authorize, sign, and dispatch bounded ASR Plus remote control requests."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from asrplus_command_integrity import load_central_hub, sign_command, verify_command


HUBS = {"masterhub": "674982", "arizona-hub": "641894", "kentucky-hub": "674980",
        "texas-hub": "674984", "kentucky": "674982", "phoenix": "641894",
        "georgia": "674980", "texas": "674984"}
REMOTE_HUBS = {"641894": "phoenix", "674980": "georgia", "674984": "texas"}
QUEUE_ROOT = Path("/var/lib/asrplus/queue")
AUDIT_PATH = Path("/var/log/asrplus-control.jsonl")
INTEGRITY_CONFIG = Path(os.environ.get(
    "ASRPLUS_COMMAND_INTEGRITY_CONFIG", "/etc/asrplus/command-integrity.json"))
MAX_QUEUE_DEPTH = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
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


def audit_outcome(payload: dict) -> None:
    """Record an outcome without changing an already committed control result."""
    try:
        audit(payload)
    except OSError:
        pass


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
        if hub not in HUBS or action not in {"connect", "disconnect", "ban", "unban", "ban-list", "ban-result"}:
            raise ValueError("invalid hub or action")
        if action == "ban-result":
            identifier = str(request.get("id", ""))
            if not re.fullmatch(r"[a-f0-9]{32}", identifier) or HUBS[hub] == "674982":
                raise ValueError("invalid result request")
            remote_hub = REMOTE_HUBS[HUBS[hub]]
            result_path = Path("/var/lib/asrplus/results") / remote_hub / (identifier + ".json")
            if not result_path.is_file():
                print(json.dumps({"ok": True, "pending": True}))
                return 0
            record = json.loads(result_path.read_text())
            if record.get("action") not in {"ban", "unban", "ban-list"}:
                raise ValueError("not a moderation result")
            print(json.dumps({"ok": True, "pending": False, "result": record.get("remoteResult", {})}))
            return 0
        if action in {"connect", "disconnect"} and (not re.fullmatch(r"[0-9]{3,10}", node) or node == HUBS[hub]):
            raise ValueError("invalid remote node")
        kind = value = ban_duration = reason = ""
        if action in {"ban", "unban", "ban-list"}:
            if HUBS[hub] == "674982":
                raise ValueError("Master moderation uses the ASR ban API")
            kind = str(request.get("kind", ""))
            value = str(request.get("value", "")).upper()
            ban_duration = str(request.get("duration", ""))
            reason = str(request.get("reason", ""))
            if action != "ban-list" and (
                kind not in {"node", "allstar-call", "echolink-call"} or
                not re.fullmatch(r"[0-9]{3,10}" if kind == "node" else r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?", value)
            ):
                raise ValueError("invalid ban target")
            if action == "ban" and (ban_duration not in {"15m", "1h", "1w", "30d", "permanent"} or len(reason) > 160):
                raise ValueError("invalid ban duration or reason")
        command_id = uuid.uuid4().hex
        command = {
            "id": command_id, "utc": utc_now(), "hub": hub, "localNode": HUBS[hub],
            "action": action, "node": node, "permanent": permanent, "actor": actor,
            "kind": kind if action in {"ban", "unban"} else "",
            "value": value if action in {"ban", "unban"} else "",
            "duration": ban_duration if action == "ban" else "",
            "reason": reason if action == "ban" else "",
        }
        if HUBS[hub] == "674982":
            audit(command | {"result": "authorized"})
            ilink = "13" if action == "connect" and permanent else "3" if action == "connect" else "11"
            result = subprocess.run(
                ["/usr/sbin/asterisk", "-rx", f"rpt cmd {HUBS[hub]} ilink {ilink} {node}"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            command.update({"result": "accepted" if result.returncode == 0 else "failed", "exitCode": result.returncode})
            audit_outcome(command)
            if result.returncode != 0:
                raise RuntimeError("Asterisk rejected the command")
            response = {"ok": True, "queued": False, "id": command_id, "message": f"{action.title()} command accepted; awaiting confirmed link state."}
        else:
            remote_hub = REMOTE_HUBS[HUBS[hub]]
            # Remote agents validate command.hub against their canonical
            # queue identity, not the dashboard's display identifier.
            command["hub"] = remote_hub
            integrity = load_central_hub(INTEGRITY_CONFIG, remote_hub)
            command = sign_command(command, integrity["key"], integrity["keyId"])
            audit(command | {"result": "authorized", "requestedHub": hub})
            queue = QUEUE_ROOT / remote_hub
            queue.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(queue, 0o700)
            lock_path = QUEUE_ROOT / f".{remote_hub}.lock"
            with lock_path.open("a+") as lock:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                if len(list(queue.glob("*.json"))) + len(list(queue.glob("*.inflight"))) >= MAX_QUEUE_DEPTH:
                    raise RuntimeError("remote command queue is full")
                # A disconnect supersedes unprocessed connects for the same
                # hub/node. Otherwise an older queued connect can undo it.
                if action == "disconnect":
                    archive = Path("/var/lib/asrplus/queue-archive") / remote_hub
                    for queued in queue.glob("*.json"):
                        try:
                            old = json.loads(queued.read_text(encoding="utf-8"))
                            signed_old = old.get("protocolVersion") == 1 or "signature" in old
                            if signed_old:
                                verify_command(old, integrity["key"], integrity["keyId"],
                                               remote_hub, HUBS[hub], allow_expired=True)
                            elif not (integrity["mode"] == "optional"
                                      and old.get("hub") == remote_hub
                                      and str(old.get("localNode")) == HUBS[hub]):
                                continue
                            if (queued.stem == str(old.get("id", ""))
                                    and str(old.get("node")) == node
                                    and old.get("action") == "connect"):
                                archive.mkdir(parents=True, exist_ok=True, mode=0o700)
                                os.chmod(archive, 0o700)
                                destination = archive / f"{queued.stem}.{int(datetime.now().timestamp())}.superseded.json"
                                audit(old | {"result": "supersede-authorized", "requestedHub": hub})
                                queued.replace(destination)
                                audit_outcome(old | {"result": "superseded", "requestedHub": hub})
                        except (OSError, ValueError):
                            continue
                atomic_json(queue / f"{command_id}.json", command)
            # Notify Kentucky immediately; the existing timer remains a
            # fallback if the restricted SSH notification is unavailable.
            if remote_hub == "georgia":
                try:
                    subprocess.Popen(
                        ["/usr/bin/ssh", "-i", "/root/.ssh/asrplus-notify-ky2",
                         "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                         "-o", "StrictHostKeyChecking=yes",
                         "-o", "ConnectTimeout=2",
                         "-o", "ConnectionAttempts=1",
                         "root@44.30.157.22", "notify"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
                except OSError:
                    pass
            audit_outcome(command | {"result": "queued", "requestedHub": hub})
            response = {"ok": True, "queued": True, "id": command_id, "message": f"{action.title()} command queued; awaiting confirmed link state."}
        print(json.dumps(response))
        return 0
    except Exception as error:
        audit_outcome({"utc": utc_now(), "result": "rejected",
                       "hub": str(locals().get("hub", ""))[:32],
                       "action": str(locals().get("action", ""))[:32],
                       "actor": str(locals().get("actor", ""))[:80],
                       "error": str(error)[:200]})
        print(json.dumps({"ok": False, "error": str(error)[:200]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
