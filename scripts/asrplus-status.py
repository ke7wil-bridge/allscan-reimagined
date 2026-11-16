#!/usr/bin/env python3
"""Build the private ASR Plus multi-hub dashboard snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime
import grp
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.request import Request, urlopen


BRIDGE_ORDER = ("dmr", "ysf", "p25", "m17", "nxdn", "zello")
BRIDGE_CONNECTION_NODES = {
    "dmr": "1001",
    "ysf": "1002",
    "p25": "1003",
    "m17": "1004",
    "nxdn": "1005",
    "zello": "1006",
}
BRIDGE_LABELS = {
    "dmr": "DMR TGIF Bridge",
    "ysf": "YSF Bridge",
    "p25": "P25 Bridge",
    "m17": "M17 Bridge",
    "nxdn": "NXDN Bridge",
    "zello": "Zello Bridge",
}
BRIDGE_DESTINATIONS = {
    "dmr": "TGIF TG 67498",
    "ysf": "YSF Reflector 67498",
    "p25": "P25 TG 6749",
    "m17": "M17-NET",
    "nxdn": "NXDN TG 6749",
    "zello": "Netoholics Network",
}


def read_json_file(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def read_json_url(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        return {}
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "ASRPlus/1.0"})
        with urlopen(request, timeout=2.5) as response:
            payload = json.loads(response.read(4 * 1024 * 1024))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def epoch(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))
        except ValueError:
            return 0
    return 0


def text(value: object, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalize_connection_duration(value: object) -> str:
    raw = text(value, 32)
    if not raw:
        return ""
    parts = raw.split(":")
    if len(parts) not in {3, 4, 5} or not all(part.isdigit() for part in parts):
        return raw
    if len(parts) == 3:
        hours, minutes, seconds = map(int, parts)
        days = 0
    elif len(parts) == 4:
        hours, minutes, seconds = map(int, parts[:3])
        days = 0  # Installed app_rpt emits H:MM:SS:subseconds.
    else:
        days, hours, minutes, seconds = map(int, parts[:4])
        if hours >= 24:
            return raw
    if minutes >= 60 or seconds >= 60:
        return raw
    days += hours // 24
    hours %= 24
    clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {clock}" if days else clock


def read_astdb(path: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("|")
            if len(parts) >= 4 and parts[0].isdigit():
                labels[parts[0]] = text(" ".join(value for value in parts[1:4] if value), 140)
    except OSError:
        pass
    return labels


def talker_entry(row: dict, connection_labels: dict[str, str], astdb: dict[str, str], current: bool) -> dict:
    node = text(row.get("node") or row.get("id"), 16)
    label = text(connection_labels.get(node) or astdb.get(node) or row.get("label") or f"Node {node}", 140)
    pieces = label.split(" ", 1)
    callsign = text(row.get("callsign"), 32) or (pieces[0] if pieces and pieces[0].lower() != "node" else node)
    description = text(row.get("description"), 80) or (pieces[1] if len(pieces) > 1 else "")
    seconds = max(0, int(row.get("duration_seconds") or 0))
    duration = text(row.get("last_keyed"), 24)
    if not current and seconds:
        duration = f"{seconds // 60:02d}:{seconds % 60:02d}"
    event_epoch = epoch(row.get("event_epoch"))
    started_epoch = epoch(row.get("keyed_started_epoch"))
    if not current and not started_epoch and event_epoch and seconds:
        started_epoch = max(0, event_epoch - seconds)
    return {
        "node": node,
        "callsign": callsign,
        "description": description,
        "location": text(row.get("location"), 80),
        "source": "AllStar",
        "duration": duration or "—",
        "eventEpoch": event_epoch,
        "startedEpoch": started_epoch,
        "current": current,
        "originatedLocally": row.get("originated_locally") is True,
    }


def same_transmission(left: dict, right: dict) -> bool:
    if left.get("node") != right.get("node") or not left.get("node"):
        return False
    left_start, right_start = int(left.get("startedEpoch") or 0), int(right.get("startedEpoch") or 0)
    left_end, right_end = int(left.get("eventEpoch") or 0), int(right.get("eventEpoch") or 0)
    if left_start and right_start:
        if abs(left_start - right_start) > 2:
            return False
        if left.get("current") and right.get("current"):
            return True
        if not left_end or not right_end or abs(left_end - right_end) > 2:
            return False
        overlap = min(left_end, right_end) - max(left_start, right_start)
        shortest = min(max(0, left_end - left_start), max(0, right_end - right_start))
        return (shortest == 0 and left_start == right_start and left_end == right_end) or overlap >= max(1, shortest // 2)
    return False


def same_active_transmission(left: dict, right: dict) -> bool:
    left_start = int(left.get("startedEpoch") or left.get("eventEpoch") or 0)
    right_start = int(right.get("startedEpoch") or right.get("eventEpoch") or 0)
    return bool(left_start and right_start and abs(left_start - right_start) <= 3)


def aggregate_talkers(hubs: list[dict], now: int) -> list[dict]:
    hub_nodes = {str(hub.get("node", "")) for hub in hubs}
    verified, relays = [], []
    for hub in hubs:
        if hub.get("stale"):
            continue
        for source in hub.get("talkers", []):
            if not isinstance(source, dict):
                continue
            talker = dict(source)
            talker.update({"hubId": hub.get("id", ""), "hubName": hub.get("name", ""),
                "hubNode": hub.get("node", ""), "heardOn": hub.get("location") or hub.get("name", "")})
            if str(talker.get("node", "")) in hub_nodes and not talker.get("originatedLocally"):
                relays.append(talker)
            else:
                verified.append(talker)
    current_candidates = sorted((row for row in verified if row.get("current")),
        key=lambda row: int(row.get("startedEpoch") or row.get("eventEpoch") or 0), reverse=True)
    current = current_candidates[0] if current_candidates else None
    completed = [row for row in verified if not row.get("current")]
    relay_current = sorted((row for row in relays if row.get("current")),
        key=lambda row: int(row.get("startedEpoch") or row.get("eventEpoch") or 0), reverse=True)
    suppressed_relays = [row for row in relay_current
        if any(same_active_transmission(row, ended) for ended in completed)]
    unmatched_relays = [row for row in relay_current if row not in suppressed_relays]
    if current is None:
        if unmatched_relays:
            source = unmatched_relays[0]
            pending_until = (int(source.get("startedEpoch") or source.get("eventEpoch") or now) + 2) * 1000
            current = {"node": "", "callsign": "Unidentified Station", "description": "Origin unavailable",
                "location": "", "source": "AllStar", "duration": source.get("duration", "—"),
                "eventEpoch": source.get("eventEpoch", now), "startedEpoch": source.get("startedEpoch", 0),
                "current": True, "hubId": source.get("hubId", ""), "hubName": source.get("hubName", ""),
                "hubNode": source.get("hubNode", ""), "heardOn": source.get("heardOn", ""),
                "identityPendingUntilEpochMs": pending_until}
    for hub in hubs:
        hub["txState"] = "idle"
        hub["txLabel"] = "Idle"
        hub["currentTalker"] = ""
        if hub.get("stale"):
            continue
        active = next((row for row in hub.get("talkers", []) if row.get("current")), None)
        if not active:
            continue
        # Suppress only the specific lagging relay observation. Timestamp
        # proximity alone must never silence a different hub's active origin.
        if any(hub.get("id") == relay.get("hubId")
               and active.get("node") == relay.get("node")
               and same_active_transmission(active, relay)
               for relay in suppressed_relays):
            continue
        # A direct station's node identity is authoritative. Timestamp-only
        # matching is permissible for hub-to-hub relay observations, not for
        # two different stations that happen to key within three seconds.
        is_hub_relay = str(active.get("node", "")) in hub_nodes and not active.get("originatedLocally")
        matches = [candidate for candidate in current_candidates
            if same_active_transmission(active, candidate)
            and (is_hub_relay or active.get("node") == candidate.get("node"))]
        origin = next((candidate for candidate in matches
            if hub.get("id") == candidate.get("hubId") and active.get("node") == candidate.get("node")), None)
        matched = origin or (matches[0] if len(matches) == 1 else None)
        if matched:
            identity = " / ".join(value for value in (matched.get("callsign"), matched.get("node")) if value)
            if origin:
                hub["txState"], hub["txLabel"] = "origin", f"TX · {identity}"
            else:
                hub["txState"], hub["txLabel"] = "relay", f"RELAY · {identity}"
            hub["currentTalker"] = hub["txLabel"]
        else:
            pending_until = int(current.get("identityPendingUntilEpochMs") or 0) if current else 0
            hub["txState"] = "pending" if pending_until > now * 1000 else "unknown"
            hub["txLabel"] = "ACTIVE · Origin not yet verified"
            hub["identityPendingUntilEpochMs"] = pending_until
            hub["currentTalker"] = hub["txLabel"]
    recent = sorted(completed,
        key=lambda row: int(row.get("eventEpoch") or 0), reverse=True)
    deduplicated = []
    for candidate in recent:
        duplicate = next((existing for existing in deduplicated if same_transmission(candidate, existing)), None)
        if duplicate is None:
            deduplicated.append(candidate)
        elif not duplicate.get("location") and candidate.get("location"):
            duplicate.update(candidate)
    return ([current] if current else []) + deduplicated[:4]


def build_hub(hub: dict, state: dict, astdb: dict[str, str], now: int) -> dict:
    key = text(hub.get("stateKey"), 32)
    status = read_json_file(text(hub.get("statusFile"), 512)) if hub.get("statusFile") else read_json_url(text(hub.get("statusUrl"), 1024))
    connection_status = status.get("connection_status") if isinstance(status.get("connection_status"), dict) else {}
    live_path = text(hub.get("talkerStatusFile"), 512)
    live = read_json_file(live_path) if live_path else {}
    live_generated = epoch(live.get("updated_epoch"))
    live_fresh_seconds = max(2, min(30, int(live.get("fresh_for_seconds") or 15)))
    live_fresh = bool(live_path and live.get("healthy") is True and live_generated > 0
        and max(0, now - live_generated) <= live_fresh_seconds)
    live_nodes = live.get("node_states") if live_fresh and isinstance(live.get("node_states"), dict) else {}
    state_links = state.get(f"{key}_links") if isinstance(state.get(f"{key}_links"), list) else []
    source_rows = connection_status.get("nodes") if isinstance(connection_status.get("nodes"), list) else []
    if not source_rows:
        source_rows = [{"node": value, "label": astdb.get(str(value), f"AllStar node {value}"), "mode": "T"} for value in state_links]
    local_node = text(hub.get("node"), 16)
    connections = []
    for row in source_rows[:150]:
        if not isinstance(row, dict):
            continue
        node = text(row.get("node"), 16)
        if not node or node in {"1", local_node}:
            continue
        live_node = live_nodes.get(node) if isinstance(live_nodes.get(node), dict) else {}
        keyed = str(live_node.get("keyed", row.get("keyed", ""))).lower() == "yes"
        connections.append({
            "node": node,
            "label": text(row.get("label") or row.get("info") or f"AllStar node {node}", 140),
            "direction": text(row.get("direction"), 12),
            "mode": text(row.get("mode"), 12),
            "keyed": keyed,
            "connected": normalize_connection_duration(row.get("elapsed")),
            "received": text(row.get("last_keyed"), 24),
        })
    connection_labels = {connection["node"]: connection["label"] for connection in connections}
    talkers = []
    current_talker = live.get("current_talker") if live_fresh else connection_status.get("current_talker")
    if isinstance(current_talker, dict) and text(current_talker.get("node"), 16):
        talkers.append(talker_entry(current_talker, connection_labels, astdb, True))
    if live_fresh:
        history = live.get("history") if isinstance(live.get("history"), list) else []
    else:
        history = connection_status.get("last_talkers") if isinstance(connection_status.get("last_talkers"), list) else []
    for row in history:
        if not isinstance(row, dict) or not text(row.get("node"), 16):
            continue
        talkers.append(talker_entry(row, connection_labels, astdb, False))
        if len(talkers) >= 5:
            break
    bridges = []
    connected_nodes = {connection["node"] for connection in connections}
    source_bridges = status.get("bridges") if isinstance(status.get("bridges"), dict) else {}
    ordered_bridges = sorted(
        source_bridges.items(),
        key=lambda item: BRIDGE_ORDER.index(str(item[0]).lower()) if str(item[0]).lower() in BRIDGE_ORDER else len(BRIDGE_ORDER),
    )
    for bridge_id, bridge in ordered_bridges[:32]:
        if not isinstance(bridge, dict):
            continue
        bridge_id = text(bridge_id, 32).lower()
        if bridge_id not in BRIDGE_ORDER:
            continue
        bridge_status = text(bridge.get("status") or "unknown", 12).lower()
        if bridge_status not in {"online", "offline", "unknown"}:
            bridge_status = "unknown"
        if BRIDGE_CONNECTION_NODES[bridge_id] in connected_nodes:
            bridge_status = "online"
        clients = []
        for client in bridge.get("clients", []) if isinstance(bridge.get("clients"), list) else []:
            if isinstance(client, dict):
                clients.append({"callsign": text(client.get("callsign") or client.get("name") or client.get("id"), 32)})
        recent_users = []
        for recent in bridge.get("recent_users", []) if isinstance(bridge.get("recent_users"), list) else []:
            if isinstance(recent, dict):
                recent_users.append({"callsign": text(recent.get("callsign") or recent.get("name"), 32)})
        last_user = text(bridge.get("last_user"), 32)
        if last_user and last_user != "-" and not any(row["callsign"] == last_user for row in recent_users):
            recent_users.insert(0, {"callsign": last_user})
        destination = text(bridge.get("channel") or bridge.get("destination"), 80)
        if not destination or destination == "-":
            destination = BRIDGE_DESTINATIONS[bridge_id]
        bridges.append({
            "id": bridge_id,
            "label": BRIDGE_LABELS[bridge_id],
            "status": bridge_status,
            "destination": destination,
            "clientCount": max(0, int(bridge.get("client_count") or 0)),
            "currentUser": text(bridge.get("current_user"), 80),
            "bridgeState": text(bridge.get("bridge_state"), 40),
            "node": BRIDGE_CONNECTION_NODES[bridge_id],
            "active": bridge.get("active") is True,
            "clients": clients[:50],
            "recentUsers": recent_users[:30],
        })
    generated = epoch(status.get("generated_epoch")) or epoch(state.get("utc"))
    age = max(0, now - generated) if generated else -1
    talker_age = max(0, now - live_generated) if live_path and live_generated else age
    ready = state.get(f"{key}_ready")
    available = generated > 0 and ready is not False
    stale_after = max(10, min(600, int(hub.get("staleAfterSeconds") or 180)))
    talker_stale_after = max(5, min(30, int(hub.get("talkerStaleAfterSeconds") or 15)))
    if talker_age < 0 or talker_age > talker_stale_after:
        talkers = [talker for talker in talkers if not talker.get("current")]
    count = len(connections)
    current = connection_status.get("current_talker") if isinstance(connection_status.get("current_talker"), dict) else {}
    if talker_age < 0 or talker_age > talker_stale_after:
        current = {}
    return {
        "id": text(hub.get("id"), 32), "name": text(hub.get("name"), 80),
        "location": text(hub.get("location"), 80), "node": local_node,
        "available": available, "stale": not available or age < 0 or age > stale_after,
        "ageSeconds": age, "capacity": max(1, min(500, int(hub.get("capacity") or 75))),
        "connectionCount": count,
        "directLinks": max(0, int(connection_status.get("direct_link_count") or len(state_links))),
        "adjacentLinks": max(0, int(connection_status.get("adjacent_link_count") or 0)),
        "currentTalker": text(current.get("person") or current.get("callsign") or current.get("info"), 80),
        "txState": "unknown" if current else "idle", "txLabel": "TX · Unidentified Station" if current else "Idle",
        "statusGeneratedEpoch": generated,
        "talkerSequence": max(0, int(status.get("talker_sequence") or 0)),
        "watcherInstance": text(status.get("watcher_instance"), 64),
        "asteriskEventEpochMs": max(0, int(status.get("asterisk_event_epoch_ms") or 0)),
        "watcherDetectedEpochMs": max(0, int(status.get("watcher_detected_epoch_ms") or 0)),
        "statePublishedEpochMs": max(0, int(status.get("state_published_epoch_ms") or 0)),
        "agentStartedEpochMs": max(0, int(status.get("agent_started_epoch_ms") or 0)),
        "agentTransmitEpochMs": max(0, int(status.get("agent_transmit_epoch_ms") or 0)),
        "kentuckyReceivedEpochMs": max(0, int(status.get("kentucky_received_epoch_ms") or 0)),
        "connections": connections, "talkers": talkers, "bridges": bridges,
    }


def write_atomic(path: Path, payload: dict, group: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, grp.getgrnam(group).gr_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/etc/allscan-reimagined/asrplus.json"))
    parser.add_argument("--output", type=Path, default=Path("/run/allscan-reimagined/asrplus-status.json"))
    parser.add_argument("--group", default="www-data")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    state = read_json_file(text(config.get("stateFile"), 512))
    astdb = read_astdb(text(config.get("astdbFile"), 512))
    now = int(time.time())
    hubs = [build_hub(hub, state, astdb, now) for hub in config.get("hubs", []) if isinstance(hub, dict)]
    talkers = aggregate_talkers(hubs, now)
    current = next((row for row in talkers if row.get("current")), None)
    selected = current or (talkers[0] if talkers else None)
    origin = next((hub for hub in hubs if selected and hub.get("id") == selected.get("hubId")), {})
    telemetry = {"aggregatedEpochMs": time.time_ns() // 1_000_000}
    for key in ("asteriskEventEpochMs", "watcherDetectedEpochMs", "statePublishedEpochMs",
            "agentStartedEpochMs", "agentTransmitEpochMs", "kentuckyReceivedEpochMs", "talkerSequence"):
        telemetry[key] = origin.get(key, 0)
    write_atomic(args.output, {"ok": True, "generatedEpoch": now, "hubs": hubs,
        "talkers": talkers, "telemetry": telemetry}, args.group)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
