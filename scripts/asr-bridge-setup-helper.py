#!/usr/bin/env python3
"""Narrow privileged boundary for Bridge Setup Wizard provisioning."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "asr-bridge-setup-core.py"
M17_PATH = HERE / "asr-bridge-setup-m17.py"
DIGITAL_PLAN_PATH = HERE / "asr-bridge-setup-digital.py"
DIGITAL_INSTALL_PATH = HERE / "asr-bridge-setup-digital-install.py"
DIGITAL_SOURCE_PATH = HERE / "asr-bridge-runtime-sources.py"
DIGITAL_STAGE_ROOT = Path("/var/cache/allscan-reimagined/bridge-runtime")
URF_PLAN_PATH = HERE / "asr-bridge-setup-urf.py"
URF_INSTALL_PATH = HERE / "asr-bridge-setup-urf-install.py"
ZELLO_PLAN_PATH = HERE / "asr-bridge-setup-zello.py"
ZELLO_INSTALL_PATH = HERE / "asr-bridge-setup-zello-install.py"
def provisioning_config_path(logical: str) -> Path:
    profile = os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE", "")
    if not profile:
        return Path(logical)
    spec = importlib.util.spec_from_file_location("asr_backend_helper", HERE / "asr-provisioning-backend.py")
    if not spec or not spec.loader:
        raise RuntimeError("provisioning backend module is unavailable")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module.map_path(Path("/"), logical)

MQTT_SECRETS_PATH = provisioning_config_path("/etc/allscan-reimagined/bridge-mqtt-secrets.json")
STATE_ROOT = Path("/var/lib/allscan-reimagined/bridge-setup")
AUDIT_LOG = Path("/var/log/allscan-reimagined/bridge-setup-audit.jsonl")
LOCK_PATH = Path("/run/lock/allscan-reimagined-bridge-setup.lock")
MAX_STDIN = 256 * 1024


class HelperError(RuntimeError):
    pass


def load_core():
    spec = importlib.util.spec_from_file_location("asr_bridge_setup_core", CORE_PATH)
    if not spec or not spec.loader:
        raise HelperError("setup core could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_STDIN + 1)
    if len(raw) > MAX_STDIN:
        raise HelperError("request exceeds size limit")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperError("request is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HelperError("request must be an object")
    return payload


def require_root() -> None:
    if os.geteuid() != 0:
        raise HelperError("bridge setup changes require root")


def secure_parent(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise HelperError(f"unsafe state directory: {path}")
    os.chmod(path, mode)


def append_audit(event: dict[str, Any], path: Path = AUDIT_LOG) -> None:
    secure_parent(path.parent, 0o750)
    record = {"schema": 1, "epoch": int(time.time()), **event}
    data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o640)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o027:
            raise HelperError("audit log permissions are unsafe")
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def plan_from_payload(core, payload: dict[str, Any]):
    allowed = {"schema", "platform", "bridge_type", "bridge_id", "actions", "protected_paths"}
    if set(payload) - allowed:
        raise HelperError("plan contains unsupported fields")
    if payload.get("schema") != 1:
        raise HelperError("unsupported plan schema")
    actions = payload.get("actions")
    protected = payload.get("protected_paths")
    if not isinstance(actions, list) or len(actions) > 128 or not isinstance(protected, list):
        raise HelperError("plan action or protected-path list is invalid")
    parsed = []
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"kind", "target", "description", "owned"}:
            raise HelperError("plan action shape is invalid")
        parsed.append(core.PlanAction(**action))
    return core.ProvisioningPlan(
        schema=1,
        platform=str(payload.get("platform", "")),
        bridge_type=str(payload.get("bridge_type", "")),
        bridge_id=str(payload.get("bridge_id", "")),
        actions=tuple(parsed),
        protected_paths=tuple(str(item) for item in protected),
    )


def backup_existing(plan, root: Path = STATE_ROOT) -> Path:
    if root == STATE_ROOT:
        require_root()
    secure_parent(root, 0o700)
    stamp = f"{int(time.time())}-{plan.digest()[:12]}"
    destination = root / "backups" / stamp
    destination.mkdir(parents=True, mode=0o700)
    manifest: list[dict[str, Any]] = []
    for index, action in enumerate(plan.actions):
        target = Path(action.target)
        if not target.is_absolute() or target.is_symlink():
            if target.is_symlink():
                raise HelperError(f"refusing to back up symlink target: {target}")
            continue
        if not target.exists():
            manifest.append({"target": str(target), "existed": False})
            continue
        if not target.is_file():
            raise HelperError(f"backup supports regular files only: {target}")
        backup = destination / f"{index:03d}-{target.name}"
        shutil.copy2(target, backup)
        os.chmod(backup, 0o600)
        manifest.append({
            "target": str(target), "existed": True, "backup": backup.name,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        })
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(destination / "manifest.json", 0o600)
    return destination


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    core = load_core()
    plan = plan_from_payload(core, payload)
    adapter = core.ASL3Adapter()
    adapter.validate_plan(plan)
    facts = adapter.detect()
    failures = core.preflight(plan, facts)
    if failures:
        raise HelperError("; ".join(failures))
    return {
        "ok": True, "digest": plan.digest(), "bridgeId": plan.bridge_id,
        "platform": facts.platform, "hostname": facts.hostname,
    }


def backup(payload: dict[str, Any]) -> dict[str, Any]:
    core = load_core()
    plan = plan_from_payload(core, payload)
    core.ASL3Adapter().validate_plan(plan)
    destination = backup_existing(plan)
    append_audit({"action": "backup", "bridgeId": plan.bridge_id, "digest": plan.digest(), "result": "ok"})
    return {"ok": True, "backup": str(destination), "digest": plan.digest()}


def load_m17():
    spec = importlib.util.spec_from_file_location("asr_bridge_setup_m17", M17_PATH)
    if not spec or not spec.loader:
        raise HelperError("M17 setup module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def m17_settings(module, payload: dict[str, Any]):
    allowed = {
        "bridgeId", "callsign", "reflector", "host", "port", "module",
        "title", "mainNode", "bridgeNode", "bridgeRole", "planDigest",
    }
    if set(payload) - allowed:
        raise HelperError("M17 request contains unsupported fields")
    required = {"bridgeId", "callsign", "reflector", "host", "port", "module"}
    if not required.issubset(payload):
        raise HelperError("M17 request is incomplete")
    return module.M17Settings(
        bridge_id=str(payload["bridgeId"]), callsign=str(payload["callsign"]),
        reflector=str(payload["reflector"]), host=str(payload["host"]),
        port=int(payload["port"]), module=str(payload["module"]),
        card_type="m17_net" if str(payload.get("bridgeRole") or "standard") == "net" else "standard",
        title=str(payload.get("title") or "M17 Bridge"),
        main_node=int(payload["mainNode"]) if payload.get("mainNode") else None,
        bridge_node=int(payload["bridgeNode"]) if payload.get("bridgeNode") else None,
    )


def m17_command(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    module = load_m17()
    settings = m17_settings(module, payload)
    if apply:
        require_root()
        expected_digest = str(payload.get("planDigest", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
            raise HelperError("M17 installation requires a current preview digest")
        result = module.install(
            Path("/"), settings, manage_services=True,
            expected_digest=expected_digest,
        )
    else:
        result = module.plan(Path("/"), settings)
    result = {key: value for key, value in result.items() if key != "files"}
    append_audit({
        "action": "m17-install" if apply else "m17-plan",
        "bridgeId": settings.bridge_id, "result": "ok",
    })
    return result


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise HelperError(f"{path.name} could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digital_settings(module, mode: str, payload: dict[str, Any]):
    allowed = {
        "bridgeId", "callsign", "digitalId", "destination", "host", "port",
        "title", "bridgeNode", "bridgeRole", "planDigest", "name",
    }
    if set(payload) - allowed:
        raise HelperError(f"{mode.upper()} request contains unsupported fields")
    required = {"bridgeId", "callsign", "digitalId", "destination", "host", "port"}
    if not required.issubset(payload):
        raise HelperError(f"{mode.upper()} request is incomplete")
    return module.DigitalSettings(
        mode=mode, bridge_id=str(payload["bridgeId"]),
        callsign=str(payload["callsign"]), digital_id=int(payload["digitalId"]),
        destination=int(payload["destination"]), reflector_host=str(payload["host"]),
        reflector_port=int(payload["port"]), title=str(payload.get("title") or ""),
        bridge_node=int(payload["bridgeNode"]) if payload.get("bridgeNode") else None,
        bridge_role=str(payload.get("bridgeRole") or "standard"),
        reflector_name=str(payload.get("name") or ""),
    )


def digital_credentials(mode: str, bridge_id: str) -> dict[str, str]:
    if mode == "ysf":
        return {}
    if MQTT_SECRETS_PATH.is_file() and not MQTT_SECRETS_PATH.is_symlink():
        info = MQTT_SECRETS_PATH.stat()
        if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise HelperError("existing MQTT credential file permissions are unsafe")
        payload = json.loads(MQTT_SECRETS_PATH.read_text(encoding="utf-8"))
        entry = payload.get("bridges", {}).get(bridge_id) if isinstance(payload, dict) else None
        if isinstance(entry, dict) and entry.get("username") and entry.get("password"):
            username, password = str(entry["username"]), str(entry["password"])
            hashed = str(entry.get("passwordHash") or "")
        else:
            username, password, hashed = f"asr_{bridge_id}", secrets.token_urlsafe(32), ""
    else:
        username, password, hashed = f"asr_{bridge_id}", secrets.token_urlsafe(32), ""
    if not hashed.startswith("$") or any(ch in hashed for ch in "\r\n\0"):
        # The container-host broker intentionally has no general /tmp write
        # access.  Keep credential hashing inside its root-only runtime cache.
        secure_parent(DIGITAL_STAGE_ROOT, 0o700)
        with tempfile.TemporaryDirectory(prefix="asr-mqtt-password-", dir=DIGITAL_STAGE_ROOT) as temporary:
            password_file = Path(temporary) / "passwords"
            subprocess.run(
                ["mosquitto_passwd", "-b", "-c", str(password_file), username, password],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            hashed = password_file.read_text(encoding="utf-8").strip().partition(":")[2]
    if not hashed.startswith("$"):
        raise HelperError("failed to generate broker credential hash")
    return {"username": username, "password": password, "passwordHash": hashed}


def prepare_digital_stage(source, mode: str) -> Path:
    secure_parent(DIGITAL_STAGE_ROOT, 0o700)
    destination = DIGITAL_STAGE_ROOT / mode
    if destination.is_symlink():
        raise HelperError("runtime cache path is unsafe")
    if destination.is_dir():
        try:
            source.verify(destination, mode)
        except source.SourceError:
            shutil.rmtree(destination)
        else:
            if not os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE"):
                source.install_packages(build=False)
            return destination
    if os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE"):
        raise HelperError("container host runtime was not pre-staged by the administrator")
    source.install_packages(build=True)
    with tempfile.TemporaryDirectory(prefix=f".{mode}-", dir=DIGITAL_STAGE_ROOT) as temporary:
        staged = Path(temporary) / "runtime"
        source.prepare(mode, staged, False)
        source.verify(staged, mode)
        os.replace(staged, destination)
    return destination


def installed_packages() -> set[str]:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${binary:Package}\\t${Status}\\n"],
        capture_output=True, text=True, check=True,
    )
    return {line.split("\t", 1)[0] for line in result.stdout.splitlines()
            if line.endswith("\tinstall ok installed")}


def rollback_packages(before: set[str]) -> None:
    added = sorted(installed_packages() - before)
    if not added:
        return
    simulation = subprocess.run(
        ["apt-get", "-s", "remove", "--purge", *added],
        capture_output=True, text=True, check=True,
    )
    removed = {line.split()[1].split(":")[0] for line in simulation.stdout.splitlines()
               if line.startswith("Remv ") or line.startswith("Purg ")}
    if removed - {name.split(":")[0] for name in added}:
        raise HelperError("package rollback would remove pre-existing packages")
    result = subprocess.run(
        ["apt-get", "remove", "--purge", "-y", *added],
        capture_output=True, text=True,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode or installed_packages() - before:
        raise HelperError(f"package rollback failed: {result.stderr[-500:]}")


def digital_command(payload: dict[str, Any], mode: str, apply: bool) -> dict[str, Any]:
    planner = load_module(f"asr_setup_{mode}_plan", DIGITAL_PLAN_PATH)
    settings = digital_settings(planner, mode, payload)
    if not apply:
        result = planner.plan(Path("/"), settings)
    else:
        require_root()
        expected = str(payload.get("planDigest", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise HelperError(f"{mode.upper()} installation requires a current preview digest")
        # Reject stale previews before package installation or runtime staging mutates the host.
        if planner.plan(Path("/"), settings)["digest"] != expected:
            raise HelperError("preview is stale; preview again")
        source = load_module(f"asr_setup_{mode}_source", DIGITAL_SOURCE_PATH)
        packages_before = installed_packages()
        try:
            stage = prepare_digital_stage(source, mode)
            credentials = digital_credentials(mode, settings.bridge_id)
            installer = load_module(f"asr_setup_{mode}_install", DIGITAL_INSTALL_PATH)
            result = installer.install(
                Path("/"), settings, stage, credentials,
                expected_digest=expected, manage_services=True,
            )
        except Exception as original:
            try:
                rollback_packages(packages_before)
            except Exception as rollback:
                raise HelperError(
                    f"installation failed ({original}); package rollback failed ({rollback})"
                ) from rollback
            raise
    append_audit({
        "action": f"{mode}-install" if apply else f"{mode}-plan",
        "bridgeId": settings.bridge_id, "result": "ok",
    })
    return result


def dmr_settings(module, payload: dict[str, Any]):
    allowed = {
        "bridgeId", "callsign", "digitalId", "destination", "title",
        "bridgeNode", "bridgeRole", "planDigest", "tgifPassword", "network", "networkUsername", "authMode",
    }
    if set(payload) - allowed:
        raise HelperError("DMR request contains unsupported fields")
    required = {"bridgeId", "callsign", "digitalId", "destination"}
    if not required.issubset(payload):
        raise HelperError("DMR request is incomplete")
    if str(payload.get("network", "tgif")).lower() != "tgif":
        raise HelperError("The selected DMR network does not have an installer yet")
    if payload.get("authMode", "legacy") not in ("legacy", "secured"):
        raise HelperError("Unsupported TGIF DMR connection method")
    callsign = str(payload["callsign"]).strip().upper()
    reflector = "URF" + re.sub(r"[^A-Z0-9]", "", callsign)[-3:]
    if len(reflector) < 5:
        reflector = "URFASR"
    return module.UrfSettings(
        bridge_id=str(payload["bridgeId"]), callsign=callsign,
        dmr_id=int(payload["digitalId"]), tgif_tg=int(payload["destination"]),
        reflector=reflector, title=str(payload.get("title") or "DMR Bridge"),
        bridge_node=int(payload["bridgeNode"]) if payload.get("bridgeNode") else None,
        bridge_role=str(payload.get("bridgeRole") or "standard"),
    )


def dmr_command(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    planner = load_module("asr_setup_dmr_plan", URF_PLAN_PATH)
    settings = dmr_settings(planner, payload)
    if not apply:
        result = planner.plan(Path("/"), settings)
    else:
        require_root()
        expected = str(payload.get("planDigest", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise HelperError("DMR installation requires a current preview digest")
        if planner.plan(Path("/"), settings)["digest"] != expected:
            raise HelperError("preview is stale; preview again")
        auth_mode = payload.get("authMode", "secured" if payload.get("tgifPassword") else "legacy")
        if auth_mode == "legacy":
            if payload.get("tgifPassword"):
                raise HelperError("A website password must not be submitted for TGIF legacy connection")
            password = "passw0rd"
        else:
            password = str(payload.get("tgifPassword") or "")
            if not password:
                raise HelperError("TGIF secured connection requires a hotspot key")
        installer = load_module("asr_setup_dmr_install", URF_INSTALL_PATH)
        result = installer.install(
            Path("/"), settings, password,
            expected_digest=expected, manage_services=True,
        )
    append_audit({
        "action": "dmr-install" if apply else "dmr-plan",
        "bridgeId": settings.bridge_id, "result": "ok",
    })
    return result



def zello_settings(module, payload: dict[str, Any]):
    allowed = {
        "bridgeId", "username", "channel", "issuer", "wsEndpoint", "title",
        "bridgeNode", "planDigest", "password", "privateKey",
    }
    if set(payload) - allowed:
        raise HelperError("Zello request contains unsupported fields")
    required = {"bridgeId", "username", "channel", "issuer", "wsEndpoint"}
    if not required.issubset(payload):
        raise HelperError("Zello request is incomplete")
    return module.ZelloSettings(
        bridge_id=str(payload["bridgeId"]), username=str(payload["username"]),
        channel=str(payload["channel"]), issuer=str(payload["issuer"]),
        ws_endpoint=str(payload["wsEndpoint"]),
        title=str(payload.get("title") or "Zello Bridge"),
        bridge_node=int(payload["bridgeNode"]) if payload.get("bridgeNode") else None,
    )


def zello_command(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    planner = load_module("asr_setup_zello_plan", ZELLO_PLAN_PATH)
    settings = zello_settings(planner, payload)
    if not apply:
        result = planner.plan(Path("/"), settings)
    else:
        require_root()
        expected = str(payload.get("planDigest", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise HelperError("Zello installation requires a current preview digest")
        if planner.plan(Path("/"), settings)["digest"] != expected:
            raise HelperError("preview is stale; preview again")
        installer = load_module("asr_setup_zello_install", ZELLO_INSTALL_PATH)
        result = installer.install(
            Path("/"), settings,
            {"password": str(payload.get("password") or ""),
             "privateKey": str(payload.get("privateKey") or "")},
            expected_digest=expected, manage_services=True,
        )
    append_audit({
        "action": "zello-install" if apply else "zello-plan",
        "bridgeId": settings.bridge_id, "result": "ok",
    })
    return result



NET_MODE_IDS = {
    "dmr": "dmr_tgif_net", "ysf": "ysf_net", "p25": "p25_net",
    "nxdn": "nxdn_net", "m17": "m17_net",
}


def net_bridge_mode_payloads(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowed = {"callsign", "digitalId", "mainNode", "modes", "planDigest"}
    if set(payload) - allowed or not isinstance(payload.get("modes"), dict):
        raise HelperError("Net Bridge request contains unsupported fields")
    if set(payload["modes"]) != set(NET_MODE_IDS):
        raise HelperError("Net Bridge requires DMR, YSF, P25, NXDN, and M17 configuration")
    callsign = str(payload.get("callsign") or "").strip().upper()
    digital_id = str(payload.get("digitalId") or "")
    main_node = str(payload.get("mainNode") or "")
    if not re.fullmatch(r"[A-Z0-9]{3,10}", callsign) or not digital_id.isdigit() or not main_node.isdigit():
        raise HelperError("Net Bridge common configuration is incomplete")
    modes = payload["modes"]
    schemas = {
        "dmr": {"destination", "authMode", "tgifPassword"},
        "ysf": {"destination", "name", "host", "port"},
        "p25": {"destination", "host", "port"},
        "nxdn": {"destination", "host", "port"},
        "m17": {"reflector", "host", "port", "module"},
    }
    for mode, value in modes.items():
        if not isinstance(value, dict) or set(value) - schemas[mode]:
            raise HelperError(f"Net Bridge {mode.upper()} configuration is invalid")
    common = {"callsign": callsign, "digitalId": digital_id, "bridgeRole": "net", "bridgeNode": "1999"}
    result = {
        "dmr": {**common, "bridgeId": NET_MODE_IDS["dmr"], "destination": str(modes["dmr"].get("destination", "")),
                "network": "tgif", "authMode": str(modes["dmr"].get("authMode") or "legacy"),
                "tgifPassword": str(modes["dmr"].get("tgifPassword") or ""), "title": "DMR Net Bridge"},
        "ysf": {**common, "bridgeId": NET_MODE_IDS["ysf"], "destination": str(modes["ysf"].get("destination", "")),
                "name": str(modes["ysf"].get("name", "")), "host": str(modes["ysf"].get("host", "")),
                "port": str(modes["ysf"].get("port", "")), "title": "YSF Net Bridge"},
        "p25": {**common, "bridgeId": NET_MODE_IDS["p25"], "destination": str(modes["p25"].get("destination", "")),
                "host": str(modes["p25"].get("host", "")), "port": str(modes["p25"].get("port", "")), "title": "P25 Net Bridge"},
        "nxdn": {**common, "bridgeId": NET_MODE_IDS["nxdn"], "destination": str(modes["nxdn"].get("destination", "")),
                 "host": str(modes["nxdn"].get("host", "")), "port": str(modes["nxdn"].get("port", "")), "title": "NXDN Net Bridge"},
        "m17": {"bridgeId": NET_MODE_IDS["m17"], "callsign": callsign,
                "reflector": str(modes["m17"].get("reflector", "")), "host": str(modes["m17"].get("host", "")),
                "port": str(modes["m17"].get("port", "")), "module": str(modes["m17"].get("module", "")),
                "mainNode": main_node, "bridgeRole": "net", "bridgeNode": "1999", "title": "M17 Net Bridge"},
    }
    # Constructor validation is part of the narrow allowlist boundary.
    dmr_settings(load_module("asr_net_dmr_validate", URF_PLAN_PATH), result["dmr"])
    planner = load_module("asr_net_digital_validate", DIGITAL_PLAN_PATH)
    for mode in ("ysf", "p25", "nxdn"):
        digital_settings(planner, mode, result[mode])
    m17_settings(load_m17(), result["m17"])
    return result


def net_bridge_plan(payload: dict[str, Any]) -> dict[str, Any]:
    per_mode = net_bridge_mode_payloads(payload)
    sanitized = json.loads(json.dumps(payload))
    sanitized.pop("planDigest", None)
    sanitized["modes"]["dmr"].pop("tgifPassword", None)
    host_plans = {
        "dmr": load_module("asr_net_dmr_plan", URF_PLAN_PATH).plan(
            Path("/"), dmr_settings(load_module("asr_net_dmr_settings", URF_PLAN_PATH), per_mode["dmr"])),
        "m17": load_m17().plan(Path("/"), m17_settings(load_m17(), per_mode["m17"])),
    }
    digital = load_module("asr_net_digital_plan", DIGITAL_PLAN_PATH)
    for mode in ("ysf", "p25", "nxdn"):
        host_plans[mode] = digital.plan(Path("/"), digital_settings(digital, mode, per_mode[mode]))
    material = {"request": sanitized, "plans": {mode: item["digest"] for mode, item in host_plans.items()}}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    active_mode = "dmr"
    config_path = provisioning_config_path("/etc/allscan-reimagined/config.json")
    if config_path.is_file():
        configured_mode = str(json.loads(config_path.read_text()).get("netBridgeMode") or "").lower()
        if configured_mode in NET_MODE_IDS:
            active_mode = configured_mode
    return {"ok": True, "schema": 1, "bridgeType": "net_bridge", "bridgeNode": 1999,
            "modes": list(NET_MODE_IDS), "activeMode": active_mode, "digest": digest}


def net_bridge_command(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    preview = net_bridge_plan(payload)
    if not apply:
        return preview
    require_root()
    if payload.get("planDigest") != preview["digest"]:
        raise HelperError("Net Bridge preview is stale; preview again")
    per_mode = net_bridge_mode_payloads(payload)
    results = {}
    for mode in ("dmr", "ysf", "p25", "nxdn", "m17"):
        request = per_mode[mode]
        if mode == "dmr":
            module = load_module("asr_net_dmr_apply_plan", URF_PLAN_PATH)
            request["planDigest"] = module.plan(Path("/"), dmr_settings(module, request))["digest"]
            results[mode] = dmr_command(request, True)
        elif mode == "m17":
            module = load_m17()
            request["planDigest"] = module.plan(Path("/"), m17_settings(module, request))["digest"]
            results[mode] = m17_command(request, True)
        else:
            module = load_module(f"asr_net_{mode}_apply_plan", DIGITAL_PLAN_PATH)
            request["planDigest"] = module.plan(Path("/"), digital_settings(module, mode, request))["digest"]
            results[mode] = digital_command(request, mode, True)
    controller = Path("/usr/local/sbin/allscan-reimagined-net-bridge-mode-control")
    if not controller.is_file() or controller.is_symlink():
        raise HelperError("Net Bridge lifecycle controller is unavailable")
    activated = subprocess.run([str(controller), "--mode", preview["activeMode"]], capture_output=True, text=True)
    if activated.returncode:
        raise HelperError(f"Net Bridge activation failed: {(activated.stderr or activated.stdout).strip()[-500:]}")
    append_audit({"action": "net-bridge-install", "bridgeId": "net_bridge", "result": "ok"})
    return {**preview, "installed": True, "results": results}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=(
        "validate", "backup", "m17-plan", "m17-install",
        "p25-plan", "p25-install", "nxdn-plan", "nxdn-install",
        "ysf-plan", "ysf-install", "dmr-plan", "dmr-install",
        "zello-plan", "zello-install",
        "net-bridge-plan", "net-bridge-install",
    ))
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = read_request()
        if args.command == "validate":
            result = validate(payload)
        else:
            require_root()
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOCK_PATH.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                if args.command == "backup":
                    result = backup(payload)
                elif args.command == "m17-plan":
                    result = m17_command(payload, False)
                elif args.command == "m17-install":
                    result = m17_command(payload, True)
                elif args.command in ("net-bridge-plan", "net-bridge-install"):
                    result = net_bridge_command(payload, args.command == "net-bridge-install")
                elif args.command in ("dmr-plan", "dmr-install"):
                    result = dmr_command(payload, args.command == "dmr-install")
                elif args.command in ("zello-plan", "zello-install"):
                    result = zello_command(payload, args.command == "zello-install")
                else:
                    mode, operation = args.command.split("-", 1)
                    result = digital_command(payload, mode, operation == "install")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (HelperError, OSError, ValueError, Exception) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
