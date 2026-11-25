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
        "ky2": "674980", "texas": "674984", "uk-hub": "674985", "uk": "674985"}
REMOTE_HUBS = {"641894": "phoenix", "674980": "ky2", "674984": "texas", "674985": "uk"}
QUEUE_ROOT = Path("/var/lib/asrplus/queue")
AUDIT_PATH = Path("/var/log/asrplus-control.jsonl")
INTEGRITY_CONFIG = Path(os.environ.get(
    "ASRPLUS_COMMAND_INTEGRITY_CONFIG", "/etc/asrplus/command-integrity.json"))
MAX_QUEUE_DEPTH = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def queue_remote(command: dict, local_node: str, requested_hub: str) -> dict:
    """Sign and queue one command for a remote hub agent."""
    remote_hub = REMOTE_HUBS[local_node]
    # Remote agents validate command.hub against their canonical
    # queue identity, not the dashboard's display identifier.
    command["hub"] = remote_hub
    integrity = load_central_hub(INTEGRITY_CONFIG, remote_hub)
    command = sign_command(command, integrity["key"], integrity["keyId"])
    audit(command | {"result": "authorized", "requestedHub": requested_hub})
    queue = QUEUE_ROOT / remote_hub
    queue.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = QUEUE_ROOT / f".{remote_hub}.lock"
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(list(queue.glob("*.json"))) + len(list(queue.glob("*.inflight"))) >= MAX_QUEUE_DEPTH:
            raise RuntimeError("remote command queue is full")
        # A disconnect supersedes unprocessed connects for the same
        # hub/node. Otherwise an older queued connect can undo it.
        if command.get("action") == "disconnect":
            archive = Path("/var/lib/asrplus/queue-archive") / remote_hub
            for queued in queue.glob("*.json"):
                try:
                    old = json.loads(queued.read_text(encoding="utf-8"))
                    signed_old = old.get("protocolVersion") == 1 or "signature" in old
                    if signed_old:
                        verify_command(old, integrity["key"], integrity["keyId"],
                                       remote_hub, local_node, allow_expired=True)
                    elif not (integrity["mode"] == "optional"
                              and old.get("hub") == remote_hub
                              and str(old.get("localNode")) == local_node):
                        continue
                    if (queued.stem == str(old.get("id", ""))
                            and str(old.get("node")) == str(command.get("node"))
                            and old.get("action") == "connect"):
                        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
                        destination = archive / f"{queued.stem}.{int(datetime.now().timestamp())}.superseded.json"
                        audit(old | {"result": "supersede-authorized", "requestedHub": requested_hub})
                        queued.replace(destination)
                        audit_outcome(old | {"result": "superseded", "requestedHub": requested_hub})
                except (OSError, ValueError):
                    continue
        atomic_json(queue / f"{command_id}.json", command)
    # Notify Kentucky immediately; the existing timer remains a
    # fallback if the restricted SSH notification is unavailable.
    if remote_hub == "ky2":
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
    audit_outcome(command | {"result": "queued", "requestedHub": requested_hub})
    return command


BAN_DURATIONS = {"15m", "1h", "1w", "30d", "permanent"}
GLOBAL_BAN_HELPER = "/usr/local/sbin/allscan-reimagined-global-ban"
ASL_BAN_HELPER = "/usr/local/sbin/allscan-reimagined-asl-ban"
CALLSIGN_RE = r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?"


def moderation_target(kind: str, value: str, verb: str, duration: str, reason: str) -> None:
    if kind not in {"node", "allstar-call", "echolink-call"} or not re.fullmatch(
            r"[0-9]{3,10}" if kind == "node" else CALLSIGN_RE, value):
        raise ValueError("invalid ban target")
    if verb == "ban" and (duration not in BAN_DURATIONS or len(reason) > 160):
        raise ValueError("invalid ban duration or reason")


def apply_master(verb: str, kind: str, value: str, duration: str, reason: str, actor: str) -> dict:
    """Apply a ban on MasterHub itself without re-triggering the hub fan-out."""
    if kind == "node":
        arguments = [ASL_BAN_HELPER, verb, "--kind", "node", "--value", value, "--actor", actor or "asrplus"]
        if verb == "ban":
            arguments += ["--duration", duration, "--reason", reason]
    else:
        call = re.sub(r"-(?:L|R)$", "", value)
        arguments = [GLOBAL_BAN_HELPER, verb, call] + ([duration] if verb == "ban" else []) + ["--actor", actor or "asrplus"]
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=60, check=False,
                            env=os.environ | {"ASRPLUS_NO_FANOUT": "1"})
    payload: dict = {}
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    ok = result.returncode == 0 and payload.get("ok") is True
    return {"ok": ok, "error": "" if ok else str(payload.get("error") or result.stderr or "MasterHub ban failed")[:200]}


def fan_out(verb: str, kind: str, value: str, duration: str, reason: str, actor: str,
            requested_hub: str) -> dict:
    """#291: every ban/unban is queued to every remote hub, never just one."""
    hubs = {}
    for local_node, remote_hub in sorted(REMOTE_HUBS.items(), key=lambda item: item[1]):
        command = {
            "id": uuid.uuid4().hex, "utc": utc_now(), "hub": remote_hub, "localNode": local_node,
            "action": verb, "node": "", "permanent": False, "actor": actor, "kind": kind, "value": value,
            "duration": duration if verb == "ban" else "", "reason": reason if verb == "ban" else "",
        }
        try:
            queued = queue_remote(command, local_node, requested_hub)
            hubs[remote_hub] = {"ok": True, "queued": True, "id": queued["id"]}
        except Exception as error:
            audit_outcome({"utc": utc_now(), "result": "fanout-failed", "hub": remote_hub,
                           "action": verb, "value": value, "error": str(error)[:200]})
            hubs[remote_hub] = {"ok": False, "queued": False, "error": str(error)[:200]}
    return hubs


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
        if action == "fanout":
            # Called by the MasterHub Global Ban / AllStar ban helpers after a
            # local ban, so every remote hub gets it with no separate step.
            verb = str(request.get("verb", "")).lower()
            kind = str(request.get("kind", ""))
            value = str(request.get("value", "")).upper()
            duration = str(request.get("duration", ""))
            reason = re.sub(r"[\x00-\x1f\x7f]", " ", str(request.get("reason", ""))).strip()[:160]
            if verb not in {"ban", "unban"}:
                raise ValueError("invalid fan-out action")
            moderation_target(kind, value, verb, duration, reason)
            hubs = fan_out(verb, kind, value, duration, reason, actor, "fanout")
            print(json.dumps({"ok": all(item["ok"] for item in hubs.values()), "hubs": hubs}))
            return 0
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
            if (not isinstance(record, dict) or record.get("id") != identifier
                    or record.get("hub") != remote_hub
                    or str(record.get("localNode")) != HUBS[hub]):
                raise ValueError("result identity mismatch")
            if record.get("action") not in {"ban", "unban", "ban-list"}:
                raise ValueError("not a moderation result")
            if not isinstance(record.get("remoteResult"), dict):
                raise ValueError("invalid moderation result")
            print(json.dumps({"ok": True, "pending": False, "result": record["remoteResult"]}))
            return 0
        if action in {"connect", "disconnect"} and (not re.fullmatch(r"[0-9]{3,10}", node) or node == HUBS[hub]):
            raise ValueError("invalid remote node")
        kind = value = ban_duration = reason = ""
        if action in {"ban", "unban", "ban-list"}:
            if HUBS[hub] == "674982" and action == "ban-list":
                raise ValueError("Master moderation uses the ASR ban API")
            kind = str(request.get("kind", ""))
            value = str(request.get("value", "")).upper()
            ban_duration = str(request.get("duration", ""))
            reason = str(request.get("reason", ""))
            if action != "ban-list":
                moderation_target(kind, value, action, ban_duration, reason)
        if action in {"ban", "unban"}:
            # #291: a ban requested for any hub applies on every hub.
            reason = re.sub(r"[\x00-\x1f\x7f]", " ", reason).strip()[:160]
            master = apply_master(action, kind, value, ban_duration, reason, actor)
            hubs = {"masterhub": master} | fan_out(action, kind, value, ban_duration, reason, actor, hub)
            failed = sorted(name for name, item in hubs.items() if not item["ok"])
            message = (f"{action.title()} applied on MasterHub and queued to all {len(hubs) - 1} other hubs."
                       if not failed else f"{action.title()} NOT applied everywhere; failed: {', '.join(failed)}.")
            requested = REMOTE_HUBS.get(HUBS[hub], "masterhub")
            print(json.dumps({"ok": not failed, "queued": True, "hubs": hubs, "message": message,
                              "id": hubs.get(requested, {}).get("id", "")}))
            return 0 if not failed else 1
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
            command = queue_remote(command, HUBS[hub], hub)
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
