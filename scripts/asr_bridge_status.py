#!/usr/bin/env python3
"""Evidence-based activity and connectivity for configured Standard Bridges."""

from __future__ import annotations

import calendar
import configparser
import json
import os
import pwd
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from typing import Any


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
ASTAPI_CACHE_DIR = Path("/run/allscan-reimagined")
MMDVM_LOG_DIR = Path("/var/log/mmdvm")
STANDARD_RUN_DIR = Path("/run/allscan-reimagined-standard-bridge-status")
STANDARD_STATUS_PATH = STANDARD_RUN_DIR / "bridge-live.json"
MANAGED_CLIENT_ROSTER_PATH = Path("/var/www/html/asr/asr-connected-clients.json")
M17_BRIDGE_INI_PATH = Path("/etc/m17/usrp2m17.ini")
BRIDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
NODE_RE = re.compile(r"^[0-9]{3,10}$")
MMDVM_LOG_NAME_RE = re.compile(
    r"^MMDVM_Bridge[A-Za-z0-9_-]*(?:-\d{4}-\d{2}-\d{2})?\.log$"
)
MMDVM_LOG_STEM_RE = re.compile(r"^MMDVM_Bridge[A-Za-z0-9_-]+$")
STANDARD_LOG_STEMS = frozenset({"MMDVM_Bridge", "MMDVM_Bridge_YSF"})
LINE_PREFIX = (
    r"^[MIWEF]:\s+(\d{4}-\d{2}-\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2})(?:\.\d+)?\s+"
)
DMR_SOURCE_RE = re.compile(
    LINE_PREFIX
    + r"DMR Slot ([12]),\s+received network (?:voice header|late entry) "
    r"from\s+(.+?)\s+to TG\s+\d+\b",
    re.IGNORECASE,
)
DMR_END_RE = re.compile(
    LINE_PREFIX
    + r"DMR Slot ([12]),\s+(?:received network end of voice transmission|"
    r"network watchdog has expired)\b.*$",
    re.IGNORECASE,
)
DMR_TX_ON_RE = re.compile(LINE_PREFIX + r"DMR,\s+TX state\s*=\s*ON\b", re.IGNORECASE)
DMR_TX_OFF_RE = re.compile(
    LINE_PREFIX
    + r"DMR,\s+TX state\s*=\s*OFF\b"
    r"(?:,\s+DMR frame count was\s+\d+\s+frames?)?\s*$",
    re.IGNORECASE,
)
YSF_SOURCE_RE = re.compile(
    LINE_PREFIX
    + r"YSF,\s+received network (?:data|voice) from\s+"
    r"([A-Za-z0-9/ -]{1,20}?)\s+to\s+.*$",
    re.IGNORECASE,
)
YSF_END_RE = re.compile(
    LINE_PREFIX
    + r"YSF,\s+(?:received network end of transmission|"
    r"network watchdog has expired)\b.*$",
    re.IGNORECASE,
)
YSF_TX_ON_RE = re.compile(LINE_PREFIX + r"YSF,\s+TX state\s*=\s*ON\b", re.IGNORECASE)
YSF_TX_OFF_RE = re.compile(
    LINE_PREFIX
    + r"YSF,\s+TX state\s*=\s*OFF\b"
    r"(?:,\s+YSF frame count was\s+\d+\s+frames?)?\s*$",
    re.IGNORECASE,
)
P25_SOURCE_RE = re.compile(LINE_PREFIX + r"P25,\s+received network transmission from\s+(.+?)\s+to TG\s+\d+\b.*$", re.I)
P25_END_RE = re.compile(LINE_PREFIX + r"P25,\s+network end of transmission\b.*$", re.I)
NXDN_SOURCE_RE = re.compile(LINE_PREFIX + r"NXDN,\s+received network transmission from\s+(.+?)\s+to TG\s+\d+\b.*$", re.I)
NXDN_END_RE = re.compile(LINE_PREFIX + r"NXDN,\s+received network end of transmission\b.*$", re.I)
NXDN_RF_HEADER_RE = re.compile(LINE_PREFIX + r"NXDN,\s+received RF header from\s+.+?\s+to TG\s+\d+\b.*$", re.I)
NXDN_RF_END_RE = re.compile(LINE_PREFIX + r"NXDN,\s+received RF end of transmission\b.*$", re.I)
M17_START_RE = re.compile(LINE_PREFIX + r"USRP voice received as first frame\s*$", re.I)
M17_INBOUND_END_RE = re.compile(LINE_PREFIX + r"M17 received end of voice transmission,\s*([0-9.]+) seconds\s*$", re.I)
M17_END_RE = re.compile(LINE_PREFIX + r"USRP received end of voice transmission,\s*([0-9.]+) seconds\s*$", re.I)
KEYED_SAMPLE_GRACE_SECONDS = 3.0
ACTIVE_STATE_MAX_AGE_SECONDS = 300
BRIDGE_RECENT_MAX_AGE_SECONDS = 86400
CLIENT_ROSTER_MAX_AGE_SECONDS = 45
ASTAPI_MAX_AGE_SECONDS = 5.0
MAX_JSON_BYTES = 2 * 1024 * 1024
INITIAL_TAIL_BYTES = 262_144
WATCH_INTERVAL = 0.75
SERVICE_CHECK_INTERVAL = 5.0
UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")


RADIO_ID_PATH = Path("/var/lib/mmdvm/DMRIds.dat")
RADIO_ID_MAX_BYTES = 64 * 1024 * 1024
_radio_id_cache: dict[int, str] = {}


def radio_id_callsign(radio_id: str, path: Path | None = None) -> str:
    """Resolve a numeric DMR radio ID through the host's full DMRIds.dat.

    Managed DMR bridges keep a bridge-only DMRIds.dat that lists just the
    node owner, so MMDVM logs every other station as a bare number (#224).
    Returns "" when the ID is unknown or the database is not trustworthy.
    """
    if not radio_id.isdecimal() or len(radio_id) > 10:
        return ""
    source_id = int(radio_id)
    if source_id in _radio_id_cache:
        return _radio_id_cache[source_id]
    path = path or RADIO_ID_PATH
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022 or info.st_size > RADIO_ID_MAX_BYTES):
            return ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.strip().replace(";", " ").replace(",", " ").split()
                if len(fields) < 2 or not fields[0].isdecimal() or int(fields[0]) != source_id:
                    continue
                callsign = fields[1].upper()
                if re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}", callsign):
                    if len(_radio_id_cache) > 4096:
                        _radio_id_cache.clear()
                    _radio_id_cache[source_id] = callsign
                    return callsign
                break
    except OSError:
        pass
    return ""


def clean_caller(value: str, limit: int = 120) -> str:
    caller = re.sub(r"\s+", " ", str(value or "")).strip()
    return caller[:limit] if caller and caller != "-" else ""


def match_epoch(match: re.Match[str], fallback: int) -> int:
    try:
        return int(
            calendar.timegm(
                time.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S")
            )
        )
    except (OverflowError, ValueError):
        return fallback


def initial_activity_state() -> dict[str, Any]:
    return {
        "observed": False,
        "role": "idle",
        "current_user": "",
        "last_user": "-",
        "last_source_user": "",
        "last_source_epoch": 0,
        "active_start_epoch": 0,
        "activity_epoch": 0,
        "last_event_epoch": 0,
        "source_slot": 0,
        "source_observed_at": 0.0,
        "network_relay": False,
        "relay_user": "",
        "reflector": "",
        "module": "",
        "link_protocol": "",
        "linked": None,
        "online": None,
        "recent_users": [],
        "tx_events": [],
        "linked_clients": [],
        "client_snapshot": None,
        "client_events": [],
        "client_snapshot_version": 0,
    }

def reconcile_client_snapshot(state: dict[str, Any], clients: list[str] | None, now: int) -> None:
    """Record evidenced roster changes; unknown snapshots never imply disconnects."""
    if clients is None:
        return
    current = sorted({clean_caller(client, 120).upper() for client in clients if clean_caller(client, 120)})
    previous = state.get("client_snapshot")
    state["client_snapshot"] = current
    baseline_version = int(state.get("client_snapshot_version", 0) or 0)
    if baseline_version < 1:
        # On first observation (including upgrade from the old collector),
        # report currently connected stations as present at observation time.
        # This is explicitly a baseline observation, not a claimed historical
        # connect timestamp. It lets already-connected operators appear in UI.
        events = [row for row in state.get("client_events", []) if isinstance(row, dict)
                  and 0 < int(row.get("epoch", 0) or 0) <= now
                  and now - int(row.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS]
        known = {str(row.get("callsign", "")).upper() for row in events}
        events.extend({"callsign": name, "event": "connect", "epoch": now,
                       "source": "baseline_observation"}
                      for name in current if name not in known)
        events.sort(key=lambda row: int(row.get("epoch", 0) or 0), reverse=True)
        state["client_events"] = events[:100]
        state["client_snapshot_version"] = 1
        return
    if not isinstance(previous, list):
        return
    old = set(previous)
    new = set(current)
    events = [row for row in state.get("client_events", []) if isinstance(row, dict)
              and 0 < int(row.get("epoch", 0) or 0) <= now
              and now - int(row.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS]
    events.extend({"callsign": name, "event": kind, "epoch": now}
                  for kind, names in (("disconnect", old - new), ("connect", new - old))
                  for name in sorted(names))
    events.sort(key=lambda row: int(row.get("epoch", 0) or 0), reverse=True)
    state["client_events"] = events[:100]


def _roster_identity(row: object, config: dict[str, Any]) -> str:
    """Return a public client identity, excluding infrastructure and service rows."""
    if not isinstance(row, dict):
        return ""
    if row.get("connected") is False or row.get("active") is False or row.get("current") is False:
        return ""
    classification = " ".join(
        str(row.get(key, "")).strip().lower()
        for key in ("role", "kind", "type", "source", "mode", "protocol")
    )
    classification_tokens = set(re.split(r"[^a-z]+", classification))
    if any(marker in classification_tokens for marker in (
        "relay", "service", "infrastructure", "allstar", "asl", "usrp",
    )):
        return ""
    if any(row.get(key) is True for key in ("relay", "service", "is_relay", "is_service")):
        return ""
    identity = ""
    for key in ("callsign", "call", "station", "username", "name", "display_name", "displayName", "user"):
        identity = clean_caller(str(row.get(key, "")), 120).upper()
        if identity:
            break
    if not identity:
        return ""
    identity = re.sub(r"\s+[A-Z]$", "", identity).strip()
    if not identity:
        return ""
    service_identities = {"RFCKRD0", "YSF-LIVE", "KF0WSS", "SRVPROB", "M7TEST"}
    if identity in service_identities:
        return ""
    filtered = set()
    for value in config.get("filteredStations", []) if isinstance(config.get("filteredStations"), list) else []:
        candidate = clean_caller(str(value), 120).upper()
        if candidate:
            filtered.add(candidate)
    return "" if config.get("filterServiceStations", True) is not False and identity in filtered else identity


def authoritative_client_snapshots(
    config: dict[str, Any],
    now: int,
    path: Path = MANAGED_CLIENT_ROSTER_PATH,
) -> dict[str, list[str]]:
    """Read only fresh, explicitly current rosters from the managed collector."""
    try:
        before = path.lstat()
    except OSError:
        return {}
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (os.geteuid() == 0 and before.st_uid != 0)
        or stat.S_IMODE(before.st_mode) & 0o002
        or before.st_size > MAX_JSON_BYTES
        or before.st_mtime > now + 300
        or now - before.st_mtime > CLIENT_ROSTER_MAX_AGE_SECONDS
    ):
        return {}
    payload = _safe_json(
        path,
        required_uid=0 if os.geteuid() == 0 else None,
        forbidden_write_mask=0o002,
    )
    try:
        after = path.lstat()
    except OSError:
        return {}
    if payload is None or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
        after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size
    ):
        return {}
    metadata = payload.get("_asr_meta")
    if not isinstance(metadata, dict):
        return {}
    snapshots: dict[str, list[str]] = {}
    for bridge in all_configured_standard_bridges(config):
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", "")).lower()
        rows = payload.get(bridge_id)
        meta = metadata.get(bridge_id)
        if (
            not isinstance(rows, list)
            or not isinstance(meta, dict)
            or str(meta.get("kind", "")).lower() != "current"
            or str(meta.get("mode", "")).lower() != mode
        ):
            continue
        snapshots[bridge_id] = sorted({
            identity for row in rows if (identity := _roster_identity(row, config))
        })
    return snapshots


def record_transmission(
    state: dict[str, Any], callsign: str, start_epoch: int, event_epoch: int,
    now: int, duration: float | None = None, direction: str = "",
    source_protocol: str = "",
) -> None:
    """Retain one completed transmission without duplicating restart replay."""
    identity = clean_caller(callsign, 120)
    started = max(0, int(start_epoch))
    ended = max(0, int(event_epoch))
    if ended <= 0:
        return
    elapsed = max(0.0, float(ended - started) if duration is None else float(duration))
    event = {
        "callsign": identity,
        "identity_available": bool(identity),
        "event": "transmit",
        "epoch": ended,
        "start_epoch": started or ended,
        "duration_seconds": elapsed,
        "direction": direction if direction in ("incoming", "outgoing") else "",
        "source_protocol": re.sub(r"[^A-Za-z0-9]", "", source_protocol).upper()[:8],
    }
    retained = [
        item for item in state.get("tx_events", [])
        if isinstance(item, dict)
        and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        and not (
            int(item.get("epoch", 0) or 0) == ended
            and int(item.get("start_epoch", 0) or 0) == event["start_epoch"]
            and clean_caller(item.get("callsign", ""), 120) == identity
        )
    ]
    retained.append(event)
    retained.sort(key=lambda item: int(item.get("epoch", 0) or 0), reverse=True)
    state["tx_events"] = retained[:100]
    if identity and direction == "incoming":
        recent = [
            item for item in state.get("recent_users", [])
            if isinstance(item, dict)
            and clean_caller(item.get("callsign", ""), 120).upper() != identity.upper()
            and now - int(item.get("last_tx_epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        state["recent_users"] = ([{"callsign": identity, "last_tx_epoch": ended}] + recent)[:50]
        state.update({
            "last_user": identity,
            "last_source_user": identity,
            "last_source_epoch": ended,
        })


def watchdog_event(line: str, mode: str, now: int) -> tuple[int, int] | None:
    """Return anchored watchdog evidence as (epoch, DMR slot or zero)."""
    end_pattern = {
        "dmr": DMR_END_RE,
        "ysf": YSF_END_RE,
    }.get(mode)
    match = end_pattern.fullmatch(line.rstrip("\r\n")) if end_pattern else None
    if match is None or "network watchdog has expired" not in line.lower():
        return None
    return match_epoch(match, now), int(match.group(3)) if mode == "dmr" else 0


def apply_activity_line(
    state: dict[str, Any],
    line: str,
    mode: str,
    now: int,
    local_identities: frozenset[str] = frozenset(),
    relay_identity: str = "",
) -> None:
    """Apply only chronological, mode-scoped MMDVM evidence to a live state."""
    text = line.rstrip("\r\n")
    if mode == "dmr":
        source = DMR_SOURCE_RE.fullmatch(text)
        end = DMR_END_RE.fullmatch(text)
        tx_on = DMR_TX_ON_RE.fullmatch(text)
        tx_off = DMR_TX_OFF_RE.fullmatch(text)
    elif mode == "ysf":
        source = YSF_SOURCE_RE.fullmatch(text)
        end = YSF_END_RE.fullmatch(text)
        tx_on = YSF_TX_ON_RE.fullmatch(text)
        tx_off = YSF_TX_OFF_RE.fullmatch(text)
    elif mode in ("p25", "nxdn"):
        source = (P25_SOURCE_RE if mode == "p25" else NXDN_SOURCE_RE).fullmatch(text)
        end = (P25_END_RE if mode == "p25" else NXDN_END_RE).fullmatch(text)
        tx_on = re.fullmatch(LINE_PREFIX + rf"{mode.upper()},\s+TX state\s*=\s*ON\b", text, re.I)
        tx_off = re.fullmatch(LINE_PREFIX + rf"{mode.upper()},\s+TX state\s*=\s*OFF\b", text, re.I)
    else:
        return
    # NXDN RF headers are generated on the local RF/USRP-facing leg.
    # They must not be mistaken for inbound NXDN-network talkers.
    rf_header = NXDN_RF_HEADER_RE.fullmatch(text) if mode == "nxdn" else None
    rf_end = NXDN_RF_END_RE.fullmatch(text) if mode == "nxdn" else None
    if rf_header is not None:
        tx_on = rf_header
    if rf_end is not None:
        tx_off = rf_end
    match = source or end or tx_on or tx_off
    if match is None:
        return
    epoch = match_epoch(match, now)
    if epoch < int(state.get("last_event_epoch", 0) or 0):
        return

    if source is not None:
        caller_group = 4 if mode == "dmr" else 3
        caller = clean_caller(source.group(caller_group), 20 if mode == "ysf" else 120)
        if mode == "dmr" and caller.isdecimal():
            caller = radio_id_callsign(caller) or caller
        if mode == "nxdn" and caller.isdecimal():
            # Resolve NXDN radio IDs before publishing the shared ASR identity.
            try:
                import csv
                with open("/etc/netoholics/nxdn/NXDN.csv", newline="", encoding="utf-8-sig") as ids:
                    caller = next((row["CALLSIGN"].strip().upper() for row in csv.DictReader(ids)
                                   if row.get("RADIO_ID", "").strip() == caller
                                   and re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}", row.get("CALLSIGN", "").strip().upper())), caller)
            except (OSError, ValueError, KeyError):
                pass

        local_relay = mode == "ysf" and caller.upper() in local_identities
        state.update({
            "observed": True,
            "role": "relay" if local_relay else "source",
            "current_user": "" if local_relay else caller,
            "active_start_epoch": epoch,
            "activity_epoch": epoch,
            "last_event_epoch": epoch,
            "source_slot": int(source.group(3)) if mode == "dmr" else 0,
            "source_observed_at": float(now),
            "network_relay": local_relay,
        })
        if not local_relay and caller:
            state.update({
                "last_user": caller,
                "last_source_user": caller,
                "last_source_epoch": epoch,
            })
        return

    if end is not None:
        end_slot = int(end.group(3)) if mode == "dmr" else 0
        source_slot = int(state.get("source_slot", 0) or 0)
        active_epoch = int(state.get("active_start_epoch", 0) or 0)
        same_stream = (
            state.get("role") == "source" or bool(state.get("network_relay"))
        ) and (mode != "dmr" or source_slot == 0 or source_slot == end_slot)
        source_user = clean_caller(state.get("current_user", ""), 120)
        if same_stream and epoch >= active_epoch:
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "source_slot": 0,
                "network_relay": False,
            })
            if source_user:
                reported_duration = re.search(
                    r",\s*([0-9]+(?:\.[0-9]+)?)\s+seconds\b", text, re.IGNORECASE
                )
                duration = float(reported_duration.group(1)) if reported_duration else max(0, epoch - active_epoch)
                # A DMR registration/keepalive can emit a 0.1s pseudo-stream.
                if mode != "dmr" or duration >= 1:
                    record_transmission(state, source_user, active_epoch, epoch, now,
                                        duration, "incoming", mode)
        state.update({"observed": True, "activity_epoch": epoch, "last_event_epoch": epoch})
        return

    if tx_on is not None:
        # A local protocol TX edge proves relayed activity, but ASTAPI's
        # current caller is an AllStar identity and cannot identify a digital
        # source. Preserve the event without fabricating a protocol talker.
        state.update({
            "observed": True,
            "role": "relay",
            "current_user": "",
            "active_start_epoch": epoch,
            "activity_epoch": epoch,
            "last_event_epoch": epoch,
            "source_slot": 0,
            "network_relay": False,
            "relay_user": "",
        })
        return

    if tx_off is not None:
        if state.get("role") == "relay":
            active_epoch = int(state.get("active_start_epoch", 0) or 0)
            if active_epoch and epoch >= active_epoch:
                record_transmission(
                    state, "", active_epoch, epoch, now,
                    direction="outgoing", source_protocol=mode,
                )
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "network_relay": False,
                "relay_user": "",
            })
        state.update({"observed": True, "activity_epoch": epoch, "last_event_epoch": epoch})


def apply_m17_activity(state: dict[str, Any], line: str, now: int, bridge_call: str = "") -> None:
    """Track M17 network-origin events separately from outgoing USRP relay."""
    text = line.rstrip("\r\n")
    start = M17_START_RE.fullmatch(text)
    end = M17_END_RE.fullmatch(text)
    inbound_end = M17_INBOUND_END_RE.fullmatch(text)
    match = start or end or inbound_end
    if not match:
        return
    event_epoch = match_epoch(match, now)
    if event_epoch < int(state.get("last_event_epoch", 0) or 0):
        return
    if start:
        if state.get("role") == "source":
            return
        state.update({"observed": True, "role": "relay", "active_start_epoch": event_epoch,
                      "activity_epoch": event_epoch, "last_event_epoch": event_epoch})
    elif end:
        if state.get("role") == "source":
            return
        started = int(state.get("active_start_epoch", 0) or 0)
        if started and event_epoch >= started:
            record_transmission(state, "", started, event_epoch, now,
                                float(end.group(3)), "outgoing", "m17")
        state.update({"observed": True, "role": "idle", "current_user": "",
                      "active_start_epoch": 0, "activity_epoch": event_epoch,
                      "last_event_epoch": event_epoch})
    else:
        # Reflector Users.LastHeardTime is end-of-stream evidence. Match only
        # a unique, recent stream; never infer identity from connected clients.
        duration = float(inbound_end.group(3))
        identity = ""
        try:
            reflector = json.loads(Path("/var/log/m17/mrefd.json").read_text())
            users = reflector.get("Users", []) if isinstance(reflector, dict) else []
            matches = [str(u.get("Source", "")).strip().upper().replace(" ", "-")
                       for u in users if isinstance(u, dict)
                       and abs(int(u.get("LastHeardTime", 0)) - event_epoch) <= 2
                       and re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,3})?",
                                        str(u.get("Source", "")).strip().upper().replace(" ", "-"))]
            if len(set(matches)) == 1 and not matches[0].startswith(bridge_call + "-") and matches[0] != bridge_call:
                identity = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", matches[0])
        except (OSError, ValueError, TypeError):
            pass
        if identity:
            started = max(0, event_epoch - max(1, int(duration)))
            record_transmission(state, identity, started, event_epoch, now,
                                duration, "incoming", "m17")
        state.update({"observed": True, "role": "idle", "current_user": "",
                      "active_start_epoch": 0, "activity_epoch": event_epoch,
                      "last_event_epoch": event_epoch})


M17_OPEN_JOURNAL_RE = re.compile(
    r"^(\d+\.\d+) .*?mrefd\[\d+\]: Opening stream on module [A-Z] from client (.+?) with id 0x[0-9a-f]+ by user (.+)$", re.I)
M17_CLOSE_JOURNAL_RE = re.compile(
    r"^(\d+\.\d+) .*?mrefd\[\d+\]: Closing stream on module [A-Z]\s*$", re.I)


def apply_m17_journal(state: dict[str, Any], line: str, now: int, bridge_call: str = "",
                      bridge_identity: str = "") -> None:
    """Use authoritative reflector stream open/close for live source identity."""
    opened = M17_OPEN_JOURNAL_RE.fullmatch(line)
    closed = M17_CLOSE_JOURNAL_RE.fullmatch(line)
    if not (opened or closed):
        return
    match = opened or closed
    epoch = int(float(match.group(1)))
    if now - epoch > 20 or epoch > now + 2:
        return
    if epoch < int(state.get("last_event_epoch", 0) or 0):
        return
    if opened:
        if bridge_identity and bridge_identity in (m17_identity(opened.group(2)), m17_identity(opened.group(3))):
            return  # The bridge relaying AllStar audio into the reflector (#134).
        caller = opened.group(3).strip().upper().replace(" ", "-")
        caller = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", caller)
        if bridge_call and (caller == bridge_call or caller.startswith(bridge_call + "-")):
            return  # Reflector bridge loopback is not a remote M17 station.
        if not re.fullmatch(r"[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,3})?", caller):
            return
        state.update({"observed": True, "role": "source", "current_user": caller,
                      "active_start_epoch": epoch, "activity_epoch": epoch,
                      "last_event_epoch": epoch, "source_observed_at": float(now)})
    elif state.get("role") == "source":
        caller = str(state.get("current_user") or "")
        started = int(state.get("active_start_epoch") or 0)
        if caller and started and epoch >= started:
            record_transmission(state, caller, started, epoch, now,
                                float(epoch - started), "incoming", "m17")
        state.update({"role": "idle", "current_user": "", "active_start_epoch": 0,
                      "activity_epoch": epoch, "last_event_epoch": epoch})


def reconcile_keyed_source(
    state: dict[str, Any],
    keyed: bool | None,
    now: float,
    grace: float = KEYED_SAMPLE_GRACE_SECONDS,
) -> None:
    """Clear only on explicit keyed NO after a short observation grace."""
    if state.get("role") != "source" or keyed is not False:
        return
    observed_at = float(state.get("source_observed_at", 0.0) or 0.0)
    if observed_at <= 0 or now - observed_at < grace:
        return
    state.update({
        "role": "idle",
        "current_user": "",
        "active_start_epoch": 0,
        "source_slot": 0,
        "network_relay": False,
        "activity_epoch": max(int(state.get("activity_epoch", 0) or 0), int(now)),
    })


def _safe_json(
    path: Path,
    max_bytes: int = MAX_JSON_BYTES,
    required_uid: int | None = None,
    forbidden_write_mask: int = 0o022,
) -> dict[str, Any] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (required_uid is not None and details.st_uid != required_uid)
            or stat.S_IMODE(details.st_mode) & forbidden_write_mask
            or details.st_size > max_bytes
        ):
            return None
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_text(path: Path, max_bytes: int = MAX_JSON_BYTES) -> tuple[str, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (os.geteuid() == 0 and details.st_uid != 0)
            or stat.S_IMODE(details.st_mode) & 0o022
            or details.st_size > max_bytes
        ):
            return None
        raw = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        return None
    try:
        return raw.decode("utf-8", errors="replace"), details
    except UnicodeError:
        return None


def astapi_bridge_evidence(
    local_node: str,
    bridge_nodes: set[str],
    now: float | None = None,
    cache_dir: Path = ASTAPI_CACHE_DIR,
) -> dict[str, Any]:
    """Read fresh per-node link/key evidence and the active non-bridge caller."""
    result: dict[str, Any] = {
        "keyed": {node: None for node in bridge_nodes},
        "connected": {node: None for node in bridge_nodes},
        "caller": "",
        "caller_node": "",
        "caller_started_epoch": 0,
    }
    if not NODE_RE.fullmatch(local_node):
        return result
    payload = _safe_json(cache_dir / f"astapi-{local_node}.json")
    current_time = time.time() if now is None else now
    try:
        updated = float(payload.get("updated", 0)) if payload is not None else 0.0
    except (TypeError, ValueError):
        return result
    if updated <= 0 or updated > current_time + 5 or current_time - updated > ASTAPI_MAX_AGE_SECONDS:
        return result
    current = payload.get("current")
    local = current.get(local_node) if isinstance(current, dict) else None
    rows = local.get("remote_nodes") if isinstance(local, dict) else None
    if not isinstance(rows, list):
        return result
    result["connected"] = {node: False for node in bridge_nodes}
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = str(row.get("node", ""))
        if node not in result["keyed"]:
            continue
        result["connected"][node] = str(row.get("link", "")).strip().lower() == "established"
        keyed = str(row.get("keyed", "")).strip().lower()
        if keyed == "yes":
            result["keyed"][node] = True
        elif keyed == "no":
            result["keyed"][node] = False
    talker = local.get("current_talker") if isinstance(local, dict) else None
    if isinstance(talker, dict):
        caller_node = str(talker.get("node", "")).strip()
        caller = clean_caller(str(talker.get("info", "")), 120)
        # A bridge transport number is routing metadata, not a talker identity.
        if caller_node not in bridge_nodes and re.search(r"[A-Za-z]", caller):
            result.update({
                "caller": caller,
                "caller_node": caller_node,
                "caller_started_epoch": max(0, int(talker.get("started_epoch", 0) or 0)),
            })
    return result


def astapi_key_states(
    local_node: str,
    bridge_nodes: set[str],
    now: float | None = None,
    cache_dir: Path = ASTAPI_CACHE_DIR,
) -> dict[str, bool | None]:
    """Compatibility wrapper for callers that need only keyed evidence."""
    return astapi_bridge_evidence(local_node, bridge_nodes, now, cache_dir)["keyed"]


def all_configured_standard_bridges(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    configured = config.get("bridges")
    if not isinstance(configured, list):
        return rows
    for bridge in configured:
        if not isinstance(bridge, dict) or bridge.get("cardType", "standard") != "standard":
            continue
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", bridge_id)).strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", mode)
        mode = (
            "dmr" if compact.startswith("dmr")
            else "ysf" if compact.startswith("ysf")
            else "p25" if compact.startswith("p25")
            else "nxdn" if compact.startswith("nxdn")
            else ""
        )
        # Every supported standard digital card requires an explicit status
        # entry, including protocols whose activity arrives by per-card logs.
        if (
            bridge_id not in seen
            and BRIDGE_ID_RE.fullmatch(bridge_id)
            and NODE_RE.fullmatch(str(bridge.get("node", "")))
            and (mode or compact.startswith(("p25", "nxdn", "m17")))
        ):
            rows.append(bridge)
            seen.add(bridge_id)
    return rows


def configured_standard_bridges(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in ("dmr", "ysf", "p25", "nxdn")}
    for bridge in all_configured_standard_bridges(config):
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", bridge_id)).strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", mode)
        mode = (
            "dmr" if compact.startswith("dmr")
            else "ysf" if compact.startswith("ysf")
            else "p25" if compact.startswith("p25")
            else "nxdn" if compact.startswith("nxdn")
            else ""
        )
        if mode in by_mode:
            by_mode[mode].append(bridge)
    # Without an explicit per-card log path, one card per mode is the only
    # unambiguous generic mapping. Multiple same-mode cards retain their feed.
    return {mode: rows[0] for mode, rows in by_mode.items() if len(rows) == 1}


def configured_net_log_stems(config: dict[str, Any]) -> frozenset[str]:
    """Return only MMDVM log stems explicitly named by DMR/YSF Net cards."""
    stems: set[str] = set()
    configured = config.get("bridges")
    if not isinstance(configured, list):
        return frozenset()
    for bridge in configured:
        if not isinstance(bridge, dict):
            continue
        card_type = str(bridge.get("cardType", ""))
        source = (
            str(bridge.get("dvswitchScript", ""))
            if card_type == "dmr_net"
            else (str(bridge.get("mmdvmConfig", "")) if card_type == "ysf_net" else "")
        )
        if not source:
            continue
        source_path = Path(source)
        expected_name = "dvswitch.sh" if card_type == "dmr_net" else "MMDVM_Bridge.ini"
        stem = source_path.parent.name
        if (
            source_path.name == expected_name
            and source_path.parent.parent == Path("/opt")
            and MMDVM_LOG_STEM_RE.fullmatch(stem)
        ):
            stems.add(stem)
            if card_type == "ysf_net":
                configured_root = mmdvm_ini_log_root(source_path)
                if configured_root:
                    stems.add(configured_root)
    return frozenset(stems)


def log_belongs_to_stem(name: str, stem: str) -> bool:
    return name == f"{stem}.log" or (name.startswith(f"{stem}-") and name.endswith(".log"))


def read_trusted_ini(path: Path) -> configparser.RawConfigParser | None:
    """Parse a small root-owned ini without following links."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (os.geteuid() == 0 and details.st_uid != 0)
            or stat.S_IMODE(details.st_mode) & 0o022
            or details.st_size > 256 * 1024
        ):
            return None
        chunks: list[bytes] = []
        remaining = 256 * 1024 + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > 256 * 1024:
        return None
    parser = configparser.RawConfigParser(
        inline_comment_prefixes=(";", "#"), strict=False
    )
    try:
        parser.read_string(raw.decode("utf-8"))
    except (UnicodeError, configparser.Error):
        return None
    return parser


def mmdvm_ini_log_root(path: Path) -> str:
    """Read a YSF Net card's validated MMDVM FileRoot without following links."""
    parser = read_trusted_ini(path)
    if parser is None:
        return ""
    try:
        log_path = parser.get("Log", "FilePath").strip()
        log_root = parser.get("Log", "FileRoot").strip()
    except configparser.Error:
        return ""
    return log_root if log_path == "/var/log/mmdvm" and MMDVM_LOG_STEM_RE.fullmatch(log_root) else ""


def m17_identity(value: object) -> str:
    """Compare M17 client names whatever their module separator (space or dash)."""
    return re.sub(r"[\s-]+", "-", str(value or "").strip().upper())


def m17_bridge_identity(bridge: dict[str, Any], ini_path: Path | None = None) -> str:
    """The callsign this M17 bridge itself uses on the reflector (#134).

    The bridge relays AllStar audio into the reflector under this callsign, so
    a reflector stream from it is outgoing relay, never a remote M17 talker.
    """
    configured = m17_identity(bridge.get("m17Callsign"))
    if configured:
        return configured
    if bridge.get("activityLogDirectory") != "/var/log/m17" or bridge.get("activityLogStem") != "USRP2M17":
        return ""
    parser = read_trusted_ini(ini_path or M17_BRIDGE_INI_PATH)
    if parser is None:
        return ""
    try:
        return m17_identity(parser.get("M17 Network", "Callsign"))
    except configparser.Error:
        return ""


class MmdvmFollower:
    def __init__(self, log_dir: Path = MMDVM_LOG_DIR) -> None:
        self.log_dir = log_dir
        self.cursors: dict[str, tuple[int, int]] = {}

    def read_lines(
        self,
        excluded_stems: frozenset[str] = frozenset(),
        allowed_stems: frozenset[str] = STANDARD_LOG_STEMS,
        tag_stem: bool = False,
    ) -> list[str]:
        try:
            try:
                permitted_owners = {0, pwd.getpwnam("asr-bridge").pw_uid}
            except KeyError:
                permitted_owners = {0}
            directory = self.log_dir.lstat()
            if (
                not stat.S_ISDIR(directory.st_mode)
                or stat.S_IMODE(directory.st_mode) & 0o022
                or (os.geteuid() == 0 and directory.st_uid not in permitted_owners)
            ):
                return []
            candidates = sorted(
                (
                    path
                    for path in self.log_dir.glob("MMDVM_Bridge*.log")
                    if MMDVM_LOG_NAME_RE.fullmatch(path.name)
                    and any(log_belongs_to_stem(path.name, stem) for stem in allowed_stems)
                    and not any(log_belongs_to_stem(path.name, stem) for stem in excluded_stems)
                    and not path.is_symlink()
                    and stat.S_ISREG(path.lstat().st_mode)
                    and path.lstat().st_nlink == 1
                    and not stat.S_IMODE(path.lstat().st_mode) & 0o022
                ),
                key=lambda path: path.lstat().st_mtime_ns,
                reverse=True,
            )[:12]
        except OSError:
            return []
        output: list[str] = []
        live_paths = {str(path) for path in candidates}
        for path in reversed(candidates):
            try:
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or stat.S_IMODE(details.st_mode) & 0o022:
                    os.close(descriptor)
                    continue
                old_inode, old_offset = self.cursors.get(str(path), (0, 0))
                changed = old_inode != int(details.st_ino) or int(details.st_size) < old_offset
                offset = max(0, int(details.st_size) - INITIAL_TAIL_BYTES) if changed or old_inode == 0 else old_offset
                with os.fdopen(descriptor, "r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    if offset and (changed or old_inode == 0):
                        handle.readline()
                    rows = handle.readlines()
                    if tag_stem:
                        stem = re.sub(r"-\d{4}-\d{2}-\d{2}\.log$|\.log$", "", path.name)
                        output.extend(stem + "\t" + row for row in rows)
                    else:
                        output.extend(rows)
                    self.cursors[str(path)] = (int(details.st_ino), handle.tell())
            except OSError:
                continue
        for stale in set(self.cursors) - live_paths:
            self.cursors.pop(stale, None)
        return output


def parse_service_properties(text: str) -> dict[str, str]:
    return {key: value for line in text.splitlines() if "=" in line for key, value in [line.split("=", 1)]}


def bridge_service_health(config: dict[str, Any], bridge_id: str) -> tuple[list[str], list[str]]:
    mapping = config.get("bridgeHealthServices") if isinstance(config.get("bridgeHealthServices"), dict) else {}
    configured = mapping.get(bridge_id)
    units = [str(unit).strip() for unit in configured] if isinstance(configured, list) else []
    if not units:
        # Direct ASR-managed bridges store their actual systemd unit names on
        # the bridge itself, not in the legacy bridgeHealthServices mapping.
        configured_bridges = config.get("bridges")
        if not isinstance(configured_bridges, list):
            configured_bridges = []
        bridge = next((item for item in configured_bridges
                       if isinstance(item, dict) and item.get("id") == bridge_id), {})
        if bridge.get("backendMode") == "managed":
            fields = ("gatewayService", "ysfGatewayService", "vocoderService", "mmdvmService",
                      "analogBridgeService", "emulatorService", "adminService",
                      "mqttService")
            units = [str(bridge[key]).strip() for key in fields if bridge.get(key)]
    units = list(dict.fromkeys(unit for unit in units if UNIT_RE.fullmatch(unit)))[:8]
    issues = []
    active_epochs: dict[str, int] = {}
    for unit in units:
        try:
            result = subprocess.run([
                "systemctl", "show", unit, "--no-pager",
                "-p", "ActiveState", "-p", "NRestarts", "-p", "ActiveEnterTimestampMonotonic",
            ], text=True, capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            issues.append(unit + " service status could not be checked.")
            continue
        details = parse_service_properties(result.stdout)
        if result.returncode != 0 or details.get("ActiveState") != "active":
            issues.append(unit + " is not active or could not be checked.")
            continue
        try:
            restarts = int(details.get("NRestarts", "0") or 0)
            epoch = int(details.get("ActiveEnterTimestampMonotonic", "0") or 0)
        except ValueError:
            issues.append(unit + " returned invalid service status.")
            continue
        if restarts > 0:
            issues.append(unit + " restarted unexpectedly; repair the full bridge chain.")
        active_epochs[unit] = epoch
    valid_epochs = [epoch for epoch in active_epochs.values() if epoch > 0]
    # A partial chain restart is actionable while it is fresh. Persistent
    # start-time skew is normal after maintenance and must not leave a healthy
    # bridge OFFLINE indefinitely.
    newest_epoch = max(valid_epochs, default=0)
    recent_restart_cutoff = int(time.monotonic() * 1_000_000) - 300_000_000
    if (not issues and len(valid_epochs) > 1
            and newest_epoch >= recent_restart_cutoff
            and newest_epoch - min(valid_epochs) > 30_000_000):
        newest = max(active_epochs, key=active_epochs.get)
        issues.append(newest + " restarted without its companion bridge services; repair the full bridge chain.")
    return units, issues


def bridge_autopatch_active(node: str) -> bool | None:
    """Return confirmed bridge autopatch state; command failure is unknown."""
    if not NODE_RE.fullmatch(node):
        return None
    try:
        result = subprocess.run(
            ["/usr/sbin/asterisk", "-rx", f"rpt show variables {node}"],
            cwd="/", text=True, capture_output=True, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(?im)^\s*RPT_AUTOPATCHUP\s*=\s*([01])\s*$", result.stdout)
    return match.group(1) == "1" if match else None


def confirmed_m17_state(bridge: dict[str, Any], now: int, runtime_dir: Path = Path("/run/allscan-reimagined-m17")) -> dict[str, Any]:
    """Use fresh authoritative M17 runtime evidence, never infer from systemd."""
    bridge_id = str(bridge.get("id", ""))
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        return {}
    path = runtime_dir / (bridge_id + ".state.json")
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict) or data.get("bridgeId") != bridge_id:
        return {}
    updated = data.get("updatedEpoch")
    if not isinstance(updated, (int, float)) or updated > now + 10 or now - updated > 30:
        return {}
    if data.get("digitalLinked") is True and data.get("linkState") == "linked":
        return {"online": True, "linked": True,
                "reflector": (data.get("confirmedTarget") or {}).get("reflector", ""),
                "module": (data.get("confirmedTarget") or {}).get("module", "")}
    if data.get("digitalLinked") is False and data.get("linkState") in ("disconnected", "offline"):
        return {"online": False, "linked": False}
    return {}


def public_entry(bridge: dict[str, Any], state: dict[str, Any], units: list[str] | None = None, service_issues: list[str] | None = None) -> dict[str, Any]:
    role = str(state.get("role", "idle"))
    caller = clean_caller(str(state.get("current_user", "")), 120) if role == "source" else ""
    if str(bridge.get("mode", "")).lower() == "m17":
        caller = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", caller)
        for key in ("last_user", "last_source_user"):
            state[key] = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", str(state.get(key) or ""))
        for row in state.get("tx_events", []):
            if isinstance(row, dict) and row.get("callsign"):
                row["callsign"] = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", str(row["callsign"]))
        for row in state.get("recent_users", []):
            if isinstance(row, dict) and row.get("callsign"):
                row["callsign"] = re.sub(r"-(?:[0-9]{1,2}|[A-Z])$", "", str(row["callsign"]))
    online = state.get("online")
    linked = state.get("linked")
    units = units or []
    service_issues = list(service_issues or [])
    if state.get("invalid_autopatch") is True:
        service_issues.append("Bridge autopatch state is invalid; transport TX is suppressed.")
    warning = service_issues[0] if service_issues else ""
    health_severity = "offline" if service_issues else None
    health_issues: list[str] = service_issues
    return {
        "active": role != "idle",
        "role": role,
        "state": "TX ACTIVE" if role == "source" else ("RELAY" if role == "relay" else "Idle"),
        "node": str(bridge.get("node", "")),
        "title": clean_caller(str(bridge.get("title", "Bridge")), 80) or "Bridge",
        "active_start_epoch": int(state.get("active_start_epoch", 0) or 0) if role != "idle" else 0,
        "activity_epoch": int(state.get("activity_epoch", 0) or 0),
        "last_time_epoch": int(state.get("activity_epoch", 0) or 0),
        "current_user": caller,
        "caller": caller,
        "last_user": clean_caller(str(state.get("last_user", "")), 120) or "-",
        "last_source_user": clean_caller(str(state.get("last_source_user", "")), 120),
        "last_source_epoch": int(state.get("last_source_epoch", 0) or 0),
        "warning": warning,
        "health_severity": health_severity,
        "health_issues": health_issues,
        "repair_available": bool(units),
        "online": online,
        "linked": linked,
        "reflector": clean_caller(str(state.get("reflector", "")), 8).upper(),
        "module": clean_caller(str(state.get("module", "")), 1).upper(),
        "link_protocol": clean_caller(str(state.get("link_protocol", "")), 20),
        "recent_users": list(state.get("recent_users", [])),
        "tx_events": list(state.get("tx_events", [])),
        "client_events": list(state.get("client_events", [])),
        "client_snapshot_version": int(state.get("client_snapshot_version", 0) or 0),
        # Internal restart baseline. The public API sanitizer intentionally
        # omits this field while the root-owned collector can restore it.
        "client_snapshot": list(state.get("client_snapshot", []))
        if isinstance(state.get("client_snapshot"), list) else None,
        "linked_clients": list(state.get("linked_clients", [])),
    }


def standard_live_payload(
    config: dict[str, Any],
    states: dict[str, dict[str, Any]],
    lines: list[str],
    now: int,
    cache_dir: Path = ASTAPI_CACHE_DIR,
    tagged_lines: list[str] | None = None,
    external_lines: list[str] | None = None,
    m17_lines: list[str] | None = None,
    m17_journal_lines: list[str] | None = None,
    client_snapshots: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    bridges = configured_standard_bridges(config)
    # Every configured card needs an Idle/health entry, even when multiple
    # cards share a protocol and generic MMDVM logs cannot identify a card.
    visible = {str(bridge["id"]): bridge for bridge in all_configured_standard_bridges(config)}
    unambiguous_ids = {str(bridge["id"]) for bridge in bridges.values()}
    # ASTAPI identifies each transport by node, independently of protocol.
    # A shared node cannot safely attribute a keyed event to one card.
    node_counts: dict[str, int] = {}
    for bridge in visible.values():
        node = str(bridge["node"])
        node_counts[node] = node_counts.get(node, 0) + 1
    bridge_nodes = {str(bridge["node"]) for bridge in visible.values()}
    astapi = astapi_bridge_evidence(
        str(config.get("node", "")), bridge_nodes, now, cache_dir
    )
    keys = astapi["keyed"]
    connected = astapi["connected"]
    relay_identity = str(astapi.get("caller", ""))
    local_call = clean_caller(str(config.get("callsign", "")), 20).upper()
    local_identities = frozenset(value for value in (local_call, f"{local_call}-RPT" if local_call else "") if value)
    for line in lines:
        for mode in ("dmr", "ysf"):
            bridge = bridges.get(mode)
            if bridge is not None and str(bridge.get("backendMode", "")) == "managed":
                continue
            if bridge is None:
                continue
            apply_activity_line(
                states.setdefault(str(bridge["id"]), initial_activity_state()),
                line,
                mode,
                now,
                local_identities,
                relay_identity,
            )
    for tagged in tagged_lines or []:
        stem, sep, message = tagged.partition("\t")
        if not sep:
            continue
        for mode in ("dmr", "ysf", "p25", "nxdn"):
            bridge = bridges.get(mode)
            if bridge is None:
                continue
            # The managed per-card log stem is unambiguous; never route a
            # similarly formatted message from another bridge to this card.
            expected = f"MMDVM_Bridge_{bridge['id']}"
            if stem == expected:
                apply_activity_line(
                    states.setdefault(str(bridge["id"]), initial_activity_state()),
                    message, mode, now, local_identities, relay_identity,
                )
    for tagged in external_lines or []:
        bridge_id, sep, message = tagged.partition("\t")
        bridge = visible.get(bridge_id)
        if sep and bridge is not None:
            mode = str(bridge.get("mode", "")).lower()
            if mode in ("dmr", "ysf", "p25", "nxdn"):
                apply_activity_line(
                    states.setdefault(bridge_id, initial_activity_state()),
                    message, mode, now, local_identities, relay_identity,
                )
    for tagged in m17_lines or []:
        bridge_id, sep, message = tagged.partition("\t")
        if sep and bridge_id in visible and str(visible[bridge_id].get("mode", "")).lower() == "m17":
            apply_m17_activity(states.setdefault(bridge_id, initial_activity_state()), message, now, local_call)
    for tagged in m17_journal_lines or []:
        bridge_id, sep, message = tagged.partition("\t")
        if sep and bridge_id in visible and str(visible[bridge_id].get("mode", "")).lower() == "m17":
            apply_m17_journal(states.setdefault(bridge_id, initial_activity_state()), message, now, local_call,
                              m17_bridge_identity(visible[bridge_id]))
    health_cache = states.setdefault("__service_health__", {})
    for bridge_id, bridge in visible.items():
        if bridge_id not in unambiguous_ids and str(bridge.get("mode", "")).lower() != "m17":
            # Do not retain or invent activity for an ambiguously routed card.
            previous = states.get(bridge_id) if isinstance(states.get(bridge_id), dict) else {}
            states[bridge_id] = initial_activity_state()
            states[bridge_id]["client_snapshot"] = (
                previous.get("client_snapshot") if isinstance(previous.get("client_snapshot"), list) else None
            )
            states[bridge_id]["client_events"] = (
                previous.get("client_events") if isinstance(previous.get("client_events"), list) else []
            )
        state = states.setdefault(bridge_id, initial_activity_state())
        if str(bridge.get("mode", "")).lower() == "m17" and local_call:
            def is_bridge_identity(value: object) -> bool:
                identity = str(value or "").upper().replace(" ", "-")
                return identity == local_call or identity.startswith(local_call + "-")
            for key in ("last_user", "last_source_user", "current_user"):
                if is_bridge_identity(state.get(key)):
                    state[key] = ""
            state["tx_events"] = [event for event in state.get("tx_events", [])
                                  if isinstance(event, dict) and not is_bridge_identity(event.get("callsign"))]
            state["recent_users"] = [row for row in state.get("recent_users", [])
                                     if isinstance(row, dict) and not is_bridge_identity(row.get("callsign"))]
            if state.get("role") == "source" and not state.get("current_user"):
                state["role"] = "idle"
                state["active_start_epoch"] = 0
        if client_snapshots is not None and bridge_id in client_snapshots:
            reconcile_client_snapshot(state, client_snapshots[bridge_id], now)
        active_start = int(state.get("active_start_epoch", 0) or 0)
        if (
            state.get("role") != "idle"
            and active_start > 0
            and now - active_start > ACTIVE_STATE_MAX_AGE_SECONDS
        ):
            # A missing EOT must not pin a card active forever. The five-minute
            # safety window preserves the verified 60-120 second transmissions.
            state.update({
                "role": "idle", "current_user": "", "active_start_epoch": 0,
                "source_slot": 0, "network_relay": False, "relay_user": "",
            })
        state["client_events"] = [
            item for item in state.get("client_events", [])
            if isinstance(item, dict)
            and 0 < int(item.get("epoch", 0) or 0) <= now + 300
            and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ][:100]
        state["tx_events"] = [
            item for item in state.get("tx_events", [])
            if isinstance(item, dict)
            and int(item.get("epoch", 0) or 0) > 0
            and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        state["recent_users"] = [
            item for item in state.get("recent_users", [])
            if isinstance(item, dict)
            and int(item.get("last_tx_epoch", 0) or 0) > 0
            and now - int(item.get("last_tx_epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        if (
            int(state.get("last_source_epoch", 0) or 0) <= 0
            or now - int(state.get("last_source_epoch", 0) or 0) > BRIDGE_RECENT_MAX_AGE_SECONDS
        ):
            state["last_user"] = "-"
            state["last_source_user"] = ""
            state["last_source_epoch"] = 0

    entries: dict[str, dict[str, Any]] = {}
    for bridge_id, bridge in visible.items():
        state = states.setdefault(bridge_id, initial_activity_state())
        node = str(bridge["node"])
        # A stale or unavailable ASTAPI sample is unknown, not evidence that a
        # formerly connected bridge remains online.
        state["online"] = connected.get(node) if isinstance(connected.get(node), bool) else None
        keyed = keys.get(node) if node_counts[node] == 1 else None
        if keyed is True and now - int(state.get("autopatch_checked_epoch", 0) or 0) >= 2:
            confirmed_autopatch = bridge_autopatch_active(node)
            state["autopatch_checked_epoch"] = now
            if isinstance(confirmed_autopatch, bool):
                state["invalid_autopatch"] = confirmed_autopatch
        elif keyed is False:
            state["invalid_autopatch"] = False
        if state.get("invalid_autopatch") is True:
            keyed = False
            state.update({"role": "idle", "current_user": "", "active_start_epoch": 0,
                          "source_slot": 0, "network_relay": False, "relay_user": ""})
        if keyed is True and state.get("role") == "idle" and str(bridge.get("mode", "")).lower() != "m17":
            # A fresh, explicit keyed sample is proof of transport TX;
            # do not invent a caller or a network-side RELAY event.
            state.update({"role": "source", "current_user": "",
                          "active_start_epoch": now, "activity_epoch": now,
                          "source_observed_at": now, "observed": True})
        if bridge_id in unambiguous_ids or node_counts[node] == 1:
            if str(bridge.get("mode", "")).lower() != "m17":
                reconcile_keyed_source(state, keyed, now)
        cached = health_cache.get(bridge_id) if isinstance(health_cache.get(bridge_id), dict) else {}
        if now - int(cached.get("epoch", 0) or 0) >= SERVICE_CHECK_INTERVAL:
            units, issues = bridge_service_health(config, bridge_id)
            cached = {"epoch": now, "units": units, "issues": issues}
            health_cache[bridge_id] = cached
        if str(bridge.get("mode", "")).lower() == "m17":
            # Never carry forward a formerly confirmed link after telemetry expires.
            state.update({"online": None, "linked": None, "reflector": "", "module": ""})
            state.update(confirmed_m17_state(bridge, now))
        # Publish explicit Idle even before the first log event. Otherwise
        # configured standard bridges disappear from the live-status feed.
        entries[bridge_id] = public_entry(bridge, state, list(cached.get("units", [])), list(cached.get("issues", [])))
    valid_ids = set(visible)
    for bridge_id in set(states) - valid_ids - {"__service_health__"}:
        states.pop(bridge_id, None)
    for bridge_id in set(health_cache) - valid_ids:
        health_cache.pop(bridge_id, None)
    return {"updated_epoch": now, "bridges": entries}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    directory = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or stat.S_IMODE(directory.st_mode) & 0o022
        or (os.geteuid() == 0 and directory.st_uid != 0)
    ):
        raise OSError(f"Unsafe status directory: {path.parent}")
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_standard_states(path: Path, now: int | None = None) -> dict[str, dict[str, Any]]:
    """Restore retained history after collector restart; never restore a live PTT edge."""
    def epoch(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    payload = _safe_json(path)
    entries = payload.get("bridges") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    current = int(time.time()) if now is None else int(now)
    states: dict[str, dict[str, Any]] = {}
    for bridge_id, entry in entries.items():
        if not BRIDGE_ID_RE.fullmatch(str(bridge_id)) or not isinstance(entry, dict):
            continue
        recent_users = entry.get("recent_users") if isinstance(entry.get("recent_users"), list) else []
        tx_events = entry.get("tx_events") if isinstance(entry.get("tx_events"), list) else []
        client_events = entry.get("client_events") if isinstance(entry.get("client_events"), list) else []
        linked_clients = entry.get("linked_clients") if isinstance(entry.get("linked_clients"), list) else []
        client_snapshot = entry.get("client_snapshot") if isinstance(entry.get("client_snapshot"), list) else None
        state = initial_activity_state()
        state.update({
            "last_user": clean_caller(entry.get("last_user", "-"), 120) or "-",
            "last_source_user": clean_caller(entry.get("last_source_user", ""), 120),
            "last_source_epoch": epoch(entry.get("last_source_epoch", 0)),
            "activity_epoch": epoch(entry.get("activity_epoch", 0)),
            "online": entry.get("online") if isinstance(entry.get("online"), bool) else None,
            "linked": entry.get("linked") if isinstance(entry.get("linked"), bool) else None,
            "reflector": clean_caller(entry.get("reflector", ""), 8),
            "module": clean_caller(entry.get("module", ""), 1),
            "link_protocol": clean_caller(entry.get("link_protocol", ""), 20),
            "recent_users": [
                row for row in recent_users
                if isinstance(row, dict)
                and 0 < epoch(row.get("last_tx_epoch", 0)) <= current + 300
                and current - epoch(row.get("last_tx_epoch", 0)) <= BRIDGE_RECENT_MAX_AGE_SECONDS
            ][:50],
            "tx_events": [
                row for row in tx_events
                if isinstance(row, dict)
                and 0 < epoch(row.get("epoch", 0)) <= current + 300
                and current - epoch(row.get("epoch", 0)) <= BRIDGE_RECENT_MAX_AGE_SECONDS
            ][:100],
            "client_events": [
                row for row in client_events
                if isinstance(row, dict)
                and 0 < epoch(row.get("epoch", 0)) <= current + 300
                and current - epoch(row.get("epoch", 0)) <= BRIDGE_RECENT_MAX_AGE_SECONDS
            ][:100],
            "client_snapshot_version": epoch(entry.get("client_snapshot_version", 0)),
            "client_snapshot": sorted({
                clean_caller(row, 120).upper() for row in client_snapshot or []
                if isinstance(row, str) and clean_caller(row, 120)
            }) if client_snapshot is not None else None,
            "linked_clients": [
                row for row in linked_clients if isinstance(row, dict)
            ][:100],
        })
        states[str(bridge_id)] = state
    return states


def card_activity_sources(
    sources: dict[str, Any], configured: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Add each card's own activityLogDirectory/activityLogStem.

    A managed card is skipped by the shared untagged log path, so without an
    explicit source its talkers never reach the card (Beta 8.5.1: YSF on the
    plain MMDVM_Bridge log showed no Talking/Last Talker). An explicit
    bridgeActivityLogs entry still wins; a log already claimed by another
    card is never shared.
    """
    merged = dict(sources)
    claimed = {
        (str(item.get("directory")), str(item.get("stem")))
        for item in sources.values() if isinstance(item, dict)
    }
    for bridge_id, bridge in configured.items():
        if bridge_id in merged or str(bridge.get("mode", "")).lower() not in ("dmr", "ysf", "p25", "nxdn"):
            continue
        directory, stem = bridge.get("activityLogDirectory"), bridge.get("activityLogStem")
        if not isinstance(directory, str) or not isinstance(stem, str) or (directory, stem) in claimed:
            continue
        merged[bridge_id] = {"directory": directory, "stem": stem}
        claimed.add((directory, stem))
    return merged


def watch_standard_status(
    config_path: Path = CONFIG_PATH,
    status_path: Path = STANDARD_STATUS_PATH,
    log_dir: Path = MMDVM_LOG_DIR,
    client_roster_path: Path = MANAGED_CLIENT_ROSTER_PATH,
    once: bool = False,
) -> None:
    follower = MmdvmFollower(log_dir)
    managed_follower = MmdvmFollower(log_dir)
    external_followers: dict[str, MmdvmFollower] = {}
    m17_offsets: dict[str, tuple[int, int]] = {}
    m17_journal_seen: set[str] = set()
    m17_journal_poll = 0
    states = restore_standard_states(status_path)
    while True:
        # Settings intentionally maintains config.json as root:<web-group>
        # 0664. Its root ownership and single-link regular-file contract make
        # that bounded group write acceptable; world write remains forbidden.
        config = _safe_json(
            config_path,
            required_uid=0 if os.geteuid() == 0 else None,
            forbidden_write_mask=0o002,
        ) or {}
        now = int(time.time())
        external_lines: list[str] = []
        sources = config.get("bridgeActivityLogs", {})
        configured = {str(b["id"]): b for b in all_configured_standard_bridges(config)}
        sources = card_activity_sources(sources if isinstance(sources, dict) else {}, configured)
        if isinstance(sources, dict):
            for bridge_id, location in sources.items():
                if bridge_id not in configured or not isinstance(location, dict):
                    continue
                directory, stem = location.get("directory"), location.get("stem")
                if not isinstance(directory, str) or not isinstance(stem, str):
                    continue
                if not ((directory == "/var/log/mmdvm" or
                         re.fullmatch(r"/var/log/netoholics/[a-z0-9_-]+", directory)) and
                        (MMDVM_LOG_STEM_RE.fullmatch(stem) or stem == "MMDVM_Bridge")):
                    continue
                follower_key = directory + "/" + stem
                follower_instance = external_followers.setdefault(follower_key, MmdvmFollower(Path(directory)))
                for row in follower_instance.read_lines(allowed_stems=frozenset({stem})):
                    external_lines.append(bridge_id + "\t" + row)
        m17_lines: list[str] = []
        for bridge in all_configured_standard_bridges(config):
            if str(bridge.get("mode", "")).lower() != "m17":
                continue
            if bridge.get("activityLogDirectory") != "/var/log/m17" or bridge.get("activityLogStem") != "USRP2M17":
                continue
            paths = sorted(Path("/var/log/m17").glob("USRP2M17-????-??-??.log"))[-2:]
            for path in paths:
                try:
                    st = path.lstat()
                    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_mode & 0o022:
                        continue
                    inode, offset = m17_offsets.get(str(path), (0, 0))
                    if inode != st.st_ino or offset > st.st_size:
                        offset = max(0, st.st_size - INITIAL_TAIL_BYTES)
                    with path.open(encoding="utf-8", errors="replace") as handle:
                        handle.seek(offset)
                        if offset and inode != st.st_ino:
                            handle.readline()
                        m17_lines.extend(str(bridge["id"]) + "\t" + row for row in handle.readlines())
                        m17_offsets[str(path)] = (st.st_ino, handle.tell())
                except OSError:
                    continue
        m17_journal_lines: list[str] = []
        if any(str(b.get("mode", "")).lower() == "m17" for b in configured.values()) and now >= m17_journal_poll:
            m17_journal_poll = now + 1
            try:
                result = subprocess.run(
                    ["journalctl", "-u", "m17-reflector.service", "--since", f"@{now-15}",
                     "-o", "short-unix", "--no-pager", "-q"],
                    capture_output=True, text=True, timeout=1.5, check=False)
                if result.returncode == 0:
                    rows = result.stdout.splitlines()[-120:]
                    for row in rows:
                        if row not in m17_journal_seen and (M17_OPEN_JOURNAL_RE.fullmatch(row) or M17_CLOSE_JOURNAL_RE.fullmatch(row)):
                            m17_journal_lines.extend(str(b["id"]) + "\t" + row for b in configured.values()
                                                     if str(b.get("mode", "")).lower() == "m17")
                    m17_journal_seen = set(rows)
            except (OSError, subprocess.TimeoutExpired):
                pass
        atomic_json(status_path, standard_live_payload(
            config,
            states,
            follower.read_lines(configured_net_log_stems(config)),
            now,
            external_lines=external_lines,
            m17_lines=m17_lines,
            m17_journal_lines=m17_journal_lines,
            tagged_lines=managed_follower.read_lines(
                allowed_stems=frozenset(
                    f"MMDVM_Bridge_{bridge['id']}"
                    for bridge in configured_standard_bridges(config).values()
                    if str(bridge.get("backendMode", "")) == "managed"
                ),
                tag_stem=True,
            ),
            client_snapshots=authoritative_client_snapshots(config, now, client_roster_path),
        ))
        if once:
            return
        time.sleep(WATCH_INTERVAL)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.watch and not args.once:
        parser.error("select --watch or --once")
    watch_standard_status(once=args.once)
