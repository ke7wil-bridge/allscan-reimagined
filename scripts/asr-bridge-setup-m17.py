#!/usr/bin/env python3
"""Transactional M17 bridge provisioning for an ASL3 host."""
from __future__ import annotations

import argparse
import base64
import ctypes.util
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "asr-bridge-setup-core.py"
STATE_PATH = "/var/lib/allscan-reimagined/bridge-setup"
CONFIG_PATH = "/etc/allscan-reimagined/config.json"
RPT_PATH = "/etc/asterisk/rpt.conf"
MODULES_PATH = "/etc/asterisk/modules.conf"
UNIT_PATH = "/etc/systemd/system/allscan-reimagined-m17-bridge@.service"
CONTROL_PATH = "/usr/local/sbin/allscan-reimagined-m17-bridge-control"
CONNECTOR_PATH = "/usr/local/sbin/allscan-reimagined-m17-usrp-connector"
MANAGED_PREFIX = "; ASR BRIDGE SETUP "

class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class M17Settings:
    bridge_id: str
    callsign: str
    reflector: str
    host: str
    port: int
    module: str
    card_type: str = "standard"
    title: str = "M17 Bridge"
    main_node: int | None = None
    bridge_node: int | None = None


def load_core():
    spec = importlib.util.spec_from_file_location("asr_bridge_setup_core", CORE_PATH)
    if not spec or not spec.loader:
        raise InstallError("setup core could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise InstallError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rooted(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/"):
        raise InstallError(f"non-absolute managed path: {absolute}")
    backend = load_module("asr_backend_m17", "asr-provisioning-backend.py")
    return backend.map_path(root, absolute)


def reject_symlink_components(root: Path, path: Path) -> None:
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise InstallError(f"managed path escapes installation root: {path}") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"managed path contains a symbolic link: {current}")


def validate_settings(settings: M17Settings) -> M17Settings:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", settings.bridge_id):
        raise InstallError("invalid bridge id")
    callsign = settings.callsign.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{2,8}", callsign) or not re.search(r"[A-Z]", callsign) or not re.search(r"[0-9]", callsign):
        raise InstallError("invalid M17 callsign")
    if settings.card_type not in {"standard", "m17_net"}:
        raise InstallError("M17 card type must be standard or m17_net")
    reflector = settings.reflector.strip().upper()
    if not re.fullmatch(r"M17-[A-Z0-9]{3}", reflector):
        raise InstallError("reflector must use M17-XXX")
    module = settings.module.strip().upper()
    if not re.fullmatch(r"[A-Z]", module):
        raise InstallError("module must be A-Z")
    host = settings.host.strip()
    if not host or len(host) > 253 or any(ch.isspace() for ch in host):
        raise InstallError("invalid reflector host")
    if not 1 <= int(settings.port) <= 65535:
        raise InstallError("invalid reflector port")
    return M17Settings(**{**asdict(settings), "callsign": callsign,
                           "reflector": reflector, "module": module, "host": host})


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"cannot read valid JSON from {path}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"JSON root is not an object: {path}")
    return value


def detect_main_node(config: dict[str, Any], requested: int | None) -> int:
    raw = requested if requested is not None else config.get("node")
    try:
        node = int(raw)
    except (TypeError, ValueError) as exc:
        raise InstallError("main AllStar node is missing or invalid") from exc
    if not 100 <= node <= 9_999_999:
        raise InstallError("main AllStar node is outside the supported range")
    return node


def existing_bridge_nodes(config: dict[str, Any]) -> set[int]:
    result: set[int] = set()
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict):
            continue
        try:
            result.add(int(bridge.get("node")))
        except (TypeError, ValueError):
            pass
    return result


def find_existing_bridge(config: dict[str, Any], bridge_id: str) -> dict[str, Any] | None:
    matches = [item for item in config.get("bridges", [])
               if isinstance(item, dict) and item.get("id") == bridge_id]
    if len(matches) > 1:
        raise InstallError("bridge id is duplicated in ASR configuration")
    return matches[0] if matches else None


def insert_nodes_mapping(text: str, node: int) -> str:
    line = f"{node} = radio@127.0.0.1:4572/{node},NONE"
    if re.search(rf"(?m)^\s*{node}\s*=", text):
        return text
    match = re.search(r"(?m)^\[nodes\]\s*$", text)
    if not match:
        raise InstallError("rpt.conf has no [nodes] section")
    next_section = re.search(r"(?m)^\[[^]]+\]", text[match.end():])
    at = match.end() + (next_section.start() if next_section else len(text))
    return text[:at].rstrip() + "\n" + line + "\n\n" + text[at:].lstrip("\n")


def ensure_bridge_control_states(text: str) -> str:
    """Keep transport-only nodes from entering autopatch/user-command state."""
    section = "asr-bridge-controlstates"
    expected = "0 = rptena,lnkena,apdis,totena,ufdis,noicd"
    legacy = "0 = rptena,lnkena,apdis,totena,ufdis,noice"
    match = re.search(
        rf"(?ms)^\[{re.escape(section)}\]\s*$.*?(?=^\[[^]]+\]\s*$|\Z)", text,
    )
    if match:
        if re.search(rf"(?m)^\s*{re.escape(legacy)}\s*$", match.group(0)):
            return text[:match.start()] + re.sub(
                rf"(?m)^\s*{re.escape(legacy)}\s*$", expected,
                match.group(0), count=1,
            ) + text[match.end():]
        if not re.search(rf"(?m)^\s*{re.escape(expected)}\s*$", match.group(0)):
            raise InstallError(f"rpt.conf [{section}] conflicts with bridge safety policy")
        return text
    return text.rstrip() + f"\n\n[{section}]\n{expected}\n"


def upsert_node_section(
    text: str, bridge_id: str, node: int,
    backend_receive_port: int, asterisk_receive_port: int,
) -> str:
    """Install chan_usrp with complementary local/remote UDP endpoints.

    chan_usrp spells its device as ``host:remote_port:local_port``.  The first
    port is where Asterisk sends audio (the backend's RxPort); the second is
    where Asterisk listens (the backend's TxPort).  Backend configuration names
    the same pair from the backend's point of view, so these values must not be
    swapped here.
    """
    begin = f"{MANAGED_PREFIX}BEGIN {bridge_id}"
    end = f"{MANAGED_PREFIX}END {bridge_id}"
    node_label = "Unified Net Bridge" if node == 1999 else "M17 Bridge"
    block = (
        f"{begin}\n[{node}] ; {node_label} ({bridge_id})\n"
        f"rxchannel = USRP/127.0.0.1:{backend_receive_port}:{asterisk_receive_port}\n"
        "duplex = 0\nhangtime = 0\nalthangtime = 0\n"
        "controlstates = asr-bridge-controlstates\n"
        "holdofftelem = 1\ntelemdefault = 0\ntelemdynamic = 0\n"
        f"beaconing = 0\n{end}"
    )
    pattern = re.compile(rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*$")
    if pattern.search(text):
        return ensure_bridge_control_states(
            pattern.sub(block, text, count=1).rstrip() + "\n"
        )
    if re.search(rf"(?m)^\s*\[{node}\](?:\s|;|$)", text):
        raise InstallError(f"rpt.conf node section {node} already exists")
    return ensure_bridge_control_states(text.rstrip() + "\n\n" + block + "\n")


def ensure_usrp_module(text: str) -> str:
    active = re.compile(r"(?mi)^\s*load\s*=\s*chan_usrp\.so\s*(?:;.*)?$")
    if active.search(text):
        return text
    disabled = re.compile(r"(?mi)^\s*noload\s*=\s*chan_usrp\.so\s*(?:;.*)?$")
    if disabled.search(text):
        return disabled.sub("load = chan_usrp.so ; ASR M17 bridge", text, count=1)
    return text.rstrip() + "\nload = chan_usrp.so ; ASR M17 bridge\n"


def bridge_entry(settings: M17Settings, node: int, ports: tuple[int, int, int]) -> dict[str, Any]:
    m17_port, usrp_rx, usrp_tx = ports
    return {
        "id": settings.bridge_id, "mode": "m17", "cardType": settings.card_type,
        "node": str(node), "title": settings.title, "backendMode": "managed",
        "bridgePermission": "self_owned", "m17Callsign": settings.callsign,
        # M17 signaling/audio must reach both local and external reflectors.
        # USRP remains loopback-only below; this wildcard applies only to the
        # ephemeral client UDP socket and does not expose the AllStar audio bus.
        "m17BindAddress": "0.0.0.0", "m17BindPort": m17_port,
        "m17UsrpBindAddress": "127.0.0.1", "m17UsrpRxPort": usrp_rx,
        "m17UsrpRemoteAddress": "127.0.0.1", "m17UsrpTxPort": usrp_tx,
        "m17AudioQualified": False, "m17QualificationState": "not_qualified",
        "m17Reflector": settings.reflector, "m17Host": settings.host,
        "m17Port": settings.port, "m17Module": settings.module,
        "m17Encrypted": False, "approvedDestinations": ([{"reflector": settings.reflector, "host": settings.host, "port": settings.port, "module": settings.module, "encrypted": False}] if settings.card_type == "m17_net" else []), "allowTune": settings.card_type == "m17_net",
        "clientSource": "m17",
    }


def update_config(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    updated = json.loads(json.dumps(config))
    bridges = updated.setdefault("bridges", [])
    if not isinstance(bridges, list):
        raise InstallError("ASR bridges configuration is not a list")
    matches = [index for index, item in enumerate(bridges)
               if isinstance(item, dict) and item.get("id") == entry["id"]]
    if len(matches) > 1:
        raise InstallError("bridge id is duplicated in ASR configuration")
    if matches:
        current = bridges[matches[0]]
        if current.get("mode") != "m17":
            raise InstallError("bridge id belongs to a different mode")
        bridges[matches[0]] = entry
    else:
        bridges.append(entry)
    return updated


def dependency_errors(root: Path) -> list[str]:
    checks = [
        (CONTROL_PATH, os.X_OK, "M17 control helper is not installed"),
        (CONNECTOR_PATH, os.X_OK, "M17 connector is not installed"),
        (UNIT_PATH, os.R_OK, "M17 systemd unit is not installed"),
    ]
    errors = [message for path, mode, message in checks
              if not os.access(rooted(root, path), mode)]
    module_available = bool(
        list(rooted(root, "/usr/lib").glob("asterisk/modules/chan_usrp.so"))
        + list(rooted(root, "/usr/lib").glob("*/asterisk/modules/chan_usrp.so"))
    ) if root != Path("/") else load_module(
        "asr_backend_m17_module", "asr-provisioning-backend.py"
    ).asterisk_module_available()
    if not module_available:
        errors.append("Asterisk chan_usrp module is not installed")
    if root == Path("/"):
        if ctypes.util.find_library("codec2") is None:
            errors.append("libcodec2 is not installed")
        for logical in (CONTROL_PATH, CONNECTOR_PATH, UNIT_PATH):
            path = rooted(root, logical)
            if path.exists():
                info = path.stat()
                if info.st_uid != 0 or info.st_mode & 0o022:
                    errors.append(f"{logical} has unsafe ownership or permissions")
        config = rooted(root, CONFIG_PATH)
        if config.exists():
            info = config.stat()
            if info.st_uid != 0 or info.st_mode & 0o002:
                errors.append(f"{CONFIG_PATH} must be root-owned and not world-writable")
    return errors


def plan(root: Path, settings: M17Settings) -> dict[str, Any]:
    settings = validate_settings(settings)
    config_path = rooted(root, CONFIG_PATH)
    rpt_path = rooted(root, RPT_PATH)
    modules_path = rooted(root, MODULES_PATH)
    for path in (config_path, rpt_path, modules_path):
        reject_symlink_components(root, path)
        if not path.is_file() or path.is_symlink():
            raise InstallError(f"required regular file is missing or unsafe: {path}")
    config = read_json(config_path)
    main_node = detect_main_node(config, settings.main_node)
    core = load_core()
    adapter = core.ASL3Adapter(root)
    facts = adapter.detect()
    existing = find_existing_bridge(config, settings.bridge_id)
    used = set(facts.node_numbers) | existing_bridge_nodes(config) | {main_node}
    shared_net_node = (settings.card_type == "m17_net"
                       and any(isinstance(item, dict)
                               and item.get("id") != settings.bridge_id
                               and item.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
                               and str(item.get("node", "")) == "1999"
                               for item in config.get("bridges", [])))
    if existing is not None:
        try:
            node = 1999 if settings.card_type == "m17_net" else int(existing["node"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InstallError("existing M17 bridge node is invalid") from exc
    elif settings.card_type == "m17_net":
        node = 1999
        if node in used and not shared_net_node:
            raise InstallError(f"requested bridge node {node} is already in use")
    elif settings.bridge_node is not None:
        node = int(settings.bridge_node)
        if node in used:
            raise InstallError(f"requested bridge node {node} is already in use")
    else:
        node = core.allocate_node(used)
    if node == main_node:
        raise InstallError("bridge node must differ from the main node")
    ports = core.derived_m17_ports(settings.bridge_id)
    if settings.card_type == "m17_net":
        ports = (ports[0], *core.unified_net_usrp_ports())
    occupied = set(facts.listening_udp_ports)
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict) or bridge.get("id") == settings.bridge_id:
            continue
        for key in ("m17BindPort", "m17UsrpRxPort"):
            try:
                occupied.add(int(bridge[key]))
            except (KeyError, TypeError, ValueError):
                pass
    if settings.card_type == "m17_net" and (shared_net_node or existing is not None):
        occupied -= set(core.unified_net_usrp_ports())
    if ports[0] in occupied or ports[1] in occupied:
        raise InstallError("derived M17 receive port is already in use")
    errors = dependency_errors(root)
    if errors:
        raise InstallError("; ".join(errors))
    entry = bridge_entry(settings, node, ports)
    new_config = update_config(config, entry)
    rpt_text = rpt_path.read_text(encoding="utf-8")
    active_mode = str(new_config.get("netBridgeMode") or "m17").lower()
    if settings.card_type == "m17_net" and "netBridgeMode" not in new_config:
        new_config["netBridgeMode"] = active_mode
    if settings.card_type != "m17_net" or active_mode == "m17":
        if settings.card_type == "m17_net":
            for other in config.get("bridges", []):
                if (not isinstance(other, dict) or other.get("id") == settings.bridge_id
                        or other.get("cardType") not in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}):
                    continue
                old_id = str(other.get("id") or "")
                if old_id:
                    begin = f"{MANAGED_PREFIX}BEGIN {old_id}"
                    end = f"{MANAGED_PREFIX}END {old_id}"
                    rpt_text = re.sub(
                        rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*\n?",
                        "", rpt_text,
                    )
        new_rpt = upsert_node_section(
            insert_nodes_mapping(rpt_text, node), settings.bridge_id, node,
            ports[1], ports[2],
        )
    else:
        new_rpt = rpt_text
    modules_text = modules_path.read_text(encoding="utf-8")
    new_modules = ensure_usrp_module(modules_text)
    files = {
        CONFIG_PATH: json.dumps(new_config, indent=2, sort_keys=True) + "\n",
        RPT_PATH: new_rpt,
        MODULES_PATH: new_modules,
    }
    changed = [path for path, content in files.items()
               if rooted(root, path).read_text(encoding="utf-8") != content]
    result = {
        "schema": 1, "bridgeType": "m17", "settings": asdict(settings),
        "mainNode": main_node, "bridgeNode": node,
        "ports": {"m17": ports[0], "usrpRx": ports[1], "usrpTx": ports[2]},
        "files": files, "changed": changed,
        "service": f"allscan-reimagined-m17-bridge@{settings.bridge_id}.service",
    }
    result["digest"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ownership = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            if ownership is not None:
                os.fchown(handle.fileno(), ownership.st_uid, ownership.st_gid)
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def backup(root: Path, install_plan: dict[str, Any]) -> Path:
    state_root = rooted(root, STATE_PATH)
    reject_symlink_components(root, state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    backup_dir = state_root / "backups" / (
        f"{int(time.time_ns())}-{install_plan['settings']['bridge_id']}"
    )
    backup_dir.mkdir(parents=True, mode=0o700)
    manifest: list[dict[str, Any]] = []
    for logical in install_plan["files"]:
        path = rooted(root, logical)
        data = path.read_bytes() if path.exists() else b""
        manifest.append({
            "path": logical, "existed": path.exists(),
            "mode": path.stat().st_mode & 0o777 if path.exists() else 0o640,
            "content": base64.b64encode(data).decode("ascii"),
        })
    atomic_write(backup_dir / "manifest.json",
                 json.dumps(manifest, indent=2) + "\n", 0o600)
    return backup_dir


def restore(root: Path, backup_dir: Path) -> None:
    manifest = read_json_list(backup_dir / "manifest.json")
    for item in reversed(manifest):
        path = rooted(root, str(item["path"]))
        if item["existed"]:
            data = base64.b64decode(item["content"], validate=True)
            atomic_write(path, data.decode("utf-8"), int(item["mode"]))
        elif path.exists():
            path.unlink()


def read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"cannot read rollback manifest: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InstallError("rollback manifest is invalid")
    return value


def verify(root: Path, install_plan: dict[str, Any]) -> None:
    for logical, expected in install_plan["files"].items():
        path = rooted(root, logical)
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"verification failed for {logical}")
        if path.read_text(encoding="utf-8") != expected:
            raise InstallError(f"content verification failed for {logical}")
    config = read_json(rooted(root, CONFIG_PATH))
    bridge = find_existing_bridge(config, install_plan["settings"]["bridge_id"])
    if not bridge or int(bridge.get("node", 0)) != install_plan["bridgeNode"]:
        raise InstallError("ASR did not retain the installed bridge")
    rpt = rooted(root, RPT_PATH).read_text(encoding="utf-8")
    node = install_plan["bridgeNode"]
    if not re.search(rf"(?m)^\s*{node}\s*=", rpt) or not re.search(rf"(?m)^\s*\[{node}\]", rpt):
        raise InstallError("Asterisk node verification failed")


def service_apply(service: str, node: int, asterisk_changed: bool) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if asterisk_changed:
        subprocess.run(["systemctl", "restart", "asterisk.service"], check=True,
                       capture_output=True, text=True)
    # Asterisk returns success for some unknown CLI commands. Verify the live
    # private node explicitly; app_rpt module reload may crash on ASL3.
    for attempt in range(20):
        result = subprocess.run(["asterisk", "-rx", f"rpt stats {node}"],
                                capture_output=True, text=True)
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            break
        if attempt == 19:
            raise InstallError(f"Asterisk did not register M17 node {node}")
        time.sleep(0.5)
    # Installation deliberately leaves M17 stopped.  The connector refuses to
    # run until reflector ACK and bidirectional audio are explicitly qualified;
    # repeatedly restarting an unqualified service would misstate readiness.
    subprocess.run(["systemctl", "disable", "--now", service], check=True,
                   capture_output=True, text=True)
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0:
        raise InstallError("unqualified M17 connector did not remain stopped")


def commit_installation(root: Path, install_plan: dict[str, Any]) -> Path:
    destination = rooted(
        root, f"{STATE_PATH}/installations/{install_plan['settings']['bridge_id']}.json"
    )
    record = {
        "schema": 1, "bridgeType": "m17",
        "bridgeId": install_plan["settings"]["bridge_id"],
        "bridgeNode": install_plan["bridgeNode"], "ports": install_plan["ports"],
        "service": install_plan["service"],
        "qualificationRequired": True, "committedAt": int(time.time()),
    }
    atomic_write(destination, json.dumps(record, indent=2) + "\n", 0o600)
    return destination


def reload_after_rollback(service: str, asterisk_changed: bool) -> None:
    subprocess.run(["systemctl", "disable", "--now", service], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "daemon-reload"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if asterisk_changed:
        subprocess.run(["systemctl", "restart", "asterisk.service"], check=True,
                       capture_output=True, text=True)


def install(root: Path, settings: M17Settings, *, manage_services: bool = False,
            fail_after: int | None = None,
            expected_digest: str | None = None) -> dict[str, Any]:
    install_plan = plan(root, settings)
    if expected_digest is not None and install_plan["digest"] != expected_digest:
        raise InstallError("installation plan changed; preview the bridge again")
    backup_dir = backup(root, install_plan)
    changed = 0
    asterisk_changed = False
    try:
        for logical in install_plan["changed"]:
            path = rooted(root, logical)
            atomic_write(path, install_plan["files"][logical],
                         path.stat().st_mode & 0o777 if path.exists() else 0o640)
            changed += 1
            if logical in (RPT_PATH, MODULES_PATH):
                asterisk_changed = True
            if fail_after is not None and changed >= fail_after:
                raise InstallError("injected apply failure")
        verify(root, install_plan)
        if manage_services:
            if root != Path("/"):
                raise InstallError("service management is only valid for the real root")
            service_apply(install_plan["service"], install_plan["bridgeNode"],
                          asterisk_changed)
        committed = commit_installation(root, install_plan)
        return {**install_plan, "ok": True, "backup": str(backup_dir),
                "committed": str(committed), "qualificationRequired": True}
    except Exception:
        restore(root, backup_dir)
        if manage_services and root == Path("/"):
            reload_after_rollback(install_plan["service"], asterisk_changed)
        raise


def settings_from_args(args: argparse.Namespace) -> M17Settings:
    return M17Settings(
        bridge_id=args.bridge_id, callsign=args.callsign,
        reflector=args.reflector, host=args.host, port=args.port,
        module=args.module, title=args.title, main_node=args.main_node,
        bridge_node=args.bridge_node,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("plan", "install", "verify"))
    result.add_argument("--root", type=Path, default=Path("/"))
    result.add_argument("--bridge-id", required=True)
    result.add_argument("--callsign", required=True)
    result.add_argument("--reflector", required=True)
    result.add_argument("--host", required=True)
    result.add_argument("--port", type=int, default=17000)
    result.add_argument("--module", default="A")
    result.add_argument("--title", default="M17 Bridge")
    result.add_argument("--main-node", type=int)
    result.add_argument("--bridge-node", type=int)
    result.add_argument("--manage-services", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        install_plan = plan(args.root, settings_from_args(args))
        if args.command == "plan":
            result = {key: value for key, value in install_plan.items() if key != "files"}
        elif args.command == "verify":
            verify(args.root, install_plan)
            result = {"ok": True, "bridgeId": args.bridge_id}
        else:
            result = install(args.root, settings_from_args(args),
                             manage_services=args.manage_services)
            result = {key: value for key, value in result.items() if key != "files"}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (InstallError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
