#!/usr/bin/env python3
"""Build the private ASR Plus multi-hub dashboard snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime
import grp
import json
import os
import re
import subprocess
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


def read_astdb_details(path: str) -> dict[str, dict[str, str]]:
    details: dict[str, dict[str, str]] = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("|")
            if len(parts) >= 4 and parts[0].isdigit():
                details[parts[0]] = {"description": text(parts[2], 80), "location": text(parts[3], 80)}
    except OSError:
        pass
    return details


def talker_entry(row: dict, connection_labels: dict[str, str], astdb: dict[str, str],
                 astdb_details: dict[str, dict[str, str]], current: bool) -> dict:
    node = text(row.get("node") or row.get("id"), 16)
    label = text(connection_labels.get(node) or astdb.get(node) or row.get("label") or f"Node {node}", 140)
    pieces = label.split(" ", 1)
    # Live IAX clients have a generic connection label; their authenticated
    # station identity is carried in info/callsign, not in that label.
    # Never put the generic IaxRpt text in the prominent callsign field.
    source = text(row.get("source"), 32)
    digital = source.upper() in {"DMR", "NXDN", "YSF", "P25", "M17", "ZELLO"}
    # ASR's validated bridge identity is carried as `info`, while the
    # node database names the transport (DMR, NXDN, etc.). Prefer the
    # validated identity; never substitute the transport as a callsign.
    candidate = text(row.get("info") or row.get("callsign"), 32).upper() if digital else text(row.get("callsign") or row.get("info"), 32).upper()
    if source.upper() == "M17":
        candidate = re.sub(r"-[0-9]{1,2}$", "", candidate)
    if not re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[A-Z0-9]+)?", candidate):
        candidate = ""
    node_call = node.upper().removesuffix("-IAX")
    iax = ("iaxrpt" in label.lower() or node.upper().endswith("-IAX")
           or source.lower() in {"iax", "iaxrpt"})
    if iax and not candidate and re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[A-Z0-9]+)?", node_call):
        candidate = node_call
    callsign = candidate or ("" if iax or digital else (pieces[0] if pieces and pieces[0].lower() != "node" else node))
    details = astdb_details.get(node, {})
    description = text(row.get("description"), 80) or text(details.get("description"), 80) or ("IaxRpt / Web Transceiver client" if iax else (pieces[1] if len(pieces) > 1 else ""))
    location = text(row.get("location"), 80) or text(details.get("location"), 80)
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
        "location": location,
        "source": source.upper() if digital else ("IAX" if iax else "AllStar"),
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
    # A completed earlier transmission must never suppress a newly keyed
    # transmission merely because their start times are 1-3 seconds apart.
    suppressed_relays = [row for row in relay_current
        if any(str(row.get("node")) == str(ended.get("node"))
               and same_active_transmission(row, ended)
               and int(ended.get("eventEpoch") or 0) >= int(row.get("startedEpoch") or 0)
               for ended in completed)]
    unmatched_relays = [row for row in relay_current if row not in suppressed_relays]
    # A relay without a currently matched origin is still a known relay.
    # Do not manufacture an "Unidentified Station" global talker while source
    # hub event timestamps are briefly out of sync.
    for hub in hubs:
        hub["txState"] = "idle"
        hub["txLabel"] = "Idle"
        hub["currentTalker"] = ""
        hub["relayOrigin"] = None
        hub["relayUpstream"] = None
        if hub.get("stale"):
            continue
        active = next((row for row in hub.get("talkers", []) if row.get("current")), None)
        if not active:
            continue
        if any(same_active_transmission(active, relay)
               and str(active.get("node")) == str(relay.get("node"))
               for relay in suppressed_relays):
            continue
        # Neighboring hub key events can lag the source by several seconds.
        # A live keyed hub-link is stronger evidence than a 3-second timestamp
        # coincidence; only inherit identity when exactly one origin is active.
        matches = [candidate for candidate in current_candidates
                   if same_active_transmission(active, candidate)
                   and (hub.get("id") == candidate.get("hubId")
                        or str(active.get("node", "")) in hub_nodes)]
        if not matches and str(active.get("node", "")) in hub_nodes and len(current_candidates) == 1:
            candidate = current_candidates[0]
            if abs(int(active.get("startedEpoch") or 0) - int(candidate.get("startedEpoch") or 0)) <= 12:
                matches = [candidate]
        # Relay watchers report the neighboring hub's node, not the actual
        # remote caller. Use the verified originating talker while it is live.
        if not matches and current and str(active.get("node", "")) in hub_nodes:
            if same_active_transmission(active, current):
                matches = [current]
        # Live inter-hub relay streams may begin well after the originating
        # transmission (e.g. 50 seconds). Match the active upstream node to
        # its currently verified originating hub, not its start timestamp.
        # Never infer origin when multiple source stations are simultaneously
        # active on that upstream hub.
        if not matches and str(active.get("node", "")) in hub_nodes:
            upstream_sources = [candidate for candidate in current_candidates
                if str(candidate.get("hubNode", "")) == str(active.get("node", ""))]
            if len(upstream_sources) == 1:
                matches = upstream_sources
        origin = next((candidate for candidate in matches
            if hub.get("id") == candidate.get("hubId") and active.get("node") == candidate.get("node")), None)
        matched = origin or (matches[0] if len(matches) == 1 else None)
        if matched:
            identity = " / ".join(value for value in (matched.get("callsign"), matched.get("node")) if value)
            if hub.get("id") == matched.get("hubId") and str(active.get("node")) == str(matched.get("node")):
                hub["txState"], hub["txLabel"] = "origin", f"TX · {identity}"
            else:
                # Relay pill identifies the *upstream hub* consistently;
                # station identity belongs in the verified current talker card.
                upstream = next((item for item in hubs if str(item.get("node", "")) == str(active.get("node", ""))), None)
                source_name = upstream.get("name", "Hub") if upstream else matched.get("hubName", "Hub")
                source_node = str(active.get("node", "")) if upstream else str(matched.get("hubNode", ""))
                # Public pill describes the transmission origin, not the
                # immediate transport hop. Preserve hop separately in metadata.
                origin_hub_name = matched.get("hubName") or "Hub"
                origin_hub_node = str(matched.get("hubNode") or "")
                hub["txState"], hub["txLabel"] = "relay", f"RELAY · {origin_hub_name} ({origin_hub_node})"
            if hub["txState"] == "relay":
                hub["relayOrigin"] = {"hubId": matched.get("hubId"), "hubNode": matched.get("hubNode"),
                                      "station": matched.get("node"), "callsign": matched.get("callsign")}
                hub["relayUpstream"] = {"hubId": upstream.get("id") if upstream else None,
                                        "hubNode": source_node, "hubName": source_name}
            hub["currentTalker"] = hub["txLabel"]
        else:
            relay_node = str(active.get("node", ""))
            relay_hub = next((item for item in hubs if str(item.get("node", "")) == relay_node), None)
            if relay_hub and not active.get("originatedLocally"):
                hub["txState"] = "relay"
                # Without verified origin, show a neutral relay indicator
                # rather than misrepresenting the immediate hop as source.
                hub["txLabel"] = "RELAY · Origin pending"
                hub["relayUpstream"] = {"hubId": relay_hub.get("id"), "hubNode": relay_node,
                                        "hubName": relay_hub.get("name")}
                hub["currentTalker"] = hub["txLabel"]
            else:
                hub["txState"] = "unknown"
                hub["txLabel"] = "TX · Unidentified Station"
                hub["currentTalker"] = hub["txLabel"]
    # Use a single coherent event for the current card, hub pill and row.
    # A hub may have an event ahead of its neighbors by 1-3 seconds.
    if current:
        origin_hub = next((hub for hub in hubs if hub.get("id") == current.get("hubId")), None)
        if not origin_hub or origin_hub.get("txState") != "origin":
            current = None
    for hub in hubs:
        if hub.get("txState") == "idle":
            for connection in hub.get("connections", []):
                connection["keyed"] = False
        elif hub.get("txState") == "origin" and current:
            for connection in hub.get("connections", []):
                connection["keyed"] = str(connection.get("node")) == str(current.get("node"))
        elif hub.get("txState") == "relay":
            active = next((row for row in hub.get("talkers", []) if row.get("current")), None)
            for connection in hub.get("connections", []):
                connection["keyed"] = bool(active and str(connection.get("node")) == str(active.get("node")))
    recent = sorted(completed,
        key=lambda row: int(row.get("eventEpoch") or 0), reverse=True)
    deduplicated = []
    for candidate in recent:
        if current and same_active_transmission(candidate, current) and int(candidate.get("eventEpoch") or 0) <= int(current.get("startedEpoch") or 0):
            continue
        duplicate = next((existing for existing in deduplicated if same_transmission(candidate, existing)), None)
        if duplicate is None:
            deduplicated.append(candidate)
        elif not duplicate.get("location") and candidate.get("location"):
            duplicate.update(candidate)
    return ([current] if current else []) + deduplicated[:5]


def build_hub(hub: dict, state: dict, astdb: dict[str, str],
              astdb_details: dict[str, dict[str, str]], now: int,
              display_policy: dict | None = None) -> dict:
    display_policy = display_policy or {}
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
    if hub.get("id") == "masterhub":
        # ASR public-status can lag behind local Asterisk link transitions.
        # Read the authoritative live link table for control confirmation.
        try:
            probe = subprocess.run(
                ["/usr/sbin/asterisk", "-rx", f"rpt lstats {hub['node']}"],
                capture_output=True, text=True, timeout=1.5, check=False)
            if probe.returncode == 0 and "CONNECT STATE" in probe.stdout:
                live_rows = []
                for line in probe.stdout.splitlines()[2:]:
                    fields = line.split()
                    # Local pseudo-nodes such as BCSTFY have no PEER address,
                    # leaving five columns instead of the usual six.
                    if len(fields) >= 5 and fields[-1] == "ESTABLISHED":
                        node = fields[0]
                        if node != str(hub["node"]):
                            prior = next((row for row in source_rows if str(row.get("node")) == node), {})
                            live_rows.append({"node": node, "label": prior.get("label") or astdb.get(node, f"AllStar node {node}"),
                                              "direction": fields[-3], "mode": prior.get("mode", "T"),
                                              "elapsed": fields[-2], "last_keyed": prior.get("last_keyed", "")})
                source_rows = live_rows
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not source_rows:
        source_rows = [{"node": value, "label": astdb.get(str(value), f"AllStar node {value}"), "mode": "T"} for value in state_links]
    # Identify live IAX sessions directly, rather than relying on the
    # eventually consistent public-status label (which briefly goes generic).
    active_iax = set()
    if hub.get("id") == "masterhub":
        try:
            probe = subprocess.run(["/usr/sbin/asterisk", "-rx", "core show channels concise"],
                                   capture_output=True, text=True, timeout=1.5, check=False)
            if probe.returncode == 0:
                import re
                for line in probe.stdout.splitlines():
                    fields = line.split("!")
                    if len(fields) > 6 and fields[0].startswith("IAX2/") and fields[1] == "iax-kn4ewt" and fields[4] == "Up" and fields[5] == "Rpt":
                        match = re.fullmatch(r"674982,X,([A-Za-z0-9_-]{2,32})", fields[6])
                        if match:
                            active_iax.add(match.group(1).upper())
        except (OSError, subprocess.TimeoutExpired):
            pass
    local_node = text(hub.get("node"), 16)
    connections = []
    for row in source_rows[:150]:
        if not isinstance(row, dict):
            continue
        node = text(row.get("node"), 16)
        if not node or node in {"1", local_node}:
            continue
        live_node = live_nodes.get(node) if isinstance(live_nodes.get(node), dict) else {}
        # IAX display suffix is not part of the Asterisk talker identity.
        # Match TX against the underlying callsign before formatting the row.
        # Do not reuse old keyed flags from the slower connection snapshot.
        # Only a fresh event watcher may mark a connection as transmitting.
        keyed = live_fresh and str(live_node.get("keyed", "")).lower() == "yes"
        iax_client = node.upper() in active_iax or text(row.get("label"), 140).strip().lower() == "iaxrpt / web transceiver client"
        connections.append({
            "node": node,
            "label": "IaxRpt / Web Transceiver client" if iax_client else text(
                (astdb.get(node) if node in astdb and
                 (node in {"1001", "1002", "1003", "1004", "1005", "1006", "BCSTFY"}
                  or text(row.get("label"), 140).strip().lower().startswith(("allstar node ", "node "))
                  or not text(row.get("label"), 140).strip()
                  or node in {"641894", "674980"})
                 else row.get("label")) or row.get("info") or astdb.get(node) or f"AllStar node {node}", 140),
            "direction": text(row.get("direction"), 12),
            "mode": ("RX" if display_policy.get("modeOverrides", {}).get(node) == "R" else
                     ("IAX" if iax_client else text(row.get("mode"), 12))),
            "keyed": keyed,
            "connected": normalize_connection_duration(row.get("elapsed")),
            "received": text(row.get("last_keyed"), 24),
        })
    connection_labels = {connection["node"]: connection["label"] for connection in connections}
    talkers = []
    current_talker = (live.get("current_talker") if live_fresh else {}) if live_path else connection_status.get("current_talker")
    if isinstance(current_talker, dict) and text(current_talker.get("node"), 16):
        active_node = text(current_talker.get("node"), 16)
        # The event feed is authoritative for a live inbound hub relay.
        # Some Asterisk node-state snapshots omit keyed=yes for these links;
        # reflect the actual active event on the matching connection row.
        for connection in connections:
            if connection["node"] == active_node or (connection["mode"] == "IAX" and connection["node"] == active_node + "-IAX"):
                connection["keyed"] = True
                if current_talker.get("last_keyed"):
                    connection["received"] = text(current_talker.get("last_keyed"), 24)
                started = epoch(current_talker.get("keyed_started_epoch"))
                if started and 0 <= now - started < 86400:
                    connection["receivedStartedEpoch"] = started
        # Current digital identity must be verified just like completed ASR talker history.
        # The fast watcher can supply only the transport label ("DMR"/"YSF").
        if active_node in BRIDGE_CONNECTION_NODES.values() and hub.get("id") == "masterhub":
            verified = ""
            mode = next((key for key, value in BRIDGE_CONNECTION_NODES.items() if value == active_node), "")
            bridge_live = read_json_file("/run/allscan-reimagined-standard-bridge-status/bridge-live.json")
            live_age = now - int(bridge_live.get("updated_epoch") or 0)
            if 0 <= live_age <= 10:
                entries = bridge_live.get("bridges", {})
                for bridge_id, bridge in entries.items() if isinstance(entries, dict) else ():
                    if not isinstance(bridge, dict) or str(bridge.get("node")) != active_node:
                        continue
                    if bridge.get("active") is not True or bridge.get("role") != "source":
                        continue
                    candidate = text(bridge.get("current_user"), 32).upper()
                    if re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[0-9]{1,2})?", candidate):
                        verified = candidate
                        break
            if mode == "m17":
                verified = re.sub(r"-[0-9]{1,2}$", "", verified)
            if verified:
                current_talker = dict(current_talker, info=verified, callsign=verified,
                                      source=mode.upper(), identity_provenance="bridge_runtime")
            else:
                # No verified station yet: never label the transport as the operator.
                current_talker = dict(current_talker, info="", callsign="", source=mode.upper())
        talkers.append(talker_entry(current_talker, connection_labels, astdb, astdb_details, True))
    if live_path:
        history = live.get("history") if live_fresh and isinstance(live.get("history"), list) else []
    else:
        history = connection_status.get("last_talkers") if isinstance(connection_status.get("last_talkers"), list) else []
    # Remote snapshots omit last_keyed for idle connections. The live
    # watcher history records when TX ended, so compute "Received" as time
    # since that event, matching the Master Hub's idle-column semantics.
    last_received = {}
    for row in history:
        if not isinstance(row, dict):
            continue
        node = text(row.get("node"), 16)
        ended = epoch(row.get("event_epoch"))
        if node and ended and 0 <= now - ended < 86400:
            last_received[node] = max(last_received.get(node, 0), ended)
    if (live_fresh if live_path else True):
        for connection in connections:
            if not connection["keyed"] and connection["node"] in last_received:
                seconds = now - last_received[connection["node"]]
                connection["received"] = f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
                connection["lastReceivedEpoch"] = last_received[connection["node"]]
    # The fast link watcher records transport node/timing only. Correlate
    # each digital event with the ASR validated bridge-runtime identity feed.
    # Do not promote the bridge label (DMR/NXDN/etc.) into a callsign.
    bridge_identity = read_json_file("/run/allscan-reimagined/talkers-674982.json") if hub.get("id") == "masterhub" else {}
    evidence = bridge_identity.get("last_talkers", []) if isinstance(bridge_identity.get("last_talkers"), list) else []
    digital_nodes = set(BRIDGE_CONNECTION_NODES.values())
    for row in history:
        if not isinstance(row, dict) or not text(row.get("node"), 16):
            continue
        if text(row.get("node"), 16) in digital_nodes:
            end = epoch(row.get("event_epoch"))
            candidates = [e for e in evidence if isinstance(e, dict)
                          and text(e.get("node"), 16) == text(row.get("node"), 16)
                          and abs(epoch(e.get("event_epoch")) - end) <= 3
                          and text(e.get("identity_provenance"), 32) == "bridge_runtime"
                          and text(e.get("info"), 32)]
            if not candidates:
                continue  # Unverified transport event is not a station.
            row = dict(row)
            row.update({key: candidates[0][key] for key in ("info", "source", "description", "location") if key in candidates[0]})
        talkers.append(talker_entry(row, connection_labels, astdb, astdb_details, False))
        if len(talkers) >= 6:
            break
    bridges = []
    connected_nodes = {connection["node"] for connection in connections}
    source_bridges = status.get("bridges") if isinstance(status.get("bridges"), dict) else {}
    ordered_bridges = sorted(
        source_bridges.items(),
        key=lambda item: BRIDGE_ORDER.index(str(item[0]).lower()) if str(item[0]).lower() in BRIDGE_ORDER else len(BRIDGE_ORDER),
    )
    # Standard ASR reads the protocol-aware managed roster, while the
    # public-status snapshot may omit it. Use the same current-session feed
    # on the local master only; never promote recent talkers to clients.
    managed_clients = {}
    if hub.get("id") == "masterhub":
        managed_path = "/var/www/html/asr/asr-connected-clients.json"
        try:
            import os
            if 0 <= now - int(os.path.getmtime(managed_path)) <= 90:
                managed = read_json_file(managed_path)
                meta = managed.get("_asr_meta", {})
                for mode in BRIDGE_ORDER:
                    rows = managed.get(mode)
                    mode_meta = meta.get(mode, {}) if isinstance(meta, dict) else {}
                    if (isinstance(rows, list) and isinstance(mode_meta, dict)
                            and mode_meta.get("kind") == "current"):
                        managed_clients[mode] = [row for row in rows
                            if isinstance(row, dict) and row.get("connected") is not False
                            and text(row.get("callsign") or row.get("name"), 32)]
        except (OSError, ValueError, TypeError):
            pass
    # Use the same protocol-aware source/relay classification as standard ASR.
    bridge_runtime = read_json_file("/run/allscan-reimagined-standard-bridge-status/bridge-live.json") if hub.get("id") == "masterhub" else {}
    bridge_runtime_fresh = 0 <= now - int(bridge_runtime.get("updated_epoch") or 0) <= 10
    runtime_bridges = bridge_runtime.get("bridges", {}) if bridge_runtime_fresh else {}
    # Public bridge IDs are not always runtime IDs (YSF is ysf_net).
    runtime_bridge_aliases = {"ysf": "ysf_net"}

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
        if bridge_id in managed_clients:
            clients = [{"callsign": text(row.get("callsign") or row.get("name"), 32)}
                       for row in managed_clients[bridge_id]]
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
        runtime_key = runtime_bridge_aliases.get(bridge_id, bridge_id)
        runtime = runtime_bridges.get(runtime_key, {}) if isinstance(runtime_bridges, dict) else {}
        if isinstance(runtime, dict) and runtime:
            role = str(runtime.get("role") or "idle")
            active = runtime.get("active") is True
            current_user = str(runtime.get("current_user") or "") if role == "source" else ""
            if bridge_id == "m17":
                current_user = re.sub(r"-[0-9]{1,2}$", "", current_user)
            bridge_state = "TX" if active and role == "source" else "RELAY" if active and role == "relay" else "Idle"
        else:
            role = "idle"
            active = False
            current_user = ""
            bridge_state = "Idle"
        bridges.append({
            "id": bridge_id,
            "label": BRIDGE_LABELS[bridge_id],
            "status": bridge_status,
            "destination": destination,
            "clientCount": len(clients) if bridge_id in managed_clients else max(0, int(bridge.get("client_count") or 0)),
            "currentUser": text(current_user, 80),
            "bridgeState": bridge_state,
            "node": BRIDGE_CONNECTION_NODES[bridge_id],
            "active": active and role == "source",
            "relay": active and role == "relay",
            "clients": clients[:50],
            "recentUsers": recent_users[:30],
        })
    generated = epoch(status.get("generated_epoch")) or epoch(state.get("utc"))
    age = max(0, now - generated) if generated else -1
    talker_age = max(0, now - live_generated) if live_path and live_generated else age
    # Hub telemetry availability is independent of geo-admission readiness.
    # Admission requires a live inter-hub link, but an isolated hub can be online.
    stale_after = max(10, min(600, int(hub.get("staleAfterSeconds") or 180)))
    available = generated > 0 and 0 <= age <= stale_after
    talker_stale_after = max(5, min(30, int(hub.get("talkerStaleAfterSeconds") or 15)))
    if talker_age < 0 or talker_age > talker_stale_after:
        talkers = [talker for talker in talkers if not talker.get("current")]
    count = len(connections)
    if live_path:
        current = next((row for row in talkers if row.get("current")), {})
    else:
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
        "currentTalker": text(current.get("person") or current.get("callsign") or current.get("info") or current.get("description"), 80),
        "txState": ("tx" if current.get("originatedLocally") else "relay") if current else "idle",
        "txLabel": (("TX · " if current.get("originatedLocally") else "RELAY · ") + text(current.get("callsign") or current.get("description") or current.get("node"), 80)) if current else "Idle",
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
    astdb_path = text(config.get("astdbFile"), 512)
    # ASR's node database and display policy are authoritative for ASRPlus.
    astdb = read_astdb("/var/www/html/asr/astdb.txt")
    astdb.update(read_astdb(astdb_path))
    display_policy = read_json_file("/etc/allscan/asr-connection-display.json")
    astdb.update(display_policy.get("labelOverrides", {}))
    # ASR maintains programmed bridge descriptions separately from Asterisk's
    # astdb. Respect these for 1001-1006; refreshable node labels stay in astdb.
    programmed_labels = read_astdb("/etc/allscan/asdb.txt")
    for bridge_node in ("1001", "1002", "1003", "1004", "1005", "1006"):
        if programmed_labels.get(bridge_node):
            astdb[bridge_node] = programmed_labels[bridge_node]
    # BCSTFY is an alphanumeric pseudo-node, excluded by numeric astdb parsing.
    # Pseudo-node descriptions come from the shared ASR policy above.
    astdb_details = read_astdb_details(astdb_path)
    now = int(time.time())
    hubs = [build_hub(hub, state, astdb, astdb_details, now, display_policy) for hub in config.get("hubs", []) if isinstance(hub, dict)]
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
