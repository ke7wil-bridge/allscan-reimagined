#!/usr/bin/env python3
"""Read-only plan and configuration rendering for ASR-managed URF/TGIF DMR."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import re
import sys
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONFIG = "/etc/allscan-reimagined/config.json"
RPT = "/etc/asterisk/rpt.conf"
MODULES = "/etc/asterisk/modules.conf"
STATE = "/var/lib/allscan-reimagined/bridge-setup/installations"
ADMIN_ENV_DIR = "/etc/allscan-reimagined/native-udp-admin"
ROOT_BASE = "/opt/allscan-reimagined-bridges/urf"
def runtime_root(bridge_id: str) -> str:
    return f"{ROOT_BASE}/{bridge_id}"
def port_block(bridge_id: str) -> dict[str, int]:
    base = 30000 + (zlib.crc32(bridge_id.encode()) & 0xFFFFFFFF) % 900 * 20
    # URFD accepts transcoder ports only through 45000.  Preserve the original
    # deterministic blocks below that ceiling and fold the formerly invalid
    # upper 150 blocks into the unused 27000-29999 range without collisions.
    if base > 44980:
        base -= 18000
    names = ("usrp_rx", "usrp_tx", "transcoder", "dmr", "dmrplus", "m17",
             "nxdn", "p25", "ysf", "tgif_local", "tgif_mmdvm", "urf_local",
             "dvswitch_tx", "dvswitch_rx", "dcs", "dextra", "dplus", "urf")
    return dict(zip(names, range(base, base + len(names))))

def tgif_network_id(dmr_id: int, bridge_node: int, essid: int | None = None) -> int:
    """Return a stable TGIF hotspot ESSID distinct for each private node."""
    # The qualified managed Net Bridge identity is intentionally independent
    # of its consolidated AllStar transport node and the URFWIL ESSID 01 peer.
    suffix = essid if essid is not None else (4 if bridge_node == 1999 else (bridge_node % 100 or 99))
    return dmr_id * 100 + suffix

class PlanError(RuntimeError):
    pass

def validate_runtime_requirements(facts, containerized: bool, asterisk_available: bool,
                                  release: dict[str, str], machine: str) -> None:
    if not facts.systemd:
        raise PlanError("URF/TGIF setup requires systemd")
    if containerized:
        if not facts.docker or not facts.docker_compose:
            raise PlanError("container-host URF/TGIF setup requires Docker with the Compose plugin")
    else:
        if (release.get("ID") != "debian"
                or release.get("VERSION_ID", "").split(".")[0] not in {"12", "13"}):
            raise PlanError("native URF/TGIF setup requires Debian 12 or 13")
        if machine.lower() not in {"x86_64", "aarch64", "armv7l"}:
            raise PlanError("native URF/TGIF setup is unsupported on this architecture")
    if not asterisk_available:
        raise PlanError("Asterisk chan_usrp module is not installed")

@dataclass(frozen=True)
class UrfSettings:
    bridge_id: str
    callsign: str
    dmr_id: int
    tgif_tg: int
    reflector: str = "URFASR"
    title: str = "DMR Bridge"
    bridge_node: int | None = None
    bridge_role: str = "standard"
    tgif_essid: int | None = None
    dmr_public_port: int | None = None
    m17_public_port: int | None = None
    p25_public_port: int | None = None
    nxdn_public_port: int | None = None
    ysf_public_port: int | None = None
    urf_public_port: int | None = None
    nxdn_reflector_id: int | None = None
    p25_reflector_id: int | None = None
    ysf_registration_id: int | None = None
    node_template: str | None = None
    dstar_enabled: bool = True
    dmrplus_enabled: bool = True
    dmr_enabled: bool = True
    m17_enabled: bool = True
    p25_enabled: bool = True
    nxdn_enabled: bool = True
    ysf_enabled: bool = True
    urf_enabled: bool = True
    legacy_bridge_nodes: tuple[int, ...] = ()
def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise PlanError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def validate(value: UrfSettings) -> UrfSettings:
    bridge_id = value.bridge_id.strip().lower()
    callsign = value.callsign.strip().upper()
    reflector = value.reflector.strip().upper()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise PlanError("invalid bridge ID")
    # SCRATCH is the isolated Beta 8 provisioning test node marker, not an on-air callsign.
    if callsign != "SCRATCH" and (not re.fullmatch(r"[A-Z0-9]{3,10}", callsign) or not re.search(r"[A-Z]", callsign) or not re.search(r"[0-9]", callsign)):
        raise PlanError("invalid station callsign")
    if not re.fullmatch(r"URF[A-Z0-9]{2,5}", reflector):
        raise PlanError("URF reflector name must be URF followed by 2-5 letters or numbers")
    if not isinstance(value.dmr_id, int) or not 1 <= value.dmr_id <= 9_999_999:
        raise PlanError("enter a valid 1-7 digit DMR ID")
    if not isinstance(value.tgif_tg, int) or not 1 <= value.tgif_tg <= 16_777_215:
        raise PlanError("enter a valid TGIF talkgroup")
    if value.bridge_role not in {"standard", "net"}:
        raise PlanError("bridge role must be standard or net")
    if value.tgif_essid is not None and (
            not isinstance(value.tgif_essid, int) or not 1 <= value.tgif_essid <= 99):
        raise PlanError("TGIF hotspot ESSID must be 1-99")
    if not isinstance(value.title, str) or not value.title.strip() or len(value.title) > 80 or any(ch in value.title for ch in "\r\n\0"):
        raise PlanError("invalid bridge title")
    if value.bridge_node is not None and not 1001 <= value.bridge_node <= 1999:
        raise PlanError("private bridge node must be in 1001-1999")
    ports = {
        "dmr_public_port": value.dmr_public_port,
        "m17_public_port": value.m17_public_port,
        "p25_public_port": value.p25_public_port,
        "nxdn_public_port": value.nxdn_public_port,
        "ysf_public_port": value.ysf_public_port,
        "urf_public_port": value.urf_public_port,
    }
    for name, port in ports.items():
        if port is not None and (not isinstance(port, int) or not 1024 <= port <= 65535):
            raise PlanError(f"{name.replace('_', ' ')} must be between 1024 and 65535")
    ids = {
        "nxdn_reflector_id": (value.nxdn_reflector_id, 1, 9999),
        "p25_reflector_id": (value.p25_reflector_id, 1, 16777215),
        "ysf_registration_id": (value.ysf_registration_id, 1, 9999999),
    }
    for name, (identity, minimum, maximum) in ids.items():
        if identity is not None and (not isinstance(identity, int) or not minimum <= identity <= maximum):
            raise PlanError(f"{name.replace('_', ' ')} must be between {minimum} and {maximum}")
    for flag in (value.dstar_enabled, value.dmrplus_enabled, value.dmr_enabled,
                 value.m17_enabled, value.p25_enabled, value.nxdn_enabled,
                 value.ysf_enabled, value.urf_enabled):
        if not isinstance(flag, bool):
            raise PlanError("URF protocol enable flags must be boolean")
    if not isinstance(value.legacy_bridge_nodes, tuple) or len(value.legacy_bridge_nodes) > 16:
        raise PlanError("legacy bridge node list is invalid")
    if any(not isinstance(node, int) or not 1 <= node <= 999999 or node in (value.bridge_node,)
           for node in value.legacy_bridge_nodes) or len(set(value.legacy_bridge_nodes)) != len(value.legacy_bridge_nodes):
        raise PlanError("legacy bridge nodes must be unique valid AllStar node numbers, different from the replacement")
    template = value.node_template.strip() if isinstance(value.node_template, str) else None
    if template is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", template):
        raise PlanError("invalid Asterisk node template")
    return UrfSettings(bridge_id, callsign, value.dmr_id, value.tgif_tg, reflector,
                       value.title.strip(), value.bridge_node, value.bridge_role, value.tgif_essid,
                       value.dmr_public_port, value.m17_public_port, value.p25_public_port,
                       value.nxdn_public_port, value.ysf_public_port, value.urf_public_port,
                       value.nxdn_reflector_id, value.p25_reflector_id, value.ysf_registration_id,
                       template, value.dstar_enabled, value.dmrplus_enabled, value.dmr_enabled,
        value.m17_enabled, value.p25_enabled, value.nxdn_enabled, value.ysf_enabled,
        value.urf_enabled, value.legacy_bridge_nodes)
def rooted(root: Path, logical: str) -> Path:
    return load("asr_backend_urf", "asr-provisioning-backend.py").map_path(root, logical)

def safe_regular(root: Path, logical: str) -> Path:
    path = rooted(root, logical)
    current = root.resolve()
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise PlanError(f"unsafe symbolic link in {logical}")
    if not path.is_file():
        raise PlanError(f"required ASL3 file is missing: {logical}")
    return path

def section_values(text: str, section_name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    active = False
    for line in text.splitlines():
        match = re.match(r"^\s*\[([^]]+)\](?:\s*\(\s*[^)]+\s*\))?", line)
        if match:
            active = match.group(1).strip() == section_name
            continue
        if active and "=" in line and not line.lstrip().startswith((";", "#")):
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.split(";", 1)[0].strip()
    return values


def validate_node_inheritance(rpt_text: str, node: int, template: str, *, require_node: bool = True) -> None:
    if require_node:
        node_header = re.search(rf"(?m)^\s*\[{node}\]\s*\(\s*{re.escape(template)}\s*\)", rpt_text)
        if not node_header:
            raise PlanError(f"URF bridge node {node} must inherit [{template}]")
    values = section_values(rpt_text, template)
    required = {
        "functions": "functions", "link_functions": "functions", "morse": "morse",
        "phone_functions": "functions", "scheduler": "schedule", "telemetry": "telemetry",
        "wait_times": "wait-times", "context": "radio",
    }
    missing = [key for key, expected in required.items() if values.get(key) != expected]
    if missing:
        raise PlanError(f"Asterisk node template [{template}] is missing required app_rpt settings: {', '.join(missing)}")


def native_admin_env(root: Path, mode: str) -> dict[str, str]:
    path = rooted(root, f"{ADMIN_ENV_DIR}/{mode}.env")
    if not path.exists():
        return {}
    if not path.is_file() or path.is_symlink():
        raise PlanError(f"native {mode.upper()} admin environment is not a safe regular file")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {"ASR_BRIDGE_ID", "ASR_BRIDGE_MODE", "ASR_PUBLIC_PORT", "ASR_UPSTREAM_HOST", "ASR_UPSTREAM_PORT", "ASR_PUBLIC_HOST", "ASR_PUBLIC_IPV6_HOST", "ASR_ENABLE_IPV6", "ASR_RUNTIME_ROOT", "ASR_GLOBAL_POLICY_DIR"}:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def migration_warnings(root: Path, rpt_text: str, nodes: tuple[int, ...]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if not nodes:
        return warnings
    candidates: list[Path] = []
    script_root = rooted(root, "/usr/local/sbin")
    if script_root.is_dir() and not script_root.is_symlink():
        for path in (*script_root.glob("*.sh"), *script_root.glob("*.py")):
            if any(word in path.name.lower() for word in ("bridge", "watchdog", "reconnect", "status")):
                candidates.append(path)
    unit_root = rooted(root, "/etc/systemd/system")
    if unit_root.is_dir() and not unit_root.is_symlink():
        for path in unit_root.glob("*.service"):
            if any(word in path.name.lower() for word in ("bridge", "watchdog", "reconnect")):
                candidates.append(path)
    for node in nodes:
        token = re.compile(rf"(?<![0-9]){node}(?![0-9])")
        if re.search(r"(?i)ilink\s*[, ]\s*13\s*[, ]\s*" + str(node) + r"(?![0-9])", rpt_text):
            warnings.append({"node": node, "source": RPT, "type": "startup-link-reference"})
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if token.search(content):
                logical = "/" + path.relative_to(root).as_posix() if root != Path("/") else "/" + path.relative_to(root).as_posix()
                warnings.append({"node": node, "source": logical, "type": "external-script-reference"})
    return warnings


def plan(root: Path, settings: UrfSettings) -> dict[str, Any]:
    settings = validate(settings)
    config_path = safe_regular(root, CONFIG)
    rpt_path = safe_regular(root, RPT)
    modules_path = safe_regular(root, MODULES)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanError("ASR configuration is invalid") from exc
    if not isinstance(config, dict) or not isinstance(config.get("bridges", []), list):
        raise PlanError("ASR bridges are invalid")
    core = load("asr_setup_core_urf", "asr-bridge-setup-core.py")
    facts = core.ASL3Adapter(root).detect()
    backend_module = load("asr_backend_urf_module", "asr-provisioning-backend.py")
    containerized = root == Path("/") and backend_module.load_profile() is not None
    if root == Path("/"):
        validate_runtime_requirements(
            facts, containerized, backend_module.asterisk_module_available(),
            platform.freedesktop_os_release(), platform.machine(),
        )
    try:
        main_node = int(config["node"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("main AllStar node is missing") from exc
    if settings.bridge_role == "net" and not 100 <= main_node <= 999999:
        raise PlanError("DMR Net Bridge requires a 3-6 digit main AllStar node")
    owned = [b for b in config["bridges"] if isinstance(b, dict) and b.get("id") == settings.bridge_id and b.get("urfReflector")]
    record = rooted(root, f"{STATE}/{settings.bridge_id}.json")
    previous_record: dict[str, Any] = {}
    if record.exists():
        if not record.is_file() or record.is_symlink():
            raise PlanError("existing URF ownership record is not a safe regular file")
        try:
            previous_record = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanError("existing URF ownership record is invalid") from exc
        if not isinstance(previous_record, dict) or previous_record.get("bridgeId") not in (None, settings.bridge_id):
            raise PlanError("existing URF ownership record does not match this reflector")
    if owned and not previous_record:
        raise PlanError("cannot replace an unowned URF reflector")
    managed_existing = bool(owned or previous_record)
    previous_node = previous_record.get("bridgeNode")
    try:
        previous_node = int(previous_node) if previous_node is not None else None
    except (TypeError, ValueError):
        previous_node = None
    used = set(facts.node_numbers) | {main_node}
    for bridge in config["bridges"]:
        if isinstance(bridge, dict):
            try: used.add(int(bridge.get("node")))
            except (TypeError, ValueError): pass
    node = (1999 if settings.bridge_role == "net" else
            (int(owned[0]["node"]) if owned else
             (settings.bridge_node or previous_node or core.allocate_node(used))))
    shared_net_node = (settings.bridge_role == "net" and node == 1999
                       and any(isinstance(item, dict)
                               and item.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
                               and str(item.get("node", "")) == "1999"
                               for item in config["bridges"]))
    if not managed_existing and node in used and not shared_net_node:
        raise PlanError("requested private node is already in use")
    if previous_node is not None and settings.bridge_node is not None and previous_node != settings.bridge_node:
        raise PlanError("changing an installed URF bridge node requires the dedicated migration workflow")
    ports = port_block(settings.bridge_id)
    if settings.bridge_role == "net":
        ports["usrp_rx"], ports["usrp_tx"] = core.unified_net_usrp_ports()
    occupied_udp = set(facts.listening_udp_ports)
    occupied_tcp = set(facts.listening_tcp_ports)
    public_ports = {
        "dmr": settings.dmr_public_port or (previous_record.get("publicPorts", {}).get("dmr") if isinstance(previous_record.get("publicPorts"), dict) else None) or (previous_record.get("ports", {}).get("dmr") if isinstance(previous_record.get("ports"), dict) else None) or ports["dmr"],
        "m17": settings.m17_public_port or (previous_record.get("publicPorts", {}).get("m17") if isinstance(previous_record.get("publicPorts"), dict) else None) or int(native_admin_env(root, "m17").get("ASR_PUBLIC_PORT") or 17000),
        "p25": settings.p25_public_port or (previous_record.get("publicPorts", {}).get("p25") if isinstance(previous_record.get("publicPorts"), dict) else None) or int(native_admin_env(root, "p25").get("ASR_PUBLIC_PORT") or 41000),
        "nxdn": settings.nxdn_public_port or (previous_record.get("publicPorts", {}).get("nxdn") if isinstance(previous_record.get("publicPorts"), dict) else None) or int(native_admin_env(root, "nxdn").get("ASR_PUBLIC_PORT") or 41400),
        "ysf": settings.ysf_public_port or (previous_record.get("publicPorts", {}).get("ysf") if isinstance(previous_record.get("publicPorts"), dict) else None) or int(native_admin_env(root, "ysf").get("ASR_PUBLIC_PORT") or 42000),
        "urf": settings.urf_public_port or (previous_record.get("publicPorts", {}).get("urf") if isinstance(previous_record.get("publicPorts"), dict) else None) or (previous_record.get("ports", {}).get("urf") if isinstance(previous_record.get("ports"), dict) else None) or ports["urf"],
    }
    listener_ports = {
        "dmr": public_ports["dmr"],
        "m17": ports["m17"], "p25": ports["p25"],
        "nxdn": ports["nxdn"], "ysf": ports["ysf"],
        "urf": public_ports["urf"],
    }
    enabled = {
        "dstar": settings.dstar_enabled, "dmrplus": settings.dmrplus_enabled,
        "dmr": settings.dmr_enabled, "m17": settings.m17_enabled,
        "p25": settings.p25_enabled, "nxdn": settings.nxdn_enabled,
        "ysf": settings.ysf_enabled, "urf": settings.urf_enabled,
    }
    active_listeners = [listener_ports[key] for key in ("dmr", "m17", "p25", "nxdn", "ysf", "urf") if enabled[key]]
    proxy_modes = ("m17", "p25", "nxdn", "ysf")
    proxy_warnings = []
    proxy_ports = []
    for mode in proxy_modes:
        if not enabled[mode]:
            continue
        env = native_admin_env(root, mode)
        public_port = public_ports[mode]
        if env:
            try:
                current_public = int(env.get("ASR_PUBLIC_PORT", "0"))
                current_upstream = int(env.get("ASR_UPSTREAM_PORT", "0"))
            except ValueError as exc:
                raise PlanError(f"native {mode.upper()} admin proxy port settings are invalid") from exc
            if current_public != public_port:
                proxy_warnings.append({"mode": mode, "message": "Public proxy port will be updated on install."})
            if current_upstream and current_upstream != listener_ports[mode]:
                proxy_warnings.append({"mode": mode, "message": f"Public proxy currently forwards to external/legacy port {current_upstream}; ASR will preserve that route."})
            proxy_ports.append(public_port)
        else:
            proxy_ports.append(public_port)
        if public_port == listener_ports[mode]:
            raise PlanError(f"{mode.upper()} public proxy port must differ from its private URF listener port")
    bind_ports = active_listeners + proxy_ports
    if enabled["dstar"]:
        bind_ports.extend((ports["dcs"], ports["dextra"], ports["dplus"]))
    if enabled["dmrplus"]:
        bind_ports.append(ports["dmrplus"])
    if len(bind_ports) != len(set(bind_ports)):
        raise PlanError("enabled URF protocol listener ports conflict with each other")
    if enabled["dstar"]:
        active_listeners.extend((ports["dcs"], ports["dextra"], ports["dplus"]))
    if enabled["dmrplus"]:
        active_listeners.append(ports["dmrplus"])
    if len(active_listeners) != len(set(active_listeners)):
        raise PlanError("enabled URF protocol listener ports conflict with each other")
    internal_udp = {value for key, value in ports.items() if key not in {
        "transcoder", "dmr", "m17", "p25", "nxdn", "ysf", "urf", "dcs", "dextra", "dplus", "dmrplus"}}
    if settings.bridge_role == "net" and shared_net_node:
        internal_udp -= set(core.unified_net_usrp_ports())
    active_udp = internal_udp | set(active_listeners) | set(proxy_ports)
    # Ignore only ports proven to belong to the existing URF installation.
    previous_ports = previous_record.get("ports", {}) if isinstance(previous_record.get("ports"), dict) else {}
    previous_public = previous_record.get("publicPorts", {}) if isinstance(previous_record.get("publicPorts"), dict) else {}
    owned_udp = {int(value) for value in (*previous_ports.values(), *previous_public.values())
                 if isinstance(value, int) and 1 <= value <= 65535}
    owned_proxy_ports = set()
    for mode in ("m17", "p25", "nxdn", "ysf"):
        env = native_admin_env(root, mode)
        try:
            current_public = int(env.get("ASR_PUBLIC_PORT", "0"))
        except ValueError:
            current_public = 0
        if current_public:
            owned_proxy_ports.add(current_public)
    occupied_udp -= owned_udp | owned_proxy_ports
    if isinstance(previous_ports.get("transcoder"), int):
        occupied_tcp.discard(previous_ports["transcoder"])
    if occupied_udp.intersection(active_udp) or ports["transcoder"] in occupied_tcp:
        raise PlanError("a required URF/TGIF public or internal port is already in use")
    if set(active_listeners).intersection(internal_udp):
        raise PlanError("a public protocol listener conflicts with an internal URF transport port")
    rpt_text = rpt_path.read_text(encoding="utf-8")
    template_match = re.search(rf"(?m)^\s*\[{main_node}\]\s*\(\s*([A-Za-z0-9_-]+)\s*\)", rpt_text)
    node_template = settings.node_template or (template_match.group(1) if template_match else "node-main")
    if not re.search(rf"(?m)^\s*\[{re.escape(node_template)}\](?:\s*\(\s*!\s*\))?\s*$", rpt_text):
        raise PlanError(f"Asterisk node template [{node_template}] is not defined in rpt.conf")
    validate_node_inheritance(rpt_text, node, node_template, require_node=False)
    runtime = runtime_root(settings.bridge_id)
    resources = {
        "root": runtime, "compose": f"{runtime}/compose.yml", "urfConfig": f"{runtime}/config/urfd.ini",
        "tcdConfig": f"{runtime}/config/tcd.ini", "blacklist": f"{runtime}/config/urfd.blacklist",
        "tgifSecret": f"{runtime}/secrets/tgif-password", "config": CONFIG, "rpt": RPT, "modules": MODULES,
        "tgifStaticToken": f"{runtime}/secrets/tgif-static-token",
        "data": f"{runtime}/data", "tgifRun": f"{runtime}/tgif-run",
    }
    backend = "container" if containerized else "native"
    native_provenance = None
    if not containerized:
        native_source = load("asr_native_urf_plan_source", "asr-bridge-urf-native-runtime.py")
        digital_source = load("asr_native_urf_plan_digital_source", "asr-bridge-runtime-sources.py")
        native_provenance = native_source.describe(digital_source)
    services = ([f"allscan-reimagined-urf-tgif-{settings.bridge_id}.service"] if containerized else [
        f"allscan-reimagined-urfd-{settings.bridge_id}.service",
        f"allscan-reimagined-tcd-{settings.bridge_id}.service",
        f"allscan-reimagined-tgif-{settings.bridge_id}.service",
    ])
    result = {
        "schema": 2, "bridgeType": "dmr", "runtime": "urf-tgif", "backend": backend,
        "settings": asdict(settings),
        "mainNode": main_node, "bridgeNode": node,
        "dmrNetworkId": tgif_network_id(settings.dmr_id, node, settings.tgif_essid),
        "ports": ports, "publicPorts": public_ports, "listenerPorts": listener_ports,
        "proxyWarnings": proxy_warnings,
        "legacyBridgeNodes": list(settings.legacy_bridge_nodes),
        "migrationWarnings": migration_warnings(root, rpt_text, settings.legacy_bridge_nodes),
        "previousRuntimePorts": {key: int(value) for key, value in
                                  (previous_record.get("ports", {}) if isinstance(previous_record.get("ports"), dict) else {}).items()
                                  if isinstance(value, int) and 1 <= value <= 65535},
        "protocolIds": {
            "nxdn": settings.nxdn_reflector_id if settings.nxdn_reflector_id is not None else max(1, (main_node // 10) % 10000),
            "p25": settings.p25_reflector_id if settings.p25_reflector_id is not None else max(1, (main_node // 10) % 65536),
            "ysf": settings.ysf_registration_id if settings.ysf_registration_id is not None else max(1, (main_node // 10) % 100000),
        },
        "asteriskNodeTemplate": node_template,
        "enabledProtocols": {
            "dstar": settings.dstar_enabled, "dmrplus": settings.dmrplus_enabled,
            "dmr": settings.dmr_enabled, "m17": settings.m17_enabled,
            "p25": settings.p25_enabled, "nxdn": settings.nxdn_enabled,
            "ysf": settings.ysf_enabled, "urf": settings.urf_enabled,
        },
        "resources": resources,
        "services": services,
        "runtimeRequirements": ({"dockerCompose": True} if containerized else {
            "os": "debian-12-or-13", "architectures": ["amd64", "arm64", "armhf"],
            "systemd": True, "account": "asr-bridge", "provenance": native_provenance,
        }),
        "images": ({"urf": "allscan-reimagined/urf:managed", "tcd": "allscan-reimagined/urf-tcd:managed"}
                   if containerized else {}),
        "hostState": {
            "configSha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "rptSha256": hashlib.sha256(rpt_path.read_bytes()).hexdigest(),
            "modulesSha256": hashlib.sha256(modules_path.read_bytes()).hexdigest(),
        },
    }
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result

def urfd_ini(plan: dict) -> str:
    s, p = plan["settings"], plan["ports"]
    public = plan.get("listenerPorts", plan.get("publicPorts", {key: p[key] for key in ("dmr", "m17", "p25", "nxdn", "ysf", "urf")}))
    ids = plan.get("protocolIds", {})
    nxdn_id = ids.get("nxdn", max(1, (int(plan["mainNode"]) // 10) % 10000))
    p25_id = ids.get("p25", max(1, (int(plan["mainNode"]) // 10) % 65536))
    ysf_id = ids.get("ysf", max(1, (int(plan["mainNode"]) // 10) % 100000))
    native = plan.get("backend") == "native"
    data = plan["resources"]["data"] if native else "/data"
    config = f"{plan['resources']['root']}/config" if native else "/config"
    return f"""[Names]\nCallsign = {s['reflector']}\nSysopEmail = asr@localhost.invalid\nCountry = US\nSponsor = {s['callsign']} ASR\nDashboardUrl = http://127.0.0.1/\n\n[IP Addresses]\nIPv4Binding = 0.0.0.0\n\n[Modules]\nModules = A\nDescriptionA = All Modes\n\n[Transcoder]\nPort = {p['transcoder']}\nBindingAddress = 127.0.0.1\nModules = A\n\n[Brandmeister]\nEnable = false\nPort = 10002\n\n[DCS]\nEnable = {str(s.get('dstar_enabled', True)).lower()}\nPort = {p['dcs']}\n[DExtra]\nEnable = {str(s.get('dstar_enabled', True)).lower()}\nPort = {p['dextra']}\n[DPlus]\nEnable = {str(s.get('dstar_enabled', True)).lower()}\nPort = {p['dplus']}\n[G3]\nEnable = false\n[DMRPlus]\nEnable = {str(s.get('dmrplus_enabled', True)).lower()}\nPort = {p['dmrplus']}\n[M17]\nEnable = {str(s.get('m17_enabled', True)).lower()}\nPort = {public['m17']}\n[MMDVM]\nEnable = {str(s.get('dmr_enabled', True)).lower()}\nPort = {public['dmr']}\nDefaultId = {s['dmr_id']}\n[NXDN]\nEnable = {str(s.get('nxdn_enabled', True)).lower()}\nPort = {public['nxdn']}\nAutoLinkModule = A\nReflectorID = {nxdn_id}\n[P25]\nEnable = {str(s.get('p25_enabled', True)).lower()}\nPort = {public['p25']}\nAutoLinkModule = A\nReflectorID = {p25_id}\n[URF]\nEnable = {str(s.get('urf_enabled', True)).lower()}\nPort = {public['urf']}\n[USRP]\nEnable = true\nCallsign = {s['callsign']}\nIPAddress = 127.0.0.1\nRxPort = {p['usrp_rx']}\nTxPort = {p['usrp_tx']}\nModule = A\n[YSF]\nEnable = {str(s.get('ysf_enabled', True)).lower()}\nPort = {public['ysf']}\nEnableDGID = false\nAutoLinkModule = A\nDefaultTxFreq = 446500000\nDefaultRxFreq = 446500000\nRegistrationID = {ysf_id}\nRegistrationName = US {s['reflector']}\nRegistrationDescription = ASR managed URF\n[DMR ID DB]\nMode = http\nFilePath = {data}/dmrid.dat\nURL = http://xlxapi.rlx.lu/api/exportdmr.php\nRefreshMin = 179\n[NXDN ID DB]\nMode = http\nFilePath = {data}/nxdn.dat\nURL = https://radioid.net/static/nxdn.csv\nRefreshMin = 1440\n[YSF TX/RX DB]\nMode = http\nFilePath = {data}/ysfnode.dat\nURL = http://xlxapi.rlx.lu/api/exportysfrepeaters.php\nRefreshMin = 191\n[Files]\nPidPath = {data}/urfd.pid\nXmlPath = {data}/urfd.xml\nWhitelistPath = {config}/urfd.whitelist\nBlacklistPath = {config}/urfd.blacklist\nInterlinkPath = {config}/urfd.interlink\nG3TerminalPath = {config}/urfd.terminal\n"""

def tcd_ini(plan: dict) -> str:
    return f"""Port = {plan['ports']['transcoder']}\nServerAddress = 127.0.0.1\nModules = A\nDStarGainIn = 0\nDStarGainOut = 0\nDmrYsfGainIn = 0\nDmrYsfGainOut = 0\nUsrpTxGain = 0\nUsrpRxGain = 0\n"""

def managed_block_node(text: str, bridge_id: str) -> int | None:
    begin = f"{load('asr_setup_m17_managed_probe','asr-bridge-setup-m17.py').MANAGED_PREFIX}BEGIN {bridge_id}"
    end = f"{load('asr_setup_m17_managed_probe','asr-bridge-setup-m17.py').MANAGED_PREFIX}END {bridge_id}"
    match = re.search(rf"(?ms)^[ \t]*{re.escape(begin)}\n(.*?)^[ \t]*{re.escape(end)}[ \t]*$", text)
    if not match:
        return None
    header = re.search(r"(?m)^\s*\[([0-9]+)\](?:\\?\([^]\n]+\))?(?:\s|;|$)", match.group(1))
    if not header:
        raise PlanError(f"managed rpt.conf block {bridge_id} has no valid numeric node section")
    return int(header.group(1))


def validate_managed_bridge_layout(text: str, urf_id: str, urf_node: int,
                                   zello_nodes: dict[str, int]) -> None:
    actual_urf = managed_block_node(text, urf_id)
    if actual_urf != urf_node:
        raise PlanError(f"rendered URF managed block node {actual_urf} does not match authoritative node {urf_node}")
    if not re.search(rf"(?m)^\s*{urf_node}\s*=", text):
        raise PlanError(f"rendered rpt.conf is missing [nodes] mapping for URF node {urf_node}")
    for bridge_id, zello_node in zello_nodes.items():
        if zello_node == urf_node:
            raise PlanError("URF and Zello cannot share an AllStar bridge node")
        actual_zello = managed_block_node(text, bridge_id)
        if actual_zello != zello_node:
            raise PlanError(f"rendered Zello managed block {bridge_id} node {actual_zello} does not match configured node {zello_node}")
        if not re.search(rf"(?m)^\s*{zello_node}\s*=", text):
            raise PlanError(f"rendered rpt.conf is missing [nodes] mapping for Zello node {zello_node}")


def zello_usrp_ports(root: Path, bridge: dict[str, Any]) -> dict[str, int]:
    saved = bridge.get("setupPorts")
    if isinstance(saved, dict):
        try:
            rx, tx = int(saved["usrp_rx"]), int(saved["usrp_tx"])
            if 1024 <= rx <= 65535 and 1024 <= tx <= 65535 and rx != tx:
                return {"usrp_rx": rx, "usrp_tx": tx}
        except (KeyError, TypeError, ValueError):
            pass
    # Some older externally managed Zello installs kept USRP ports in a
    # systemd drop-in rather than config.json. Read only these two non-secret
    # numeric settings; never echo or return the rest of the unit environment.
    override = rooted(root, "/etc/systemd/system/asl-zello-bridge.service.d/override.conf")
    if override.is_file() and not override.is_symlink():
        text = override.read_text(encoding="utf-8", errors="replace")
        values = {}
        for key in ("USRP_RXPORT", "USRP_TXPORT"):
            match = re.search(rf"(?m)^\s*Environment={key}=([0-9]{{4,5}})\s*$", text)
            if match:
                values[key] = int(match.group(1))
        if len(values) == 2 and values["USRP_RXPORT"] != values["USRP_TXPORT"]:
            return {"usrp_rx": values["USRP_RXPORT"], "usrp_tx": values["USRP_TXPORT"]}
    raise PlanError("URF node reassignment would orphan the configured Zello bridge: its USRP receive/transmit ports are not recorded safely")


def render_native_admin_env(root: Path, mode: str, public_port: int,
                            listener_port: int, previous_ports: dict[str, int]) -> tuple[bytes, int]:
    logical = f"{ADMIN_ENV_DIR}/{mode}.env"
    path = rooted(root, logical)
    existing = native_admin_env(root, mode)
    upstream = listener_port
    if existing.get("ASR_UPSTREAM_PORT"):
        try:
            current_upstream = int(existing["ASR_UPSTREAM_PORT"])
        except ValueError as exc:
            raise PlanError(f"native {mode.upper()} admin proxy upstream port is invalid") from exc
        # Do not redirect an independently managed/legacy service. Only
        # follow the new URF listener when the old upstream was owned by URF.
        if current_upstream not in (int(previous_ports.get(mode, -1)), listener_port):
            upstream = current_upstream
    values = {
        "ASR_BRIDGE_ID": existing.get("ASR_BRIDGE_ID", mode),
        "ASR_BRIDGE_MODE": existing.get("ASR_BRIDGE_MODE", mode),
        "ASR_PUBLIC_PORT": str(public_port),
        "ASR_UPSTREAM_HOST": existing.get("ASR_UPSTREAM_HOST", "127.0.0.1"),
        "ASR_UPSTREAM_PORT": str(upstream),
        "ASR_GLOBAL_POLICY_DIR": existing.get("ASR_GLOBAL_POLICY_DIR", "/var/lib/allscan-reimagined/admin"),
    }
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            if key not in seen:
                output.append(f"{key}={values[key]}")
                seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    mode_bits = path.stat().st_mode & 0o777 if path.is_file() else 0o640
    return (("\n".join(output).rstrip() + "\n").encode(), mode_bits)


def integration_files(root: Path, plan: dict) -> dict[str, tuple[bytes, int]]:
    config_path=safe_regular(root, CONFIG); rpt_path=safe_regular(root, RPT); modules_path=safe_regular(root, MODULES)
    config=json.loads(config_path.read_text(encoding="utf-8")); s=plan["settings"]; node=plan["bridgeNode"]; p=plan["ports"]
    role=s.get("bridge_role","standard")
    enabled=plan.get("enabledProtocols", {})
    urf_modes=[mode for mode in ("dmr","m17","p25","nxdn","ysf") if enabled.get(mode, True)]
    entry={"id":s["bridge_id"],"mode":"dmr","architecture":"urf-reflector","bridgeType":"urf-reflector","node":str(node),"title":s["title"],"detailTitle":"Connected Clients","friendlyName":s["title"],"cardType":"dmr_net" if role=="net" else "standard","instance":s["bridge_id"],"bridgePermission":"self_owned","backendMode":"managed","clientSource":"dmr","urfReflector":True,"urfGroupId":s["bridge_id"],"urfName":s["reflector"],"urfModes":urf_modes,"publicPorts":plan.get("publicPorts", {}),"protocolIds":plan.get("protocolIds", {}),"enabledProtocols":enabled,"fixedDestination":str(s["tgif_tg"]),"setupPorts":p,"setupPublicPorts":plan.get("publicPorts", {}),"setupListenerPorts":plan.get("listenerPorts", {}),"proxyWarnings":plan.get("proxyWarnings", []),"tgifTalkgroup":str(s["tgif_tg"]),"dmrId":str(s["dmr_id"]),"dmrNetworkId":str(plan["dmrNetworkId"]),"allowTune":role=="net","approvedDestinations":[str(s["tgif_tg"])] if role=="net" else [],"managedNetControl":role=="net","managedTargetFile":f"{plan['resources']['root']}/tgif-run/net-target" if role=="net" else "","tgifStaticTokenPath":plan["resources"]["tgifStaticToken"] if role=="net" else "","fixedBridgeRecovery":True,"liveAudioVerified":False}
    bridges=config.setdefault("bridges",[]); matches=[i for i,b in enumerate(bridges) if isinstance(b,dict) and b.get("id")==s["bridge_id"]]
    if len(matches)>1: raise PlanError("duplicate bridge identity")
    if matches:
        previous = bridges[matches[0]]
        if isinstance(previous.get("fixedBridgeRecovery"), bool):
            entry["fixedBridgeRecovery"] = previous["fixedBridgeRecovery"]
        bridges[matches[0]]=entry
    else: bridges.append(entry)
    retired_nodes = set(plan.get("legacyBridgeNodes", []))
    if retired_nodes:
        for bridge in bridges:
            if not isinstance(bridge, dict):
                continue
            try:
                bridge_node = int(bridge.get("node"))
            except (TypeError, ValueError):
                continue
            if bridge_node in retired_nodes:
                bridge["fixedBridgeRecovery"] = False
    if role == "net" and str(config.get("netBridgeMode", "")).lower() not in {"dmr", "ysf", "p25", "nxdn", "m17"}:
        config["netBridgeMode"] = "dmr"
    m17=load("asr_setup_m17_urf","asr-bridge-setup-m17.py")
    rpt=rpt_path.read_text()
    if role == "net" and config.get("netBridgeMode") == "dmr":
        for other in bridges:
            if not isinstance(other, dict) or other.get("id") == s["bridge_id"] or other.get("cardType") not in {"dmr_net","ysf_net","p25_net","nxdn_net","m17_net"}:
                continue
            old_id=str(other.get("id") or "")
            if old_id:
                begin=f"{m17.MANAGED_PREFIX}BEGIN {old_id}"; end=f"{m17.MANAGED_PREFIX}END {old_id}"
                rpt=re.sub(rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*\n?","",rpt)
    existing_urf_node = managed_block_node(rpt, s["bridge_id"])
    if role != "net" or config.get("netBridgeMode") == "dmr":
        rpt=m17.insert_nodes_mapping(rpt,node)
        rpt=m17.upsert_node_section(rpt,s["bridge_id"],node,p["usrp_rx"],p["usrp_tx"])
        rpt=rpt.replace(f"[{node}] ; M17 Bridge ({s['bridge_id']})",f"[{node}]({plan['asteriskNodeTemplate']}) ; DMR/URF Bridge ({s['bridge_id']})")
        if existing_urf_node is not None and existing_urf_node != node and managed_block_node(rpt, s["bridge_id"]) != node:
            raise PlanError(f"could not migrate stale URF managed block from node {existing_urf_node} to authoritative node {node}")
    zello_nodes: dict[str, int] = {}
    # A dedicated Zello bridge must retain its own app_rpt node and USRP pair.
    # This also repairs older nodes where the URF block accidentally occupied
    # the Zello node number. External Zello service configuration is untouched.
    for zello in bridges:
        if not isinstance(zello, dict) or str(zello.get("mode", "")).lower() != "zello":
            continue
        try:
            zello_node = int(zello.get("node"))
        except (TypeError, ValueError):
            raise PlanError("configured Zello bridge has an invalid AllStar node")
        if zello_node == node or zello_node == int(plan["mainNode"]):
            raise PlanError("URF and Zello cannot share an AllStar bridge node")
        zello_id = str(zello.get("id") or "zello")
        zello_nodes[zello_id] = zello_node
        zello_ports = zello_usrp_ports(root, zello)
        zello["setupPorts"] = zello_ports
        rpt=m17.insert_nodes_mapping(rpt,zello_node)
        rpt=m17.upsert_node_section(rpt,zello_id,zello_node,zello_ports["usrp_rx"],zello_ports["usrp_tx"])
        rpt=rpt.replace(f"[{zello_node}] ; M17 Bridge ({zello_id})",f"[{zello_node}]({plan['asteriskNodeTemplate']}) ; Zello Bridge ({zello_id})")
        validate_node_inheritance(rpt,zello_node,plan["asteriskNodeTemplate"])
    if role != "net" or config.get("netBridgeMode") == "dmr":
        validate_managed_bridge_layout(rpt, s["bridge_id"], node, zello_nodes)
    modules=m17.ensure_usrp_module(modules_path.read_text()).replace("ASR M17 bridge","ASR managed bridge")
    outputs = {
        CONFIG: ((json.dumps(config, sort_keys=True, indent=2) + "\n").encode(), config_path.stat().st_mode & 0o777),
        RPT: (rpt.encode(), rpt_path.stat().st_mode & 0o777),
        MODULES: (modules.encode(), modules_path.stat().st_mode & 0o777),
        plan["resources"]["urfConfig"]: (urfd_ini(plan).encode(), 0o644),
        plan["resources"]["tcdConfig"]: (tcd_ini(plan).encode(), 0o644),
        plan["resources"]["blacklist"]: (b"", 0o644),
    }
    previous_ports = plan.get("previousRuntimePorts", {})
    if plan.get("legacyBridgeNodes"):
        quiesce_path = rooted(root, "/var/lib/allscan-reimagined/bridge-setup/migration-quiesced.json")
        current_nodes: set[int] = set()
        if quiesce_path.is_file() and not quiesce_path.is_symlink():
            try:
                current = json.loads(quiesce_path.read_text(encoding="utf-8"))
                current_nodes = {int(item) for item in current.get("nodes", []) if str(item).isdigit()}
            except (OSError, ValueError, AttributeError):
                raise PlanError("existing bridge migration quiesce state is invalid")
        current_nodes.update(int(item) for item in plan["legacyBridgeNodes"])
        outputs["/var/lib/allscan-reimagined/bridge-setup/migration-quiesced.json"] = (
            (json.dumps({"schema": 1, "nodes": sorted(current_nodes), "bridgeId": plan["settings"]["bridge_id"]}, sort_keys=True, indent=2) + "\n").encode(), 0o600)
    enabled = plan.get("enabledProtocols", {})
    for mode in ("m17", "p25", "nxdn", "ysf"):
        if enabled.get(mode, True):
            logical = f"{ADMIN_ENV_DIR}/{mode}.env"
            outputs[logical] = render_native_admin_env(
                root, mode, plan["publicPorts"][mode], plan["listenerPorts"][mode], previous_ports)
    return outputs
