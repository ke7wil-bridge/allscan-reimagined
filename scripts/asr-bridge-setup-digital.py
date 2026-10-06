#!/usr/bin/env python3
"""Read-only ASL3 provisioning plans for isolated P25, NXDN and YSF bridges.

No host mutation belongs in this module. The installer must re-plan under the
privileged lock and compare the preview digest before applying any changes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
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
MODES = {"p25": "P25Gateway", "nxdn": "NXDNGateway", "ysf": "YSFGateway"}

class PlanError(RuntimeError):
    pass

@dataclass(frozen=True)
class DigitalSettings:
    mode: str
    bridge_id: str
    callsign: str
    digital_id: int
    destination: int
    reflector_host: str
    reflector_port: int
    title: str = ""
    bridge_node: int | None = None
    bridge_role: str = "standard"
    reflector_name: str = ""


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise PlanError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate(value: DigitalSettings) -> DigitalSettings:
    if value.mode not in MODES:
        raise PlanError("unsupported digital mode")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", value.bridge_id):
        raise PlanError("invalid bridge ID")
    callsign = value.callsign.upper().strip()
    if not re.fullmatch(r"[A-Z0-9]{3,10}", callsign) or not re.search(r"[A-Z]", callsign) or not re.search(r"[0-9]", callsign):
        raise PlanError("invalid station callsign")
    if not isinstance(value.digital_id, int) or not 1 <= value.digital_id <= 9_999_999:
        raise PlanError("enter a valid 1-7 digit digital ID")
    if not isinstance(value.destination, int) or not 11 <= value.destination <= 65534:
        raise PlanError("destination is outside the supported range")
    reserved = set(range(1, 11)) | {20, 9999}
    if value.mode == "p25":
        reserved |= {10999}
    if value.destination in reserved:
        raise PlanError("destination is reserved")
    if value.mode == "ysf" and value.destination < 10000:
        raise PlanError("YSF destination must have five digits")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value.reflector_host):
        raise PlanError("invalid reflector host")
    if not isinstance(value.reflector_port, int) or not 1 <= value.reflector_port <= 65535:
        raise PlanError("invalid reflector port")
    reflector_name = value.reflector_name.strip()
    if value.mode == "ysf" and reflector_name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}", reflector_name):
        raise PlanError("invalid YSF reflector name")
    if not isinstance(value.title, str) or len(value.title) > 80 or any(ch in value.title for ch in "\r\n\0"):
        raise PlanError("invalid bridge title")
    if value.bridge_role not in {"standard", "net"}:
        raise PlanError("bridge role must be standard or net")
    if value.bridge_node is not None and (not isinstance(value.bridge_node, int) or not 1001 <= value.bridge_node <= 1999):
        raise PlanError("private bridge node must be in 1001-1999")
    return DigitalSettings(**{**asdict(value), "callsign": callsign, "reflector_name": reflector_name})


def rooted(root: Path, logical: str) -> Path:
    return load("asr_backend_digital", "asr-provisioning-backend.py").map_path(root, logical)


def safe_regular(root: Path, logical: str) -> Path:
    path = rooted(root, logical)
    current = root.resolve()
    for component in path.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise PlanError(f"unsafe symbolic link in {logical}")
    if not path.is_file():
        raise PlanError(f"required ASL3 file is missing: {logical}")
    return path


def port_block(bridge_id: str) -> dict[str, int]:
    # 16 adjacent ports per instance; the plan includes every receiver.
    base = 23000 + (zlib.crc32(bridge_id.encode()) & 0xffffffff) % 1800 * 16
    return dict(zip(("usrp_rx", "usrp_tx", "analog_rx", "analog_tx",
                     "mmdvm_rx", "mmdvm_tx", "gateway_rx", "gateway_tx",
                     "network", "remote", "emulator", "mqtt"),
                    range(base, base + 12)))


def plan(root: Path, settings: DigitalSettings) -> dict[str, Any]:
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
    core = load("asr_setup_core_digital", "asr-bridge-setup-core.py")
    source = load("asr_runtime_sources_digital", "asr-bridge-runtime-sources.py")
    facts = core.ASL3Adapter(root).detect()
    if root == Path("/"):
        if not facts.systemd:
            raise PlanError("managed digital bridges require systemd")
        if not load("asr_backend_digital_module", "asr-provisioning-backend.py").asterisk_module_available():
            raise PlanError("Asterisk chan_usrp module is not installed")
    runtime = source.describe(settings.mode)
    try:
        main_node = int(config["node"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PlanError("main AllStar node is missing") from exc
    matches = [bridge for bridge in config["bridges"]
               if isinstance(bridge, dict) and bridge.get("id") == settings.bridge_id]
    shared_net_node = (settings.bridge_role == "net"
                       and any(isinstance(item, dict)
                               and item.get("id") != settings.bridge_id
                               and item.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
                               and str(item.get("node", "")) == "1999"
                               for item in config["bridges"]))
    if len(matches) > 1 or (matches and matches[0].get("mode") != settings.mode):
        raise PlanError("bridge ID belongs to another or duplicate bridge")
    record_path = rooted(root, f"{STATE}/{settings.bridge_id}.json")
    if matches:
        if not record_path.is_file() or record_path.is_symlink():
            raise PlanError("cannot change an unowned bridge instance")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("bridgeType") != settings.mode:
            raise PlanError("installation ownership record does not match")
        try:
            node = 1999 if settings.bridge_role == "net" else int(matches[0]["node"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError("existing bridge node is invalid") from exc
        if record.get("bridgeNode") != node and not (
                settings.bridge_role == "net" and node == 1999
                and record.get("bridgeType") == settings.mode):
            raise PlanError("existing bridge node differs from installation record")
    else:
        used = set(facts.node_numbers) | {main_node}
        for bridge in config["bridges"]:
            if isinstance(bridge, dict):
                try:
                    used.add(int(bridge.get("node")))
                except (ValueError, TypeError):
                    pass
        node = 1999 if settings.bridge_role == "net" else (settings.bridge_node or core.allocate_node(used))
        if node in used and not shared_net_node:
            raise PlanError("requested private node is already in use")
    ports = port_block(settings.bridge_id)
    if settings.bridge_role == "net":
        ports["usrp_rx"], ports["usrp_tx"] = core.unified_net_usrp_ports()
    for bridge in config["bridges"]:
        if not isinstance(bridge, dict) or bridge.get("id") == settings.bridge_id:
            continue
        occupied = bridge.get("setupPorts", {})
        candidate_ports = set(ports.values())
        occupied_ports = ({p for p in occupied.values() if isinstance(p, int)}
                          if isinstance(occupied, dict) else set())
        if (settings.bridge_role == "net"
                and bridge.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}):
            candidate_ports -= set(core.unified_net_usrp_ports())
            occupied_ports -= set(core.unified_net_usrp_ports())
        if candidate_ports & occupied_ports:
            raise PlanError("instance port block overlaps another ASR bridge")
    listening_candidates = set(ports.values())
    if settings.bridge_role == "net" and shared_net_node:
        listening_candidates -= set(core.unified_net_usrp_ports())
    if not matches and listening_candidates & set(facts.listening_udp_ports):
        raise PlanError("instance port block overlaps an active UDP listener")
    if (not matches and settings.mode in {"p25", "nxdn"}
            and ports["mqtt"] in facts.listening_tcp_ports):
        raise PlanError("private MQTT port overlaps an active TCP listener")
    gateway = MODES[settings.mode]
    instance = settings.bridge_id
    resources = {
        "gateway": f"/opt/{gateway}_{instance}/{gateway}.ini",
        "analog": f"/opt/Analog_Bridge_{instance}/Analog_Bridge.ini",
        "mmdvm": f"/opt/MMDVM_Bridge_{instance}/MMDVM_Bridge.ini",
        "logMarker": f"/var/log/mmdvm/.asr-{instance}",
        "config": CONFIG, "rpt": RPT, "modules": MODULES,
    }
    if settings.mode != "p25":
        resources["emulator"] = f"/opt/md380-emu_{instance}/md380-emu"
    if settings.mode in {"p25", "nxdn"}:
        resources.update({
            "mqttConfig": f"/etc/mosquitto/asr-{instance}.conf",
            "mqttPasswords": f"/etc/mosquitto/asr-{instance}.passwords",
            "mqttAcl": f"/etc/mosquitto/asr-{instance}.acl",
        })
    services = [f"{settings.mode}gateway-{instance}.service",
                f"mmdvm-bridge-{instance}.service",
                f"analog-bridge-{instance}.service"]
    if settings.mode != "p25":
        services.append(f"md380-emu-{instance}.service")
    if settings.mode in {"p25", "nxdn"}:
        services.append(f"asr-mqtt-{instance}.service")
    for path in (*resources.values(), *(f"/etc/systemd/system/{name}" for name in services)):
        candidate = rooted(root, path)
        if candidate.is_symlink():
            raise PlanError(f"unsafe resource link: {path}")
        if candidate.exists() and not matches and path not in (CONFIG, RPT, MODULES):
            raise PlanError(f"unmanaged bridge resource already exists: {path}")
    sources = {"gateway": runtime["gateway"], "binaryArtifacts": runtime["binaryArtifacts"],
               "softwareVocoder": runtime["softwareVocoder"]}
    result = {
        "schema": 1, "bridgeType": settings.mode, "settings": asdict(settings),
        "mainNode": main_node, "bridgeNode": node, "ports": ports,
        "resources": resources, "services": services, "sources": sources,
        "hostState": {
            "configSha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "rptSha256": hashlib.sha256(rpt_path.read_bytes()).hexdigest(),
            "modulesSha256": hashlib.sha256(modules_path.read_bytes()).hexdigest(),
        },
    }
    result["digest"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result
