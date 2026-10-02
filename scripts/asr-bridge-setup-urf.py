#!/usr/bin/env python3
"""Read-only plan and configuration rendering for ASR-managed URF/TGIF DMR."""
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

def tgif_network_id(dmr_id: int, bridge_node: int) -> int:
    """Return a stable TGIF hotspot ESSID distinct for each private node."""
    # The qualified managed Net Bridge identity is intentionally independent
    # of its consolidated AllStar transport node.
    suffix = 4 if bridge_node == 1999 else (bridge_node % 100 or 99)
    return dmr_id * 100 + suffix

class PlanError(RuntimeError):
    pass

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
    if not isinstance(value.title, str) or not value.title.strip() or len(value.title) > 80 or any(ch in value.title for ch in "\r\n\0"):
        raise PlanError("invalid bridge title")
    if value.bridge_node is not None and not 1001 <= value.bridge_node <= 1999:
        raise PlanError("private bridge node must be in 1001-1999")
    return UrfSettings(bridge_id, callsign, value.dmr_id, value.tgif_tg, reflector,
                       value.title.strip(), value.bridge_node, value.bridge_role)
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
    if root == Path("/"):
        if not facts.systemd:
            raise PlanError("URF/TGIF setup requires systemd")
        if not facts.docker or not facts.docker_compose:
            raise PlanError("URF/TGIF setup requires Docker with the Compose plugin")
        if not load("asr_backend_urf_module", "asr-provisioning-backend.py").asterisk_module_available():
            raise PlanError("Asterisk chan_usrp module is not installed")
    try:
        main_node = int(config["node"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError("main AllStar node is missing") from exc
    if settings.bridge_role == "net" and not 100 <= main_node <= 999999:
        raise PlanError("DMR Net Bridge requires a 3-6 digit main AllStar node")
    owned = [b for b in config["bridges"] if isinstance(b, dict) and b.get("id") == settings.bridge_id and b.get("urfReflector")]
    record = rooted(root, f"{STATE}/{settings.bridge_id}.json")
    if owned and (not record.is_file() or record.is_symlink()):
        raise PlanError("cannot replace an unowned URF reflector")
    used = set(facts.node_numbers) | {main_node}
    for bridge in config["bridges"]:
        if isinstance(bridge, dict):
            try: used.add(int(bridge.get("node")))
            except (TypeError, ValueError): pass
    node = (1999 if settings.bridge_role == "net" else
            (int(owned[0]["node"]) if owned else (settings.bridge_node or core.allocate_node(used))))
    shared_net_node = (settings.bridge_role == "net" and node == 1999
                       and any(isinstance(item, dict)
                               and item.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
                               and str(item.get("node", "")) == "1999"
                               for item in config["bridges"]))
    if not owned and node in used and not shared_net_node:
        raise PlanError("requested private node is already in use")
    ports = port_block(settings.bridge_id)
    occupied_udp = set(facts.listening_udp_ports)
    occupied_tcp = set(facts.listening_tcp_ports)
    udp_ports = {value for key, value in ports.items() if key != "transcoder"}
    if not owned and (occupied_udp.intersection(udp_ports) or ports["transcoder"] in occupied_tcp):
        raise PlanError("a required URF/TGIF port is already in use")
    runtime = runtime_root(settings.bridge_id)
    resources = {
        "root": runtime, "compose": f"{runtime}/compose.yml", "urfConfig": f"{runtime}/config/urfd.ini",
        "tcdConfig": f"{runtime}/config/tcd.ini", "blacklist": f"{runtime}/config/urfd.blacklist",
        "tgifSecret": f"{runtime}/secrets/tgif-password", "config": CONFIG, "rpt": RPT, "modules": MODULES,
    }
    result = {
        "schema": 1, "bridgeType": "dmr", "runtime": "urf-tgif", "settings": asdict(settings),
        "mainNode": main_node, "bridgeNode": node, "ports": ports, "resources": resources,
        "images": {"urf": "allscan-reimagined/urf:managed", "tcd": "allscan-reimagined/urf-tcd:managed"},
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
    reflector_id = max(1, (int(plan["mainNode"]) // 10) % 100000)
    return f"""[Names]\nCallsign = {s['reflector']}\nSysopEmail = asr@localhost.invalid\nCountry = US\nSponsor = {s['callsign']} ASR\nDashboardUrl = http://127.0.0.1/\n\n[IP Addresses]\nIPv4Binding = 0.0.0.0\n\n[Modules]\nModules = A\nDescriptionA = All Modes\n\n[Transcoder]\nPort = {p['transcoder']}\nBindingAddress = 127.0.0.1\nModules = A\n\n[Brandmeister]\nEnable = false\nPort = 10002\n\n[DCS]\nPort = {p['dcs']}\n[DExtra]\nPort = {p['dextra']}\n[DPlus]\nPort = {p['dplus']}\n[G3]\nEnable = false\n[DMRPlus]\nPort = {p['dmrplus']}\n[M17]\nPort = {p['m17']}\n[MMDVM]\nPort = {p['dmr']}\nDefaultId = {s['dmr_id']}\n[NXDN]\nPort = {p['nxdn']}\nAutoLinkModule = A\nReflectorID = {reflector_id}\n[P25]\nPort = {p['p25']}\nAutoLinkModule = A\nReflectorID = {reflector_id}\n[URF]\nPort = {p['urf']}\n[USRP]\nEnable = true\nCallsign = {s['callsign']}\nIPAddress = 127.0.0.1\nRxPort = {p['usrp_rx']}\nTxPort = {p['usrp_tx']}\nModule = A\n[YSF]\nPort = {p['ysf']}\nEnableDGID = false\nAutoLinkModule = A\nDefaultTxFreq = 446500000\nDefaultRxFreq = 446500000\nRegistrationID = {reflector_id}\nRegistrationName = US {s['reflector']}\nRegistrationDescription = ASR managed URF\n[DMR ID DB]\nMode = http\nFilePath = /data/dmrid.dat\nURL = http://xlxapi.rlx.lu/api/exportdmr.php\nRefreshMin = 179\n[NXDN ID DB]\nMode = http\nFilePath = /data/nxdn.dat\nURL = https://radioid.net/static/nxdn.csv\nRefreshMin = 1440\n[YSF TX/RX DB]\nMode = http\nFilePath = /data/ysfnode.dat\nURL = http://xlxapi.rlx.lu/api/exportysfrepeaters.php\nRefreshMin = 191\n[Files]\nPidPath = /data/urfd.pid\nXmlPath = /data/urfd.xml\nWhitelistPath = /config/urfd.whitelist\nBlacklistPath = /config/urfd.blacklist\nInterlinkPath = /config/urfd.interlink\nG3TerminalPath = /config/urfd.terminal\n"""

def tcd_ini(plan: dict) -> str:
    return f"""Port = {plan['ports']['transcoder']}\nServerAddress = 127.0.0.1\nModules = A\nDStarGainIn = 16\nDStarGainOut = -16\nDmrYsfGainIn = -3\nDmrYsfGainOut = 0\nUsrpTxGain = 12\nUsrpRxGain = 3\n"""

def integration_files(root: Path, plan: dict) -> dict[str, tuple[bytes, int]]:
    config_path=safe_regular(root, CONFIG); rpt_path=safe_regular(root, RPT); modules_path=safe_regular(root, MODULES)
    config=json.loads(config_path.read_text(encoding="utf-8")); s=plan["settings"]; node=plan["bridgeNode"]; p=plan["ports"]
    role=s.get("bridge_role","standard"); entry={"id":s["bridge_id"],"mode":"dmr","node":str(node),"title":s["title"],"detailTitle":"Connected Clients","friendlyName":s["title"],"cardType":"dmr_net" if role=="net" else "standard","instance":s["bridge_id"],"bridgePermission":"self_owned","backendMode":"managed","clientSource":"dmr","urfReflector":True,"urfGroupId":"urf","urfName":s["reflector"],"urfModes":["dmr"],"fixedDestination":str(s["tgif_tg"]),"setupPorts":p,"tgifTalkgroup":str(s["tgif_tg"]),"dmrId":str(s["dmr_id"]),"dmrNetworkId":str(tgif_network_id(s["dmr_id"], node)),"allowTune":role=="net","approvedDestinations":[str(s["tgif_tg"])] if role=="net" else [],"managedNetControl":role=="net","managedTargetFile":f"{plan['resources']['root']}/tgif-run/net-target" if role=="net" else "","liveAudioVerified":False}
    if role == "net": entry["linkAlias"] = "999" + str(plan["mainNode"]).zfill(6)
    bridges=config.setdefault("bridges",[]); matches=[i for i,b in enumerate(bridges) if isinstance(b,dict) and b.get("id")==s["bridge_id"]]
    if len(matches)>1: raise PlanError("duplicate bridge identity")
    if matches: bridges[matches[0]]=entry
    else: bridges.append(entry)
    if role == "net" and str(config.get("netBridgeMode", "")).lower() not in {"dmr", "ysf", "p25", "nxdn", "m17"}:
        config["netBridgeMode"] = "dmr"
    m17=load("asr_setup_m17_urf","asr-bridge-setup-m17.py")
    rpt=rpt_path.read_text()
    if role != "net" or config.get("netBridgeMode") == "dmr":
        for other in bridges:
            if not isinstance(other, dict) or other.get("id") == s["bridge_id"] or other.get("cardType") not in {"dmr_net","ysf_net","p25_net","nxdn_net","m17_net"}:
                continue
            old_id=str(other.get("id") or "")
            if old_id:
                begin=f"{m17.MANAGED_PREFIX}BEGIN {old_id}"; end=f"{m17.MANAGED_PREFIX}END {old_id}"
                rpt=re.sub(rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*\n?","",rpt)
        rpt=m17.insert_nodes_mapping(rpt,node); rpt=m17.upsert_node_section(rpt,s["bridge_id"],node,p["usrp_rx"],p["usrp_tx"]).replace(f"[{node}] ; M17 Bridge ({s['bridge_id']})",f"[{node}] ; DMR/URF Bridge ({s['bridge_id']})")
    modules=m17.ensure_usrp_module(modules_path.read_text()).replace("ASR M17 bridge","ASR managed bridge")
    return {
        CONFIG: ((json.dumps(config, sort_keys=True, indent=2) + "\n").encode(), config_path.stat().st_mode & 0o777),
        RPT: (rpt.encode(), rpt_path.stat().st_mode & 0o777),
        MODULES: (modules.encode(), modules_path.stat().st_mode & 0o777),
        plan["resources"]["urfConfig"]: (urfd_ini(plan).encode(), 0o644),
        plan["resources"]["tcdConfig"]: (tcd_ini(plan).encode(), 0o644),
        plan["resources"]["blacklist"]: (b"", 0o644),
    }
