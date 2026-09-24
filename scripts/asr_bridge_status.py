#!/usr/bin/env python3
"""Shared, evidence-based DMR/YSF/D-Star activity helpers for AllScan Reimagined."""

from __future__ import annotations

import calendar
import configparser
import html
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
ASTAPI_CACHE_DIR = Path("/run/allscan-reimagined")
MMDVM_LOG_DIR = Path("/var/log/mmdvm")
DSTAR_MMDVM_LOG_DIR = Path(os.getenv("ASR_DSTAR_MMDVM_LOG_DIR", "/var/log/dstar-mmdvm"))
DSTAR_GATEWAY_LOG_DIR = Path(os.getenv("ASR_DSTAR_GATEWAY_LOG_DIR", "/var/log/dstar-ircddbgateway"))
DSTAR_HEALTH_PATH = Path(os.getenv("ASR_DSTAR_HEALTH_PATH", "/run/dstar-bridge/health"))
DSTAR_REFLECTOR_STATUS_PATH = Path(os.getenv("ASR_DSTAR_REFLECTOR_STATUS_PATH", "/run/dstar-reflector/xlxd.xml"))
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
DSTAR_SOURCE_RE = re.compile(
    LINE_PREFIX
    + r"D-Star,\s+received network header from\s+"
    r"([A-Z0-9]{1,8})(?:\s*/\s*([A-Z0-9]{1,4}))?\s+to\s+.+?\s+via\s+"
    r"([A-Z0-9]{3,8})\s+([A-Z])\s*$",
    re.IGNORECASE,
)
DSTAR_END_RE = re.compile(
    LINE_PREFIX
    + r"D-Star,\s+(?:received network end of transmission|"
    r"network watchdog has expired)\b.*$",
    re.IGNORECASE,
)
DSTAR_TX_ON_RE = re.compile(LINE_PREFIX + r"D-Star,\s+TX state\s*=\s*ON\b", re.IGNORECASE)
DSTAR_TX_OFF_RE = re.compile(
    LINE_PREFIX + r"D-Star,\s+TX state\s*=\s*OFF\b.*$", re.IGNORECASE
)
DSTAR_FALLBACK_CALL_RE = re.compile(
    LINE_PREFIX + r"D-Star,\s+No call or id found, using ini value:\s*([A-Z0-9]{1,8})\s*$",
    re.IGNORECASE,
)
DSTAR_LINK_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}):\s+"
    r"(DExtra|DCS|DPlus)\s+(link|unlink)\s+-.*?\bRefl:\s*"
    r"([A-Z0-9]{3,8})\s+([A-Z])\b(?:\s+.*)?$",
    re.IGNORECASE,
)
KEYED_SAMPLE_GRACE_SECONDS = 3.0
DSTAR_HEALTH_MAX_AGE_SECONDS = 20
BRIDGE_RECENT_MAX_AGE_SECONDS = 86400
ASTAPI_MAX_AGE_SECONDS = 5.0
MAX_JSON_BYTES = 2 * 1024 * 1024
INITIAL_TAIL_BYTES = 262_144
WATCH_INTERVAL = 0.75


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


def watchdog_event(line: str, mode: str, now: int) -> tuple[int, int] | None:
    """Return anchored watchdog evidence as (epoch, DMR slot or zero)."""
    end_pattern = {
        "dmr": DMR_END_RE,
        "ysf": YSF_END_RE,
        "dstar": DSTAR_END_RE,
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
    elif mode == "dstar":
        source = DSTAR_SOURCE_RE.fullmatch(text)
        end = DSTAR_END_RE.fullmatch(text)
        tx_on = DSTAR_TX_ON_RE.fullmatch(text)
        tx_off = DSTAR_TX_OFF_RE.fullmatch(text)
        fallback_call = DSTAR_FALLBACK_CALL_RE.fullmatch(text)
    else:
        return
    if mode != "dstar":
        fallback_call = None
    match = source or end or tx_on or tx_off or fallback_call
    if match is None:
        return
    epoch = match_epoch(match, now)
    if epoch < int(state.get("last_event_epoch", 0) or 0):
        return

    if source is not None:
        caller_group = 4 if mode == "dmr" else 3
        caller = clean_caller(source.group(caller_group), 20 if mode in {"ysf", "dstar"} else 120)
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
        if mode == "dstar":
            state["reflector"] = clean_caller(source.group(5), 8).upper()
            state["module"] = clean_caller(source.group(6), 1).upper()
        if not local_relay and caller:
            state.update({
                "last_user": caller,
                "last_source_user": caller,
                "last_source_epoch": epoch,
            })
        return

    if fallback_call is not None:
        caller = clean_caller(fallback_call.group(3), 20)
        if state.get("role") == "relay" and caller:
            state.update({
                "relay_user": caller,
                "current_user": caller,
                "last_user": caller,
                "last_source_user": caller,
                "last_source_epoch": epoch,
                "activity_epoch": epoch,
                "last_event_epoch": epoch,
            })
        return

    if end is not None:
        end_slot = int(end.group(3)) if mode == "dmr" else 0
        source_slot = int(state.get("source_slot", 0) or 0)
        active_epoch = int(state.get("active_start_epoch", 0) or 0)
        same_stream = (
            state.get("role") == "source" or bool(state.get("network_relay"))
        ) and (mode != "dmr" or source_slot == 0 or source_slot == end_slot)
        if same_stream and epoch >= active_epoch:
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "source_slot": 0,
                "network_relay": False,
            })
            if state.get("last_source_user"):
                duration = max(0.0, float(epoch) - float(active_epoch))
                tx_events = [
                    item for item in state.get("tx_events", [])
                    if isinstance(item, dict) and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
                ]
                tx_events.insert(0, {
                    "callsign": state["last_source_user"],
                    "event": "transmit",
                    "epoch": epoch,
                    "start_epoch": active_epoch,
                    "duration_seconds": duration,
                })
                state["tx_events"] = tx_events[:100]
                state["recent_users"] = [{"callsign": state["last_source_user"], "last_tx_epoch": epoch}]
                state["last_source_epoch"] = epoch
        state.update({"observed": True, "activity_epoch": epoch, "last_event_epoch": epoch})
        return

    if tx_on is not None:
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
            relay_user = clean_caller(state.get("relay_user", ""), 20)
            if relay_user and active_epoch and epoch >= active_epoch:
                duration = max(0.0, float(epoch) - float(active_epoch))
                tx_events = [
                    item for item in state.get("tx_events", [])
                    if isinstance(item, dict) and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
                ]
                tx_events.insert(0, {
                    "callsign": relay_user,
                    "event": "transmit",
                    "epoch": epoch,
                    "start_epoch": active_epoch,
                    "duration_seconds": duration,
                })
                state["tx_events"] = tx_events[:100]
                state["recent_users"] = [{"callsign": relay_user, "last_tx_epoch": epoch}]
                state["last_user"] = relay_user
                state["last_source_user"] = relay_user
                state["last_source_epoch"] = epoch
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "network_relay": False,
                "relay_user": "",
            })
        state.update({"observed": True, "activity_epoch": epoch, "last_event_epoch": epoch})


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


def dstar_runtime_online(
    path: Path = DSTAR_HEALTH_PATH,
    now: int | None = None,
) -> bool:
    current = int(time.time()) if now is None else now
    source = _safe_text(path, 64)
    if source is None:
        return False
    text, details = source
    try:
        heartbeat = int(text.strip())
    except ValueError:
        return False
    return (
        heartbeat > 0
        and heartbeat <= current + 5
        and current - heartbeat <= DSTAR_HEALTH_MAX_AGE_SECONDS
        and current - int(details.st_mtime) <= DSTAR_HEALTH_MAX_AGE_SECONDS
    )


def dstar_link_status(
    path: Path = DSTAR_GATEWAY_LOG_DIR / "Links.log",
) -> dict[str, Any]:
    source = _safe_text(path, 256 * 1024)
    if source is None:
        return {"linked": None, "reflector": "", "module": "", "link_protocol": ""}
    latest: tuple[int, re.Match[str]] | None = None
    for line in source[0].splitlines():
        match = DSTAR_LINK_RE.fullmatch(line.strip())
        if match is None:
            continue
        epoch = match_epoch(match, 0)
        if latest is None or epoch >= latest[0]:
            latest = (epoch, match)
    if latest is None:
        return {"linked": None, "reflector": "", "module": "", "link_protocol": ""}
    match = latest[1]
    linked = match.group(4).lower() == "link"
    return {
        "linked": linked,
        "reflector": match.group(5).upper() if linked else "",
        "module": match.group(6).upper() if linked else "",
        "link_protocol": match.group(3),
    }


def _xml_element(block: str, name: str) -> str:
    match = re.search(
        rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>",
        block,
        re.DOTALL | re.IGNORECASE,
    )
    return html.unescape(match.group(1)).strip() if match else ""


def dstar_reflector_clients(
    path: Path = DSTAR_REFLECTOR_STATUS_PATH,
    local_callsign: str = "",
    now: int | None = None,
) -> list[dict[str, Any]] | None:
    current = int(time.time()) if now is None else now
    source = _safe_text(path)
    if source is None:
        return None
    text, details = source
    if details.st_mtime <= 0 or details.st_mtime > current + 5 or current - details.st_mtime > 30:
        return None
    section = re.search(
        r"<XLX[0-9]{3}\s+linked nodes>(.*?)</XLX[0-9]{3}\s+linked nodes>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if section is None:
        return None
    local = clean_caller(local_callsign, 20).upper()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in re.findall(r"<NODE>(.*?)</NODE>", section.group(1), re.DOTALL | re.IGNORECASE):
        raw_call = clean_caller(_xml_element(node, "Callsign"), 20).upper()
        callsign = re.split(r"[\s-]+", raw_call, maxsplit=1)[0]
        # Exclude only this bridge's own persistent gateway node (normally the
        # local callsign with module B).  A direct station/DroidStar client can
        # legitimately use the same base callsign with a different terminal.
        terminal = raw_call[len(callsign):].strip() if raw_call.startswith(callsign) else ""
        is_local_gateway = callsign == local and terminal == "B"
        if not callsign or is_local_gateway or raw_call in seen:
            continue
        seen.add(raw_call)
        rows.append({
            "callsign": raw_call,
            "module": clean_caller(_xml_element(node, "LinkedModule"), 1).upper(),
            "protocol": clean_caller(_xml_element(node, "Protocol"), 20),
            "connected": True,
        })
    return rows


def astapi_key_states(
    local_node: str,
    bridge_nodes: set[str],
    now: float | None = None,
    cache_dir: Path = ASTAPI_CACHE_DIR,
) -> dict[str, bool | None]:
    """Read explicit tri-state keyed evidence from ASR's fresh ASTAPI cache."""
    result = {node: None for node in bridge_nodes}
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
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = str(row.get("node", ""))
        if node not in result:
            continue
        keyed = str(row.get("keyed", "")).strip().lower()
        if keyed == "yes":
            result[node] = True
        elif keyed == "no":
            result[node] = False
    return result


def all_configured_standard_bridges(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict) or bridge.get("cardType", "standard") != "standard":
            continue
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", bridge_id)).strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", mode)
        mode = (
            "dmr" if compact.startswith("dmr")
            else "ysf" if compact.startswith("ysf")
            else "dstar" if compact.startswith("dstar")
            else ""
        )
        if (
            mode
            and bridge_id not in seen
            and BRIDGE_ID_RE.fullmatch(bridge_id)
            and NODE_RE.fullmatch(str(bridge.get("node", "")))
        ):
            rows.append(bridge)
            seen.add(bridge_id)
    return rows


def configured_standard_bridges(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_mode: dict[str, list[dict[str, Any]]] = {"dmr": [], "ysf": [], "dstar": []}
    for bridge in all_configured_standard_bridges(config):
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", bridge_id)).strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", mode)
        mode = (
            "dmr" if compact.startswith("dmr")
            else "ysf" if compact.startswith("ysf")
            else "dstar" if compact.startswith("dstar")
            else ""
        )
        by_mode[mode].append(bridge)
    # Without an explicit per-card log path, one card per mode is the only
    # unambiguous generic mapping. Multiple same-mode cards retain their feed.
    return {mode: rows[0] for mode, rows in by_mode.items() if len(rows) == 1}


def configured_net_log_stems(config: dict[str, Any]) -> frozenset[str]:
    """Return only MMDVM log stems explicitly named by DMR/YSF Net cards."""
    stems: set[str] = set()
    for bridge in config.get("bridges", []):
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
    ) -> list[str]:
        try:
            directory = self.log_dir.lstat()
            if (
                not stat.S_ISDIR(directory.st_mode)
                or stat.S_IMODE(directory.st_mode) & 0o022
                or (os.geteuid() == 0 and directory.st_uid != 0)
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
                    output.extend(handle.readlines())
                    self.cursors[str(path)] = (int(details.st_ino), handle.tell())
            except OSError:
                continue
        for stale in set(self.cursors) - live_paths:
            self.cursors.pop(stale, None)
        return output


def public_entry(bridge: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    role = str(state.get("role", "idle"))
    caller = clean_caller(str(state.get("current_user", "")), 120) if role == "source" else ""
    mode = str(bridge.get("mode", bridge.get("id", ""))).lower()
    online = state.get("online")
    linked = state.get("linked")
    warning = ""
    health_severity = None
    health_issues: list[str] = []
    if mode == "dstar":
        if online is False:
            health_severity = "offline"
            health_issues.append("D-Star runtime is offline.")
        elif online is None:
            health_severity = "warning"
            health_issues.append("D-Star runtime state is unavailable.")
        if online is True and linked is False:
            health_severity = "unhealthy"
            health_issues.append("D-Star gateway is unlinked.")
        elif online is True and linked is None:
            health_severity = "warning"
            health_issues.append("D-Star link state is unavailable.")
        warning = health_issues[0] if health_issues else ""
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
    dstar_lines: list[str] | None = None,
    dstar_online: bool | None = None,
    dstar_link: dict[str, Any] | None = None,
    dstar_clients: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bridges = configured_standard_bridges(config)
    local_call = clean_caller(str(config.get("callsign", "")), 20).upper()
    local_identities = frozenset(value for value in (local_call, f"{local_call}-RPT" if local_call else "") if value)
    for line in lines:
        for mode in ("dmr", "ysf"):
            bridge = bridges.get(mode)
            if bridge is None:
                continue
            apply_activity_line(
                states.setdefault(str(bridge["id"]), initial_activity_state()),
                line,
                mode,
                now,
                local_identities,
            )
    dstar_bridge = bridges.get("dstar")
    if dstar_bridge is not None:
        dstar_state = states.setdefault(str(dstar_bridge["id"]), initial_activity_state())
        for line in dstar_lines or []:
            apply_activity_line(dstar_state, line, "dstar", now, local_identities)
        dstar_state["online"] = dstar_online if isinstance(dstar_online, bool) else None
        if dstar_link is not None:
            dstar_state.update({
                key: dstar_link.get(key)
                for key in ("linked", "reflector", "module", "link_protocol")
            })
        events = [
            item for item in dstar_state.get("client_events", [])
            if isinstance(item, dict) and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        if dstar_clients is not None:
            current_clients = {
                str(item.get("callsign", "")).strip().upper()
                for item in dstar_clients if isinstance(item, dict) and str(item.get("callsign", "")).strip()
            }
            previous_clients = dstar_state.get("client_snapshot")
            if isinstance(previous_clients, set):
                changes = [(call, "connect") for call in current_clients - previous_clients]
                changes += [(call, "disconnect") for call in previous_clients - current_clients]
                for callsign, event in sorted(changes):
                    events.insert(0, {"callsign": callsign, "event": event, "epoch": now})
            dstar_state["client_snapshot"] = current_clients
            dstar_state["linked_clients"] = list(dstar_clients)
        dstar_state["client_events"] = events[:100]
        dstar_state["tx_events"] = [
            item for item in dstar_state.get("tx_events", [])
            if isinstance(item, dict)
            and int(item.get("epoch", 0) or 0) > 0
            and now - int(item.get("epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        dstar_state["recent_users"] = [
            item for item in dstar_state.get("recent_users", [])
            if isinstance(item, dict)
            and int(item.get("last_tx_epoch", 0) or 0) > 0
            and now - int(item.get("last_tx_epoch", 0) or 0) <= BRIDGE_RECENT_MAX_AGE_SECONDS
        ]
        if (
            int(dstar_state.get("last_source_epoch", 0) or 0) <= 0
            or now - int(dstar_state.get("last_source_epoch", 0) or 0) > BRIDGE_RECENT_MAX_AGE_SECONDS
        ):
            dstar_state["last_user"] = "-"
            dstar_state["last_source_user"] = ""
            dstar_state["last_source_epoch"] = 0
        dstar_state["observed"] = True
        if dstar_state["online"] is False:
            dstar_state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "network_relay": False,
            })
    for bridge in bridges.values():
        state = states.setdefault(str(bridge["id"]), initial_activity_state())
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

    bridge_nodes = {str(bridge["node"]) for bridge in bridges.values()}
    keys = astapi_key_states(str(config.get("node", "")), bridge_nodes, now, cache_dir)
    entries: dict[str, dict[str, Any]] = {}
    for bridge in bridges.values():
        bridge_id = str(bridge["id"])
        state = states.setdefault(bridge_id, initial_activity_state())
        reconcile_keyed_source(state, keys.get(str(bridge["node"])), now)
        if state.get("observed"):
            entries[bridge_id] = public_entry(bridge, state)
    for bridge_id in set(states) - {str(bridge["id"]) for bridge in bridges.values()}:
        states.pop(bridge_id, None)
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


def watch_standard_status(
    config_path: Path = CONFIG_PATH,
    status_path: Path = STANDARD_STATUS_PATH,
    log_dir: Path = MMDVM_LOG_DIR,
    once: bool = False,
) -> None:
    follower = MmdvmFollower(log_dir)
    dstar_follower = MmdvmFollower(DSTAR_MMDVM_LOG_DIR)
    states: dict[str, dict[str, Any]] = {}
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
        atomic_json(status_path, standard_live_payload(
            config,
            states,
            follower.read_lines(configured_net_log_stems(config)),
            now,
            dstar_lines=dstar_follower.read_lines(allowed_stems=frozenset({"MMDVM_Bridge"})),
            dstar_online=dstar_runtime_online(now=now),
            dstar_link=dstar_link_status(),
            dstar_clients=dstar_reflector_clients(local_callsign=str(config.get("callsign", "")), now=now),
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
