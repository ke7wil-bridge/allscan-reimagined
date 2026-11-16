#!/usr/bin/env python3
"""Forced-command SSH endpoint for remote ASR Plus agents."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


HUB_NODES = {"phoenix": "641894", "georgia": "674980"}
QUEUE_ROOT = Path("/var/lib/asrplus/queue")
STATUS_ROOT = Path("/var/lib/asrplus/remote-status")
RESULT_ROOT = Path("/var/lib/asrplus/results")
STATE_PATH = Path("/var/lib/asl3-geo-monitor/admission_state.json")
STATUS_EVENT_PATH = Path("/var/lib/asrplus/status-event")


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


def read_input(limit: int = 4 * 1024 * 1024) -> dict:
    raw = sys.stdin.buffer.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("payload too large")
    payload = json.loads(raw or b"{}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    return payload


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
        started = int(row.get("keyed_started_epoch") or 0)
        if current and (started <= 0 or abs(generated - started) > 86400):
            return {}
        if not current and (event <= 0 or event > generated + 30 or generated - event > 86400):
            return {}
        return {
            "node": node, "label": str(row.get("label", ""))[:140],
            "callsign": str(row.get("callsign", ""))[:32],
            "description": str(row.get("description", ""))[:80],
            "location": str(row.get("location", ""))[:80],
            "event_epoch": event, "keyed_started_epoch": started,
            "duration_seconds": max(0, min(86400, int(row.get("duration_seconds") or 0))),
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
    if not changed:
        for key in ("asterisk_event_epoch_ms", "watcher_detected_epoch_ms", "state_published_epoch_ms",
                "agent_started_epoch_ms", "agent_transmit_epoch_ms", "kentucky_received_epoch_ms"):
            cleaned[key] = previous.get(key, cleaned.get(key, 0))
    atomic_json(destination, cleaned)
    if changed:
        STATUS_EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_EVENT_PATH.touch()
    return changed


def poll(hub: str) -> dict:
    queue = QUEUE_ROOT / hub
    queue.mkdir(parents=True, exist_ok=True)
    lock_path = QUEUE_ROOT / f".{hub}.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        candidates = sorted(queue.glob("*.json"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            return {"ok": True, "command": None}
        source = candidates[0]
        destination = source.with_suffix(".inflight")
        source.rename(destination)
        command = json.loads(destination.read_text(encoding="utf-8"))
        return {"ok": True, "command": command}


def acknowledge(hub: str, command_id: str, result: dict) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", command_id):
        raise ValueError("invalid command id")
    source = QUEUE_ROOT / hub / f"{command_id}.inflight"
    if not source.is_file():
        raise ValueError("command is not in flight")
    command = json.loads(source.read_text(encoding="utf-8"))
    record = command | {"acknowledgedEpoch": int(time.time()), "remoteResult": {
        "ok": result.get("ok") is True, "exitCode": int(result.get("exitCode") or 0),
        "message": str(result.get("message", ""))[:300],
    }}
    destination = RESULT_ROOT / hub / f"{command_id}.json"
    atomic_json(destination, record, 0o600)
    source.unlink()
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
        print(json.dumps({"ok": False, "error": str(error)[:200]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
