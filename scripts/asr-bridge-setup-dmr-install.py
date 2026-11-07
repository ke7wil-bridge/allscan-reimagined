#!/usr/bin/env python3
"""Transactional installer for ASR-managed direct DVSwitch DMR bridges."""
from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


class InstallError(RuntimeError):
    pass


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise InstallError(f"missing {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


planner = load("direct_dmr_install_plan", "asr-bridge-setup-dmr.py")
renderer = load("direct_dmr_install_render", "asr-bridge-setup-dmr-render.py")
sources = load("direct_dmr_install_sources", "asr-bridge-runtime-sources.py")
m17 = load("direct_dmr_install_m17", "asr-bridge-setup-m17.py")
lifecycle = load("direct_dmr_install_lifecycle", "asr-bridge-lifecycle.py")
SECRETS = "/etc/allscan-reimagined/bridge-mqtt-secrets.json"


def rooted(root: Path, logical: str) -> Path:
    if not logical.startswith("/") or ".." in Path(logical).parts:
        raise InstallError("unsafe managed path")
    backend = load("direct_dmr_install_backend", "asr-provisioning-backend.py")
    path = backend.map_path(root, logical)
    if root == Path("/") and backend.load_profile() is not None:
        return path
    current = root.resolve()
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise InstallError(f"symbolic link in managed path: {logical}")
    return path


def atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data); handle.flush(); os.fchmod(handle.fileno(), mode); os.fsync(handle.fileno())
            if path.exists():
                owner = path.stat(); os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def file_spec(plan: dict, stage: Path, password: str, creation_id: str) -> dict[str, tuple[bytes, int]]:
    if not password or len(password) > 128 or any(ord(ch) < 33 or ord(ch) == 127 for ch in password):
        raise InstallError("a valid DMR master credential is required")
    base = plan["resources"]["root"]
    outputs: dict[str, tuple[bytes, int]] = {}
    for folder, name in (("analog", "Analog_Bridge"), ("mmdvm", "MMDVM_Bridge"),
                         ("vocoder", "md380-emu"), ("vocoder", "qemu-arm-static")):
        source = stage / name
        if source.is_symlink() or not source.is_file():
            raise InstallError(f"missing verified binary {name}")
        outputs[f"{base}/{folder}/{name}"] = source.read_bytes(), 0o750
    proxy = HERE.parent / "runtime/urf/tgif/asr-tgif-urf-dmr-bridge.py"
    if not proxy.is_file():
        proxy = HERE / "asr-tgif-urf-dmr-bridge.py"
    if proxy.is_symlink() or not proxy.is_file():
        raise InstallError("direct DMR TGIF proxy is unavailable")
    proxy_data = proxy.read_bytes()
    if b"URF_ENABLED" not in proxy_data or b"banned_callsigns" not in proxy_data:
        raise InstallError("direct DMR TGIF proxy lacks direct-mode client administration support")
    outputs.update({
        f"{base}/analog/Analog_Bridge.ini": (renderer.analog_ini(plan).encode(), 0o640),
        f"{base}/mmdvm/DVSwitch.ini": (renderer.dvswitch_ini(plan).encode(), 0o640),
        f"{base}/mmdvm/MMDVM_Bridge.ini.template": (renderer.mmdvm_template(plan).encode(), 0o640),
        f"{base}/mmdvm/run-mmdvm.sh": (renderer.run_mmdvm(plan).encode(), 0o750),
        f"{base}/mmdvm/asr-tgif-dmr-proxy.py": (proxy_data, 0o750),
        f"{base}/mmdvm/DMRIds.dat": ((f"{plan['settings']['dmr_id']} {plan['settings']['callsign']}\n"
                                      f"{plan['dmrNetworkId']} {plan['settings']['callsign']}\n").encode(), 0o640),
        f"{base}/secrets/master-password": ((password + "\n").encode(), 0o640),
    })
    admin = HERE / "asr-dmr-standalone-admin-adapter.py"
    if admin.is_symlink() or not admin.is_file():
        raise InstallError("direct DMR administration adapter is unavailable")
    outputs[f"{base}/admin/asr-dmr-standalone-admin-adapter.py"] = admin.read_bytes(), 0o750
    for path, content in renderer.unit_texts(plan, creation_id).items():
        outputs[path] = content.encode(), 0o644
    marker = lifecycle.ownership_payload(plan["settings"]["bridge_id"], creation_id)
    outputs[f"{base}/.asr-bridge-owner.json"] = (json.dumps(marker, sort_keys=True).encode() + b"\n", 0o640)
    return outputs


def integration_files(root: Path, plan: dict, outputs: dict[str, tuple[bytes, int]],
                      password: str, creation_id: str) -> None:
    identity, s, p = plan["settings"]["bridge_id"], plan["settings"], plan["ports"]
    config_path = rooted(root, planner.CONFIG)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    entry = {
        "id": identity, "mode": "dmr", "node": str(plan["bridgeNode"]), "title": s["title"],
        "detailTitle": "Connected DMR Clients", "cardType": "standard",
        "architecture": "direct-dvswitch", "instance": identity,
        "backendMode": "managed", "bridgePermission": "self_owned", "clientSource": "local_json",
        "clientUrl": f"/run/asr-direct-dmr-{identity}/dmr-clients.json",
        "fixedDestination": str(s["talkgroup"]), "dmrNetwork": s["network"],
        "dmrMasterHost": s["master_host"], "dmrMasterPort": s["master_port"],
        "dmrCallsign": s["callsign"],
        "dmrId": str(s["dmr_id"]), "dmrNetworkId": str(plan["dmrNetworkId"]),
        "tgifEssid": "" if s["tgif_essid"] is None else str(s["tgif_essid"]),
        "dmrTimeslot": s["timeslot"], "dmrColorCode": s["color_code"],
        "dmrToAslGainDb": s["dmr_to_asl_gain_db"], "aslToDmrGainDb": s["asl_to_dmr_gain_db"],
        "setupPorts": p, "vocoderService": plan["services"][0],
        "mmdvmService": plan["services"][1], "analogBridgeService": plan["services"][2],
        "adminService": plan["services"][3],
        "credentialRef": f"{plan['resources']['root']}/secrets/master-password",
        "fixedBridgeRecovery": True, "liveAudioVerified": False,
    }
    bridges = config.setdefault("bridges", [])
    matches = [index for index, item in enumerate(bridges) if isinstance(item, dict) and item.get("id") == identity]
    if len(matches) > 1:
        raise InstallError("duplicate DMR bridge identity")
    if matches:
        bridges[matches[0]] = entry
    else:
        bridges.append(entry)
    outputs[planner.CONFIG] = json.dumps(config, indent=2, sort_keys=True).encode() + b"\n", config_path.stat().st_mode & 0o777
    rpt_path = rooted(root, planner.RPT)
    rpt = m17.insert_nodes_mapping(rpt_path.read_text(), plan["bridgeNode"])
    # Convert only this bridge's exact owned block to the M17 helper's
    # temporary marker format, so an update can replace its existing node.
    owned_begin = f"; BEGIN ASR BRIDGE {identity} {creation_id}"
    owned_end = f"; END ASR BRIDGE {identity} {creation_id}"
    if owned_begin in rpt or owned_end in rpt:
        if rpt.count(owned_begin) != 1 or rpt.count(owned_end) != 1:
            raise InstallError("direct DMR node ownership markers are incomplete or duplicated")
        rpt = rpt.replace(owned_begin, f"{m17.MANAGED_PREFIX}BEGIN {identity}", 1)
        rpt = rpt.replace(owned_end, f"{m17.MANAGED_PREFIX}END {identity}", 1)
    rpt = m17.upsert_node_section(rpt, identity, plan["bridgeNode"], p["usrp_rx"], p["usrp_tx"])
    rpt = rpt.replace(f"[{plan['bridgeNode']}] ; M17 Bridge ({identity})", f"[{plan['bridgeNode']}] ; Direct DMR Bridge ({identity})")
    rpt = rpt.replace(f"{m17.MANAGED_PREFIX}BEGIN {identity}", f"; BEGIN ASR BRIDGE {identity} {creation_id}")
    rpt = rpt.replace(f"{m17.MANAGED_PREFIX}END {identity}", f"; END ASR BRIDGE {identity} {creation_id}")
    outputs[planner.RPT] = rpt.encode(), rpt_path.stat().st_mode & 0o777
    modules_path = rooted(root, planner.MODULES)
    outputs[planner.MODULES] = m17.ensure_usrp_module(modules_path.read_text()).encode(), modules_path.stat().st_mode & 0o777
    secrets_path = rooted(root, SECRETS)
    secrets = json.loads(secrets_path.read_text()) if secrets_path.is_file() else {"schema": 1, "bridges": {}}
    if not isinstance(secrets, dict) or not isinstance(secrets.get("bridges"), dict):
        raise InstallError("bridge credential store is invalid")
    secrets["bridges"][identity] = {
        "dmrMasterPassword": password,
        "credentialType": "dmr-master",
        "asrOwnership": lifecycle.ownership_payload(identity, creation_id),
    }
    outputs[SECRETS] = json.dumps(secrets, indent=2, sort_keys=True).encode() + b"\n", 0o600


def verify_asterisk(node: int, restart: bool) -> None:
    if restart:
        subprocess.run(["systemctl", "restart", "asterisk.service"], check=True, capture_output=True, text=True)
    for _ in range(20):
        result = subprocess.run(["asterisk", "-rx", f"rpt stats {node}"], capture_output=True, text=True)
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            return
        time.sleep(0.5)
    raise InstallError(f"Asterisk did not register direct DMR node {node}")


def apply_services(plan: dict, restart_asterisk: bool) -> None:
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    verify_asterisk(plan["bridgeNode"], restart_asterisk)
    health = Path(f"/run/asr-direct-dmr-{plan['settings']['bridge_id']}/tgif-health")
    health.unlink(missing_ok=True)
    for service in plan["services"]:
        subprocess.run(["systemctl", "enable", service], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for service in plan["services"]:
        subprocess.run(["systemctl", "restart", service], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # TGIF may retain the previous connection briefly during a managed
    # replacement.  Allow the old master session to expire and the new proxy
    # to complete its challenge/response before classifying authentication as
    # failed.  The loop remains bounded so installation still rolls back.
    for _ in range(180):
        if health.is_file() and time.time() - health.stat().st_mtime < 15:
            return
        time.sleep(0.5)
    raise InstallError("TGIF authentication did not become healthy")


def ownership_manifest(plan: dict, creation_id: str) -> dict:
    identity, root = plan["settings"]["bridge_id"], Path(plan["resources"]["root"])
    units = []
    marker = f"# ASR-BRIDGE-OWNER: {identity} {creation_id}"
    for name in plan["services"]:
        path = Path("/etc/systemd/system") / name
        units.append({"name": name, "unitFile": str(path), "sha256": lifecycle.sha256_path(path),
                      "ownerMarker": marker})
    found = lifecycle.marked_block(Path(planner.RPT), identity, creation_id)
    if found is None:
        raise InstallError("direct DMR Asterisk ownership block is missing")
    _outside, owned = found
    admin_root = Path("/run/asr-standalone-admin") / identity
    return {
        "schema": 1, "ownership": "asr", "createdBy": lifecycle.CREATED_BY,
        "creationId": creation_id, "bridgeId": identity, "mode": "dmr", "role": "standard",
        "mainNode": str(plan["mainNode"]), "bridgeNode": str(plan["bridgeNode"]),
        "asteriskBlocks": [{"path": planner.RPT, "marker": identity, "creationId": creation_id,
                            "sha256": hashlib.sha256("".join(owned).encode()).hexdigest()}],
        "units": units,
        "ownedPaths": [{"path": str(root), "kind": "directory", "sha256": "",
                        "marker": str(root / ".asr-bridge-owner.json")}],
        "runtimePaths": [{"path": str(admin_root / "capabilities.json"),
                          "marker": str(admin_root / ".asr-bridge-owner.json")}],
        "credentialEntries": [{"path": SECRETS, "rootKey": "bridges", "key": identity,
                               "createdBy": lifecycle.CREATED_BY, "creationId": creation_id}],
        "firewallFiles": [],
        "listeners": [{"protocol": "udp", "port": port} for port in sorted(set(plan["ports"].values()))],
        "topics": [], "createdAt": int(time.time()),
    }


def install(root: Path, settings, stage: Path, password: str, *, expected_digest: str,
            manage_services: bool = False, fail_after: int | None = None) -> dict:
    if manage_services and root != Path("/"):
        raise InstallError("service management is valid only on the real host")
    plan = planner.plan(root, settings)
    if plan["digest"] != expected_digest:
        raise InstallError("preview is stale; preview again")
    sources.verify(stage, "dmr")
    record_logical = plan["resources"]["record"]
    record_path = rooted(root, record_logical)
    old_record = json.loads(record_path.read_text()) if record_path.is_file() else None
    new_lifecycle = False
    creation_id = "0" * 32
    if manage_services:
        manifest_path = lifecycle.MANIFEST_ROOT / f"{settings.bridge_id}.json"
        if old_record:
            manifest = lifecycle.validate_manifest(lifecycle.read_json_regular(manifest_path, root_only=True))
            if manifest["bridgeId"] != settings.bridge_id or manifest["mode"] != "dmr":
                raise InstallError("direct DMR lifecycle ownership does not match its installation record")
            creation_id = manifest["creationId"]
        else:
            intent = lifecycle.preflight_creation({
                "bridgeId": settings.bridge_id, "mode": "dmr", "role": "standard",
                "mainNode": str(plan["mainNode"]), "bridgeNode": str(plan["bridgeNode"]),
                "units": plan["services"],
                "runtimePaths": [f"/run/asr-standalone-admin/{settings.bridge_id}/capabilities.json"],
                "firewallFiles": [], "credentialEntry": True,
            })
            creation_id = intent["creationId"]
            new_lifecycle = True
    outputs = file_spec(plan, stage, password, creation_id)
    integration_files(root, plan, outputs, password, creation_id)
    allowed = set(old_record.get("ownedFiles", [])) if isinstance(old_record, dict) else set()
    shared = {planner.CONFIG, planner.RPT, planner.MODULES, SECRETS, record_logical}
    for logical in (*outputs, record_logical):
        target = rooted(root, logical)
        if target.exists() and logical not in shared and logical not in allowed:
            raise InstallError(f"pre-existing resource is not owned by this DMR bridge: {logical}")
        if target.exists() and (not target.is_file() or target.is_symlink() or target.stat().st_nlink != 1):
            raise InstallError(f"unsafe managed file: {logical}")
    paths = list(outputs) + [record_logical]
    before = {logical: (rooted(root, logical).read_bytes(), stat.S_IMODE(rooted(root, logical).stat().st_mode))
              if rooted(root, logical).is_file() else None for logical in paths}
    owners = {logical: (rooted(root, logical).stat().st_uid, rooted(root, logical).stat().st_gid)
              if rooted(root, logical).is_file() else None for logical in paths}
    backup = rooted(root, f"/var/lib/allscan-reimagined/bridge-setup/backups/{time.time_ns()}-{settings.bridge_id}")
    backup.mkdir(parents=True, mode=0o700)
    atomic(backup / "manifest.json", (json.dumps({k: {"existed": v is not None,
           "sha256": hashlib.sha256(v[0]).hexdigest() if v else None} for k, v in before.items()},
           indent=2, sort_keys=True) + "\n").encode(), 0o600)
    changed = 0
    asterisk_changed = False
    try:
        for logical, (data, mode) in outputs.items():
            if before[logical] == (data, mode):
                continue
            atomic(rooted(root, logical), data, mode); changed += 1
            asterisk_changed |= logical in {planner.RPT, planner.MODULES}
            if fail_after is not None and changed >= fail_after:
                raise InstallError("injected apply failure")
        if root == Path("/"):
            group = grp.getgrnam("asr-bridge").gr_gid
            for logical in outputs:
                if logical.startswith(plan["resources"]["root"] + "/"):
                    os.chown(rooted(root, logical), 0, group)
        if manage_services:
            apply_services(plan, asterisk_changed)
        if new_lifecycle:
            lifecycle.register_created_manifest(ownership_manifest(plan, creation_id))
        record = {"schema": 1, "bridgeType": "dmr", "architecture": "direct-dvswitch",
                  "bridgeId": settings.bridge_id, "bridgeNode": plan["bridgeNode"],
                  "dmrNetworkId": plan["dmrNetworkId"], "ports": plan["ports"],
                  "services": plan["services"], "credentialRef": f"{plan['resources']['root']}/secrets/master-password",
                  "creationId": creation_id, "lifecycleOwned": manage_services,
                  "ownedFiles": sorted(path for path in outputs if path not in shared),
                  "provisioningVerified": manage_services, "liveAudioVerified": False,
                  "committedAt": old_record.get("committedAt", int(time.time())) if old_record else int(time.time())}
        atomic(record_path, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(), 0o600)
        return {"ok": True, "digest": plan["digest"], "bridgeId": settings.bridge_id,
                "bridgeNode": plan["bridgeNode"], "dmrNetworkId": str(plan["dmrNetworkId"]),
                "services": plan["services"], "changedFiles": changed, "backup": str(backup),
                "provisioningVerified": manage_services, "liveAudioVerified": False}
    except Exception:
        for logical in reversed(paths):
            target, original = rooted(root, logical), before[logical]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic(target, original[0], original[1]); os.chown(target, *owners[logical])
        if manage_services:
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            if old_record:
                if asterisk_changed:
                    subprocess.run(["systemctl", "restart", "asterisk.service"], check=False)
                for service in plan["services"]:
                    subprocess.run(["systemctl", "enable", service], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for service in plan["services"]:
                    subprocess.run(["systemctl", "restart", service], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                for service in reversed(plan["services"]):
                    subprocess.run(["systemctl", "disable", "--now", service], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if new_lifecycle:
            intent_path = lifecycle.CREATION_INTENT_ROOT / f"{settings.bridge_id}.json"
            try:
                intent = lifecycle.read_json_regular(intent_path, root_only=True)
                if (isinstance(intent, dict) and intent.get("bridgeId") == settings.bridge_id
                        and intent.get("creationId") == creation_id):
                    intent_path.unlink()
            except (FileNotFoundError, OSError, ValueError):
                pass
        raise
