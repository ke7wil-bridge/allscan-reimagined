#!/usr/bin/env python3
"""Read-only provisioning plans for ASR-managed direct DMR bridges.

A Standard bridge relays one fixed TGIF talkgroup on its own private node.
A Net bridge shares node 1999 with the other Net Bridge modes and follows
the talkgroup selected through the managed Net DMR controller.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
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
ROOT_BASE = "/opt/allscan-reimagined-bridges"


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class DmrSettings:
    bridge_id: str
    callsign: str
    dmr_id: int
    talkgroup: int
    network: str = "tgif"
    master_host: str = "tgif.network"
    master_port: int = 62031
    title: str = "DMR Bridge"
    bridge_node: int | None = None
    tgif_essid: int | None = None
    timeslot: int = 2
    color_code: int = 1
    dmr_to_asl_gain_db: float = 0.0
    asl_to_dmr_gain_db: float = 0.0
    bridge_role: str = "standard"


NET_NODE = 1999
NET_CARD_TYPES = {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise PlanError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rooted(root: Path, logical: str) -> Path:
    return load("asr_backend_direct_dmr", "asr-provisioning-backend.py").map_path(root, logical)


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


def network_id(dmr_id: int, essid: int | None) -> int:
    if dmr_id <= 999_999:
        if essid is not None:
            raise PlanError("six-digit repeater DMR IDs must not use a TGIF hotspot ESSID")
        return dmr_id
    return dmr_id * 100 + (essid if essid is not None else 1)


def gain_factor(db: float) -> float:
    return round(math.pow(10.0, db / 20.0), 4)


def validate(value: DmrSettings) -> DmrSettings:
    bridge_id = value.bridge_id.strip().lower()
    callsign = value.callsign.strip().upper()
    network = value.network.strip().lower()
    host = value.master_host.strip().lower()
    title = value.title.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise PlanError("invalid bridge ID")
    if not re.fullmatch(r"[A-Z0-9]{3,10}", callsign) or not re.search(r"[A-Z]", callsign) or not re.search(r"[0-9]", callsign):
        raise PlanError("invalid station callsign")
    if network != "tgif":
        raise PlanError("only direct TGIF DMR is currently supported")
    if not isinstance(value.dmr_id, int) or not 1 <= value.dmr_id <= 9_999_999:
        raise PlanError("enter a valid 1-7 digit DMR ID")
    if not isinstance(value.talkgroup, int) or not 1 <= value.talkgroup <= 16_777_215 or value.talkgroup == 4000:
        raise PlanError("enter a valid fixed DMR talkgroup")
    if value.tgif_essid is not None and (not isinstance(value.tgif_essid, int) or not 1 <= value.tgif_essid <= 99):
        raise PlanError("TGIF hotspot ESSID must be 1-99")
    network_id(value.dmr_id, value.tgif_essid)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
        raise PlanError("invalid DMR master hostname")
    if not isinstance(value.master_port, int) or not 1 <= value.master_port <= 65535:
        raise PlanError("invalid DMR master port")
    if value.timeslot not in (1, 2):
        raise PlanError("DMR timeslot must be 1 or 2")
    if not isinstance(value.color_code, int) or not 0 <= value.color_code <= 15:
        raise PlanError("DMR color code must be 0-15")
    for label, gain in (("DMR-to-ASL", value.dmr_to_asl_gain_db), ("ASL-to-DMR", value.asl_to_dmr_gain_db)):
        if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not -20.0 <= gain <= 12.0:
            raise PlanError(f"{label} gain must be between -20 and +12 dB")
    if not title or len(title) > 80 or any(ch in title for ch in "\r\n\0"):
        raise PlanError("invalid bridge title")
    if value.bridge_node is not None and not 1001 <= value.bridge_node <= 1999:
        raise PlanError("private bridge node must be in 1001-1999")
    if value.bridge_role not in {"standard", "net"}:
        raise PlanError("bridge role must be standard or net")
    if value.bridge_role == "net" and value.bridge_node not in (None, NET_NODE):
        raise PlanError("the DMR Net Bridge always uses node 1999")
    return DmrSettings(bridge_id, callsign, value.dmr_id, value.talkgroup, network,
                       host, value.master_port, title, value.bridge_node,
                       value.tgif_essid, value.timeslot, value.color_code,
                       float(value.dmr_to_asl_gain_db), float(value.asl_to_dmr_gain_db),
                       value.bridge_role)


def port_block(bridge_id: str) -> dict[str, int]:
    base = 23000 + (zlib.crc32(bridge_id.encode()) & 0xFFFFFFFF) % 1800 * 16
    return {
        "usrp_rx": base,
        "usrp_tx": base + 1,
        "analog_rx": base + 2,
        "analog_tx": base + 3,
        "mmdvm_local": base + 4,
        "mmdvm_remote": base + 5,
        "emulator": base + 10,
    }


def plan(root: Path, settings: DmrSettings) -> dict[str, Any]:
    settings = validate(settings)
    config_path = safe_regular(root, CONFIG)
    rpt_path = safe_regular(root, RPT)
    modules_path = safe_regular(root, MODULES)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("bridges", []), list):
        raise PlanError("ASR bridges are invalid")
    core = load("asr_setup_core_direct_dmr", "asr-bridge-setup-core.py")
    facts = core.ASL3Adapter(root).detect()
    main_node = int(config["node"])
    matches = [item for item in config["bridges"] if isinstance(item, dict) and item.get("id") == settings.bridge_id]
    if len(matches) > 1 or (matches and matches[0].get("mode") != "dmr"):
        raise PlanError("bridge ID belongs to another or duplicate bridge")
    net = settings.bridge_role == "net"
    shared_net_node = net and any(
        isinstance(item, dict) and item.get("id") != settings.bridge_id
        and item.get("cardType") in NET_CARD_TYPES and str(item.get("node", "")) == str(NET_NODE)
        for item in config["bridges"])
    record_path = rooted(root, f"{STATE}/{settings.bridge_id}.json")
    if matches:
        if not record_path.is_file() or record_path.is_symlink():
            raise PlanError("cannot edit a DMR bridge without its ASR installation record")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("bridgeType") != "dmr" or record.get("architecture") != "direct-dvswitch":
            raise PlanError("installed DMR backend is not an ASR direct-DVSwitch instance")
        node = int(record["bridgeNode"])
        if (record.get("role") or "standard") != settings.bridge_role:
            raise PlanError("installed DMR bridge role is immutable; remove and reinstall to change it")
        if settings.bridge_node not in (None, node):
            raise PlanError("installed DMR bridge node is immutable; remove and reinstall to change it")
    else:
        used = set(facts.node_numbers) | {main_node}
        for bridge in config["bridges"]:
            if isinstance(bridge, dict):
                try:
                    used.add(int(bridge.get("node")))
                except (TypeError, ValueError):
                    pass
        node = NET_NODE if net else (settings.bridge_node or core.allocate_node(used))
        if node in used and not shared_net_node:
            raise PlanError("requested private node is already in use")
    ports = port_block(settings.bridge_id)
    if net:
        ports["usrp_rx"], ports["usrp_tx"] = core.unified_net_usrp_ports()
    endpoint_guard = load("asr_bridge_endpoint_guard_asr_bridge_setup_dmr", "asr-bridge-endpoint-collisions.py")
    try:
        endpoint_guard.check_candidate(root, config, settings.bridge_id, "dmr",
                                       ports, role=settings.bridge_role)
    except ValueError as exc:
        raise PlanError(str(exc)) from exc
    base = f"{ROOT_BASE}/{settings.bridge_id}"
    services = [f"asr-dmr-vocoder-{settings.bridge_id}.service",
                f"asr-dmr-mmdvm-{settings.bridge_id}.service",
                f"asr-dmr-analog-{settings.bridge_id}.service",
                f"asr-dmr-admin-{settings.bridge_id}.service"]
    result = {
        "schema": 1, "bridgeType": "dmr", "architecture": "direct-dvswitch",
        "settings": asdict(settings), "mainNode": main_node, "bridgeNode": node,
        "dmrNetworkId": network_id(settings.dmr_id, settings.tgif_essid),
        "ports": ports, "gains": {"dmrToAsl": gain_factor(settings.dmr_to_asl_gain_db),
                                     "aslToDmr": gain_factor(settings.asl_to_dmr_gain_db)},
        "role": settings.bridge_role,
        "resources": {"root": base, "config": CONFIG, "rpt": RPT,
                      "modules": MODULES, "record": f"{STATE}/{settings.bridge_id}.json",
                      **({"netTarget": f"{base}/tgif-run/net-target",
                          "staticToken": f"{base}/secrets/tgif-static-token"} if net else {})},
        "services": services,
        "hostState": {"configSha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                      "rptSha256": hashlib.sha256(rpt_path.read_bytes()).hexdigest(),
                      "modulesSha256": hashlib.sha256(modules_path.read_bytes()).hexdigest()},
    }
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
