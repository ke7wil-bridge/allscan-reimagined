#!/usr/bin/env python3
"""Transactional installer for the ASR-managed Zello bridge."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME_SOURCE = Path(os.environ.get(
    "ASR_ZELLO_RUNTIME_SOURCE", "/usr/local/share/allscan-reimagined/runtime/zello"))
STATE = "/var/lib/allscan-reimagined/bridge-setup/installations"
SERVICE = "allscan-reimagined-zello.service"
UNIT = f"/etc/systemd/system/{SERVICE}"

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

planner = load("zello_installer_plan", "asr-bridge-setup-zello.py")

def rooted(root: Path, logical: str) -> Path:
    if not logical.startswith("/") or ".." in Path(logical).parts:
        raise InstallError("unsafe managed path")
    path = root / logical.lstrip("/")
    current = root.resolve()
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise InstallError(f"symbolic link in managed path: {logical}")
    return path

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

def clean_secret(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value or "\r" in value or "\n" in value:
        raise InstallError(f"invalid {label}")
    return value

def validate_private_key(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 16384 or "\0" in value:
        raise InstallError("invalid Zello private key")
    value = value.strip().replace("\r\n", "\n")
    if not re.fullmatch(r"-----BEGIN (?:RSA )?PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+\n-----END (?:RSA )?PRIVATE KEY-----", value):
        raise InstallError("Zello private key is not PEM formatted")
    return value + "\n"

def env_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'

def compose_yaml(plan: dict[str, Any]) -> str:
    root = plan["resources"]["root"]
    stock = plan["resources"]["stockWeb"]
    return f"""services:
  zello:
    image: allscan-reimagined/zello:managed
    container_name: asr-zello-bridge
    network_mode: host
    restart: unless-stopped
    environment:
      ASR_ZELLO_SECRETS: "/run/secrets/zello.json"
      USRP_BIND: "127.0.0.1"
      USRP_HOST: "127.0.0.1"
      USRP_RXPORT: "{plan['ports']['usrp_rx']}"
      USRP_TXPORT: "{plan['ports']['usrp_tx']}"
      ZELLO_PRIVATE_KEY: "/run/secrets/zello.key"
      ASR_ZELLO_HEALTH: "/run/zello/status.json"
    volumes:
      - {root}/secrets/zello.json:/run/secrets/zello.json:ro
      - {root}/secrets/zello.key:/run/secrets/zello.key:ro
      - {root}/runtime:/run/zello
      - {stock}:/var/www/html/allscan
      - /run/urf-wil-config:/run/asr-global:ro
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          python3 -c 'import json,time; d=json.load(open("/run/zello/status.json")); assert d["state"] == "online" and time.time()-int(d.get("epoch",0)) < 120'
      interval: 10s
      timeout: 3s
      retries: 9
      start_period: 10s
"""

def unit_text(plan: dict[str, Any]) -> str:
    root = plan["resources"]["root"]
    compose = plan["resources"]["compose"]
    return f"""[Unit]
Description=ASR managed Zello bridge
After=docker.service network-online.target asterisk.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p {root}/runtime
ExecStartPre=/bin/sh -c 'if [ ! -e /run/urf-wil-config ]; then mkdir -p /var/lib/allscan-reimagined/global-ban; touch /var/lib/allscan-reimagined/global-ban/urfd.blacklist; ln -s /var/lib/allscan-reimagined/global-ban /run/urf-wil-config; fi'
ExecStart=/usr/bin/docker compose -f {compose} up -d
ExecStop=/usr/bin/docker compose -f {compose} down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
"""

def file_spec(root: Path, plan: dict[str, Any], secrets: dict[str, str]) -> dict[str, tuple[bytes, int]]:
    outputs = planner.integration_files(root, plan)
    settings = plan["settings"]
    password = clean_secret(secrets.get("password", ""), "Zello password", 256)
    private_key = validate_private_key(secrets.get("privateKey", ""))
    credentials = {
        "username": settings["username"], "password": password,
        "channel": settings["channel"], "issuer": settings["issuer"],
        "wsEndpoint": settings["ws_endpoint"],
    }
    outputs[plan["resources"]["credentials"]] = (
        (json.dumps(credentials, separators=(",", ":")) + "\n").encode(), 0o600)
    outputs[plan["resources"]["privateKey"]] = (private_key.encode(), 0o600)
    outputs[plan["resources"]["compose"]] = (compose_yaml(plan).encode(), 0o644)
    outputs[UNIT] = (unit_text(plan).encode(), 0o644)
    return outputs

def build_image() -> str:
    if not RUNTIME_SOURCE.is_dir():
        raise InstallError("Zello runtime source is not installed")
    check = subprocess.run(["docker", "image", "inspect", "allscan-reimagined/zello:managed",
                            "--format", "{{.Id}}"], capture_output=True, text=True)
    prior = check.stdout.strip() if check.returncode == 0 else ""
    result = subprocess.run(["docker", "build", "-t", "allscan-reimagined/zello:managed",
                             str(RUNTIME_SOURCE)], capture_output=True, text=True)
    if result.returncode:
        raise InstallError(f"Zello runtime build failed: {result.stdout[-300:]} {result.stderr[-500:]}")
    return prior

def restore_image(prior: str) -> None:
    if prior:
        subprocess.run(["docker", "image", "tag", prior, "allscan-reimagined/zello:managed"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["docker", "image", "rm", "-f", "allscan-reimagined/zello:managed"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def verify_runtime(plan: dict[str, Any]) -> None:
    running = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "asr-zello-bridge"],
                             capture_output=True, text=True)
    if running.returncode or running.stdout.strip() != "true":
        raise InstallError("Zello container did not start")
    last = ""
    for _ in range(40):
        health = subprocess.run(["docker", "inspect", "-f",
                                 "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                                 "asr-zello-bridge"], capture_output=True, text=True)
        last = health.stdout.strip()
        if health.returncode == 0 and last == "healthy":
            break
        time.sleep(1)
    else:
        raise InstallError(f"Zello authentication/channel health did not pass: {last or 'unknown'}")
    node = plan["bridgeNode"]
    for _ in range(20):
        result = subprocess.run(["asterisk", "-rx", f"rpt stats {node}"],
                                capture_output=True, text=True)
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            return
        time.sleep(0.5)
    raise InstallError(f"Asterisk did not register Zello transport node {node}")

def install(root: Path, settings, secrets: dict[str, str], *, expected_digest: str,
            manage_services: bool = False, fail_after: int | None = None) -> dict[str, Any]:
    if manage_services and root != Path("/"):
        raise InstallError("service management is only valid for the real host")
    plan = planner.plan(root, settings)
    if plan["digest"] != expected_digest:
        raise InstallError("preview is stale; preview again")
    outputs = file_spec(root, plan, secrets)
    identity = settings.bridge_id
    record = f"{STATE}/{identity}.json"
    existing = rooted(root, record)
    old_record = json.loads(existing.read_text()) if existing.is_file() else None
    if old_record and old_record.get("bridgeType") != "zello":
        raise InstallError("existing installation belongs to another bridge type")
    allowed = set(old_record.get("ownedFiles", [])) if old_record else set()
    for logical in (*outputs, record):
        target = rooted(root, logical)
        if target.exists() and logical not in (planner.CONFIG, planner.RPT, planner.MODULES, record) and logical not in allowed:
            raise InstallError(f"existing file is not owned by this bridge: {logical}")
        if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
            raise InstallError(f"managed path is not a regular single-link file: {logical}")
    before = {logical: (rooted(root, logical).read_bytes(), stat.S_IMODE(rooted(root, logical).stat().st_mode))
              if rooted(root, logical).is_file() else None for logical in (*outputs, record)}
    owners = {logical: (rooted(root, logical).stat().st_uid, rooted(root, logical).stat().st_gid)
              if rooted(root, logical).is_file() else None for logical in (*outputs, record)}
    backup = rooted(root, f"/var/lib/allscan-reimagined/bridge-setup/backups/{time.time_ns()}-{identity}")
    backup.mkdir(parents=True, mode=0o700)
    atomic(backup / "manifest.json", (json.dumps({
        k: {"existed": v is not None, "sha256": hashlib.sha256(v[0]).hexdigest() if v else None}
        for k, v in before.items()}, indent=2, sort_keys=True) + "\n").encode(), 0o600)
    prior_image = ""
    service_was_enabled = False
    service_was_active = False
    asterisk_changed = False
    changed = 0
    try:
        for logical, (data, mode) in outputs.items():
            if before[logical] == (data, mode):
                continue
            atomic(rooted(root, logical), data, mode)
            changed += 1
            asterisk_changed |= logical in (planner.RPT, planner.MODULES)
            if fail_after is not None and changed >= fail_after:
                raise InstallError("injected apply failure")
        if manage_services:
            service_was_enabled = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", SERVICE]).returncode == 0
            service_was_active = subprocess.run(
                ["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
            prior_image = build_image()
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            if asterisk_changed:
                subprocess.run(["systemctl", "restart", "asterisk.service"], check=True,
                               capture_output=True, text=True)
            subprocess.run(["systemctl", "enable", "--now", SERVICE], check=True,
                           capture_output=True, text=True)
            verify_runtime(plan)
        owned = sorted(path for path in outputs if path not in (planner.CONFIG, planner.RPT, planner.MODULES))
        record_data = {
            "schema": 1, "bridgeType": "zello", "bridgeId": identity,
            "bridgeNode": plan["bridgeNode"], "ports": plan["ports"], "service": SERVICE,
            "provisioningVerified": manage_services, "liveAudioVerified": False,
            "committedAt": old_record.get("committedAt", int(time.time())) if old_record else int(time.time()),
            "ownedFiles": owned,
        }
        atomic(existing, (json.dumps(record_data, indent=2, sort_keys=True) + "\n").encode(), 0o600)
        return {"ok": True, "digest": plan["digest"], "bridgeId": identity,
                "bridgeNode": plan["bridgeNode"], "service": SERVICE,
                "changedFiles": changed, "backup": str(backup),
                "provisioningVerified": manage_services, "liveAudioVerified": False}
    except Exception:
        rollback_error = None
        if manage_services:
            subprocess.run(["systemctl", "disable", "--now", SERVICE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for logical, original in reversed(list(before.items())):
                target = rooted(root, logical)
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic(target, original[0], original[1])
                    if owners[logical] is not None:
                        os.chown(target, *owners[logical])
            for logical, original in before.items():
                target = rooted(root, logical)
                if original is None and target.exists():
                    raise InstallError(f"rollback verification failed: {logical}")
                if original is not None and (not target.is_file() or
                    target.read_bytes() != original[0] or
                    stat.S_IMODE(target.stat().st_mode) != original[1]):
                    raise InstallError(f"rollback verification failed: {logical}")
        except Exception as exc:
            rollback_error = exc
        if manage_services:
            subprocess.run(["systemctl", "daemon-reload"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if asterisk_changed:
                subprocess.run(["systemctl", "restart", "asterisk.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                restore_image(prior_image)
                if service_was_enabled:
                    subprocess.run(["systemctl", "enable", SERVICE], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if service_was_active:
                    subprocess.run(["systemctl", "start", SERVICE], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                rollback_error = rollback_error or exc
        if rollback_error:
            raise rollback_error
        raise
