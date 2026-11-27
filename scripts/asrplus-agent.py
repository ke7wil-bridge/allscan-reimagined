#!/usr/bin/env python3
"""Publish remote hub connections and execute queued ASR Plus commands."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from asrplus_command_integrity import (
    IntegrityError, legacy_digest, load_agent_config, sign_acknowledgment,
    verify_command,
)


TALKER_HEARTBEAT_SECONDS = 5
LEDGER_RETENTION_SECONDS = 7 * 86400
LEDGER_MAX_RECORDS = 1000


def run_ssh(config: dict, command: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    control_path = config.get("controlPath", "/run/asrplus/ssh-%C")
    Path(control_path).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run([
        "/usr/bin/ssh", "-i", config["key"], "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={config['knownHosts']}",
        "-o", "ClearAllForwardings=yes", "-o", "RequestTTY=no", "-o", "PermitLocalCommand=no",
        "-o", "ControlMaster=auto", "-o", "ControlPersist=15", "-o", f"ControlPath={control_path}",
        "-o", "ConnectTimeout=6", config.get("server", "root@44.30.157.21"), command,
    ], input=stdin, capture_output=True, text=True, timeout=12, check=False)


def labels(path: str) -> dict[str, str]:
    result = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("|")
            if len(parts) >= 4 and parts[0].isdigit():
                result[parts[0]] = " ".join(value for value in parts[1:4] if value).strip()[:140]
    except OSError:
        pass
    return result


def atomic_json(path: Path, payload: dict, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EventPublisher:
    """Serialize transition pushes without losing changes during an active push."""

    def __init__(self, config: dict):
        self.config = config
        self.condition = threading.Condition()
        self.pending = False
        # A split watcher config intentionally has no hub/SSH identity. It is
        # local-only and must never acquire an outbound publisher by default.
        default_mode = "outbound" if config.get("hub") else "masterhub-pull"
        mode = config.get("eventPublishMode", default_mode)
        self.enabled = mode == "outbound"
        if mode not in {"outbound", "masterhub-pull"}:
            raise ValueError("invalid eventPublishMode")
        if self.enabled:
            threading.Thread(target=self.run, name="asrplus-event-publisher", daemon=True).start()

    def trigger(self) -> None:
        if not self.enabled:
            return
        with self.condition:
            self.pending = True
            self.condition.notify()

    def run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending)
                self.pending = False
            try:
                publish_transition(self.config)
            except Exception as error:
                print(f"event publication failed: {str(error)[:160]}", file=sys.stderr, flush=True)


def watch_talkers(config: dict) -> int:
    """Relay authoritative ASR snapshots; never interpret AMI talker events."""
    publisher = EventPublisher(config)
    previous = None
    last_write = 0.0
    heartbeat = int(config.get("talkerHeartbeatSeconds", TALKER_HEARTBEAT_SECONDS))
    if not 1 <= heartbeat <= 60:
        raise ValueError("talkerHeartbeatSeconds must be 1..60")
    status_path = Path(config.get("talkerStatus", "/run/asrplus/talkers.json"))
    event_path = Path(config.get("talkerEvent", "/run/asrplus/talker-event"))
    while True:
        snapshot = read_asr_talkers(config)
        signature = json.dumps([bool(snapshot), snapshot.get("current_talker", {}), snapshot.get("last_talkers", [])], sort_keys=True)
        changed = signature != previous
        now = time.time()
        if changed or now - last_write >= heartbeat:
            atomic_json(status_path, snapshot | {
                "healthy": bool(snapshot), "updated_epoch": int(now),
                "talker_authority": "asr" if snapshot else "unavailable",
            })
            last_write = now
        if changed:
            previous = signature
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_path.touch()
            publisher.trigger()
        time.sleep(0.5)


def read_asr_talkers(config: dict) -> dict:
    try:
        path = Path(config.get("authoritativeTalkerCache",
            f"/run/allscan-reimagined/astapi-{config['node']}.json"))
        cache = json.loads(path.read_text(encoding="utf-8"))
        if 0 <= time.time() - float(cache.get("updated", 0)) <= 10:
            local = cache.get("current", {}).get(str(config["node"]))
            if isinstance(local, dict):
                return {"talker_source_epoch": float(cache["updated"]), "current_talker": local.get("current_talker") or {}, "last_talkers": local.get("last_talkers") or []}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


def collect(config: dict) -> dict:
    result = subprocess.run(["/usr/sbin/asterisk", "-rx", f"rpt lstats {config['node']}"], capture_output=True, text=True, timeout=8, check=False)
    if result.returncode != 0:
        raise RuntimeError("rpt lstats failed")
    names = labels(config.get("astdb", "/var/lib/asterisk/astdb.txt"))
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 6:
            node, _peer, _reconnects, direction, connected, state = fields
        elif len(fields) == 5:
            node, _reconnects, direction, connected, state = fields
        else:
            continue
        if state != "ESTABLISHED":
            continue
        if node in {"1", str(config["node"])}:
            continue
        rows.append({"node": node, "label": names.get(node, f"AllStar node {node}"),
            "direction": direction, "mode": "T", "connected": connected,
            "received": "", "keyed": False})
    bridges = {}
    public_path = Path(config.get("publicStatus", "/var/www/html/allscan/public-status.json"))
    try:
        public = json.loads(public_path.read_text(encoding="utf-8"))
        if str(public.get("node", {}).get("number")) == str(config["node"]) and isinstance(public.get("bridges"), dict):
            bridges = public["bridges"]
    except (OSError, ValueError):
        pass
    # Consume only the local ASR collector snapshot; never infer a talker.
    talkers = read_asr_talkers(config)
    current_node = str(talkers.get("current_talker", {}).get("node", ""))
    for row in rows:
        if row["node"] == current_node:
            row["keyed"] = True
            row["received"] = str(talkers["current_talker"].get("last_keyed", ""))[:24]
    telemetry = {key: talkers.get(key) for key in ("talker_sequence", "watcher_instance",
        "asterisk_event_epoch_ms", "watcher_detected_epoch_ms", "state_published_epoch_ms")}
    return {"hub": config["hub"], "node": str(config["node"]), "generated_epoch": int(time.time()),
        "connections": rows, "bridges": bridges, "talker_authority": "asr", "talker_source_epoch": talkers.get("talker_source_epoch", 0), "current_talker": talkers.get("current_talker", {}),
        "last_talkers": talkers.get("last_talkers", [])} | telemetry


def stamped_status(config: dict) -> dict:
    started_ms = time.time_ns() // 1_000_000
    return collect(config) | {"agent_started_epoch_ms": started_ms,
        "agent_transmit_epoch_ms": time.time_ns() // 1_000_000}


def publish_transition(config: dict) -> None:
    pushed = run_ssh(config, "asrplus-status", json.dumps(stamped_status(config)))
    if pushed.returncode != 0:
        raise RuntimeError("transition status push failed")


def execute_action(config: dict, command: dict) -> dict:
    if command.get("hub") != config["hub"] or str(command.get("localNode")) != str(config["node"]):
        return {"ok": False, "exitCode": 2, "message": "command identity mismatch"}
    action = command.get("action")
    remote = str(command.get("node", ""))
    if action in {"connect", "disconnect"}:
        if not re.fullmatch(r"[0-9]{3,10}", remote) or remote == str(config["node"]):
            return {"ok": False, "exitCode": 2, "message": "command validation failed"}
        ilink = "13" if action == "connect" and command.get("permanent") is True else "3" if action == "connect" else "11"
        configured_helper = config.get("controlHelper", "")
        helper = ([str(item) for item in configured_helper]
                  if isinstance(configured_helper, list) else
                  ([str(configured_helper).strip()] if str(configured_helper).strip() else []))
        arguments = (helper + [action, str(config["node"]), remote,
                      "permanent" if command.get("permanent") is True else "temporary"]
                     if helper else
                     ["/usr/sbin/asterisk", "-rx",
                      f"rpt cmd {config['node']} ilink {ilink} {remote}"])
        result = subprocess.run(arguments, capture_output=True, text=True,
                                timeout=10, check=False)
        return {"ok": result.returncode == 0, "exitCode": result.returncode,
                "message": (result.stdout or result.stderr).strip()[:300]}
    if action not in {"ban", "unban", "ban-list"}:
        return {"ok": False, "exitCode": 2, "message": "command validation failed"}
    helper = str(config.get("banHelper", "/usr/local/sbin/allscan-reimagined-asl-ban"))
    arguments = [helper, "list" if action == "ban-list" else action]
    if action != "ban-list":
        kind = str(command.get("kind", ""))
        value = str(command.get("value", "")).upper()
        pattern = r"[0-9]{3,10}" if kind == "node" else r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?"
        if kind not in {"node", "allstar-call", "echolink-call"} or not re.fullmatch(pattern, value):
            return {"ok": False, "exitCode": 2, "message": "moderation target validation failed"}
        actor = re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(command.get("actor", "unknown")))[:80] or "unknown"
        duration = str(command.get("duration", ""))
        if action == "ban" and duration not in {"15m", "1h", "1w", "30d", "permanent"}:
            return {"ok": False, "exitCode": 2, "message": "moderation duration validation failed"}
        if kind != "node":
            call = re.sub(r"-(?:L|R)$", "", value)
            arguments = [str(config.get("globalBanHelper", "/usr/local/sbin/allscan-reimagined-global-ban")),
                         action, call] + ([duration] if action == "ban" else []) + ["--actor", actor]
        else:
            arguments.extend(["--kind", kind, "--value", value, "--actor", actor])
            if action == "ban":
                reason = re.sub(r"[\x00-\x1f\x7f]", " ", str(command.get("reason", ""))).strip()[:160]
                arguments.extend(["--duration", duration, "--reason", reason])
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=45, check=False)
    payload = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict):
                payload = candidate
                break
        except ValueError:
            continue
    message = str((payload or {}).get("error") or result.stderr or result.stdout).strip()[:300]
    outcome = {"ok": result.returncode == 0 and bool((payload or {}).get("ok")),
               "exitCode": result.returncode, "message": message}
    if action == "ban-list" and isinstance((payload or {}).get("bans"), list):
        bans = list(payload["bans"])
        try:
            listed = subprocess.run(
                [str(config.get("globalBanHelper", "/usr/local/sbin/allscan-reimagined-global-ban")), "list"],
                capture_output=True, text=True, timeout=45, check=False)
            global_payload = json.loads(listed.stdout.strip().splitlines()[-1]) if listed.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            global_payload = {}
        if listed_ok := isinstance(global_payload, dict) and global_payload.get("ok") is True:
            seen = {(str(row.get("kind")), str(row.get("value", "")).upper())
                    for row in bans if isinstance(row, dict)}
            for row in global_payload.get("bans") or []:
                rule = str((row or {}).get("rule", "")).upper()
                if re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}", rule) \
                        and ("allstar-call", rule) not in seen:
                    bans.append({"kind": "allstar-call", "value": rule,
                        "createdAt": int(row.get("createdAt") or 0),
                        "expiresAt": int(row.get("expiresAt") or 0),
                        "reason": "ASR Global Ban", "actor": "global"})
        outcome["bans"] = bans[:200]
        if not listed_ok:
            outcome["message"] = (outcome["message"] + " Global Ban list unavailable.").strip()[:300]
    return outcome


def load_ledger(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "commands": {}}
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("commands"), dict):
        raise IntegrityError("execution ledger is invalid")
    return payload


def prune_ledger(ledger: dict, now: int) -> None:
    commands = ledger["commands"]
    removable = sorted(
        (identifier for identifier, record in commands.items()
         if record.get("state") == "complete"
         and now - int(record.get("updatedEpoch") or 0) > LEDGER_RETENTION_SECONDS),
        key=lambda identifier: int(commands[identifier].get("updatedEpoch") or 0))
    for identifier in removable:
        commands.pop(identifier, None)
    completed = sorted(
        (identifier for identifier, record in commands.items() if record.get("state") == "complete"),
        key=lambda identifier: int(commands[identifier].get("updatedEpoch") or 0))
    while len(commands) > LEDGER_MAX_RECORDS and completed:
        commands.pop(completed.pop(0), None)


def execute_once(config: dict, command: dict) -> dict:
    integrity = load_agent_config(config)
    signed = command.get("protocolVersion") == 1 or "signature" in command
    if signed:
        digest = verify_command(command, integrity["key"], integrity["keyId"],
                                str(config["hub"]), str(config["node"]))
    elif integrity["mode"] == "optional":
        if command.get("hub") != config["hub"] or str(command.get("localNode")) != str(config["node"]):
            raise IntegrityError("legacy command hub identity mismatch")
        digest = legacy_digest(command)
    else:
        raise IntegrityError("unsigned legacy command is disabled")
    identifier = str(command.get("id", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise IntegrityError("command ID is invalid")
    ledger_path = Path(config.get("commandLedger", "/var/lib/asrplus-agent/executed-commands.json"))
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ledger_path.parent, 0o700)
    now = int(time.time())
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = load_ledger(ledger_path)
        record = ledger["commands"].get(identifier)
        if record:
            if record.get("digest") != digest:
                raise IntegrityError("command ID was replayed with different content")
            if record.get("state") == "complete" and isinstance(record.get("acknowledgment"), dict):
                return record["acknowledgment"]
            outcome = {"ok": False, "exitCode": 75,
                       "message": "prior command execution outcome is uncertain; duplicate execution refused"}
            acknowledgment = sign_acknowledgment(
                outcome, command, integrity["key"], integrity["keyId"], now=now) if signed else outcome
            record.update({"state": "complete", "updatedEpoch": now,
                           "acknowledgment": acknowledgment})
            atomic_json(ledger_path, ledger, 0o600)
            return acknowledgment
        ledger["commands"][identifier] = {
            "digest": digest, "state": "executing", "updatedEpoch": now,
        }
        prune_ledger(ledger, now)
        atomic_json(ledger_path, ledger, 0o600)

    outcome = execute_action(config, command)
    acknowledgment = sign_acknowledgment(
        outcome, command, integrity["key"], integrity["keyId"]) if signed else outcome
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = load_ledger(ledger_path)
        record = ledger["commands"].get(identifier)
        if not record or record.get("digest") != digest:
            raise IntegrityError("execution ledger changed during command execution")
        record.update({"state": "complete", "updatedEpoch": int(time.time()),
                       "acknowledgment": acknowledgment})
        atomic_json(ledger_path, ledger, 0o600)
    return acknowledgment


def sync_once(config: dict) -> int:
    status = stamped_status(config)
    if config.get("controlPolling", True) is False:
        pushed = run_ssh(config, "asrplus-status", json.dumps(status))
        if pushed.returncode != 0:
            raise SystemExit("status push failed")
        return 0
    synced = run_ssh(config, "asrplus-sync", json.dumps(status))
    if synced.returncode != 0:
        pushed = run_ssh(config, "asrplus-status", json.dumps(status))
        if pushed.returncode != 0:
            raise SystemExit("status push failed")
        synced = run_ssh(config, "asrplus-poll")
    if synced.returncode != 0:
        raise SystemExit("status sync failed")
    response = json.loads(synced.stdout or "{}")
    if response.get("ok") is not True:
        raise SystemExit("status sync was rejected")
    command = response.get("command")
    if isinstance(command, dict):
        outcome = execute_once(config, command)
        acknowledged = run_ssh(config, f"asrplus-ack {command['id']}", json.dumps(outcome))
        # Preserve live hub behavior: publish confirmed link transitions
        # promptly, without treating a queued command as confirmed state.
        if outcome.get("ok") and command.get("action") in {"connect", "disconnect"}:
            for attempt in range(12):
                snapshot = stamped_status(config)
                present = any(str(row.get("node")) == str(command.get("node"))
                              for row in snapshot.get("connections", []))
                if present == (command["action"] == "connect"):
                    pushed = run_ssh(config, "asrplus-status", json.dumps(snapshot))
                    if pushed.returncode != 0:
                        raise RuntimeError("post-command status push failed")
                    break
                if attempt < 11:
                    time.sleep(0.2)
        if acknowledged.returncode != 0:
            raise SystemExit("command acknowledgement failed")
        ack_response = json.loads(acknowledged.stdout or "{}")
        if ack_response.get("ok") is not True:
            raise SystemExit("command acknowledgement was rejected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--watch-talkers", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.watch_talkers:
        return watch_talkers(config)
    return sync_once(config)


if __name__ == "__main__":
    raise SystemExit(main())
