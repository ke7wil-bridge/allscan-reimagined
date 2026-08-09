#!/usr/bin/env python3
"""Safely control and cache status for configured ASR YSF Net Bridge cards."""

from __future__ import annotations

import argparse
import calendar
import configparser
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
RUN_DIR = Path("/run/allscan-reimagined-ysf-bridge-control")
STATUS_PATH = RUN_DIR / "ysf-live.json"
AUDIT_LOG = Path("/var/log/allscan-reimagined/ysf-bridge-control.log")
CUSTOM_HOSTS_DIR = Path("/var/lib/allscan-reimagined/ysf-hosts")
ASTERISK_BIN = Path("/usr/sbin/asterisk")
SYSTEMCTL_BIN = Path("/usr/bin/systemctl")
MMDVM_LOG_DIR = Path("/var/log/mmdvm")
BRIDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
NODE_RE = re.compile(r"^[0-9]{3,10}$")
DESTINATION_RE = re.compile(r"^[0-9]{5}$")
GATEWAY_CONFIG_RE = re.compile(
    r"^/opt/YSFGateway_([A-Za-z0-9_-]+)/YSFGateway\.ini$"
)
MMDVM_CONFIG_RE = re.compile(
    r"^/opt/MMDVM_Bridge_([A-Za-z0-9_-]+)/MMDVM_Bridge\.ini$"
)
HOSTS_RE = re.compile(r"^/var/lib/mmdvm/[A-Za-z0-9_.-]*YSF[A-Za-z0-9_.-]*Hosts[A-Za-z0-9_.-]*$")
CUSTOM_HOSTS_RE = re.compile(
    r"^/var/lib/allscan-reimagined/ysf-hosts/[a-z][a-z0-9_-]{1,31}-YSFHosts\.txt$"
)
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9@_.-]{0,79}\.service$")
USERNAME_RE = re.compile(r"[^A-Za-z0-9_.@+-]")
CUSTOM_NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9 _.-]{0,15}$")
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
LOG_ROOT_RE = re.compile(r"^YSFGateway_[A-Za-z0-9_-]+$")
MMDVM_LOG_ROOT_RE = re.compile(r"^MMDVM_Bridge_[A-Za-z0-9_-]+$")
YSF_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,10}$")
YSF_SUFFIX_RE = re.compile(r"^[A-Z0-9]{1,5}$")
WATCH_INTERVAL = 1.0
ACTIVITY_STALE_SECONDS = 180
SOURCE_RETENTION_SECONDS = 300
VERIFY_TIMEOUT = 10.0
STATE_TRANSITION_GRACE_SECONDS = 15
TAIL_BYTES = 262_144
MAX_CUSTOM_REFLECTORS = 32
PUBLIC_HOSTS_URL = "https://hostfiles.refcheck.radio/YSFHosts.txt"
MAX_PUBLIC_HOSTS_BYTES = 2_000_000
MIN_PUBLIC_DESTINATIONS = 1000


class ControlError(RuntimeError):
    pass


def ini_parser(path: Path) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(
        inline_comment_prefixes=(";", "#"), strict=False
    )
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ControlError("Configured YSF INI file could not be read.") from exc
    return parser


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("ASR bridge configuration could not be read.") from exc
    if not isinstance(payload, dict):
        raise ControlError("ASR bridge configuration is invalid.")
    return payload


def validate_service(value: object, label: str) -> str:
    service = str(value or "")
    if not SERVICE_RE.fullmatch(service):
        raise ControlError(f"Configured {label} service name is invalid.")
    return service


def validate_custom_host(value: object) -> str:
    host = str(value or "").strip()
    if not host or any(character in host for character in ";\r\n\0/\\[]@"):
        raise ControlError("Custom YSF reflector host is invalid.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOSTNAME_RE.fullmatch(host):
            raise ControlError("Custom YSF reflector host is invalid.")
    return host


def validate_custom_reflectors(value: object) -> list[dict]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_CUSTOM_REFLECTORS:
        raise ControlError("Custom YSF reflector configuration is invalid.")
    reflectors: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ControlError("Custom YSF reflector configuration is invalid.")
        destination = str(entry.get("id", "")).strip()
        name = re.sub(r"\s+", " ", str(entry.get("name", "")).strip()).upper()
        host = validate_custom_host(entry.get("host"))
        try:
            port = int(entry.get("port"))
        except (TypeError, ValueError) as exc:
            raise ControlError("Custom YSF reflector port is invalid.") from exc
        description = clean_name(str(entry.get("description", "Custom ASR reflector")), 120)
        if not DESTINATION_RE.fullmatch(destination) or destination == "00000":
            raise ControlError("Custom YSF reflector ID must be five digits and cannot be 00000.")
        if not CUSTOM_NAME_RE.fullmatch(name) or DESTINATION_RE.fullmatch(name):
            raise ControlError("Custom YSF reflector name must use 1-16 letters, numbers, spaces, dots, dashes, or underscores.")
        if not 1 <= port <= 65535:
            raise ControlError("Custom YSF reflector port must be between 1 and 65535.")
        if any(character in description for character in ";\r\n\0"):
            raise ControlError("Custom YSF reflector description is invalid.")
        folded = name.casefold()
        if destination in seen_ids or folded in seen_names:
            raise ControlError("Custom YSF reflector IDs and names must be unique within each bridge.")
        seen_ids.add(destination)
        seen_names.add(folded)
        reflectors.append({
            "id": destination,
            "name": name,
            "host": host,
            "port": port,
            "description": description or "Custom ASR reflector",
        })
    return reflectors


def validate_bridge(bridge: dict, config: dict) -> dict:
    bridge_id = str(bridge.get("id", ""))
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    if bridge.get("cardType") != "ysf_net":
        raise ControlError("Selected bridge is not a YSF Net Bridge.")

    local_node = str(config.get("node", ""))
    bridge_node = str(bridge.get("node", ""))
    if not NODE_RE.fullmatch(local_node) or not NODE_RE.fullmatch(bridge_node):
        raise ControlError("YSF Net Bridge AllStar node configuration is invalid.")
    if local_node == bridge_node:
        raise ControlError("YSF Net Bridge node must be separate from the main node.")

    gateway_text = str(bridge.get("ysfGatewayConfig", ""))
    mmdvm_text = str(bridge.get("mmdvmConfig", ""))
    gateway_match = GATEWAY_CONFIG_RE.fullmatch(gateway_text)
    mmdvm_match = MMDVM_CONFIG_RE.fullmatch(mmdvm_text)
    if not gateway_match or not mmdvm_match:
        raise ControlError("Configured YSF Net Bridge paths are not allowed.")
    if gateway_match.group(1).lower() != mmdvm_match.group(1).lower():
        raise ControlError("Configured YSF Gateway and MMDVM instances do not match.")
    if str(bridge.get("commandTransport", "")) != "remote_command":
        raise ControlError("Configured YSF command transport is not allowed.")
    if "allowTune" in bridge and not isinstance(bridge["allowTune"], bool):
        raise ControlError("Configured YSF tuning permission is invalid.")
    custom_reflectors = validate_custom_reflectors(bridge.get("ysfCustomReflectors", []))
    if custom_reflectors and not HOSTS_RE.fullmatch(str(bridge.get("ysfHostsPath", ""))):
        raise ControlError("Custom YSF reflectors require the updater-owned YSF source hosts path.")
    for other in config.get("bridges", []):
        if not isinstance(other, dict) or other is bridge:
            continue
        if str(other.get("node", "")) == bridge_node:
            raise ControlError("YSF Net Bridge node overlaps another configured bridge.")
        if gateway_text and str(other.get("ysfGatewayConfig", "")) == gateway_text:
            raise ControlError("YSF Gateway instance overlaps another configured bridge.")
        if mmdvm_text and str(other.get("mmdvmConfig", "")) == mmdvm_text:
            raise ControlError("MMDVM instance overlaps another configured bridge.")

    services = {
        "gateway": validate_service(bridge.get("ysfGatewayService"), "YSF Gateway"),
        "mmdvm": validate_service(bridge.get("mmdvmService"), "MMDVM Bridge"),
    }
    for key, label in (
        ("analogBridgeService", "Analog Bridge"),
        ("emulatorService", "emulator"),
    ):
        if bridge.get(key) not in (None, ""):
            services[key] = validate_service(bridge.get(key), label)

    return {
        **bridge,
        "id": bridge_id,
        "localNode": local_node,
        "node": bridge_node,
        "gatewayPath": Path(gateway_text),
        "mmdvmPath": Path(mmdvm_text),
        "remoteCommand": Path(mmdvm_text).parent / "RemoteCommand",
        "services": services,
        "ysfCustomReflectors": custom_reflectors,
    }


def bridge_config(bridge_id: str, path: Path = CONFIG_PATH) -> dict:
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    config = load_config(path)
    for bridge in config.get("bridges", []):
        if isinstance(bridge, dict) and bridge.get("id") == bridge_id:
            return validate_bridge(bridge, config)
    raise ControlError("Configured YSF Net Bridge was not found.")


def configured_bridges(path: Path = CONFIG_PATH) -> list[dict]:
    config = load_config(path)
    found = []
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict) or bridge.get("cardType") != "ysf_net":
            continue
        try:
            found.append(validate_bridge(bridge, config))
        except ControlError:
            continue
    return found


def require_secure_root_file(path: Path, label: str, executable: bool = False) -> None:
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ControlError(f"{label} must not be a symbolic link.")
        file_info = path.stat()
        parent_info = path.parent.stat()
    except OSError as exc:
        raise ControlError(f"{label} does not exist.") from exc
    if not stat.S_ISREG(file_info.st_mode):
        raise ControlError(f"{label} is not a regular file.")
    for candidate, info in ((path, file_info), (path.parent, parent_info)):
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise ControlError(f"{candidate} must be root-owned and not group/world-writable.")
    if executable and not os.access(path, os.X_OK):
        raise ControlError(f"{label} is not executable.")


def require_secure_config_file(path: Path) -> None:
    """Allow the authenticated Settings group to write config, never the world."""
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ControlError("ASR bridge configuration must not be a symbolic link.")
        file_info = path.stat()
        parent_info = path.parent.stat()
    except OSError as exc:
        raise ControlError("ASR bridge configuration does not exist.") from exc
    if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != 0:
        raise ControlError("ASR bridge configuration must be a root-owned regular file.")
    if stat.S_IMODE(file_info.st_mode) & 0o002:
        raise ControlError("ASR bridge configuration must not be world-writable.")
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o002:
        raise ControlError("ASR configuration directory must be root-owned and not world-writable.")


def secure_run_dir() -> None:
    RUN_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    info = RUN_DIR.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise ControlError(f"{RUN_DIR} must be root-owned and not group/world-writable.")


def atomic_json(path: Path, payload: object) -> None:
    secure_run_dir()
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


def atomic_root_text(path: Path, content: str, mode: int = 0o644) -> bool:
    if not path.parent.exists():
        if path.parent != CUSTOM_HOSTS_DIR:
            raise ControlError(f"{path.parent} does not exist.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        os.chown(path.parent, 0, 0)
        os.chmod(path.parent, 0o755)
    try:
        parent_info = path.parent.stat()
    except OSError as exc:
        raise ControlError(f"{path.parent} could not be inspected.") from exc
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise ControlError(f"{path.parent} must be root-owned and not group/world-writable.")
    try:
        if path.read_text(encoding="utf-8", errors="replace") == content:
            return False
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def custom_hosts_path(bridge_id: str) -> Path:
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    path = CUSTOM_HOSTS_DIR / f"{bridge_id}-YSFHosts.txt"
    if not CUSTOM_HOSTS_RE.fullmatch(str(path)):
        raise ControlError("Managed YSF hosts path is invalid.")
    return path


def gateway_configuration(bridge: dict) -> dict:
    parser = ini_parser(bridge["gatewayPath"])
    try:
        enabled = parser.getint("Remote Commands", "Enable")
        port = parser.getint("Remote Commands", "Port")
        hosts = parser.get("YSF Network", "Hosts").strip()
        log_path = parser.get("Log", "FilePath").strip()
        log_root = parser.get("Log", "FileRoot").strip()
        local_identities = gateway_local_identities(parser)
    except (configparser.Error, ValueError) as exc:
        raise ControlError("YSF Gateway control configuration is incomplete.") from exc
    if enabled != 1 or not 1024 <= port <= 65535:
        raise ControlError("YSF Gateway remote commands are not safely enabled.")
    if not HOSTS_RE.fullmatch(hosts) and not CUSTOM_HOSTS_RE.fullmatch(hosts):
        raise ControlError("Configured YSF hosts path is not allowed.")
    if log_path != "/var/log/mmdvm" or not LOG_ROOT_RE.fullmatch(log_root):
        raise ControlError("Configured YSF Gateway log path is not allowed.")
    return {
        "port": port,
        "hosts": Path(hosts),
        "logDir": Path(log_path),
        "logRoot": log_root,
        "localIdentities": local_identities,
    }


def gateway_local_identities(parser: configparser.ConfigParser) -> frozenset[str]:
    try:
        callsign = parser.get("General", "Callsign").strip().upper()
        suffix = parser.get("General", "Suffix", fallback="").strip().upper()
    except configparser.Error as exc:
        raise ControlError("YSF Gateway identity configuration is incomplete.") from exc
    if not YSF_CALLSIGN_RE.fullmatch(callsign):
        raise ControlError("Configured YSF Gateway callsign is invalid.")
    if suffix and not YSF_SUFFIX_RE.fullmatch(suffix):
        raise ControlError("Configured YSF Gateway suffix is invalid.")
    identities = {callsign}
    if suffix:
        identities.add(f"{callsign}-{suffix}")
    return frozenset(identities)


def source_hosts_path(bridge: dict, current_hosts: Path) -> Path:
    explicit = str(bridge.get("ysfHostsPath", "")).strip()
    if explicit:
        if not HOSTS_RE.fullmatch(explicit):
            raise ControlError("Configured YSF source hosts path is not allowed.")
        return Path(explicit)
    if HOSTS_RE.fullmatch(str(current_hosts)):
        return current_hosts
    raise ControlError("YSF source hosts path is required for custom reflectors.")


def gateway_settings(bridge: dict) -> dict:
    settings = gateway_configuration(bridge)
    source_hosts = source_hosts_path(bridge, settings["hosts"])
    expected_hosts = (
        custom_hosts_path(bridge["id"])
        if bridge.get("ysfCustomReflectors")
        else source_hosts
    )
    if settings["hosts"] != expected_hosts:
        raise ControlError("YSF custom reflector catalog has not been applied; run the ASR reapply service.")
    settings["sourceHosts"] = source_hosts
    return settings


def mmdvm_settings(bridge: dict) -> dict:
    parser = ini_parser(bridge["mmdvmPath"])
    try:
        log_path = parser.get("Log", "FilePath").strip()
        log_root = parser.get("Log", "FileRoot").strip()
    except configparser.Error as exc:
        raise ControlError("MMDVM Bridge log configuration is incomplete.") from exc
    if log_path != "/var/log/mmdvm" or not MMDVM_LOG_ROOT_RE.fullmatch(log_root):
        raise ControlError("Configured MMDVM log path is not allowed.")
    return {"logDir": Path(log_path), "logRoot": log_root}


def latest_log(log_dir: Path, root: str) -> Path | None:
    candidates = [p for p in log_dir.glob(f"{root}-*.log") if p.is_file()]
    plain = log_dir / f"{root}.log"
    if plain.is_file():
        candidates.append(plain)
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except (ValueError, OSError):
        return None


def line_epoch(line: str) -> int:
    match = re.search(
        r"[MIWEF]:\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", line
    )
    if not match:
        return 0
    try:
        return int(calendar.timegm(time.strptime(" ".join(match.groups()), "%Y-%m-%d %H:%M:%S")))
    except (OverflowError, ValueError):
        return 0


def clean_name(value: str, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def parse_destinations(path: Path) -> list[dict]:
    destinations = []
    seen = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise ControlError("YSF reflector list could not be read.") from exc
    for line in lines:
        if not line or line.startswith("#"):
            continue
        fields = line.split(";")
        if len(fields) < 5:
            continue
        destination = fields[0].strip()
        name = clean_name(fields[1])
        description = clean_name(fields[2], 120)
        if not DESTINATION_RE.fullmatch(destination) or not name or destination in seen:
            continue
        seen.add(destination)
        destinations.append({"id": destination, "name": name, "description": description})
    return sorted(destinations, key=lambda row: (row["name"].casefold(), row["id"]))


def download_public_hosts(node: str) -> str:
    request = urllib.request.Request(
        PUBLIC_HOSTS_URL,
        headers={"User-Agent": (
            f"AllScan-Reimagined-YSF/{node or 'unknown'} "
            "(+https://github.com/ke7wil-bridge/allscan-reimagined)"
        )},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(MAX_PUBLIC_HOSTS_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise ControlError("The public YSF hostfile could not be downloaded; the existing catalog was kept.") from exc
    if len(content) > MAX_PUBLIC_HOSTS_BYTES:
        raise ControlError("The public YSF hostfile exceeded the safety limit; the existing catalog was kept.")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("The public YSF hostfile was not valid UTF-8; the existing catalog was kept.") from exc


def refresh_public_hosts(path: Path = CONFIG_PATH) -> dict:
    config = load_config(path)
    bridges = configured_bridges(path)
    targets = sorted({
        source_hosts_path(bridge, gateway_configuration(bridge)["hosts"])
        for bridge in bridges
    })
    if not targets:
        return {"updated": False, "destinations": 0, "paths": []}
    content = download_public_hosts(str(config.get("node", "")))
    with tempfile.TemporaryDirectory() as raw_tmp:
        candidate = Path(raw_tmp) / "YSFHosts.txt"
        candidate.write_text(content, encoding="utf-8")
        destinations = parse_destinations(candidate)
    if len(destinations) < MIN_PUBLIC_DESTINATIONS:
        raise ControlError("The downloaded public YSF hostfile failed validation; the existing catalog was kept.")
    updated_paths = [str(target) for target in targets if atomic_root_text(target, content)]
    sync_all_custom_catalogs(path)
    return {"updated": bool(updated_paths), "destinations": len(destinations), "paths": updated_paths}


def merged_hosts_content(source: Path, custom_reflectors: list[dict]) -> str:
    try:
        base = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ControlError("YSF source reflector list could not be read.") from exc
    official = parse_destinations(source)
    if not official:
        raise ControlError("YSF source reflector list is not the supported semicolon format.")
    by_id = {item["id"]: item for item in official}
    by_name: dict[str, list[dict]] = {}
    for item in official:
        by_name.setdefault(item["name"].casefold(), []).append(item)
    custom_lines = []
    for item in custom_reflectors:
        official_id = by_id.get(item["id"])
        official_names = by_name.get(item["name"].casefold(), [])
        if official_id is not None:
            if official_id["name"].casefold() == item["name"].casefold():
                continue
            raise ControlError(
                f"Custom YSF reflector {item['name']} conflicts with installed reflector ID {item['id']}."
            )
        if official_names:
            choices = ", ".join(sorted(entry["id"] for entry in official_names))
            raise ControlError(
                f"Custom YSF reflector name {item['name']} conflicts with installed reflector ID {choices}."
            )
        custom_lines.append(
            ";".join((
                item["id"], item["name"], item["description"],
                item["host"], str(item["port"]), "000", "",
            ))
        )
    normalized = base.rstrip("\r\n") + "\n"
    if custom_lines:
        normalized += (
            "# BEGIN ALLSCAN REIMAGINED CUSTOM YSF REFLECTORS\n"
            + "\n".join(custom_lines)
            + "\n# END ALLSCAN REIMAGINED CUSTOM YSF REFLECTORS\n"
        )
    return normalized


def replace_gateway_hosts(path: Path, hosts: Path) -> bool:
    require_secure_root_file(path, "Configured YSF Gateway file")
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise ControlError("Configured YSF Gateway file could not be read.") from exc
    lines = original.splitlines(keepends=True)
    in_network = False
    replaced = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_network = stripped.casefold() == "[ysf network]"
            continue
        if in_network and re.match(r"^\s*Hosts\s*=", line, re.IGNORECASE):
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            lines[index] = f"Hosts={hosts}{ending}"
            replaced += 1
    if replaced != 1:
        raise ControlError("YSF Gateway must contain exactly one Hosts entry in [YSF Network].")
    updated = "".join(lines)
    return atomic_root_text(path, updated, mode)


def signal_gateway_reload(service: str) -> None:
    active = subprocess.run(
        [str(SYSTEMCTL_BIN), "is-active", "--quiet", service],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if active.returncode != 0:
        return
    result = subprocess.run(
        [str(SYSTEMCTL_BIN), "restart", service],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise ControlError("YSF Gateway could not restart with the updated reflector catalog.")


def refresh_custom_catalog(bridge: dict) -> bool:
    settings = gateway_configuration(bridge)
    source = source_hosts_path(bridge, settings["hosts"])
    customs = bridge.get("ysfCustomReflectors", [])
    if not customs:
        return False
    managed = custom_hosts_path(bridge["id"])
    if settings["hosts"] != managed:
        raise ControlError("YSF custom reflector catalog has not been applied; run the ASR reapply service.")
    require_secure_root_file(source, "Configured YSF source hosts file")
    return atomic_root_text(managed, merged_hosts_content(source, customs))


def sync_custom_catalog(bridge: dict, reload_gateway: bool = True) -> dict:
    require_secure_root_file(bridge["gatewayPath"], "Configured YSF Gateway file")
    settings = gateway_configuration(bridge)
    source = source_hosts_path(bridge, settings["hosts"])
    require_secure_root_file(source, "Configured YSF source hosts file")
    customs = bridge.get("ysfCustomReflectors", [])
    desired = custom_hosts_path(bridge["id"]) if customs else source
    catalog_changed = False
    if customs:
        catalog_changed = atomic_root_text(
            desired, merged_hosts_content(source, customs)
        )
    config_changed = settings["hosts"] != desired
    if config_changed:
        replace_gateway_hosts(bridge["gatewayPath"], desired)
    if reload_gateway and (config_changed or catalog_changed):
        signal_gateway_reload(bridge["services"]["gateway"])
    return {
        "bridgeId": bridge["id"],
        "source": str(source),
        "effective": str(desired),
        "customCount": len(customs),
        "changed": bool(config_changed or catalog_changed),
    }


def sync_all_custom_catalogs(path: Path = CONFIG_PATH) -> list[dict]:
    require_secure_config_file(path)
    return [sync_custom_catalog(bridge) for bridge in configured_bridges(path)]


def destination_by_id(settings: dict, destination: str) -> dict:
    if not DESTINATION_RE.fullmatch(destination):
        raise ControlError("YSF destination must be a five-digit reflector ID.")
    for item in parse_destinations(settings["hosts"]):
        if item["id"] == destination:
            return item
    raise ControlError("YSF destination is not present in the configured reflector list.")


def gateway_event(line: str) -> tuple[str, str, str] | None:
    connect = re.search(
        r'Connect by remote command to ([0-9]{5}) - "([^"]+)"', line
    )
    if connect:
        return ("connect", connect.group(1), clean_name(connect.group(2)))
    linked = re.search(r"Linked to\s+(.+?)\s*$", line)
    if linked:
        return ("linked", "", clean_name(linked.group(1)))
    if "Disconnect by remote command" in line:
        return ("disconnect", "", "")
    return None


def gateway_link_state(
    log_path: Path | None,
    prior: dict | None = None,
    destinations: list[dict] | None = None,
) -> dict:
    result = {
        "linked": bool((prior or {}).get("linked")),
        "destination": str((prior or {}).get("destination", "")),
        "name": str((prior or {}).get("name", "")),
        "eventEpoch": int((prior or {}).get("eventEpoch", 0) or 0),
    }
    if log_path is None:
        return result
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            if size > TAIL_BYTES:
                handle.readline()
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return result
    pending: tuple[str, str, int] | None = None
    name_ids: dict[str, list[str]] = {}
    for item in destinations or []:
        name_ids.setdefault(str(item.get("name", "")), []).append(str(item.get("id", "")))
    for line in lines:
        event = gateway_event(line)
        epoch = line_epoch(line)
        if not event:
            continue
        if event[0] == "connect":
            pending = (event[1], event[2], epoch)
        elif event[0] == "linked" and pending and event[2] == pending[1]:
            result = {
                "linked": True,
                "destination": pending[0],
                "name": pending[1],
                "eventEpoch": epoch or pending[2],
            }
            pending = None
        elif event[0] == "linked" and len(name_ids.get(event[2], [])) == 1:
            result = {
                "linked": True,
                "destination": name_ids[event[2]][0],
                "name": event[2],
                "eventEpoch": epoch,
            }
        elif event[0] == "disconnect":
            result = {"linked": False, "destination": "", "name": "", "eventEpoch": epoch}
            pending = None
    return result


def log_marker(path: Path | None) -> tuple[str, int, int]:
    if path is None:
        return ("", 0, 0)
    try:
        info = path.stat()
        return (str(path), int(info.st_ino), int(info.st_size))
    except OSError:
        return (str(path), 0, 0)


def new_log_lines(log_dir: Path, root: str, marker: tuple[str, int, int]) -> list[str]:
    path = latest_log(log_dir, root)
    if path is None:
        return []
    try:
        info = path.stat()
        offset = marker[2] if str(path) == marker[0] and int(info.st_ino) == marker[1] else 0
        if info.st_size < offset:
            offset = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.readlines()
    except OSError:
        return []


def wait_gateway_connect(settings: dict, marker: tuple[str, int, int], item: dict) -> bool:
    deadline = time.monotonic() + VERIFY_TIMEOUT
    saw_connect = False
    while time.monotonic() < deadline:
        for line in new_log_lines(settings["logDir"], settings["logRoot"], marker):
            event = gateway_event(line)
            if not event:
                continue
            if event == ("connect", item["id"], item["name"]):
                saw_connect = True
            elif saw_connect and event == ("linked", "", item["name"]):
                return True
        time.sleep(0.2)
    return False


def wait_gateway_disconnect(settings: dict, marker: tuple[str, int, int]) -> bool:
    deadline = time.monotonic() + VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        if any(
            gateway_event(line) == ("disconnect", "", "")
            for line in new_log_lines(settings["logDir"], settings["logRoot"], marker)
        ):
            return True
        time.sleep(0.2)
    return False


def remote_command(bridge: dict, port: int, command: str) -> None:
    try:
        completed = subprocess.run(
            [str(bridge["remoteCommand"]), str(port), command],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("YSF remote command could not be sent.") from exc
    if completed.returncode != 0 or "Command sent:" not in completed.stdout:
        raise ControlError("YSF remote command returned an error.")


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


def asterisk_output(command: str) -> str:
    try:
        completed = subprocess.run(
            [str(ASTERISK_BIN), "-rx", command], capture_output=True, text=True,
            timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("Asterisk direct-link status could not be read.") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ControlError("Asterisk direct-link status returned an error.")
    return completed.stdout


def direct_linked(local_node: str, bridge_node: str) -> bool:
    return (bridge_node, "OUT") in parse_lstats_links(
        asterisk_output(f"rpt lstats {local_node}")
    )


def set_direct_link(local_node: str, bridge_node: str, linked: bool) -> None:
    if direct_linked(local_node, bridge_node) == linked:
        return
    command = f"rpt cmd {local_node} ilink {'3' if linked else '11'} {bridge_node}"
    try:
        completed = subprocess.run(
            [str(ASTERISK_BIN), "-rx", command], capture_output=True, text=True,
            timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("Asterisk bridge-link command failed.") from exc
    if completed.returncode != 0:
        raise ControlError("Asterisk bridge-link command returned an error.")
    for _ in range(40):
        if direct_linked(local_node, bridge_node) == linked:
            return
        time.sleep(0.25)
    raise ControlError(f"Asterisk did not confirm the bridge-node {'link' if linked else 'unlink'}.")


def audit(
    user: str,
    bridge_id: str,
    action: str,
    old_destination: str,
    new_destination: str,
    result: str,
) -> None:
    safe_user = USERNAME_RE.sub("_", user)[:80] or "unknown"
    safe_result = re.sub(r"[\r\n\t]+", " ", result).strip()[:240]
    line = (
        f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} user={safe_user} "
        f"bridge={bridge_id} action={action} old={old_destination or '-'} "
        f"new={new_destination or '-'} result={safe_result}\n"
    )
    parent_info = AUDIT_LOG.parent.stat()
    if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise OSError("audit directory is not secure")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(AUDIT_LOG, flags, 0o640)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise OSError("audit file is not secure")
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o640)
    finally:
        os.close(descriptor)


def prepare_control(bridge: dict) -> dict:
    require_secure_root_file(bridge["gatewayPath"], "Configured YSF Gateway file")
    require_secure_root_file(bridge["mmdvmPath"], "Configured MMDVM Bridge file")
    require_secure_root_file(bridge["remoteCommand"], "Configured RemoteCommand", executable=True)
    settings = gateway_settings(bridge)
    require_secure_root_file(settings["hosts"], "Configured YSF hosts file")
    mmdvm = mmdvm_settings(bridge)
    gateway_log = latest_log(settings["logDir"], settings["logRoot"])
    if gateway_log is None:
        raise ControlError("YSF Gateway log does not exist.")
    require_secure_root_file(gateway_log, "YSF Gateway log")
    mmdvm_log = latest_log(mmdvm["logDir"], mmdvm["logRoot"])
    if mmdvm_log is not None:
        require_secure_root_file(mmdvm_log, "MMDVM Bridge log")
    secure_run_dir()
    return settings


def connect(bridge_id: str, destination: str, user: str, path: Path = CONFIG_PATH) -> dict:
    require_secure_config_file(path)
    bridge = bridge_config(bridge_id, path)
    if not bridge.get("allowTune"):
        raise ControlError("YSF Net Bridge tuning is disabled by configuration.")
    settings = prepare_control(bridge)
    item = destination_by_id(settings, destination)
    lock_path = RUN_DIR / f"ysf-control-{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another YSF bridge-control action is already running.") from exc
        previous = gateway_link_state(
            latest_log(settings["logDir"], settings["logRoot"]),
            None,
            parse_destinations(settings["hosts"]),
        )
        old_destination = str(previous["destination"]) if previous["linked"] else ""
        try:
            audit(user, bridge_id, "connect", old_destination, destination, "attempt")
        except OSError as exc:
            raise ControlError("YSF audit log is unavailable; no command was sent.") from exc
        marker = log_marker(latest_log(settings["logDir"], settings["logRoot"]))
        try:
            remote_command(bridge, settings["port"], f"LinkYSF{destination}")
            if not wait_gateway_connect(settings, marker, item):
                raise ControlError("YSF Gateway did not confirm the requested reflector link.")
        except ControlError:
            try:
                audit(user, bridge_id, "connect", old_destination, destination, "digital link failed")
            except OSError:
                pass
            raise
        try:
            set_direct_link(bridge["localNode"], bridge["node"], True)
        except ControlError as link_error:
            rollback = "unconfirmed"
            try:
                rollback_marker = log_marker(latest_log(settings["logDir"], settings["logRoot"]))
                if old_destination and old_destination != destination:
                    previous_item = destination_by_id(settings, old_destination)
                    remote_command(bridge, settings["port"], f"LinkYSF{old_destination}")
                    rollback = (
                        f"restored {old_destination}"
                        if wait_gateway_connect(settings, rollback_marker, previous_item)
                        else "restore unconfirmed"
                    )
                else:
                    remote_command(bridge, settings["port"], "UnLink")
                    rollback = "success" if wait_gateway_disconnect(settings, rollback_marker) else "unconfirmed"
            except ControlError:
                rollback = "failed"
            try:
                audit(user, bridge_id, "connect", old_destination, destination, f"AllStar link failed; UnLink {rollback}")
            except OSError:
                pass
            if rollback.startswith("restored "):
                raise ControlError("The AllStar bridge link failed; the previous YSF reflector was restored.") from link_error
            if rollback == "success":
                raise ControlError("The AllStar bridge link failed; YSF was disconnected.") from link_error
            raise ControlError("The AllStar bridge link failed, and YSF disconnect could not be confirmed.") from link_error
        try:
            audit(user, bridge_id, "connect", old_destination, destination, "success")
        except OSError:
            pass
        return {
            "ok": True, "bridgeId": bridge_id, "destination": destination,
            "name": item["name"], "linked": True,
            "message": f"YSF Net Bridge connected to {item['name']} and node {bridge['node']} linked.",
        }


def disconnect(bridge_id: str, user: str, path: Path = CONFIG_PATH) -> dict:
    require_secure_config_file(path)
    bridge = bridge_config(bridge_id, path)
    if not bridge.get("allowTune"):
        raise ControlError("YSF Net Bridge tuning is disabled by configuration.")
    settings = prepare_control(bridge)
    lock_path = RUN_DIR / f"ysf-control-{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another YSF bridge-control action is already running.") from exc
        previous = gateway_link_state(
            latest_log(settings["logDir"], settings["logRoot"]),
            None,
            parse_destinations(settings["hosts"]),
        )
        old_destination = str(previous["destination"]) if previous["linked"] else ""
        try:
            audit(user, bridge_id, "disconnect", old_destination, "", "attempt")
        except OSError as exc:
            raise ControlError("YSF audit log is unavailable; no command was sent.") from exc

        digital_ok = False
        digital_error = ""
        marker = log_marker(latest_log(settings["logDir"], settings["logRoot"]))
        try:
            remote_command(bridge, settings["port"], "UnLink")
            digital_ok = wait_gateway_disconnect(settings, marker)
            if not digital_ok:
                digital_error = "YSF Gateway did not confirm disconnect"
        except ControlError as exc:
            digital_error = str(exc)

        allstar_ok = False
        allstar_error = ""
        try:
            set_direct_link(bridge["localNode"], bridge["node"], False)
            allstar_ok = True
        except ControlError as exc:
            allstar_error = str(exc)

        if digital_ok and allstar_ok:
            try:
                audit(user, bridge_id, "disconnect", old_destination, "", "success")
            except OSError:
                pass
            return {
                "ok": True, "bridgeId": bridge_id, "linked": False,
                "message": f"YSF Net Bridge disconnected and node {bridge['node']} unlinked.",
            }
        failures = []
        if not digital_ok:
            failures.append(digital_error or "YSF disconnect failed")
        if not allstar_ok:
            failures.append(allstar_error or "AllStar unlink failed")
        result = "; ".join(failures)
        try:
            audit(user, bridge_id, "disconnect", old_destination, "", f"partial failure: {result}")
        except OSError:
            pass
        raise ControlError("Disconnect was only partially successful: " + result + ".")


def service_active(service: str) -> bool:
    try:
        completed = subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", service],
            timeout=4, check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def initial_activity_state() -> dict:
    return {
        "path": "", "inode": 0, "offset": 0, "role": "idle",
        "current_user": "", "last_user": "-", "active_start_epoch": 0,
        "last_source_user": "", "last_source_epoch": 0,
        "network_relay": False,
        "activity_epoch": 0, "last_event_epoch": 0,
        "gateway": {"linked": False, "destination": "", "name": "", "eventEpoch": 0},
        "service_check_epoch": 0, "service_states": {},
    }


def apply_activity_line(
    state: dict,
    line: str,
    now: int,
    local_identities: frozenset[str] = frozenset(),
) -> None:
    epoch = line_epoch(line) or now
    source = re.search(
        r"YSF,\s+received network (?:data|voice) from\s+([A-Za-z0-9/ -]{1,20}?)\s+to\s+",
        line, re.IGNORECASE,
    )
    if source:
        caller = clean_name(source.group(1), 20).upper()
        if caller in local_identities:
            state.update({
                "role": "relay", "current_user": "", "network_relay": True,
                "active_start_epoch": int(state.get("active_start_epoch", 0) or 0) or epoch,
                "activity_epoch": epoch, "last_event_epoch": epoch,
            })
            return
        state.update({
            "role": "source", "current_user": caller,
            "last_user": caller or state.get("last_user", "-"),
            "last_source_user": caller,
            "last_source_epoch": epoch,
            "network_relay": False,
            "active_start_epoch": int(state.get("active_start_epoch", 0) or 0) or epoch,
            "activity_epoch": epoch, "last_event_epoch": epoch,
        })
        return
    if re.search(r"YSF,\s+received network end of transmission", line, re.IGNORECASE):
        if state.get("role") == "source" or state.get("network_relay"):
            state.update({
                "role": "idle", "current_user": "", "active_start_epoch": 0,
                "network_relay": False,
            })
            if state.get("last_source_user"):
                state["last_source_epoch"] = epoch
        state.update({"activity_epoch": epoch, "last_event_epoch": epoch})
        return
    if re.search(r"\bYSF,\s+TX state\s*=\s*ON\b", line, re.IGNORECASE):
        state.update({
            "role": "relay", "current_user": "",
            "network_relay": False,
            "active_start_epoch": int(state.get("active_start_epoch", 0) or 0) or epoch,
            "activity_epoch": epoch, "last_event_epoch": epoch,
        })
        return
    if re.search(r"\bYSF,\s+TX state\s*=\s*OFF\b", line, re.IGNORECASE):
        if state.get("role") == "relay":
            state.update({
                "role": "idle", "current_user": "", "active_start_epoch": 0,
                "network_relay": False,
            })
        state.update({"activity_epoch": epoch, "last_event_epoch": epoch})


def refresh_activity(
    bridge: dict,
    state: dict,
    now: int,
    local_identities: frozenset[str] = frozenset(),
) -> dict:
    settings = mmdvm_settings(bridge)
    path = latest_log(settings["logDir"], settings["logRoot"])
    if path is None:
        return state
    try:
        info = path.stat()
        changed = (
            str(path) != str(state.get("path", ""))
            or int(info.st_ino) != int(state.get("inode", 0) or 0)
            or int(info.st_size) < int(state.get("offset", 0) or 0)
        )
        if changed:
            gateway = state.get("gateway") if isinstance(state.get("gateway"), dict) else None
            state = initial_activity_state()
            if gateway is not None:
                state["gateway"] = gateway
            state.update({"path": str(path), "inode": int(info.st_ino)})
            offset = max(0, int(info.st_size) - TAIL_BYTES)
        else:
            offset = int(state.get("offset", 0) or 0)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            if changed and offset:
                handle.readline()
            for line in handle:
                apply_activity_line(state, line, now, local_identities)
            state["offset"] = handle.tell()
    except OSError:
        return state
    if state.get("role") in ("source", "relay") and now - int(state.get("last_event_epoch", 0) or 0) > ACTIVITY_STALE_SECONDS:
        state.update({"role": "idle", "current_user": "", "active_start_epoch": 0})
    return state


def clear_disconnected_activity(state: dict, linked: bool) -> None:
    """Never retain local or remote talker evidence for an unlinked Net Bridge."""
    if linked:
        return
    state.update({
        "role": "idle",
        "current_user": "",
        "last_user": "-",
        "last_source_user": "",
        "last_source_epoch": 0,
        "active_start_epoch": 0,
        "network_relay": False,
    })


def ysf_allstar_mismatch_visible(
    ysf_linked: bool,
    allstar_linked: bool,
    event_epoch: int,
    now: int,
) -> bool:
    """Keep the existing 15-second event-based transition grace testable."""
    if ysf_linked == allstar_linked:
        return False
    return event_epoch <= 0 or now - event_epoch > STATE_TRANSITION_GRACE_SECONDS


def bridge_status(bridge: dict, state: dict, now: int) -> tuple[dict, dict]:
    warning = ""
    ready = False
    linked = False
    destination = ""
    name = ""
    event_epoch = 0
    allstar_linked = bool(state.get("allstar_linked"))
    try:
        settings = prepare_control(bridge)
        destinations = parse_destinations(settings["hosts"])
        gateway = gateway_link_state(
            latest_log(settings["logDir"], settings["logRoot"]),
            state.get("gateway") if isinstance(state.get("gateway"), dict) else None,
            destinations,
        )
        state["gateway"] = gateway
        linked = bool(gateway["linked"])
        destination = str(gateway["destination"])
        name = str(gateway["name"])
        event_epoch = int(gateway["eventEpoch"])
        state = refresh_activity(
            bridge,
            state,
            now,
            frozenset(settings.get("localIdentities", frozenset())),
        )
        clear_disconnected_activity(state, linked)
        service_states = state.get("service_states") if isinstance(state.get("service_states"), dict) else {}
        # A Gateway link event and its paired AllStar command do not become
        # visible atomically. Recheck Asterisk on every watcher pass while the
        # cached states disagree instead of retaining a stale value for five
        # seconds during a normal connect or disconnect transition.
        if (
            now - int(state.get("service_check_epoch", 0) or 0) >= 5
            or linked != allstar_linked
        ):
            service_states = {key: service_active(value) for key, value in bridge["services"].items()}
            state["service_states"] = service_states
            state["service_check_epoch"] = now
            try:
                allstar_linked = direct_linked(bridge["localNode"], bridge["node"])
                state["allstar_linked"] = allstar_linked
                state["allstar_check_ok"] = True
            except ControlError:
                state["allstar_check_ok"] = False
        ready = (
            bool(bridge.get("allowTune"))
            and all(service_states.values())
            and bool(state.get("allstar_check_ok"))
        )
        if not all(service_states.values()):
            warning = "One or more configured YSF Net Bridge services are inactive."
        elif not state.get("allstar_check_ok"):
            warning = "Asterisk bridge-link status is unavailable."
        elif ysf_allstar_mismatch_visible(linked, allstar_linked, event_epoch, now):
            warning = "YSF and AllStar link states do not match."
    except ControlError as exc:
        state = initial_activity_state()
        warning = str(exc)
    role = str(state.get("role", "idle")) if linked else "idle"
    if role not in ("idle", "source", "relay"):
        role = "idle"
    caller = clean_name(str(state.get("current_user", "")), 20) if role == "source" else ""
    return ({
        "bridgeId": bridge["id"], "node": bridge["node"], "ready": ready,
        "linked": linked, "digitalLinked": linked, "allstarLinked": allstar_linked,
        "destination": destination, "name": name,
        "state": "TX ACTIVE" if role == "source" else ("RELAY" if role == "relay" else ("Idle" if linked else "Disconnected")),
        "role": role, "current_user": caller, "caller": caller,
        "last_user": clean_name(str(state.get("last_user", "-")), 20) or "-",
        "last_source_user": clean_name(str(state.get("last_source_user", "")), 20),
        "last_source_epoch": int(state.get("last_source_epoch", 0) or 0),
        "active_start_epoch": int(state.get("active_start_epoch", 0) or 0) if role != "idle" else 0,
        "activity_epoch": int(state.get("activity_epoch", 0) or 0),
        "event_epoch": event_epoch, "warning": warning[:160],
    }, state)


def cached_watcher_states() -> dict[str, dict]:
    if not STATUS_PATH.exists():
        return {}
    try:
        require_secure_root_file(STATUS_PATH, "YSF watcher status cache")
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (ControlError, OSError, json.JSONDecodeError):
        return {}
    states = {}
    for bridge_id, entry in (payload.get("bridges", {}) if isinstance(payload, dict) else {}).items():
        if not BRIDGE_ID_RE.fullmatch(str(bridge_id)) or not isinstance(entry, dict):
            continue
        state = initial_activity_state()
        destination = str(entry.get("destination", ""))
        name = clean_name(str(entry.get("name", "")))
        linked = bool(entry.get("linked"))
        if linked and DESTINATION_RE.fullmatch(destination) and name:
            state["gateway"] = {
                "linked": True, "destination": destination, "name": name,
                "eventEpoch": int(entry.get("event_epoch", 0) or 0),
            }
        source_user = clean_name(str(entry.get("last_source_user", "")), 20)
        source_epoch = int(entry.get("last_source_epoch", 0) or 0)
        now = int(time.time())
        if source_user and source_epoch > 0 and source_epoch <= now + 300 and now - source_epoch <= SOURCE_RETENTION_SECONDS:
            state["last_source_user"] = source_user
            state["last_source_epoch"] = source_epoch
        states[str(bridge_id)] = state
    return states


def watch_status(path: Path = CONFIG_PATH, once: bool = False) -> None:
    require_secure_config_file(path)
    states = cached_watcher_states()
    destination_mtimes: dict[str, int] = {}
    catalog_signatures: dict[str, str] = {}
    while True:
        now = int(time.time())
        entries = {}
        bridges = configured_bridges(path)
        configured_ids = set()
        for bridge in bridges:
            bridge_id = bridge["id"]
            configured_ids.add(bridge_id)
            entry, state = bridge_status(
                bridge, states.get(bridge_id, initial_activity_state()), now
            )
            entries[bridge_id] = entry
            states[bridge_id] = state
            try:
                raw_settings = gateway_configuration(bridge)
                source_hosts = source_hosts_path(bridge, raw_settings["hosts"])
                source_mtime = source_hosts.stat().st_mtime_ns
                custom_hash = hashlib.sha256(json.dumps(
                    bridge.get("ysfCustomReflectors", []),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")).hexdigest()
                signature = f"{source_mtime}:{custom_hash}"
                if catalog_signatures.get(bridge_id) != signature:
                    refresh_custom_catalog(bridge)
                    catalog_signatures[bridge_id] = signature
                settings = gateway_settings(bridge)
                hosts_mtime = settings["hosts"].stat().st_mtime_ns
                destination_path = RUN_DIR / f"destinations-{bridge_id}.json"
                if destination_mtimes.get(bridge_id) != hosts_mtime or not destination_path.exists():
                    atomic_json(destination_path, {
                        "ok": True, "bridgeId": bridge_id, "updated_epoch": now,
                        "destinations": parse_destinations(settings["hosts"]),
                    })
                    destination_mtimes[bridge_id] = hosts_mtime
            except (ControlError, OSError):
                pass
        for bridge_id in set(states) - configured_ids:
            states.pop(bridge_id, None)
            catalog_signatures.pop(bridge_id, None)
            destination_mtimes.pop(bridge_id, None)
        atomic_json(STATUS_PATH, {"ok": True, "updated_epoch": now, "bridges": entries})
        if once:
            return
        time.sleep(WATCH_INTERVAL)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        hosts = tmp / "YSFHosts.txt"
        hosts.write_text(
            "# fixture\n12345;US-EXAMPLE;Example Network;127.0.0.1;41002;000;\n"
            "23456;US-TEST-WIDE;Test Wide Area;127.0.0.1;41000;000;\n",
            encoding="utf-8",
        )
        parsed = parse_destinations(hosts)
        assert {item["id"] for item in parsed} == {"12345", "23456"}
        custom = validate_custom_reflectors([{
            "id": "34567", "name": "us-custom-test",
            "host": "ysf.example.net", "port": 41000,
            "description": "Synthetic test reflector",
        }])
        assert custom[0]["name"] == "US-CUSTOM-TEST"
        merged = tmp / "merged-YSFHosts.txt"
        merged.write_text(merged_hosts_content(hosts, custom), encoding="utf-8")
        merged_rows = parse_destinations(merged)
        assert {item["id"] for item in merged_rows} == {"12345", "23456", "34567"}
        assert next(item for item in merged_rows if item["id"] == "34567")["name"] == "US-CUSTOM-TEST"
        refreshed = tmp / "refreshed-YSFHosts.txt"
        refreshed.write_text(hosts.read_text(encoding="utf-8"), encoding="utf-8")
        refreshed.write_text(merged_hosts_content(refreshed, custom), encoding="utf-8")
        assert any(item["id"] == "34567" for item in parse_destinations(refreshed))
        for invalid in (
            [{"id": "1234", "name": "BAD", "host": "example.net", "port": 41000}],
            [{"id": "34567", "name": "BAD;NAME", "host": "example.net", "port": 41000}],
            [{"id": "34567", "name": "BAD", "host": "https://example.net", "port": 41000}],
            [{"id": "34567", "name": "BAD", "host": "example.net", "port": 70000}],
        ):
            try:
                validate_custom_reflectors(invalid)
            except ControlError:
                pass
            else:
                raise AssertionError("invalid custom YSF reflector was accepted")
        assert gateway_event('M: 2026-01-01 12:00:01.000 Connect by remote command to 23456 - "US-TEST-WIDE    "') == ("connect", "23456", "US-TEST-WIDE")
        assert gateway_event("M: 2026-01-01 12:00:01.100 Linked to US-TEST-WIDE    ") == ("linked", "", "US-TEST-WIDE")
        assert gateway_event("M: 2026-01-01 12:10:00.000 Disconnect by remote command") == ("disconnect", "", "")

        gateway_log = tmp / "gateway.log"
        gateway_log.write_text(
            'M: 2026-01-01 12:00:01.000 Connect by remote command to 23456 - "US-TEST-WIDE    "\n'
            "M: 2026-01-01 12:00:01.100 Linked to US-TEST-WIDE    \n",
            encoding="utf-8",
        )
        state = gateway_link_state(gateway_log)
        assert state["linked"] and state["destination"] == "23456" and state["name"] == "US-TEST-WIDE"
        with gateway_log.open("a", encoding="utf-8") as handle:
            handle.write("M: 2026-01-01 12:10:00.000 Disconnect by remote command\n")
        assert not gateway_link_state(gateway_log)["linked"]
        startup_log = tmp / "startup.log"
        startup_log.write_text(
            "M: 2026-01-01 00:00:01.000 Linked to US-EXAMPLE\n",
            encoding="utf-8",
        )
        startup_state = gateway_link_state(startup_log, destinations=parsed)
        assert startup_state["linked"] and startup_state["destination"] == "12345"

        identity_config = configparser.ConfigParser(interpolation=None, strict=False)
        identity_config.optionxform = str
        identity_config.read_string(
            "[General]\nCallsign=N0CALL\nSuffix=RPT\n"
        )
        local_identities = gateway_local_identities(identity_config)
        assert local_identities == frozenset({"N0CALL", "N0CALL-RPT"})
        local_activity = initial_activity_state()
        apply_activity_line(
            local_activity,
            "M: 2026-01-01 12:01:00.000 YSF, received network data from N0CALL-RPT to ALL at N0CALL",
            1,
            local_identities,
        )
        assert local_activity["role"] == "relay"
        assert local_activity["current_user"] == ""
        assert local_activity["last_source_user"] == ""
        apply_activity_line(
            local_activity,
            "M: 2026-01-01 12:01:01.000 YSF, received network end of transmission",
            2,
            local_identities,
        )
        assert local_activity["role"] == "idle"

        activity = initial_activity_state()
        apply_activity_line(
            activity,
            "M: 2026-01-01 12:02:00.000 YSF, received network data from REMOTE1    to ALL        at REMOTE1",
            1,
            local_identities,
        )
        assert activity["role"] == "source" and activity["current_user"] == "REMOTE1"
        source_epoch = activity["last_source_epoch"]
        assert activity["last_source_user"] == "REMOTE1" and source_epoch > 0
        apply_activity_line(activity, "M: 2026-08-03 15:26:57.006 YSF, received network end of transmission", 2)
        assert activity["role"] == "idle"
        assert activity["last_source_epoch"] >= source_epoch
        apply_activity_line(activity, "M: 2026-08-03 15:27:00.000 YSF, TX state = ON", 3)
        assert activity["role"] == "relay" and activity["current_user"] == ""
        assert activity["last_source_user"] == "REMOTE1"
        connected_activity = dict(activity)
        clear_disconnected_activity(connected_activity, True)
        assert connected_activity["last_source_user"] == "REMOTE1"
        clear_disconnected_activity(activity, False)
        assert activity["role"] == "idle"
        assert activity["last_user"] == "-"
        assert activity["last_source_user"] == ""
        assert activity["last_source_epoch"] == 0
        assert not ysf_allstar_mismatch_visible(True, False, 100, 115)
        assert ysf_allstar_mismatch_visible(True, False, 100, 116)
        assert not ysf_allstar_mismatch_visible(True, True, 0, 500)
        assert parse_lstats_links(
            "NODE      PEER                RECONNECTS  DIRECTION  CONNECT TIME        CONNECT STATE\n"
            "4321      127.0.0.1:4569      0           OUT        00:00:02            ESTABLISHED\n"
        ) == {("4321", "OUT")}

        config = {
            "node": "123456",
            "bridges": [{
                "id": "ysf_net", "node": "4321", "cardType": "ysf_net",
                "allowTune": True,
                "ysfGatewayConfig": "/opt/YSFGateway_TestNet/YSFGateway.ini",
                "mmdvmConfig": "/opt/MMDVM_Bridge_TestNet/MMDVM_Bridge.ini",
                "ysfGatewayService": "ysfgateway_testnet.service",
                "mmdvmService": "mmdvm_bridge_testnet.service",
                "analogBridgeService": "analog_bridge_testnet.service",
                "emulatorService": "md380-emu-testnet.service",
                "ysfHostsPath": "/var/lib/mmdvm/YSFHosts.txt",
                "ysfCustomReflectors": custom,
                "commandTransport": "remote_command",
            }],
        }
        validated = validate_bridge(config["bridges"][0], config)
        assert validated["remoteCommand"] == Path("/opt/MMDVM_Bridge_TestNet/RemoteCommand")
        bad = dict(config["bridges"][0], ysfGatewayService="../../bad.service")
        try:
            validate_bridge(bad, config)
        except ControlError:
            pass
        else:
            raise AssertionError("unsafe service was accepted")
    print("YSF bridge-control self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--connect", metavar="BRIDGE_ID")
    action.add_argument("--disconnect", metavar="BRIDGE_ID")
    action.add_argument("--watch", action="store_true")
    action.add_argument("--sync-custom-hosts", action="store_true")
    action.add_argument("--refresh-public-hosts", action="store_true")
    action.add_argument("--self-test", action="store_true")
    parser.add_argument("destination", nargs="?")
    parser.add_argument("--user", default="unknown")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        elif args.sync_custom_hosts:
            print(json.dumps({
                "ok": True,
                "bridges": sync_all_custom_catalogs(),
            }, separators=(",", ":")))
        elif args.refresh_public_hosts:
            print(json.dumps({"ok": True, **refresh_public_hosts()}, separators=(",", ":")))
        elif args.watch:
            watch_status(once=args.once)
        elif args.connect:
            if args.destination is None:
                raise ControlError("YSF destination is required.")
            print(json.dumps(connect(args.connect, args.destination, args.user), separators=(",", ":")))
        elif args.disconnect:
            print(json.dumps(disconnect(args.disconnect, args.user), separators=(",", ":")))
        return 0
    except ControlError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
