#!/usr/bin/env python3
"""Read-only planning and host rendering for ASR-managed Zello."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import urllib.parse
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONFIG = "/etc/allscan-reimagined/config.json"
RPT = "/etc/asterisk/rpt.conf"
MODULES = "/etc/asterisk/modules.conf"
STATE = "/var/lib/allscan-reimagined/bridge-setup/installations"
ROOT = "/opt/allscan-reimagined-bridges/zello"

class PlanError(RuntimeError):
    pass

@dataclass(frozen=True)
class ZelloSettings:
    bridge_id: str
    username: str
    channel: str
    issuer: str
    ws_endpoint: str = "wss://zello.io/ws"
    title: str = "Zello Bridge"
    bridge_node: int | None = None

def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise PlanError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def rooted(root: Path, logical: str) -> Path:
    return root / logical.lstrip("/")

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

def port_block(bridge_id: str) -> dict[str, int]:
    base = 63000 + (zlib.crc32(bridge_id.encode()) & 0xffffffff) % 900
    return {"usrp_rx": base, "usrp_tx": base + 1}

def validate(value: ZelloSettings) -> ZelloSettings:
    bridge_id = value.bridge_id.strip().lower()
    username = value.username.strip()
    channel = value.channel.strip()
    issuer = value.issuer.strip()
    endpoint = value.ws_endpoint.strip()
    title = value.title.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise PlanError("invalid bridge ID")
    for label, item, maximum in (("Zello username", username, 80), ("Zello channel", channel, 120),
                                 ("Zello issuer", issuer, 160), ("bridge title", title, 80)):
        if not item or len(item) > maximum or any(ch in item for ch in "\r\n\0"):
            raise PlanError(f"invalid {label}")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "wss" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise PlanError("Zello WebSocket endpoint must be a wss:// URL")
    if value.bridge_node is not None and not 1001 <= value.bridge_node <= 1999:
        raise PlanError("private bridge node must be in 1001-1999")
    return ZelloSettings(bridge_id, username, channel, issuer, endpoint, title, value.bridge_node)

def stock_web_dir(root: Path) -> str:
    for logical in ("/var/www/html/allscan", "/srv/http/allscan"):
        if rooted(root, logical).is_dir():
            return logical
    if root == Path("/"):
        raise PlanError("stock AllScan web directory was not detected")
    logical = "/var/www/html/allscan"
    rooted(root, logical).mkdir(parents=True, exist_ok=True)
    return logical

def plan(root: Path, settings: ZelloSettings) -> dict[str, Any]:
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
    core = load("asr_setup_core_zello", "asr-bridge-setup-core.py")
    facts = core.ASL3Adapter(root).detect()
    if root == Path("/"):
        if not facts.systemd:
            raise PlanError("Zello setup requires systemd")
        if not facts.docker or not facts.docker_compose:
            raise PlanError("Zello setup requires Docker with the Compose plugin")
        modules = list((root / "usr/lib").glob("asterisk/modules/chan_usrp.so"))
        modules += list((root / "usr/lib").glob("*/asterisk/modules/chan_usrp.so"))
        if not modules:
            raise PlanError("Asterisk chan_usrp module is not installed")
    try:
        main_node = int(config["node"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("main AllStar node is missing") from exc
    matches = [b for b in config["bridges"] if isinstance(b, dict) and b.get("id") == settings.bridge_id]
    record_path = rooted(root, f"{STATE}/{settings.bridge_id}.json")
    if len(matches) > 1 or (matches and str(matches[0].get("mode", "")).lower() != "zello"):
        raise PlanError("bridge ID belongs to another or duplicate bridge")
    if matches:
        if not record_path.is_file() or record_path.is_symlink():
            raise PlanError("cannot change an unowned Zello bridge")
        try:
            node = int(matches[0]["node"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError("existing Zello bridge node is invalid") from exc
    else:
        used = set(facts.node_numbers) | {main_node}
        for bridge in config["bridges"]:
            if isinstance(bridge, dict):
                try:
                    used.add(int(bridge.get("node")))
                except (TypeError, ValueError):
                    pass
        node = settings.bridge_node or core.allocate_node(used)
        if node in used:
            raise PlanError("requested private node is already in use")
    ports = port_block(settings.bridge_id)
    if not matches and set(ports.values()).intersection(facts.listening_udp_ports):
        raise PlanError("a required Zello USRP port is already in use")
    resources = {
        "root": ROOT,
        "compose": f"{ROOT}/compose.yml",
        "credentials": f"{ROOT}/secrets/zello.json",
        "privateKey": f"{ROOT}/secrets/zello.key",
        "runtime": f"{ROOT}/runtime",
        "stockWeb": stock_web_dir(root),
        "config": CONFIG,
        "rpt": RPT,
        "modules": MODULES,
    }
    result = {
        "schema": 1, "bridgeType": "zello", "settings": asdict(settings),
        "mainNode": main_node, "bridgeNode": node, "ports": ports,
        "resources": resources, "image": "allscan-reimagined/zello:managed",
        "hostState": {
            "configSha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "rptSha256": hashlib.sha256(rpt_path.read_bytes()).hexdigest(),
            "modulesSha256": hashlib.sha256(modules_path.read_bytes()).hexdigest(),
        },
    }
    result["digest"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result

def integration_files(root: Path, plan: dict[str, Any]) -> dict[str, tuple[bytes, int]]:
    config_path = safe_regular(root, CONFIG)
    rpt_path = safe_regular(root, RPT)
    modules_path = safe_regular(root, MODULES)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    settings, node, ports = plan["settings"], plan["bridgeNode"], plan["ports"]
    entry = {
        "id": settings["bridge_id"], "mode": "zello", "node": str(node),
        "title": settings["title"], "detailTitle": "Recent Talkers",
        "friendlyName": settings["title"], "clientSource": "auto",
        "clientUrl": "", "clientUsername": "", "cardType": "standard",
        "backendMode": "managed", "bridgePermission": "self_owned",
        "fixedBridgeRecovery": False, "allowTune": False,
        "instance": settings["bridge_id"], "setupPorts": ports,
        "zelloChannel": settings["channel"], "liveAudioVerified": False,
    }
    bridges = config.setdefault("bridges", [])
    matches = [i for i, item in enumerate(bridges)
               if isinstance(item, dict) and item.get("id") == settings["bridge_id"]]
    if len(matches) > 1:
        raise PlanError("duplicate bridge identity")
    if matches:
        bridges[matches[0]] = entry
    else:
        bridges.append(entry)
    m17 = load("asr_setup_m17_zello", "asr-bridge-setup-m17.py")
    rpt = m17.insert_nodes_mapping(rpt_path.read_text(encoding="utf-8"), node)
    rpt = m17.upsert_node_section(rpt, settings["bridge_id"], node, ports["usrp_rx"], ports["usrp_tx"])
    rpt = rpt.replace(f"[{node}] ; M17 Bridge ({settings['bridge_id']})",
                      f"[{node}] ; Zello Bridge ({settings['bridge_id']})")
    modules = m17.ensure_usrp_module(modules_path.read_text(encoding="utf-8")).replace(
        "ASR M17 bridge", "ASR managed bridge")
    return {
        CONFIG: ((json.dumps(config, sort_keys=True, indent=2) + "\n").encode(),
                 config_path.stat().st_mode & 0o777),
        RPT: (rpt.encode(), rpt_path.stat().st_mode & 0o777),
        MODULES: (modules.encode(), modules_path.stat().st_mode & 0o777),
    }
