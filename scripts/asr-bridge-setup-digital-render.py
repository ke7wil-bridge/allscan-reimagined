#!/usr/bin/env python3
"""Render isolated bridge configuration from a verified pinned runtime stage.

Pure renderer: no file writes, subprocesses or host mutation. The transactional
installer owns safe creation, rollback and service management.
"""
from __future__ import annotations

import json
import importlib.util
import re
import shlex
import sys
from pathlib import Path
from typing import Any

class RenderError(RuntimeError):
    pass


def provisioning_backend():
    path = Path(__file__).resolve().parent / "asr-provisioning-backend.py"
    spec = importlib.util.spec_from_file_location("asr_backend_digital_render", path)
    if not spec or not spec.loader:
        raise RenderError("provisioning backend module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def set_ini(text: str, section: str, values: dict[str, object]) -> str:
    """Replace unique upstream settings while preserving other upstream fields."""
    lines = text.splitlines()
    active = ""
    found: set[str] = set()
    wanted = {key.casefold(): key for key in values}
    result = []
    section_seen = False
    for line in lines:
        header = re.fullmatch(r"\s*\[([^]]+)\]\s*", line)
        if header:
            if active.casefold() == section.casefold():
                for key in values:
                    if key.casefold() not in found:
                        result.append(f"{key}={values[key]}")
                        found.add(key.casefold())
            active = header.group(1)
            section_seen |= active.casefold() == section.casefold()
        if active.casefold() == section.casefold():
            match = re.match(r"^\s*([^=;#\s]+)\s*=", line)
            if match and match.group(1).casefold() in wanted:
                key = match.group(1).casefold()
                if key in found:
                    continue
                line = f"{wanted[key]}={values[wanted[key]]}"
                found.add(key)
        result.append(line)
    if not section_seen:
        raise RenderError(f"upstream template lacks [{section}]")
    if active.casefold() == section.casefold():
        for key in values:
            if key.casefold() not in found:
                result.append(f"{key}={values[key]}")
    return "\n".join(result).rstrip() + "\n"


def template(stage: Path, name: str) -> str:
    path = stage / name
    if path.is_symlink() or not path.is_file():
        raise RenderError(f"verified runtime lacks {name}")
    return path.read_text(encoding="utf-8")


def gateway_ini(plan: dict[str, Any], stage: Path,
                mqtt_user: str = "", mqtt_password: str = "") -> str:
    mode = plan["bridgeType"]
    settings, ports = plan["settings"], plan["ports"]
    name = {"p25": "P25Gateway", "nxdn": "NXDNGateway", "ysf": "YSFGateway"}[mode]
    content = template(stage, f"{name}.ini")
    general = {"Callsign": settings["callsign"], "RptAddress": "127.0.0.1",
               "RptPort": ports["gateway_tx"], "LocalPort": ports["gateway_rx"],
               "Daemon": 0}
    if mode == "ysf":
        general.update({"Id": settings["digital_id"], "LocalAddress": "127.0.0.1", "Suffix": "ASR"})
    content = set_ini(content, "General", general)
    content = set_ini(content, "Log", {
        "DisplayLevel": 1, "FileLevel": 2, "FilePath": "/var/log/mmdvm",
        "FileRoot": f"{name}_{settings['bridge_id']}",
    })
    content = set_ini(content, "Remote Commands", {"Enable": 1, "Port": ports["remote"]})
    if mode in {"p25", "nxdn"}:
        if not mqtt_user or not mqtt_password or any(c in mqtt_user + mqtt_password for c in "\r\n\0"):
            raise RenderError("authenticated gateway MQTT settings are required")
        content = set_ini(content, "MQTT", {
            "Address": "127.0.0.1", "Port": ports["mqtt"], "Auth": 1,
            "Username": mqtt_user, "Password": mqtt_password,
            "Name": f"{mode}_{settings['bridge_id']}",
        })
        content = set_ini(content, "Network", {
            "Port": ports["network"],
            "HostsFile1": f"/opt/{name}_{settings['bridge_id']}/{name}Hosts.json",
            "HostsFile2": f"/opt/{name}_{settings['bridge_id']}/private-hosts.txt",
            # A Net Bridge must remain disconnected across startup/recovery;
            # only an explicit control action may select its destination.
            "Static": (0 if settings.get("bridge_role") == "net"
                       else settings["destination"]),
            "ReloadTime": 60,
        })
    else:
        content = set_ini(content, "Network", {
            "Startup": f"{settings['callsign']}-ASR", "InactivityTimeout": 0,
        })
        content = set_ini(content, "YSF Network", {
            "Enable": 1, "Port": ports["network"],
            "Hosts": f"/var/lib/mmdvm/ASR-{settings['bridge_id']}-YSFHosts.txt",
        })
        content = set_ini(content, "FCS Network", {"Enable": 0})
    return content


def analog_ini(plan: dict[str, Any]) -> str:
    mode, settings, ports = plan["bridgeType"], plan["settings"], plan["ports"]
    codec = {"p25": "P25", "nxdn": "NXDN", "ysf": "YSFN"}[mode]
    emulator = mode != "p25"
    return f"""[GENERAL]
logLevel=2
exportMetadata=true
decoderFallBack=true
useEmulator={'true' if emulator else 'false'}
emulatorAddress=127.0.0.1:{ports['emulator']}
pcmPort=0

[AMBE_AUDIO]
address=127.0.0.1
txPort={ports['analog_tx']}
rxPort={ports['analog_rx']}
ambeMode={codec}
minTxTimeMS=2500
gatewayDmrId={settings['digital_id']}
repeaterID={settings['digital_id'] * 100 + 1}
txTg={settings['destination']}
txTs=2
colorCode=0

[USRP]
address=127.0.0.1
txPort={ports['usrp_tx']}
rxPort={ports['usrp_rx']}
usrpAudio=AUDIO_USE_GAIN
usrpGain=1.00
tlvAudio=AUDIO_UNITY

[DV3000]
address=127.0.0.1
rxPort={ports['emulator']}
"""


def mmdvm_ini(plan: dict[str, Any], stage: Path) -> str:
    mode, settings, ports = plan["bridgeType"], plan["settings"], plan["ports"]
    text = template(stage, "MMDVM_Bridge.ini.template")
    text = set_ini(text, "General", {"Callsign": settings["callsign"], "Id": settings["digital_id"]})
    text = set_ini(text, "DMR Id Lookup", {
        "File": f"/opt/MMDVM_Bridge_{settings['bridge_id']}/DMRIds.dat",
    })
    text = set_ini(text, "Log", {
        "DisplayLevel": 1, "FileLevel": 2, "FilePath": "/var/log/mmdvm",
        "FileRoot": f"MMDVM_Bridge_{settings['bridge_id']}",
    })
    enabled = {"p25": "P25", "nxdn": "NXDN", "ysf": "System Fusion"}[mode]
    for section in ("D-Star", "DMR", "System Fusion", "P25", "NXDN"):
        text = set_ini(text, section, {"Enable": 1 if section == enabled else 0})
    network = {"p25": "P25 Network", "nxdn": "NXDN Network", "ysf": "System Fusion Network"}[mode]
    for section in ("D-Star Network", "DMR Network", "System Fusion Network", "P25 Network", "NXDN Network"):
        text = set_ini(text, section, {"Enable": 1 if section == network else 0})
    text = set_ini(text, network, {
        "GatewayAddress": "127.0.0.1", "GatewayPort": ports["gateway_rx"],
        "LocalPort": ports["gateway_tx"],
    })
    if mode == "nxdn":
        text = set_ini(text, "NXDN", {"Id": settings["destination"]})
    return text


def dvswitch_ini(plan: dict[str, Any], stage: Path) -> str:
    mode, ports = plan["bridgeType"], plan["ports"]
    text = template(stage, "DVSwitch.ini.template")
    section = {"p25": "P25", "nxdn": "NXDN", "ysf": "YSF"}[mode]
    return set_ini(text, section, {
        "address": "127.0.0.1", "txPort": ports["analog_rx"],
        "rxPort": ports["analog_tx"], "RemotePort": ports["remote"],
    })


def hosts_files(plan: dict[str, Any]) -> dict[str, str]:
    mode, settings = plan["bridgeType"], plan["settings"]
    gateway = {"p25": "P25Gateway", "nxdn": "NXDNGateway", "ysf": "YSFGateway"}[mode]
    if mode == "ysf":
        host = settings["reflector_host"]
        return {f"/var/lib/mmdvm/ASR-{settings['bridge_id']}-YSFHosts.txt":
                f"{settings['destination']:05d};US-{settings['callsign']}-YSF;ASR Bridge;{host};{settings['reflector_port']};0\n"}
    row = {"designator": settings["destination"],
           "ipv4": settings["reflector_host"], "ipv6": None,
           "port": settings["reflector_port"]}
    root = f"/opt/{gateway}_{settings['bridge_id']}"
    return {f"{root}/{gateway}Hosts.json": json.dumps({"reflectors": [row]}, indent=2) + "\n",
            f"{root}/private-hosts.txt": "# ASR private overrides\n"}


def service_units(plan: dict[str, Any]) -> dict[str, str]:
    """Render isolated, restart-persistent units for one managed instance."""
    mode, identity, ports = plan["bridgeType"], plan["settings"]["bridge_id"], plan["ports"]
    gateway = {"p25": "P25Gateway", "nxdn": "NXDNGateway", "ysf": "YSFGateway"}[mode]
    root = {
        "gateway": f"/opt/{gateway}_{identity}",
        "mmdvm": f"/opt/MMDVM_Bridge_{identity}",
        "analog": f"/opt/Analog_Bridge_{identity}",
        "emulator": f"/opt/md380-emu_{identity}",
    }
    names = plan["services"]
    gateway_after = "network-online.target"
    if mode in {"p25", "nxdn"}:
        gateway_after += f" {names[-1]}"
    common = """Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/log/mmdvm /var/log/allscan-reimagined
"""
    def unit(description: str, after: str, cwd: str, command: str,
             account: str = "") -> str:
        backend = provisioning_backend()
        containerized = backend.load_profile() is not None
        if containerized and not account:
            account = "asr-bridge"
        identity_lines = f"User={account}\nGroup={account}\n" if account and not containerized else ""
        command = backend.systemd_exec(shlex.split(command), account if containerized else "")
        return (f"[Unit]\nDescription={description}\nAfter={after}\n\n"
                f"[Service]\nType=simple\n{identity_lines}WorkingDirectory={cwd}\n"
                f"ExecStart={command}\n{common}\n"
                "[Install]\nWantedBy=multi-user.target\n")
    result = {
        f"/etc/systemd/system/{names[0]}": unit(
            f"ASR {mode.upper()} gateway ({identity})", gateway_after,
            root["gateway"], f"{root['gateway']}/{gateway} {root['gateway']}/{gateway}.ini"),
        f"/etc/systemd/system/{names[1]}": unit(
            f"ASR MMDVM bridge ({identity})", names[0],
            root["mmdvm"], f"{root['mmdvm']}/MMDVM_Bridge {root['mmdvm']}/MMDVM_Bridge.ini"),
        f"/etc/systemd/system/{names[2]}": unit(
            f"ASR Analog Bridge ({identity})", names[1] + (f" {names[3]}" if mode != "p25" else ""),
            root["analog"], f"{root['analog']}/Analog_Bridge {root['analog']}/Analog_Bridge.ini"),
    }
    if mode != "p25":
        result[f"/etc/systemd/system/{names[3]}"] = unit(
            f"ASR {mode.upper()} software vocoder ({identity})", "network.target",
            root["emulator"], f"{root['emulator']}/qemu-arm-static {root['emulator']}/md380-emu -S {ports['emulator']}")
    if mode in {"p25", "nxdn"}:
        result[f"/etc/systemd/system/{names[-1]}"] = unit(
            f"ASR private MQTT broker ({identity})", "network.target",
            "/var/lib/mosquitto", f"/usr/sbin/mosquitto -c /etc/mosquitto/asr-{identity}.conf",
            "mosquitto")
    return result
