#!/usr/bin/env python3
"""Transactional installation for staged, isolated digital bridges.

The browser helper uses this module only after digest-bound planning and pinned
runtime verification. File, broker, service and Asterisk changes roll back
together when production activation fails.
"""
from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MQTT_SECRETS = "/etc/allscan-reimagined/bridge-mqtt-secrets.json"

class InstallError(RuntimeError):
    pass

def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise InstallError(f"missing {filename}")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

planner = load("digital_installer_plan", "asr-bridge-setup-digital.py")
renderer = load("digital_installer_render", "asr-bridge-setup-digital-render.py")
sources = load("digital_installer_source", "asr-bridge-runtime-sources.py")
m17 = load("digital_installer_m17", "asr-bridge-setup-m17.py")

def rooted(root: Path, logical: str) -> Path:
    if not logical.startswith("/") or ".." in Path(logical).parts:
        raise InstallError("unsafe managed path")
    backend = load("asr_backend_digital_install", "asr-provisioning-backend.py")
    path = backend.map_path(root, logical)
    if root == Path("/") and backend.load_profile() is not None:
        return path
    current = root.resolve()
    for piece in path.relative_to(root).parts:
        current /= piece
        if current.is_symlink():
            raise InstallError(f"symbolic link in managed path: {logical}")
    return path

def file_spec(plan: dict, stage: Path, credentials: dict | None = None) -> dict[str, tuple[bytes, int]]:
    """Build all bytes in memory before mutating any installed bridge file."""
    mode, identity = plan["bridgeType"], plan["settings"]["bridge_id"]
    gateway = planner.MODES[mode]
    base = {
        "gateway": f"/opt/{gateway}_{identity}",
        "analog": f"/opt/Analog_Bridge_{identity}",
        "mmdvm": f"/opt/MMDVM_Bridge_{identity}",
        "emulator": f"/opt/md380-emu_{identity}",
    }
    credential = credentials or {}
    user = credential.get("username", "") if mode != "ysf" else ""
    password = credential.get("password", "") if mode != "ysf" else ""
    outputs: dict[str, tuple[bytes, int]] = {}
    binaries = [
        (base["gateway"], gateway),
        (base["analog"], "Analog_Bridge"),
        (base["mmdvm"], "MMDVM_Bridge"),
        (base["mmdvm"], "RemoteCommand"),
    ]
    if mode != "p25":
        binaries += [(base["emulator"], "md380-emu"),
                     (base["emulator"], "qemu-arm-static")]
    for parent, name in binaries:
        path = stage / name
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"missing verified binary {name}")
        outputs[f"{parent}/{name}"] = path.read_bytes(), 0o755
    for folder, name, target in (
        ("analog", "dvsm.macro", "dvsm.macro"),
        ("mmdvm", "dvswitch.sh", "dvswitch.sh"),
    ):
        source = stage / name
        outputs[f"{base[folder]}/{target}"] = source.read_bytes(), 0o644
    audio = stage / "Audio"
    if audio.is_dir():
        for item in audio.rglob("*"):
            if item.is_symlink():
                raise InstallError("staged Audio asset is a symbolic link")
            if item.is_file():
                outputs[f"{base['gateway']}/Audio/{item.relative_to(audio)}"] = item.read_bytes(), 0o644
    texts = {
        plan["resources"]["gateway"]: renderer.gateway_ini(plan, stage, user, password),
        plan["resources"]["analog"]: renderer.analog_ini(plan),
        plan["resources"]["mmdvm"]: renderer.mmdvm_ini(plan, stage),
        f"{base['mmdvm']}/DVSwitch.ini": renderer.dvswitch_ini(plan, stage),
        **renderer.hosts_files(plan),
    }
    containerized = load("asr_backend_digital_files", "asr-provisioning-backend.py").load_profile() is not None
    for path, content in texts.items():
        secret_gateway = path == plan["resources"]["gateway"] and mode != "ysf"
        outputs[path] = content.encode("utf-8"), (0o640 if containerized else 0o600) if secret_gateway else 0o644
    for path, content in renderer.service_units(plan).items():
        outputs[path] = content.encode("utf-8"), 0o644
    outputs[f"/var/log/mmdvm/.asr-{identity}"] = b"ASR-managed log directory marker\n", 0o644
    if mode in {"p25", "nxdn"}:
        password_hash = credential.get("passwordHash", "")
        if (not isinstance(password_hash, str) or not password_hash.startswith("$")
                or any(ch in password_hash for ch in "\r\n\0")):
            raise InstallError("a valid broker password hash is required")
        prefix = f"/etc/mosquitto/asr-{identity}"
        # Production installation assigns these 0640 files to root:mosquitto.
        outputs[f"{prefix}.passwords"] = f"{user}:{password_hash}\n".encode(), 0o640
        outputs[f"{prefix}.acl"] = (
            f"user {user}\ntopic readwrite {mode}_{identity}/#\n"
            f"topic readwrite mmdvm_{mode}_{identity}/#\n"
        ).encode(), 0o640
        outputs[f"{prefix}.conf"] = (
            f"listener {plan['ports']['mqtt']} 127.0.0.1\n"
            "allow_anonymous false\npersistence false\n"
            f"password_file {prefix}.passwords\nacl_file {prefix}.acl\n"
        ).encode(), 0o640
    return outputs

def integration_files(root: Path, plan: dict, outputs: dict[str, tuple[bytes, int]],
                      credentials: dict | None = None) -> None:
    config_path = rooted(root, planner.CONFIG)
    current = json.loads(config_path.read_text())
    identity, mode = plan["settings"]["bridge_id"], plan["bridgeType"]
    instance, ports = identity, plan["ports"]
    units = plan["services"]
    role = str(plan["settings"].get("bridge_role") or "standard")
    card_type = f"{mode}_net" if role == "net" else "standard"
    entry = {
        "id": identity, "mode": mode, "node": str(plan["bridgeNode"]),
        "title": plan["settings"]["title"] or f"{mode.upper()}{' Net' if role == 'net' else ''} Bridge",
        "cardType": card_type,
        "instance": instance, "bridgePermission": "self_owned",
        "backendMode": "managed", "clientSource": mode,
        "fixedDestination": str(plan["settings"]["destination"]),
        "reflectorHost": str(plan["settings"]["reflector_host"]),
        "reflectorPort": int(plan["settings"]["reflector_port"]),
        "setupPorts": ports, "gatewayService": units[0],
        "mmdvmService": units[1], "analogBridgeService": units[2],
    }
    if mode != "p25":
        entry["emulatorService"] = units[3]
    if mode in {"p25", "nxdn"}:
        entry["mqttService"] = units[-1]
    if mode == "ysf":
        entry.update({
            "ysfReflectorName": str(plan["settings"].get("reflector_name") or ""),
            "ysfGatewayConfig": plan["resources"]["gateway"],
            "mmdvmConfig": plan["resources"]["mmdvm"],
            "ysfGatewayService": units[0], "emulatorService": units[3],
            "commandTransport": "remote_command",
            "ysfHostsPath": f"/var/lib/mmdvm/ASR-{identity}-YSFHosts.txt",
            "ysfCustomReflectors": [], "allowTune": role == "net",
            "approvedDestinations": [str(plan["settings"]["destination"])] if role == "net" else [],
        })
    else:
        entry.update({
            "digitalMode": mode, "bridgeRole": role,
            "gatewayConfig": plan["resources"]["gateway"],
            "mqttHost": "127.0.0.1", "mqttPort": ports["mqtt"],
            "mqttName": f"{mode}_{identity}",
            "mmdvmMqttName": f"mmdvm_{mode}_{identity}",
            "approvedDestinations": [str(plan["settings"]["destination"])] if role == "net" else [],
            "allowTune": role == "net",
        })
    bridges = current.setdefault("bridges", [])
    prior_net_ids = [
        str(item.get("id")) for item in bridges
        if isinstance(item, dict)
        and item.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
        and str(item.get("node", "")) == "1999" and item.get("id")
    ]
    matches = [i for i, existing in enumerate(bridges) if isinstance(existing, dict) and existing.get("id") == identity]
    if len(matches) > 1:
        raise InstallError("duplicate bridge identity")
    if matches:
        bridges[matches[0]] = entry
    else:
        bridges.append(entry)
    outputs[planner.CONFIG] = (json.dumps(current, sort_keys=True, indent=2).encode() + b"\n", config_path.stat().st_mode & 0o777)
    # Net backends share node 1999, but only the selected mode may own its
    # managed rpt.conf section. Provisioning an inactive backend must not
    # disturb the live transport; the mode controller performs later swaps.
    active_mode = str(current.get("netBridgeMode") or mode).lower()
    if role == "net" and "netBridgeMode" not in current:
        current["netBridgeMode"] = active_mode
        outputs[planner.CONFIG] = (json.dumps(current, sort_keys=True, indent=2).encode() + b"\n", config_path.stat().st_mode & 0o777)
    rpt_path = rooted(root, planner.RPT)
    rpt = rpt_path.read_text()
    if role != "net" or active_mode == mode:
        if role == "net":
            for old_id in prior_net_ids:
                if old_id == identity:
                    continue
                begin = f"{m17.MANAGED_PREFIX}BEGIN {old_id}"
                end = f"{m17.MANAGED_PREFIX}END {old_id}"
                rpt = re.sub(rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*\n?", "", rpt)
        rpt = m17.insert_nodes_mapping(rpt, plan["bridgeNode"])
        rpt = m17.upsert_node_section(rpt, identity, plan["bridgeNode"], ports["usrp_rx"], ports["usrp_tx"])
        rpt = rpt.replace(f"[{plan['bridgeNode']}] ; M17 Bridge ({identity})", f"[{plan['bridgeNode']}] ; {mode.upper()} Bridge ({identity})")
        outputs[planner.RPT] = rpt.encode(), rpt_path.stat().st_mode & 0o777
    modules_path = rooted(root, planner.MODULES)
    outputs[planner.MODULES] = m17.ensure_usrp_module(modules_path.read_text()).encode(), modules_path.stat().st_mode & 0o777
    if mode in {"p25", "nxdn"}:
        credential = credentials or {}
        secrets_path = MQTT_SECRETS
        target = rooted(root, secrets_path)
        if target.exists():
            try:
                secrets = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise InstallError("existing MQTT credential file is invalid") from exc
        else:
            secrets = {"schema": 1, "bridges": {}}
        if not isinstance(secrets, dict) or not isinstance(secrets.get("bridges"), dict):
            raise InstallError("existing MQTT credential structure is invalid")
        secrets["bridges"][identity] = {
            "aclEnforced": True,
            "gatewayMqttName": f"{mode}_{identity}",
            "mmdvmMqttName": f"mmdvm_{mode}_{identity}",
            "username": credential.get("username", ""),
            "password": credential.get("password", ""),
            "passwordHash": credential.get("passwordHash", ""),
        }
        outputs[secrets_path] = (
            json.dumps(secrets, sort_keys=True, indent=2).encode() + b"\n", 0o600
        )

def atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            if path.exists():
                owner = path.stat()
                os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def service_order(plan: dict) -> list[str]:
    services = plan["services"]
    order = [services[3]] if plan["bridgeType"] != "p25" else []
    if plan["bridgeType"] in {"p25", "nxdn"}:
        order.insert(0, services[-1])
    return [*order, services[0], services[1], services[2]]


def asterisk_command(command: str) -> str:
    result = subprocess.run(
        ["asterisk", "-rx", command], capture_output=True, text=True,
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode or "No such command" in output or "No such module" in output:
        raise InstallError(f"Asterisk {command} failed: {output[:300]}")
    return output


def verify_asterisk(node: int, restart: bool) -> None:
    # ASL3 app_rpt can crash on live module reload when adding a node. Restart
    # only when rpt.conf/modules.conf changed, then wait for node initialization.
    if restart:
        subprocess.run(["systemctl", "restart", "asterisk.service"], check=True,
                       capture_output=True, text=True)
    last_error = ""
    for _ in range(20):
        try:
            modules = asterisk_command("module show like chan_usrp.so")
            stats = asterisk_command(f"rpt stats {node}")
            if ("chan_usrp.so" in modules
                    and f"NODE {node} STATISTICS" in stats):
                return
            last_error = f"private node {node} is not initialized"
        except InstallError as error:
            last_error = str(error)
        time.sleep(0.5)
    raise InstallError(f"Asterisk verification failed: {last_error}")


def apply_services(plan: dict, restart_asterisk: bool) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    verify_asterisk(plan["bridgeNode"], restart_asterisk)
    for service in service_order(plan):
        subprocess.run(["systemctl", "enable", service], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "restart", service], check=True)
    for service in service_order(plan):
        subprocess.run(["systemctl", "is-active", "--quiet", service], check=True)


def stop_services(plan: dict) -> None:
    """Install an inactive Net Bridge backend without leaving it enabled."""
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    for service in reversed(service_order(plan)):
        subprocess.run(["systemctl", "disable", "--now", service], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def restore_services(plan: dict, existed: bool, restart_asterisk: bool) -> None:
    order = list(reversed(service_order(plan)))
    if not existed:
        for service in order:
            subprocess.run(["systemctl", "disable", "--now", service], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "daemon-reload"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if restart_asterisk:
        subprocess.run(["systemctl", "restart", "asterisk.service"], check=True,
                       capture_output=True, text=True)
    if existed:
        for service in service_order(plan):
            subprocess.run(["systemctl", "restart", service], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install(root: Path, settings, stage: Path, credentials: dict | None = None,
            expected_digest: str | None = None, fail_after: int | None = None,
            manage_services: bool = False) -> dict:
    if manage_services and root != Path("/"):
        raise InstallError("service management is only valid for the real host")
    plan = planner.plan(root, settings)
    if plan["digest"] != expected_digest:
        raise InstallError("preview is stale; preview again")
    sources.verify(stage, plan["bridgeType"])
    outputs = file_spec(plan, stage, credentials)
    integration_files(root, plan, outputs, credentials)
    identity = settings.bridge_id
    record = f"{planner.STATE}/{identity}.json"
    # Preflight every path and reject any unowned existing runtime asset.
    existing = rooted(root, record)
    old_record = json.loads(existing.read_text()) if existing.is_file() else None
    if old_record and old_record.get("bridgeType") != settings.mode:
        raise InstallError("existing installation belongs to another bridge type")
    allowed = set(old_record.get("ownedFiles", [])) if old_record else set()
    for logical in (*outputs, record):
        target = rooted(root, logical)
        if target.exists() and logical not in (planner.CONFIG, planner.RPT, planner.MODULES, MQTT_SECRETS, record) and logical not in allowed:
            raise InstallError(f"existing file is not owned by this bridge: {logical}")
        if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
            raise InstallError(f"managed path is not a regular single-link file: {logical}")
    before = {logical: (rooted(root, logical).read_bytes(), rooted(root, logical).stat().st_mode & 0o777)
              if rooted(root, logical).is_file() else None for logical in (*outputs, record)}
    before_owner = {
        logical: (rooted(root, logical).stat().st_uid, rooted(root, logical).stat().st_gid)
        if rooted(root, logical).is_file() else None
        for logical in (*outputs, record)
    }
    backup_dir = rooted(root, f"/var/lib/allscan-reimagined/bridge-setup/backups/{time.time_ns()}-{identity}")
    backup_dir.mkdir(parents=True, mode=0o700)
    manifest = {path: {"existed": item is not None, "sha256": hashlib.sha256(item[0]).hexdigest() if item else None}
                for path, item in before.items()}
    atomic(backup_dir / "manifest.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(), 0o600)
    for index, (path, item) in enumerate(before.items()):
        if item is not None:
            atomic(backup_dir / f"{index:04d}.bak", item[0], 0o600)
    changed = 0
    asterisk_changed = False
    try:
        for logical, (data, mode) in outputs.items():
            target = rooted(root, logical)
            if before[logical] == (data, mode):
                continue
            atomic(target, data, mode)
            changed += 1
            if logical in (planner.RPT, planner.MODULES):
                asterisk_changed = True
            if fail_after is not None and changed >= fail_after:
                raise InstallError("injected apply failure")
        broker_files = [path for path in outputs if path.startswith("/etc/mosquitto/asr-")]
        if root == Path("/") and broker_files:
            for logical in broker_files:
                shutil.chown(rooted(root, logical), user="root", group="mosquitto")
        backend_profile = load("asr_backend_digital_owner", "asr-provisioning-backend.py").load_profile()
        service_config = plan["resources"]["gateway"]
        if root == Path("/") and backend_profile is not None and plan["bridgeType"] != "ysf":
            shutil.chown(rooted(root, service_config), user="root", group="asr-bridge")
        broker_gid = grp.getgrnam("mosquitto").gr_gid if root == Path("/") and broker_files else None
        for logical, (data, mode) in outputs.items():
            target = rooted(root, logical)
            info = target.stat()
            if target.read_bytes() != data or stat.S_IMODE(info.st_mode) != mode:
                raise InstallError(f"verification failed: {logical}")
            if broker_gid is not None and logical in broker_files and (info.st_uid != 0 or info.st_gid != broker_gid):
                raise InstallError(f"broker file ownership verification failed: {logical}")
            if (backend_profile is not None and logical == service_config
                    and plan["bridgeType"] != "ysf"
                    and (info.st_uid != 0 or info.st_gid != grp.getgrnam("asr-bridge").gr_gid)):
                raise InstallError("gateway credential ownership verification failed")
        net_inactive = (str(plan["settings"].get("bridge_role") or "standard") == "net"
                        and json.loads(rooted(root, planner.CONFIG).read_text()).get("netBridgeMode") != plan["bridgeType"])
        if manage_services:
            if net_inactive:
                stop_services(plan)
            else:
                apply_services(plan, asterisk_changed)
        record_data = {
            "schema": 1, "bridgeType": settings.mode, "bridgeId": identity,
            "bridgeNode": plan["bridgeNode"], "ports": plan["ports"],
            "services": plan["services"], "provisioningVerified": manage_services and not net_inactive,
            "liveAudioVerified": False,
            "committedAt": old_record.get("committedAt", int(time.time())) if old_record else int(time.time()),
            "ownedFiles": sorted(path for path in outputs if path not in (planner.CONFIG, planner.RPT, planner.MODULES, MQTT_SECRETS)),
        }
        atomic(existing, (json.dumps(record_data, sort_keys=True, indent=2) + "\n").encode(), 0o600)
        return {
            "ok": True, "digest": plan["digest"], "bridgeId": identity,
            "bridgeNode": plan["bridgeNode"], "services": plan["services"],
            "changedFiles": changed, "backup": str(backup_dir),
            "provisioningVerified": manage_services and not net_inactive,
            "liveAudioVerified": False,
        }
    except Exception:
        rollback_error = None
        try:
            for logical, original in reversed(list(before.items())):
                target = rooted(root, logical)
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic(target, original[0], original[1])
                    restored = target.stat()
                    if (restored.st_uid, restored.st_gid) != before_owner[logical]:
                        os.chown(target, *before_owner[logical])
            for logical, original in before.items():
                target = rooted(root, logical)
                info = target.stat() if target.exists() else None
                mismatch = (
                    original is None and target.exists()
                    or original is not None and (
                        target.read_bytes() != original[0]
                        or stat.S_IMODE(info.st_mode) != original[1]
                        or (info.st_uid, info.st_gid) != before_owner[logical]
                    )
                )
                if mismatch:
                    raise InstallError(f"rollback verification failed: {logical}")
        except Exception as exc:
            rollback_error = exc
        if manage_services:
            try:
                restore_services(plan, old_record is not None, asterisk_changed)
            except Exception as exc:
                rollback_error = rollback_error or exc
        if rollback_error:
            raise rollback_error
        raise
