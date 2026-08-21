#!/usr/bin/env python3
"""Shared, evidence-based DMR/YSF activity helpers for AllScan Reimagined."""

from __future__ import annotations

import calendar
import configparser
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
DMR_TX_OFF_RE = re.compile(LINE_PREFIX + r"DMR,\s+TX state\s*=\s*OFF\b", re.IGNORECASE)
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
YSF_TX_OFF_RE = re.compile(LINE_PREFIX + r"YSF,\s+TX state\s*=\s*OFF\b", re.IGNORECASE)
KEYED_SAMPLE_GRACE_SECONDS = 3.0
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
    }


def watchdog_event(line: str, mode: str, now: int) -> tuple[int, int] | None:
    """Return anchored watchdog evidence as (epoch, DMR slot or zero)."""
    match = DMR_END_RE.fullmatch(line.rstrip("\r\n")) if mode == "dmr" else YSF_END_RE.fullmatch(line.rstrip("\r\n"))
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
    else:
        return
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
        if same_stream and epoch >= active_epoch:
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "source_slot": 0,
                "network_relay": False,
            })
            if state.get("last_source_user"):
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
        })
        return

    if tx_off is not None:
        if state.get("role") == "relay":
            state.update({
                "role": "idle",
                "current_user": "",
                "active_start_epoch": 0,
                "network_relay": False,
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
        mode = "dmr" if compact.startswith("dmr") else ("ysf" if compact.startswith("ysf") else "")
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
    by_mode: dict[str, list[dict[str, Any]]] = {"dmr": [], "ysf": []}
    for bridge in all_configured_standard_bridges(config):
        bridge_id = str(bridge.get("id", ""))
        mode = str(bridge.get("mode", bridge_id)).strip().lower()
        compact = re.sub(r"[^a-z0-9]", "", mode)
        mode = "dmr" if compact.startswith("dmr") else ("ysf" if compact.startswith("ysf") else "")
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
        "recent_users": [],
    }


def standard_live_payload(
    config: dict[str, Any],
    states: dict[str, dict[str, Any]],
    lines: list[str],
    now: int,
    cache_dir: Path = ASTAPI_CACHE_DIR,
) -> dict[str, Any]:
    bridges = configured_standard_bridges(config)
    local_call = clean_caller(str(config.get("callsign", "")), 20).upper()
    local_identities = frozenset(value for value in (local_call, f"{local_call}-RPT" if local_call else "") if value)
    for line in lines:
        for mode, bridge in bridges.items():
            bridge_id = str(bridge["id"])
            apply_activity_line(
                states.setdefault(bridge_id, initial_activity_state()),
                line,
                mode,
                now,
                local_identities,
            )
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
