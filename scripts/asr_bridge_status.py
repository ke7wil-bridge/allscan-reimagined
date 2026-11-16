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
M17_END_RE = re.compile(LINE_PREFIX + r"USRP received end of voice transmission,\s*([0-9.]+) seconds\s*$", re.I)
KEYED_SAMPLE_GRACE_SECONDS = 3.0
ACTIVE_STATE_MAX_AGE_SECONDS = 300
BRIDGE_RECENT_MAX_AGE_SECONDS = 86400
ASTAPI_MAX_AGE_SECONDS = 5.0
MAX_JSON_BYTES = 2 * 1024 * 1024
INITIAL_TAIL_BYTES = 262_144
WATCH_INTERVAL = 0.75
SERVICE_CHECK_INTERVAL = 5.0
UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")


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
    }


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
                record_transmission(
                    state, source_user, active_epoch, epoch, now,
                    float(reported_duration.group(1)) if reported_duration else None,
                    "incoming", mode,
                )
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


def apply_m17_activity(state: dict[str, Any], line: str, now: int) -> None:
    """Track USRP audio without inventing a digital callsign or link state."""
    text = line.rstrip("\r\n")
    start, end = M17_START_RE.fullmatch(text), M17_END_RE.fullmatch(text)
    match = start or end
    if not match:
        return
    epoch = match_epoch(match, now)
    if epoch < int(state.get("last_event_epoch", 0) or 0):
        return
    if start:
        state.update({"observed": True, "role": "relay", "active_start_epoch": epoch,
                      "activity_epoch": epoch, "last_event_epoch": epoch})
    elif end:
        started = int(state.get("active_start_epoch", 0) or 0)
        if started and epoch >= started:
            record_transmission(
                state, "", started, epoch, now, float(end.group(3)),
                "outgoing", "m17",
            )
        state.update({"observed": True, "role": "idle", "active_start_epoch": 0,
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


def mmdvm_ini_log_root(path: Path) -> str:
    """Read a YSF Net card's validated MMDVM FileRoot without following links."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return ""
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (os.geteuid() == 0 and details.st_uid != 0)
            or stat.S_IMODE(details.st_mode) & 0o022
            or details.st_size > 256 * 1024
        ):
            return ""
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
        return ""
    parser = configparser.RawConfigParser(
        inline_comment_prefixes=(";", "#"), strict=False
    )
    try:
        parser.read_string(raw.decode("utf-8"))
        log_path = parser.get("Log", "FilePath").strip()
        log_root = parser.get("Log", "FileRoot").strip()
    except (UnicodeError, configparser.Error):
        return ""
    return log_root if log_path == "/var/log/mmdvm" and MMDVM_LOG_STEM_RE.fullmatch(log_root) else ""


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
            fields = ("gatewayService", "vocoderService", "mmdvmService",
                      "analogBridgeService", "emulatorService", "adminService",
                      "mqttService")
            units = [str(bridge[key]).strip() for key in fields if bridge.get(key)]
    units = list(dict.fromkeys(unit for unit in units if UNIT_RE.fullmatch(unit)))[:8]
    issues = []
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
        # Different start times alone are not a failure. A gateway can be
        # restarted independently while the rest of the chain stays healthy.
    return units, issues


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
    online = state.get("online")
    linked = state.get("linked")
    units = units or []
    service_issues = service_issues or []
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
            apply_m17_activity(states.setdefault(bridge_id, initial_activity_state()), message, now)
    health_cache = states.setdefault("__service_health__", {})
    for bridge_id, bridge in visible.items():
        if bridge_id not in unambiguous_ids and str(bridge.get("mode", "")).lower() != "m17":
            # Do not retain or invent activity for an ambiguously routed card.
            states[bridge_id] = initial_activity_state()
        state = states.setdefault(bridge_id, initial_activity_state())
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
        if keyed is True and state.get("role") == "idle":
            # A fresh, explicit keyed sample is proof of transport TX;
            # do not invent a caller or a network-side RELAY event.
            state.update({"role": "source", "current_user": "",
                          "active_start_epoch": now, "activity_epoch": now,
                          "source_observed_at": now, "observed": True})
        if bridge_id in unambiguous_ids or node_counts[node] == 1:
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
                row for row in client_events if isinstance(row, dict)
            ][:100],
            "linked_clients": [
                row for row in linked_clients if isinstance(row, dict)
            ][:100],
        })
        states[str(bridge_id)] = state
    return states


def watch_standard_status(
    config_path: Path = CONFIG_PATH,
    status_path: Path = STANDARD_STATUS_PATH,
    log_dir: Path = MMDVM_LOG_DIR,
    once: bool = False,
) -> None:
    follower = MmdvmFollower(log_dir)
    managed_follower = MmdvmFollower(log_dir)
    external_followers: dict[str, MmdvmFollower] = {}
    m17_offsets: dict[str, tuple[int, int]] = {}
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
        if isinstance(sources, dict):
            for bridge_id, location in sources.items():
                if bridge_id not in configured or not isinstance(location, dict):
                    continue
                directory, stem = location.get("directory"), location.get("stem")
                if not isinstance(directory, str) or not isinstance(stem, str):
                    continue
                if not ((directory == "/var/log/mmdvm" or
                         re.fullmatch(r"/var/log/netoholics/[a-z0-9_-]+", directory)) and
                        MMDVM_LOG_STEM_RE.fullmatch(stem)):
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
        atomic_json(status_path, standard_live_payload(
            config,
            states,
            follower.read_lines(configured_net_log_stems(config)),
            now,
            external_lines=external_lines,
            m17_lines=m17_lines,
            tagged_lines=managed_follower.read_lines(
                allowed_stems=frozenset(
                    f"MMDVM_Bridge_{bridge['id']}"
                    for bridge in configured_standard_bridges(config).values()
                    if str(bridge.get("backendMode", "")) == "managed"
                ),
                tag_stem=True,
            ),
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
