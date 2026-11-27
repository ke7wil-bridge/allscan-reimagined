#!/usr/bin/env python3
"""Forced-command SSH endpoint for remote ASR Plus agents."""

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
import time

from asrplus_command_integrity import (
    IntegrityError, legacy_digest, load_central_hub,
    verify_acknowledgment, verify_command,
)


HUB_NODES = {"phoenix": "641894", "ky2": "674980", "texas": "674984"}
QUEUE_ROOT = Path("/var/lib/asrplus/queue")
STATUS_ROOT = Path("/var/lib/asrplus/remote-status")
RESULT_ROOT = Path("/var/lib/asrplus/results")
ARCHIVE_ROOT = Path("/var/lib/asrplus/queue-archive")
STATE_PATH = Path("/var/lib/asl3-geo-monitor/admission_state.json")
STATUS_EVENT_PATH = Path("/var/lib/asrplus/status-event")
SECURITY_AUDIT_PATH = Path("/var/log/asrplus-command-integrity.jsonl")
INTEGRITY_CONFIG = Path(os.environ.get(
    "ASRPLUS_COMMAND_INTEGRITY_CONFIG", "/etc/asrplus/command-integrity.json"))
LEASE_SECONDS = 15
LEGACY_MAX_AGE_SECONDS = 300


def atomic_json(path: Path, payload: dict, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def read_input(limit: int = 4 * 1024 * 1024) -> dict:
    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("payload too large")
    payload = json.loads(raw or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


def security_audit(hub: str, event: str, allowed: bool, reason: str = "",
                   command_id: str = "") -> None:
    record = {"epoch": int(time.time()), "hub": hub, "event": event,
              "allowed": allowed, "reason": reason[:200],
              "commandId": command_id if re.fullmatch(r"[a-f0-9]{32}", command_id) else ""}
    SECURITY_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SECURITY_AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    os.chmod(SECURITY_AUDIT_PATH, 0o640)


def sanitize_status(hub: str, payload: dict) -> dict:
    if str(payload.get("hub")) != hub or str(payload.get("node")) != HUB_NODES[hub]:
        raise ValueError("status identity mismatch")
    generated = int(payload.get("generated_epoch") or 0)
    if generated <= 0 or abs(int(time.time()) - generated) > 120:
        raise ValueError("status timestamp invalid")
    rows = []
    for row in payload.get("connections", [])[:200]:
        if not isinstance(row, dict):
            continue
        node = str(row.get("node", ""))
        if not re.fullmatch(r"[A-Za-z0-9*#-]{1,16}", node):
            continue
        rows.append({
            "node": node, "label": str(row.get("label", ""))[:140],
            "direction": str(row.get("direction", ""))[:12], "mode": str(row.get("mode", "T"))[:12],
            "keyed": "yes" if row.get("keyed") is True else "no",
            "elapsed": str(row.get("connected", ""))[:24], "last_keyed": str(row.get("received", ""))[:24],
        })
    bridges = payload.get("bridges") if isinstance(payload.get("bridges"), dict) else {}
    def talker(row: object, current: bool) -> dict:
        if not isinstance(row, dict):
            return {}
        node = str(row.get("node", ""))[:16]
        if not re.fullmatch(r"[A-Za-z0-9*#-]{1,16}", node):
            return {}
        event = int(row.get("event_epoch") or 0)
        started = int(row.get("started_epoch") or row.get("keyed_started_epoch") or 0)
        if current and (started <= 0 or abs(generated - started) > 86400):
            return {}
        if not current and (event <= 0 or event > generated + 30 or generated - event > 86400):
            return {}
        network_node = str(row.get("network_node", ""))[:16]
        if network_node and not re.fullmatch(r"[A-Za-z0-9*#-]{1,16}", network_node):
            network_node = ""
        raw_duration = row.get("duration")
        if type(raw_duration) not in (int, float):
            raw_duration = row.get("duration_seconds")
        seconds = max(0, min(86400, int(raw_duration))) if type(raw_duration) in (int, float) and 0 <= raw_duration <= 86400 else 0
        return {
            "node": node, "network_node": network_node, "label": str(row.get("label", ""))[:140],
            "callsign": str(row.get("callsign", ""))[:80],
            "info": str(row.get("info", ""))[:140],
            "source": str(row.get("source", ""))[:32],
            "identity_provenance": str(row.get("identity_provenance", ""))[:64],
            "started_epoch": started,
            "description": str(row.get("description", ""))[:80],
            "location": str(row.get("location", ""))[:80],
            "event_epoch": event, "keyed_started_epoch": started,
            "duration_seconds": seconds,
            "last_keyed": str(row.get("last_keyed", ""))[:24],
            "originated_locally": row.get("originated_locally") is True,
        }
    current_talker = talker(payload.get("current_talker"), True)
    last_talkers = []
    for candidate in payload.get("last_talkers", [])[:25]:
        cleaned = talker(candidate, False)
        if cleaned:
            last_talkers.append(cleaned)
    telemetry = {
        "talker_sequence": max(0, int(payload.get("talker_sequence") or 0)),
        "watcher_instance": str(payload.get("watcher_instance") or "")[:64],
        "asterisk_event_epoch_ms": max(0, int(payload.get("asterisk_event_epoch_ms") or 0)),
        "watcher_detected_epoch_ms": max(0, int(payload.get("watcher_detected_epoch_ms") or 0)),
        "state_published_epoch_ms": max(0, int(payload.get("state_published_epoch_ms") or 0)),
        "agent_started_epoch_ms": max(0, int(payload.get("agent_started_epoch_ms") or 0)),
        "agent_transmit_epoch_ms": max(0, int(payload.get("agent_transmit_epoch_ms") or 0)),
        "kentucky_received_epoch_ms": time.time_ns() // 1_000_000,
    }
    return {
        "generated_epoch": generated,
        "talker_source_epoch": int(payload.get("talker_source_epoch") or 0),
        "talker_authority": "asr" if payload.get("talker_authority") == "asr" else "unknown",
        "connection_status": {"connected_node_count": len(rows), "direct_link_count": len(rows),
            "adjacent_link_count": 0, "current_talker": current_talker,
            "last_talkers": last_talkers, "nodes": rows},
        "bridges": dict(list(bridges.items())[:32]),
    } | telemetry


def lifecycle_token(status: dict) -> tuple:
    connection = status.get("connection_status") if isinstance(status.get("connection_status"), dict) else {}
    current = connection.get("current_talker") if isinstance(connection.get("current_talker"), dict) else {}
    history = connection.get("last_talkers") if isinstance(connection.get("last_talkers"), list) else []
    recent = history[0] if history and isinstance(history[0], dict) else {}
    return (status.get("watcher_instance", ""), int(status.get("talker_sequence") or 0),
        str(current.get("node", "")), int(current.get("keyed_started_epoch") or 0),
        str(recent.get("node", "")), int(recent.get("keyed_started_epoch") or 0),
        int(recent.get("event_epoch") or 0))


def store_status(hub: str, payload: dict) -> bool:
    destination = STATUS_ROOT / f"{hub}.json"
    cleaned = sanitize_status(hub, payload)
    previous = {}
    try:
        previous = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    old_generated = int(previous.get("generated_epoch") or 0)
    new_generated = int(cleaned.get("generated_epoch") or 0)
    if new_generated < old_generated:
        return False
    if new_generated == old_generated and int(cleaned.get("talker_sequence") or 0) < int(previous.get("talker_sequence") or 0):
        return False
    changed = lifecycle_token(previous) != lifecycle_token(cleaned)
    old_nodes = {str(row.get("node")) for row in previous.get("connection_status", {}).get("nodes", [])}
    new_nodes = {str(row.get("node")) for row in cleaned.get("connection_status", {}).get("nodes", [])}
    links_changed = old_nodes != new_nodes
    if not changed:
        for key in ("asterisk_event_epoch_ms", "watcher_detected_epoch_ms", "state_published_epoch_ms",
                "agent_started_epoch_ms", "agent_transmit_epoch_ms", "kentucky_received_epoch_ms"):
            cleaned[key] = previous.get(key, cleaned.get(key, 0))
    atomic_json(destination, cleaned)
    if links_changed:
        # Timestamp actual link transitions for end-to-end latency diagnosis.
        try:
            with open("/var/log/asrplus-link-timing.jsonl", "a", encoding="utf-8") as timing:
                timing.write(json.dumps({"hub": hub, "received_epoch_ms": time.time_ns() // 1000000,
                    "source_generated_epoch": new_generated,
                    "added": sorted(new_nodes - old_nodes),
                    "removed": sorted(old_nodes - new_nodes)}) + "\n")
        except OSError:
            pass
        # Publish a changed connection list immediately instead of waiting
        # for the periodic snapshot timer.
        subprocess.run(["/usr/bin/systemctl", "start", "--no-block",
                        "asrplus-status.service"], timeout=2, check=False)
    if changed:
        STATUS_EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_EVENT_PATH.touch()
    return changed


def is_signed_command(command: dict) -> bool:
    return command.get("protocolVersion") == 1 or "signature" in command


def validate_queued_command(hub: str, command: dict, integrity: dict, *,
                            now: int, allow_expired: bool = False) -> str:
    if is_signed_command(command):
        return verify_command(command, integrity["key"], integrity["keyId"], hub,
                              HUB_NODES[hub], now=now, allow_expired=allow_expired)
    if integrity["mode"] != "optional":
        raise IntegrityError("unsigned legacy command is disabled")
    if not re.fullmatch(r"[a-f0-9]{32}", str(command.get("id", ""))):
        raise IntegrityError("legacy command ID is invalid")
    if command.get("hub") != hub or str(command.get("localNode")) != HUB_NODES[hub]:
        raise IntegrityError("legacy command hub identity mismatch")
    return legacy_digest(command)


def archive_command(hub: str, source: Path, reason: str, now: int) -> None:
    safe_reason = re.sub(r"[^a-z0-9-]", "-", reason.lower())[:40].strip("-") or "rejected"
    destination = ARCHIVE_ROOT / hub / f"{source.stem}.{now}.{time.time_ns()}.{safe_reason}.json"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source.replace(destination)


def rejected_result(hub: str, command: dict, message: str, now: int) -> None:
    identifier = str(command.get("id", ""))
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        return
    record = command | {"acknowledgedEpoch": now, "remoteResult": {
        "ok": False, "exitCode": 124, "message": message[:300],
    }}
    atomic_json(RESULT_ROOT / hub / f"{identifier}.json", record, 0o600)


def read_queue_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IntegrityError("queued command must be an object")
    if path.stem != str(payload.get("id", "")):
        raise IntegrityError("queue filename does not match command ID")
    return payload


def poll(hub: str) -> dict:
    queue = QUEUE_ROOT / hub
    queue.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = QUEUE_ROOT / f".{hub}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = int(time.time())
        integrity = load_central_hub(INTEGRITY_CONFIG, hub)
        for source in sorted(queue.glob("*.inflight"), key=lambda item: item.stat().st_mtime):
            try:
                command = read_queue_file(source)
                validate_queued_command(hub, command, integrity, now=now, allow_expired=True)
                if is_signed_command(command) and int(command.get("expiresEpoch") or 0) < now:
                    security_audit(hub, "inflight-expired", False, command_id=str(command.get("id", "")))
                    rejected_result(hub, command, "command expired before acknowledgment", now)
                    archive_command(hub, source, "expired", now)
                    continue
                if not is_signed_command(command) and now - int(source.stat().st_mtime) > LEGACY_MAX_AGE_SECONDS:
                    security_audit(hub, "legacy-inflight-expired", False, command_id=str(command.get("id", "")))
                    rejected_result(hub, command, "legacy command lease expired", now)
                    archive_command(hub, source, "legacy-expired", now)
                    continue
                if now - int(source.stat().st_mtime) < LEASE_SECONDS:
                    return {"ok": True, "command": None, "inflight": True}
                os.utime(source, (now, now))
                security_audit(hub, "command-redelivered", True, command_id=str(command.get("id", "")))
                return {"ok": True, "command": command, "recovered": True}
            except Exception as error:
                security_audit(hub, "inflight-rejected", False, str(error), source.stem)
                archive_command(hub, source, "invalid-inflight", now)

        candidates = []
        for source in queue.glob("*.json"):
            try:
                command = read_queue_file(source)
                validate_queued_command(hub, command, integrity, now=now, allow_expired=True)
                if is_signed_command(command) and int(command.get("expiresEpoch") or 0) < now:
                    security_audit(hub, "queued-expired", False, command_id=str(command.get("id", "")))
                    rejected_result(hub, command, "command expired before delivery", now)
                    archive_command(hub, source, "expired", now)
                    continue
                if not is_signed_command(command) and now - int(source.stat().st_mtime) > LEGACY_MAX_AGE_SECONDS:
                    security_audit(hub, "legacy-queued-expired", False, command_id=str(command.get("id", "")))
                    rejected_result(hub, command, "legacy command expired before delivery", now)
                    archive_command(hub, source, "legacy-expired", now)
                    continue
                candidates.append((0 if command.get("action") == "disconnect" else 1,
                                   source.stat().st_mtime, source, command))
            except Exception as error:
                security_audit(hub, "queued-rejected", False, str(error), source.stem)
                archive_command(hub, source, "invalid-queued", now)
        if not candidates:
            return {"ok": True, "command": None}
        _, _, source, command = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
        destination = source.with_suffix(".inflight")
        source.rename(destination)
        os.utime(destination, (now, now))
        security_audit(hub, "command-delivered", True, command_id=str(command.get("id", "")))
        return {"ok": True, "command": command, "recovered": False}


def acknowledge(hub: str, command_id: str, result: dict) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", command_id):
        raise ValueError("invalid command id")
    queue = QUEUE_ROOT / hub
    lock_path = QUEUE_ROOT / f".{hub}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source = queue / f"{command_id}.inflight"
        existing_path = RESULT_ROOT / hub / f"{command_id}.json"
        duplicate = not source.is_file() and existing_path.is_file()
        if duplicate:
            existing_record = json.loads(existing_path.read_text(encoding="utf-8"))
            command = {key: value for key, value in existing_record.items()
                       if key not in {"acknowledgedEpoch", "ackDigest", "remoteResult"}}
        elif source.is_file():
            command = read_queue_file(source)
        else:
            raise ValueError("command is not in flight")
        integrity = load_central_hub(INTEGRITY_CONFIG, hub)
        command_hash = validate_queued_command(
            hub, command, integrity, now=int(time.time()), allow_expired=True)
        if is_signed_command(command):
            ack_hash = verify_acknowledgment(result, command, integrity["key"],
                                             integrity["keyId"], hub)
        elif integrity["mode"] == "optional":
            ack_hash = legacy_digest(result)
        else:
            raise IntegrityError("unsigned legacy acknowledgment is disabled")
        if duplicate:
            if existing_path.is_file() and existing_path.stat().st_size > 0:
                existing = existing_record
                if existing.get("ackDigest") == ack_hash and existing.get("commandDigest") in {None, command_hash}:
                    security_audit(hub, "acknowledgment-duplicate", True, command_id=command_id)
                    return {"ok": True, "duplicate": True}
            raise IntegrityError("conflicting duplicate acknowledgment")
        bans = result.get("bans")
        if command.get("action") == "ban-list" and isinstance(bans, list):
            safe_bans = []
            for entry in bans[:200]:
                if isinstance(entry, dict):
                    safe_bans.append({key: entry.get(key) for key in (
                        "kind", "value", "createdAt", "expiresAt", "reason", "actor")})
        else:
            safe_bans = None
        record = command | {"acknowledgedEpoch": int(time.time()), "ackDigest": ack_hash,
            "remoteResult": {
                "ok": result.get("ok") is True, "exitCode": int(result.get("exitCode") or 0),
                "message": str(result.get("message", ""))[:300],
            }}
        if safe_bans is not None:
            record["remoteResult"]["bans"] = safe_bans
        atomic_json(RESULT_ROOT / hub / f"{command_id}.json", record, 0o600)
        source.unlink()
        security_audit(hub, "acknowledgment-accepted", True, command_id=command_id)
        return {"ok": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hub", choices=sorted(HUB_NODES))
    args = parser.parse_args()
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
    try:
        if original == "":
            sys.stdout.buffer.write(STATE_PATH.read_bytes())
            return 0
        if original == "asrplus-status":
            store_status(args.hub, read_input())
            response = {"ok": True}
        elif original == "asrplus-sync":
            store_status(args.hub, read_input())
            response = poll(args.hub)
        elif original == "asrplus-poll":
            response = poll(args.hub)
        elif original.startswith("asrplus-ack "):
            response = acknowledge(args.hub, original.split(" ", 1)[1], read_input(16384))
        else:
            raise ValueError("command not allowed")
        print(json.dumps(response))
        return 0
    except Exception as error:
        try:
            security_audit(args.hub, "endpoint-rejected", False, str(error),
                           original.split(" ", 1)[1] if original.startswith("asrplus-ack ") else "")
        except OSError:
            pass
        print(json.dumps({"ok": False, "error": str(error)[:200]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
