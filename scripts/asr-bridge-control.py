#!/usr/bin/env python3
"""Safely control configured ASR DMR Net Bridge cards."""

from __future__ import annotations

import argparse
import calendar
import configparser
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import tempfile
import time

CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
RUN_DIR = Path("/run/allscan-reimagined-bridge-control")
LIVE_STATUS_PATH = RUN_DIR / "bridge-live.json"
AUDIT_LOG = Path("/var/log/allscan-reimagined-bridge-control.log")
HOST_ROOT = Path("/proc/1/root")
ASTERISK_BIN = Path("/usr/sbin/asterisk")
MMDVM_LOG_DIR = Path("/var/log/mmdvm")
BRIDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
LINK_ALIAS_RE = re.compile(r"^999[0-9]{6}$")
ABINFO_RE = re.compile(r"^/tmp/ABInfo_[0-9]{2,5}\.json$")
DVSWITCH_RE = re.compile(r"^/opt/MMDVM_Bridge[A-Za-z0-9_-]+/dvswitch\.sh$")
ANALOG_CONFIG_RE = re.compile(r"^/opt/Analog_Bridge[A-Za-z0-9_-]+/Analog_Bridge\.ini$")
USERNAME_RE = re.compile(r"[^A-Za-z0-9_.@+-]")
MAX_TG = 16_777_215
DISCONNECT_TG = 4000
LIVE_STATUS_INTERVAL = 0.75
LIVE_STATUS_STALE_SECONDS = 180
LIVE_STATUS_INITIAL_TAIL_BYTES = 262_144


class ControlError(RuntimeError):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("ASR bridge configuration could not be read.") from exc
    if not isinstance(payload, dict):
        raise ControlError("ASR bridge configuration is invalid.")
    return payload


def bridge_config(bridge_id: str, path: Path = CONFIG_PATH) -> dict:
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    for bridge in load_config(path).get("bridges", []):
        if isinstance(bridge, dict) and bridge.get("id") == bridge_id:
            if bridge.get("cardType") != "dmr_net":
                raise ControlError("Selected bridge is not a DMR Net Bridge.")
            validate_paths(bridge)
            return bridge
    raise ControlError("Configured DMR Net Bridge was not found.")


def validate_paths(bridge: dict) -> None:
    if not ABINFO_RE.fullmatch(str(bridge.get("abinfoPath", ""))):
        raise ControlError("Configured ABInfo path is not allowed.")
    if not DVSWITCH_RE.fullmatch(str(bridge.get("dvswitchScript", ""))):
        raise ControlError("Configured DVSwitch script path is not allowed.")
    if not ANALOG_CONFIG_RE.fullmatch(str(bridge.get("analogConfig", ""))):
        raise ControlError("Configured Analog Bridge path is not allowed.")


def current_tg(path: Path) -> int | None:
    try:
        contents = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^\s*txTg\s*=\s*(\d+)\b", contents, re.MULTILINE | re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= MAX_TG else None


def runtime_tg(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    digital = payload.get("digital")
    values = [
        payload.get("last_tune"),
        digital.get("tg") if isinstance(digital, dict) else None,
    ]
    for value in values:
        try:
            talkgroup = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= talkgroup <= MAX_TG:
            return talkgroup
    return None


def resolve_abinfo_path(path: Path, host_root: Path = HOST_ROOT) -> Path:
    """Return the host /tmp path when called through Apache PrivateTmp."""
    if not ABINFO_RE.fullmatch(str(path)):
        raise ControlError("Configured ABInfo path is not allowed.")
    host_path = host_root / path.relative_to("/")
    try:
        if host_root.is_dir():
            return host_path
    except OSError:
        pass
    return path


def cached_tg(bridge_id: str) -> int | None:
    try:
        payload = json.loads(
            (RUN_DIR / f"bridge-control-{bridge_id}.json").read_text(encoding="utf-8")
        )
        value = int(payload.get("currentTg", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
        return None
    return value if 1 <= value <= MAX_TG else None


def secure_run_dir() -> None:
    RUN_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory_stat = RUN_DIR.stat()
    if directory_stat.st_uid != 0 or stat.S_IMODE(directory_stat.st_mode) & 0o022:
        raise ControlError(f"{RUN_DIR} must be root-owned and not group/world-writable.")


def remember_tg(bridge_id: str, talkgroup: int) -> None:
    secure_run_dir()
    destination = RUN_DIR / f"bridge-control-{bridge_id}.json"
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=RUN_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"currentTg": str(talkgroup), "updated": int(time.time())}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def forget_tg(bridge_id: str) -> None:
    secure_run_dir()
    try:
        (RUN_DIR / f"bridge-control-{bridge_id}.json").unlink()
    except FileNotFoundError:
        pass


def atomic_json(path: Path, payload: dict, mode: int = 0o644) -> None:
    secure_run_dir()
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mmdvm_log_stem(bridge: dict) -> str:
    validate_paths(bridge)
    stem = Path(str(bridge["dvswitchScript"])).parent.name
    if not re.fullmatch(r"MMDVM_Bridge[A-Za-z0-9_-]+", stem):
        raise ControlError("Configured DMR Net Bridge log name is invalid.")
    return stem


def latest_mmdvm_log(bridge: dict, log_dir: Path = MMDVM_LOG_DIR) -> Path | None:
    stem = mmdvm_log_stem(bridge)
    candidates = [path for path in log_dir.glob(f"{stem}-*.log") if path.is_file()]
    fallback = log_dir / f"{stem}.log"
    if fallback.is_file():
        candidates.append(fallback)
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except (ValueError, OSError):
        return None


def mmdvm_line_epoch(line: str) -> int:
    match = re.search(
        r"[MIWEF]:\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})",
        line,
    )
    if not match:
        return 0
    try:
        return int(
            calendar.timegm(
                time.strptime(
                    f"{match.group(1)} {match.group(2)}",
                    "%Y-%m-%d %H:%M:%S",
                )
            )
        )
    except (OverflowError, ValueError):
        return 0


def clean_live_caller(value: str) -> str:
    caller = re.sub(r"\s+", " ", str(value or "")).strip()
    return caller[:120] if caller and caller != "-" else ""


def initial_live_state() -> dict:
    return {
        "path": "",
        "inode": 0,
        "offset": 0,
        "role": "idle",
        "current_user": "",
        "last_user": "-",
        "active_start_epoch": 0,
        "activity_epoch": 0,
        "last_event_epoch": 0,
    }


def apply_mmdvm_activity_line(state: dict, line: str, now: int) -> None:
    epoch = mmdvm_line_epoch(line) or now
    source = re.search(
        r"DMR Slot \d+,\s+received network "
        r"(?:voice header|late entry) from\s+(.+?)\s+to TG\s+\d+\b",
        line,
        re.IGNORECASE,
    )
    if source:
        caller = clean_live_caller(source.group(1))
        state["role"] = "source"
        state["current_user"] = caller
        if caller:
            state["last_user"] = caller
        state["active_start_epoch"] = (
            int(state.get("active_start_epoch", 0) or 0) or epoch
        )
        state["activity_epoch"] = epoch
        state["last_event_epoch"] = epoch
        return

    if re.search(
        r"DMR Slot \d+,\s+received network end of voice transmission",
        line,
        re.IGNORECASE,
    ):
        if state.get("role") == "source":
            state["role"] = "idle"
            state["current_user"] = ""
            state["active_start_epoch"] = 0
        state["activity_epoch"] = epoch
        state["last_event_epoch"] = epoch
        return

    if re.search(r"\bDMR,\s+TX state\s*=\s*ON\b", line, re.IGNORECASE):
        state["role"] = "relay"
        state["current_user"] = ""
        state["active_start_epoch"] = (
            int(state.get("active_start_epoch", 0) or 0) or epoch
        )
        state["activity_epoch"] = epoch
        state["last_event_epoch"] = epoch
        return

    if re.search(r"\bDMR,\s+TX state\s*=\s*OFF\b", line, re.IGNORECASE):
        if state.get("role") == "relay":
            state["role"] = "idle"
            state["current_user"] = ""
            state["active_start_epoch"] = 0
        state["activity_epoch"] = epoch
        state["last_event_epoch"] = epoch


def refresh_mmdvm_activity(
    bridge: dict,
    state: dict,
    now: int,
    log_dir: Path = MMDVM_LOG_DIR,
) -> dict:
    path = latest_mmdvm_log(bridge, log_dir)
    if path is None:
        return state
    try:
        file_stat = path.stat()
        identity_changed = (
            str(path) != str(state.get("path", ""))
            or int(file_stat.st_ino) != int(state.get("inode", 0) or 0)
            or int(file_stat.st_size) < int(state.get("offset", 0) or 0)
        )
        if identity_changed:
            state = initial_live_state()
            state["path"] = str(path)
            state["inode"] = int(file_stat.st_ino)
            offset = max(0, int(file_stat.st_size) - LIVE_STATUS_INITIAL_TAIL_BYTES)
            skip_partial_line = offset > 0
        else:
            offset = int(state.get("offset", 0) or 0)
            skip_partial_line = False

        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            if skip_partial_line:
                handle.readline()
            for line in handle:
                apply_mmdvm_activity_line(state, line, now)
            state["offset"] = handle.tell()
    except OSError:
        return state

    if (
        state.get("role") in ("source", "relay")
        and now - int(state.get("last_event_epoch", 0) or 0)
        > LIVE_STATUS_STALE_SECONDS
    ):
        state["role"] = "idle"
        state["current_user"] = ""
        state["active_start_epoch"] = 0
    return state


def configured_dmr_net_bridges(path: Path = CONFIG_PATH) -> list[dict]:
    bridges = []
    for bridge in load_config(path).get("bridges", []):
        if not isinstance(bridge, dict) or bridge.get("cardType") != "dmr_net":
            continue
        bridge_id = str(bridge.get("id", ""))
        if not BRIDGE_ID_RE.fullmatch(bridge_id):
            continue
        try:
            validate_paths(bridge)
            node_numbers(bridge, path)
            mmdvm_log_stem(bridge)
        except ControlError:
            continue
        bridges.append(bridge)
    return bridges


def dmr_net_live_payload(
    states: dict[str, dict],
    path: Path = CONFIG_PATH,
    log_dir: Path = MMDVM_LOG_DIR,
) -> dict:
    now = int(time.time())
    entries = {}
    configured_ids = set()
    for bridge in configured_dmr_net_bridges(path):
        bridge_id = str(bridge["id"])
        configured_ids.add(bridge_id)
        state = refresh_mmdvm_activity(
            bridge,
            states.get(bridge_id, initial_live_state()),
            now,
            log_dir,
        )
        states[bridge_id] = state
        talkgroup = cached_tg(bridge_id)
        linked = talkgroup is not None
        role = str(state.get("role", "idle")) if linked else "idle"
        if role not in ("idle", "source", "relay"):
            role = "idle"
        active = role != "idle"
        current_user = (
            clean_live_caller(str(state.get("current_user", "")))
            if role == "source"
            else ""
        )
        entries[bridge_id] = {
            "active": active,
            "role": role,
            "state": (
                "TX ACTIVE"
                if role == "source"
                else ("RELAY" if role == "relay" else ("Idle" if linked else "Disconnected"))
            ),
            "node": str(bridge.get("node", "")),
            "title": str(bridge.get("title", "DMR Net Bridge"))[:80],
            "channel": f"DMR TG {talkgroup}" if talkgroup else "-",
            "active_start_epoch": (
                int(state.get("active_start_epoch", 0) or 0) if active else 0
            ),
            "activity_epoch": int(state.get("activity_epoch", 0) or 0),
            "last_time_epoch": int(state.get("activity_epoch", 0) or 0),
            "warning": "",
            "current_user": current_user,
            "last_user": clean_live_caller(str(state.get("last_user", ""))) or "-",
            "caller": current_user,
            "recent_users": [],
        }
    for bridge_id in set(states) - configured_ids:
        states.pop(bridge_id, None)
    return {
        "updated_epoch": now,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S Local", time.localtime(now)),
        "bridges": entries,
    }


def watch_dmr_net_status(
    path: Path = CONFIG_PATH,
    live_path: Path = LIVE_STATUS_PATH,
    log_dir: Path = MMDVM_LOG_DIR,
    once: bool = False,
) -> None:
    states: dict[str, dict] = {}
    while True:
        payload = dmr_net_live_payload(states, path, log_dir)
        atomic_json(live_path, payload)
        if once:
            return
        time.sleep(LIVE_STATUS_INTERVAL)


def require_secure_root_file(path: Path, label: str, executable: bool = False) -> None:
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ControlError(f"{label} must not be a symbolic link.")
        file_stat = path.stat()
        parent_stat = path.parent.stat()
    except OSError as exc:
        raise ControlError(f"{label} does not exist.") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ControlError(f"{label} is not a regular file.")
    for candidate, candidate_stat in ((path, file_stat), (path.parent, parent_stat)):
        if candidate_stat.st_uid != 0 or stat.S_IMODE(candidate_stat.st_mode) & 0o022:
            raise ControlError(f"{candidate} must be root-owned and not group/world-writable.")
    if executable and not os.access(path, os.X_OK):
        raise ControlError(f"{label} is not executable.")


def public_status(bridge_id: str, path: Path = CONFIG_PATH) -> dict:
    bridge = bridge_config(bridge_id, path)
    node_numbers(bridge, path)
    analog_config = Path(str(bridge["analogConfig"]))
    script = Path(str(bridge["dvswitchScript"]))
    abinfo = resolve_abinfo_path(Path(str(bridge["abinfoPath"])))
    secure_run_dir()
    live_tg = runtime_tg(abinfo)
    tg = None if live_tg == DISCONNECT_TG else (live_tg or cached_tg(bridge_id) or current_tg(analog_config))
    return {
        "ok": True,
        "bridgeId": bridge_id,
        "currentTg": str(tg or ""),
        "ready": script.is_file() and os.access(script, os.X_OK) and analog_config.is_file(),
        "abinfoAvailable": abinfo.is_file(),
    }


def node_numbers(bridge: dict, path: Path = CONFIG_PATH) -> tuple[str, str, str]:
    config = load_config(path)
    local_node = str(config.get("node", ""))
    bridge_node = str(bridge.get("node", ""))
    link_alias = str(bridge.get("linkAlias", ""))
    if not local_node.isdigit() or not bridge_node.isdigit():
        raise ControlError("DMR Net Bridge AllStar node configuration is invalid.")
    if not re.fullmatch(r"[0-9]{3,6}", local_node):
        raise ControlError("DMR Net Bridge local AllStar node configuration is invalid.")
    expected_alias = f"999{int(local_node):06d}"
    if LINK_ALIAS_RE.fullmatch(link_alias) is None or link_alias != expected_alias:
        raise ControlError("DMR Net Bridge AllStar link alias configuration is invalid.")
    if link_alias in (local_node, bridge_node):
        raise ControlError("DMR Net Bridge AllStar link alias must be unique.")
    return local_node, bridge_node, link_alias


def parse_lstats_links(output: str) -> set[tuple[str, str]]:
    if not re.search(
        r"^NODE\s+PEER\s+RECONNECTS\s+DIRECTION\s+CONNECT TIME\s+CONNECT STATE\s*$",
        output,
        re.MULTILINE,
    ):
        raise ControlError("Asterisk direct-link status was not recognized.")
    return set(
        re.findall(
            r"^([A-Za-z0-9_][A-Za-z0-9_-]*)[ \t]+\S+[ \t]+\d+[ \t]+(IN|OUT)[ \t]+",
            output,
            re.MULTILINE,
        )
    )


def asterisk_output(command: str, label: str) -> str:
    try:
        completed = subprocess.run(
            [str(ASTERISK_BIN), "-rx", command],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"{label} could not be read.") from exc
    output = completed.stdout
    if completed.returncode != 0 or not output.strip():
        raise ControlError(f"{label} returned an error.")
    return output


def direct_links(local_node: str) -> set[tuple[str, str]]:
    return parse_lstats_links(
        asterisk_output(f"rpt lstats {local_node}", "Asterisk direct-link status")
    )


def direct_linked(local_node: str, link_alias: str) -> bool:
    return (link_alias, "OUT") in direct_links(local_node)


def set_direct_link(local_node: str, link_alias: str, linked: bool) -> None:
    if direct_linked(local_node, link_alias) == linked:
        return
    ilink = "3" if linked else "11"
    try:
        completed = subprocess.run(
            [str(ASTERISK_BIN), "-rx", f"rpt cmd {local_node} ilink {ilink} {link_alias}"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("Asterisk bridge-link command failed.") from exc
    if completed.returncode != 0:
        raise ControlError("Asterisk bridge-link command returned an error.")
    for _ in range(60):
        if direct_linked(local_node, link_alias) == linked:
            return
        time.sleep(0.25)
    action = "link" if linked else "unlink"
    raise ControlError(f"Asterisk did not confirm the bridge-node {action}.")


def audit(user: str, bridge_id: str, old_tg: int | None, new_tg: int, result: str) -> None:
    safe_user = USERNAME_RE.sub("_", user)[:80] or "unknown"
    safe_result = re.sub(r"[\r\n\t]+", " ", result).strip()[:240]
    line = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} "
        f"user={safe_user} bridge={bridge_id} old_tg={old_tg or 'unknown'} "
        f"new_tg={new_tg} result={safe_result}\n"
    )
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line)
    os.chmod(AUDIT_LOG, 0o640)


def analog_control_port(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        parser = configparser.RawConfigParser(
            inline_comment_prefixes=(";", "#"),
            strict=False,
        )
        parser.read_string("[PREAMBLE]\n" + raw)
        port = parser.getint("AMBE_AUDIO", "rxPort")
    except (OSError, configparser.Error, ValueError) as exc:
        raise ControlError("Analog Bridge control port could not be read.") from exc
    if not 1024 <= port <= 65535:
        raise ControlError("Analog Bridge control port is outside the allowed range.")
    return port


def send_tune_command(port: int, target_tg: int) -> None:
    command = f"txTg={target_tg}".encode("ascii")
    payload = struct.pack("BB", 0x05, len(command)) + command
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            control.sendto(payload, ("127.0.0.1", port))
    except OSError as exc:
        raise ControlError("Analog Bridge tune command could not be sent.") from exc


def tune_with_retry(
    analog_config: Path,
    abinfo: Path,
    target_tg: int,
) -> bool:
    port = analog_control_port(analog_config)
    sent = False
    for _attempt in range(2):
        try:
            before_mtime = abinfo.stat().st_mtime_ns
        except OSError:
            before_mtime = -1
        try:
            send_tune_command(port, target_tg)
            sent = True
        except ControlError:
            continue
        for _ in range(40):
            try:
                refreshed = abinfo.stat().st_mtime_ns > before_mtime
            except OSError:
                refreshed = False
            if refreshed and runtime_tg(abinfo) == target_tg:
                return True
            time.sleep(0.25)
    if not sent:
        raise ControlError("Analog Bridge tune command could not be sent.")
    return False


def connect(bridge_id: str, tg_text: str, user: str, path: Path = CONFIG_PATH) -> dict:
    if not tg_text.isdigit():
        raise ControlError("Talkgroup must contain digits only.")
    target_tg = int(tg_text)
    if not 1 <= target_tg <= MAX_TG:
        raise ControlError(f"Talkgroup must be between 1 and {MAX_TG}.")
    if target_tg == DISCONNECT_TG:
        raise ControlError("TG 4000 is reserved for Disconnect.")

    bridge = bridge_config(bridge_id, path)
    local_node, bridge_node, link_alias = node_numbers(bridge, path)
    script = Path(str(bridge["dvswitchScript"]))
    abinfo = resolve_abinfo_path(Path(str(bridge["abinfoPath"])))
    analog_config = Path(str(bridge["analogConfig"]))
    require_secure_root_file(script, "Configured DVSwitch script", executable=True)
    require_secure_root_file(analog_config, "Configured Analog Bridge file")

    secure_run_dir()
    lock_path = RUN_DIR / f"bridge-control-{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another talkgroup change is already running.") from exc

        old_tg = runtime_tg(abinfo) or cached_tg(bridge_id) or current_tg(analog_config)
        try:
            audit(user, bridge_id, old_tg, target_tg, "attempt")
        except OSError as exc:
            raise ControlError("Bridge-control audit log is unavailable; no tuning command was sent.") from exc
        try:
            tune_verified = tune_with_retry(
                analog_config,
                abinfo,
                target_tg,
            )
        except ControlError as exc:
            try:
                audit(user, bridge_id, old_tg, target_tg, "command failed")
            except OSError:
                pass
            raise

        if not tune_verified:
            try:
                audit(user, bridge_id, old_tg, target_tg, "live verification failed")
            except OSError:
                pass
            raise ControlError("DVSwitch did not report the requested TG in its live ABInfo state.")

        cache_warning = ""
        try:
            remember_tg(bridge_id, target_tg)
        except (ControlError, OSError):
            cache_warning = " The local TG status cache could not be updated."
        try:
            set_direct_link(local_node, link_alias, True)
        except ControlError as link_error:
            rollback_verified = False
            try:
                try:
                    set_direct_link(local_node, link_alias, False)
                except ControlError:
                    pass
                rollback_verified = tune_with_retry(
                    analog_config,
                    abinfo,
                    DISCONNECT_TG,
                )
                rollback_result = (
                    "link failed; DMR disconnect rollback success"
                    if rollback_verified
                    else "link failed; DMR disconnect rollback unconfirmed"
                )
            except ControlError:
                rollback_result = "link failed; DMR disconnect rollback command failed"
            try:
                forget_tg(bridge_id)
            except (ControlError, OSError):
                rollback_result += "; local status cache cleanup failed"
            try:
                audit(user, bridge_id, target_tg, DISCONNECT_TG, rollback_result)
            except OSError:
                pass
            if rollback_verified:
                raise ControlError(
                    "The AllStar bridge link failed; DMR was returned to Disconnect."
                ) from link_error
            raise ControlError(
                "The AllStar bridge link failed, and DMR Disconnect could not be confirmed."
            ) from link_error

        audit_warning = ""
        try:
            audit(user, bridge_id, old_tg, target_tg, "success")
        except OSError:
            audit_warning = " The initial audit entry was saved, but the final result entry could not be written."
        return {
            "ok": True,
            "bridgeId": bridge_id,
            "oldTg": str(old_tg or ""),
            "currentTg": str(target_tg),
            "linked": True,
            "node": bridge_node,
            "message": (
                f"DMR Net Bridge connected to TG {target_tg} and node {bridge_node} linked."
                f"{cache_warning}{audit_warning}"
            ),
        }


def disconnect(bridge_id: str, user: str, path: Path = CONFIG_PATH) -> dict:
    bridge = bridge_config(bridge_id, path)
    local_node, bridge_node, link_alias = node_numbers(bridge, path)
    script = Path(str(bridge["dvswitchScript"]))
    abinfo = resolve_abinfo_path(Path(str(bridge["abinfoPath"])))
    analog_config = Path(str(bridge["analogConfig"]))
    require_secure_root_file(script, "Configured DVSwitch script", executable=True)
    require_secure_root_file(analog_config, "Configured Analog Bridge file")

    secure_run_dir()
    lock_path = RUN_DIR / f"bridge-control-{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another bridge-control action is already running.") from exc

        old_tg = runtime_tg(abinfo) or cached_tg(bridge_id) or current_tg(analog_config)
        try:
            audit(user, bridge_id, old_tg, DISCONNECT_TG, "disconnect attempt")
        except OSError as exc:
            raise ControlError("Bridge-control audit log is unavailable; no disconnect command was sent.") from exc

        tune_verified = False
        tune_error: ControlError | None = None
        try:
            tune_verified = tune_with_retry(
                analog_config,
                abinfo,
                DISCONNECT_TG,
            )
        except ControlError as exc:
            tune_error = exc

        unlink_error: ControlError | None = None
        try:
            set_direct_link(local_node, link_alias, False)
        except ControlError as exc:
            unlink_error = exc

        cache_warning = ""
        try:
            forget_tg(bridge_id)
        except (ControlError, OSError):
            cache_warning = " The local TG status cache could not be cleared."
        if not tune_verified or unlink_error is not None:
            if not tune_verified and unlink_error is None:
                failure = "AllStar node unlinked; DMR disconnect was not confirmed"
                message = (
                    f"Node {bridge_node} was unlinked, but DMR Disconnect could not be confirmed."
                )
            elif tune_verified and unlink_error is not None:
                failure = "DMR disconnect confirmed; AllStar node unlink was not confirmed"
                message = (
                    f"DMR disconnected, but Asterisk did not confirm node {bridge_node} was unlinked."
                )
            else:
                failure = "DMR disconnect and AllStar node unlink were not confirmed"
                message = (
                    f"Neither DMR Disconnect nor the node {bridge_node} unlink could be confirmed."
                )
            try:
                audit(user, bridge_id, old_tg, DISCONNECT_TG, failure)
            except OSError:
                pass
            if tune_error is not None:
                raise ControlError(message) from tune_error
            if unlink_error is not None:
                raise ControlError(message) from unlink_error
            raise ControlError(message)

        audit_warning = ""
        try:
            audit(user, bridge_id, old_tg, DISCONNECT_TG, "disconnect success")
        except OSError:
            audit_warning = " The initial audit entry was saved, but the final result entry could not be written."
        return {
            "ok": True,
            "bridgeId": bridge_id,
            "oldTg": str(old_tg or ""),
            "currentTg": "",
            "linked": False,
            "node": bridge_node,
            "message": (
                f"DMR Net Bridge disconnected from its talkgroup and node {bridge_node} unlinked."
                f"{cache_warning}{audit_warning}"
            ),
        }


def self_test() -> None:
    assert ABINFO_RE.fullmatch("/tmp/ABInfo_34004.json")
    assert DVSWITCH_RE.fullmatch("/opt/MMDVM_Bridge_TGIFNet/dvswitch.sh")
    assert ANALOG_CONFIG_RE.fullmatch("/opt/Analog_Bridge_TGIFNet/Analog_Bridge.ini")
    assert LINK_ALIAS_RE.fullmatch("999674982")
    assert not LINK_ALIAS_RE.fullmatch("1884")
    assert not LINK_ALIAS_RE.fullmatch("999674982;reload")
    assert not DVSWITCH_RE.fullmatch("/opt/MMDVM_Bridge/dvswitch.sh")
    assert not ANALOG_CONFIG_RE.fullmatch("/opt/Analog_Bridge/Analog_Bridge.ini")
    assert DISCONNECT_TG == 4000
    assert ASTERISK_BIN == Path("/usr/sbin/asterisk")
    assert not DVSWITCH_RE.fullmatch("/tmp/dvswitch.sh")
    lstats = """\
NODE      PEER                RECONNECTS  DIRECTION  CONNECT TIME        CONNECT STATE
----      ----                ----------  ---------  ------------        -------------
1884      127.0.0.1           0           OUT        00:01:00:000        ESTABLISHED
"""
    assert parse_lstats_links(lstats) == {("1884", "OUT")}
    alias_lstats = lstats.replace("1884      ", "999674982 ")
    assert parse_lstats_links(alias_lstats) == {("999674982", "OUT")}
    inbound = lstats.replace(
        "127.0.0.1           0           OUT",
        "10.0.0.1            0           IN",
    )
    assert parse_lstats_links(inbound) == {("1884", "IN")}
    assert ("1884", "OUT") not in parse_lstats_links(inbound)
    assert parse_lstats_links(lstats.splitlines()[0] + "\n") == set()
    with tempfile.TemporaryDirectory(prefix="asr-bridge-control-") as directory:
        config = Path(directory) / "Analog_Bridge.ini"
        config.write_text(
            "include=/opt/Analog_Bridge_TGIFNet/Analog_Bridge.ini\n"
            "[AMBE_AUDIO]\n"
            "rxPort=31200 ; local control\n"
            "txTg=86753\n",
            encoding="utf-8",
        )
        assert analog_control_port(config) == 31200
        assert current_tg(config) == 86753
        config.write_text("[AMBE_AUDIO]\ntxTg=999999999\n", encoding="utf-8")
        assert current_tg(config) is None
        abinfo = Path(directory) / "ABInfo.json"
        abinfo.write_text('{"last_tune":"86753","digital":{"tg":"86753"}}\n', encoding="utf-8")
        assert runtime_tg(abinfo) == 86753
        abinfo.write_text('{"last_tune":"4000","digital":{"tg":"4000"}}\n', encoding="utf-8")
        assert runtime_tg(abinfo) == DISCONNECT_TG
        configured_abinfo = Path("/tmp/ABInfo_34004.json")
        fake_host_root = Path(directory) / "host-root"
        host_abinfo = fake_host_root / "tmp/ABInfo_34004.json"
        host_abinfo.parent.mkdir(parents=True)
        assert resolve_abinfo_path(configured_abinfo, fake_host_root) == host_abinfo
        host_abinfo.write_text('{"last_tune":"86753"}\n', encoding="utf-8")
        assert resolve_abinfo_path(configured_abinfo, fake_host_root) == host_abinfo
        assert resolve_abinfo_path(
            configured_abinfo,
            Path(directory) / "missing-host-root",
        ) == configured_abinfo
        try:
            resolve_abinfo_path(Path("/tmp/not-abinfo.json"), fake_host_root)
        except ControlError:
            pass
        else:
            raise AssertionError("Unsafe ABInfo path was accepted.")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            send_tune_command(receiver.getsockname()[1], 86753)
            packet, sender = receiver.recvfrom(128)
            assert sender[0] == "127.0.0.1"
            assert packet == bytes((0x05, len(b"txTg=86753"))) + b"txTg=86753"
        runtime_config = Path(directory) / "config.json"
        runtime_config.write_text(
            json.dumps(
                {
                    "node": "674982",
                    "bridges": [
                        {
                            "id": "dmr_net",
                            "node": "1884",
                            "linkAlias": "999674982",
                            "cardType": "dmr_net",
                            "abinfoPath": "/tmp/ABInfo_34004.json",
                            "dvswitchScript": "/opt/MMDVM_Bridge_TGIFNet/dvswitch.sh",
                            "analogConfig": "/opt/Analog_Bridge_TGIFNet/Analog_Bridge.ini",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        bridge = json.loads(runtime_config.read_text(encoding="utf-8"))["bridges"][0]
        assert node_numbers(bridge, runtime_config) == (
            "674982",
            "1884",
            "999674982",
        )
        bridge["linkAlias"] = "999123456"
        try:
            node_numbers(bridge, runtime_config)
        except ControlError:
            pass
        else:
            raise AssertionError("A link alias for another local node was accepted.")
        log_dir = Path(directory) / "mmdvm"
        log_dir.mkdir()
        log_path = log_dir / "MMDVM_Bridge_TGIFNet-2026-07-23.log"
        log_path.write_text(
            "M: 2026-07-23 19:30:51.274 DMR Slot 2, received network voice header "
            "from KE7WIL to TG 86753\n",
            encoding="utf-8",
        )
        activity = refresh_mmdvm_activity(
            bridge,
            initial_live_state(),
            1_784_835_052,
            log_dir,
        )
        assert activity["role"] == "source"
        assert activity["current_user"] == "KE7WIL"
        assert activity["active_start_epoch"] == 1_784_835_051
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "M: 2026-07-23 19:31:02.301 DMR Slot 2, received network end "
                "of voice transmission, 11.3 seconds\n"
            )
            handle.write("M: 2026-07-23 19:31:13.770 DMR, TX state = ON\n")
        activity = refresh_mmdvm_activity(
            bridge,
            activity,
            1_784_835_074,
            log_dir,
        )
        assert activity["role"] == "relay"
        assert activity["current_user"] == ""
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "M: 2026-07-23 19:31:21.000 DMR, TX state = OFF, "
                "DMR frame count was 100 frames\n"
            )
        activity = refresh_mmdvm_activity(
            bridge,
            activity,
            1_784_835_082,
            log_dir,
        )
        assert activity["role"] == "idle"
        assert activity["last_user"] == "KE7WIL"
    print("DMR Net Bridge control self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status-json", metavar="BRIDGE_ID")
    parser.add_argument("--connect", nargs=2, metavar=("BRIDGE_ID", "TG"))
    parser.add_argument("--disconnect", metavar="BRIDGE_ID")
    parser.add_argument("--watch-status", action="store_true")
    parser.add_argument("--status-once", action="store_true")
    parser.add_argument("--user", default="unknown")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    try:
        if args.self_test:
            self_test()
            return 0
        if args.status_json:
            print(json.dumps(public_status(args.status_json), separators=(",", ":")))
            return 0
        if args.connect:
            print(json.dumps(connect(args.connect[0], args.connect[1], args.user), separators=(",", ":")))
            return 0
        if args.disconnect:
            print(json.dumps(disconnect(args.disconnect, args.user), separators=(",", ":")))
            return 0
        if args.watch_status:
            watch_dmr_net_status()
            return 0
        if args.status_once:
            watch_dmr_net_status(once=True)
            return 0
        raise ControlError("Select a bridge-control action.")
    except ControlError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
