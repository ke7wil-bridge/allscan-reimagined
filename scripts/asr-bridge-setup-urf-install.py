#!/usr/bin/env python3
"""Transactional installer for ASR-managed URF + TGIF/DMR."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME_SOURCE = Path(os.environ.get(
    "ASR_URF_RUNTIME_SOURCE",
    "/usr/local/share/allscan-reimagined/runtime/urf",
))
STATE = "/var/lib/allscan-reimagined/bridge-setup/installations"
CONTAINER_DOCKER_HOME = "/var/cache/allscan-reimagined/docker-home"
CONTAINER_DOCKER_CONFIG = "/var/cache/allscan-reimagined/docker-config"
CONTAINER_BUILDX_CONFIG = "/var/cache/allscan-reimagined/buildx"
def service_name(bridge_id: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise InstallError("invalid bridge ID")
    return f"allscan-reimagined-urf-tgif-{bridge_id}.service"

def unit_path(bridge_id: str) -> str:
    return f"/etc/systemd/system/{service_name(bridge_id)}"

class InstallError(RuntimeError):
    pass


def docker_environment() -> dict[str, str]:
    """Pin Docker/buildx state for the container-host privileged boundary."""
    environment = os.environ.copy()
    if environment.get("ASR_CONTAINER_PROVISIONING_PROFILE"):
        environment.update({
            "HOME": CONTAINER_DOCKER_HOME,
            "DOCKER_CONFIG": CONTAINER_DOCKER_CONFIG,
            "BUILDX_CONFIG": CONTAINER_BUILDX_CONFIG,
        })
    return environment


def docker_run(argv: list[str], **kwargs):
    """Run only a fixed Docker argv with the backend-specific state boundary."""
    if not argv or argv[0] != "docker" or not all(isinstance(value, str) for value in argv):
        raise InstallError("invalid Docker command")
    return subprocess.run(argv, env=docker_environment(), **kwargs)

def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise InstallError(f"missing {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

planner = load("urf_installer_plan", "asr-bridge-setup-urf.py")
sources = load("urf_installer_sources", "asr-bridge-runtime-sources.py")
def rooted(root: Path, logical: str) -> Path:
    if not logical.startswith("/") or ".." in Path(logical).parts:
        raise InstallError("unsafe managed path")
    backend = load("asr_backend_urf_install", "asr-provisioning-backend.py")
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

def runtime_names(plan: dict[str, Any]) -> tuple[str, str, str]:
    bridge_id = plan["settings"]["bridge_id"]
    return (f"asr-urf-reflector-{bridge_id}", f"asr-urf-transcoder-{bridge_id}", f"asr-urf-tgif-{bridge_id}")

def compose_yaml(plan: dict[str, Any]) -> str:
    # A sandbox using the host Docker socket needs paths from the daemon's host.
    # Keep the override on disk so the web helper and CLI see the same mapping.
    host_config = Path("/etc/allscan-reimagined/bridge-setup-docker-host.json")
    docker_host = json.loads(host_config.read_text()) if host_config.is_file() else {}
    host_base = os.environ.get("ASR_URF_DOCKER_HOST_BASE", "")
    root = (str(Path(host_base) / "urf" / plan["settings"]["bridge_id"])
            if host_base else os.environ.get("ASR_URF_DOCKER_HOST_ROOT", docker_host.get("root", plan["resources"]["root"])))
    network_mode = os.environ.get("ASR_URF_DOCKER_NETWORK_MODE", docker_host.get("networkMode", "host"))
    if not isinstance(root, str) or not root.startswith("/") or ".." in Path(root).parts:
        raise InstallError("invalid Docker host path for URF runtime")
    if (not isinstance(network_mode, str)
            or not re.fullmatch(r"(?:host|container:[A-Za-z0-9][A-Za-z0-9_.-]{0,127})", network_mode)):
        raise InstallError("invalid URF Docker network mode")
    reflector_name, transcoder_name, tgif_name = runtime_names(plan)
    network_dmr_id = planner.tgif_network_id(
        plan["settings"]["dmr_id"], plan["bridgeNode"])
    return f"""services:
  urfd:
    image: allscan-reimagined/urf:managed
    container_name: {reflector_name}
    network_mode: {network_mode}
    restart: unless-stopped
    volumes:
      - {root}/config:/config:ro
      - {root}/data:/data
    command: ["exec /usr/local/bin/urfd /config/urfd.ini"]
  tcd:
    image: allscan-reimagined/urf-tcd:managed
    container_name: {transcoder_name}
    network_mode: {network_mode}
    restart: unless-stopped
    volumes:
      - {root}/config:/config:ro
    command: ["/config/tcd.ini"]
  tgif:
    image: allscan-reimagined/urf-tgif:managed
    container_name: {tgif_name}
    network_mode: {network_mode}
    restart: unless-stopped
    environment:
      DMR_ID: "{plan['settings']['dmr_id']}"
      DMR_NETWORK_ID: "{network_dmr_id}"
      CALLSIGN: "{plan['settings']['callsign']}"
      TGIF_HOST: "tgif.network"
      TGIF_PORT: "62031"
      TGIF_TG: "{plan['settings']['tgif_tg']}"
      TGIF_TARGET_FILE: "/run/tgif-dmr/net-target"
      LOCAL_PORT: "{plan['ports']['tgif_local']}"
      MMDVM_PORT: "{plan['ports']['tgif_mmdvm']}"
      URF_HOST: "127.0.0.1"
      URF_PORT: "{plan['ports']['dmr']}"
      URF_LOCAL_PORT: "{plan['ports']['urf_local']}"
      URF_TG: "4001"
      DVSWITCH_TX: "{plan['ports']['dvswitch_tx']}"
      DVSWITCH_RX: "{plan['ports']['dvswitch_rx']}"
      HEALTH_DIR: "/run/tgif-dmr"
      DMR_ROSTER_FILE: "/run/tgif-dmr/dmr-clients.json"
    volumes:
      - {root}/secrets/tgif-password:/run/secrets/tgif-password:ro
      - {root}/tgif-run:/run/tgif-dmr
    healthcheck:
      test: ["CMD-SHELL", "test -s /run/tgif-dmr/adapter.pid && test -s /run/tgif-dmr/mmdvm.pid && kill -0 $$(cat /run/tgif-dmr/adapter.pid) && kill -0 $$(cat /run/tgif-dmr/mmdvm.pid) && find /run/tgif-dmr -name 'urf-health' -mmin -1 | grep -q . && find /run/tgif-dmr -name 'tgif-health' -mmin -1 | grep -q ."]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 10s
"""
def unit_text(plan: dict[str, Any]) -> str:
    backend = load("asr_backend_urf_unit", "asr-provisioning-backend.py")
    compose = backend.map_path(Path("/"), plan["resources"]["compose"])
    root = backend.map_path(Path("/"), plan["resources"]["root"])
    return f"""[Unit]
Description=ASR managed URF + TGIF bridge
After=docker.service network-online.target asterisk.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p {root}/data {root}/tgif-run
ExecStart=/usr/bin/docker compose -f {compose} up -d
ExecStop=/usr/bin/docker compose -f {compose} stop
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
"""

def file_spec(root: Path, plan: dict[str, Any], password: str) -> dict[str, tuple[bytes, int]]:
    outputs = planner.integration_files(root, plan)
    outputs[plan["resources"]["compose"]] = (compose_yaml(plan).encode(), 0o644)
    outputs[plan["resources"]["tgifSecret"]] = ((password + "\n").encode(), 0o600)
    outputs[unit_path(plan["settings"]["bridge_id"])] = (unit_text(plan).encode(), 0o644)
    return outputs

def validate_password(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise InstallError("TGIF hotspot password is required")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise InstallError("TGIF hotspot password contains control characters")
    return value


def image_id(tag: str) -> str:
    result = docker_run(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        capture_output=True, text=True,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    if value and not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise InstallError(f"Docker returned an invalid image ID for {tag}")
    return value


def tag_image(source: str, target: str, purpose: str) -> None:
    result = docker_run(
        ["docker", "image", "tag", source, target],
        capture_output=True, text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise InstallError(f"{purpose} failed ({source} -> {target}): {detail or 'Docker returned no error text'}")


def remove_image_tag(tag: str) -> str:
    result = docker_run(
        ["docker", "image", "rm", tag], capture_output=True, text=True,
    )
    if result.returncode and "No such image" not in result.stderr:
        return (result.stderr or result.stdout).strip()[-800:]
    return ""


def cleanup_image_transaction(transaction: dict[str, dict[str, str]]) -> list[str]:
    errors = []
    for state in transaction.values():
        for key in ("candidate", "backup"):
            if state.get(key):
                detail = remove_image_tag(state[key])
                if detail:
                    errors.append(f"could not remove {state[key]}: {detail}")
    return errors


def restore_images(transaction: dict[str, dict[str, str]]) -> None:
    errors = []
    for tag, state in transaction.items():
        try:
            if state.get("priorId"):
                tag_image(state["backup"], tag, "runtime image rollback")
                if image_id(tag) != state["priorId"]:
                    raise InstallError(f"runtime image rollback verification failed for {tag}")
            else:
                detail = remove_image_tag(tag)
                if detail:
                    raise InstallError(f"runtime image rollback failed for {tag}: {detail}")
        except InstallError as exc:
            errors.append(str(exc))
    errors.extend(cleanup_image_transaction(transaction))
    if errors:
        raise InstallError("; ".join(errors))


def build_images() -> dict[str, dict[str, str]]:
    if not RUNTIME_SOURCE.is_dir():
        raise InstallError("URF runtime source is not installed")
    artifacts = sources.binary_artifacts()
    mmdvm = artifacts["MMDVM_Bridge"]
    images = {
        "allscan-reimagined/urf:managed": ["docker", "build"],
        "allscan-reimagined/urf-tcd:managed": [
            "docker", "build", "-f", str(RUNTIME_SOURCE / "Dockerfile.tcd-arm-qemu")],
        "allscan-reimagined/urf-tgif:managed": [
            "docker", "build", "-f", str(RUNTIME_SOURCE / "Dockerfile.tgif"),
            "--build-arg", f"MMDVM_URL={mmdvm['url']}",
            "--build-arg", f"MMDVM_SHA256={mmdvm['sha256']}",
        ],
    }
    transaction_id = f"{os.getpid()}-{time.time_ns():x}"
    transaction: dict[str, dict[str, str]] = {}
    try:
        for tag, command in images.items():
            repository = tag.rsplit(":", 1)[0]
            state = {
                "priorId": image_id(tag),
                "backup": f"{repository}:asr-previous-{transaction_id}",
                "candidate": f"{repository}:asr-build-{transaction_id}",
                "candidateId": "",
            }
            transaction[tag] = state
            if state["priorId"]:
                # A retained tag is required with Docker's containerd image
                # store, which may collect an untagged manifest immediately.
                tag_image(tag, state["backup"], "runtime image backup")
                if image_id(state["backup"]) != state["priorId"]:
                    raise InstallError(f"runtime image backup verification failed for {tag}")
            build = [*command, "--load", "-t", state["candidate"], str(RUNTIME_SOURCE)]
            result = docker_run(build, capture_output=True, text=True)
            if result.returncode:
                raise InstallError(
                    f"runtime image build failed for {tag}: "
                    f"{result.stdout[-300:]} {result.stderr[-800:]}"
                )
            state["candidateId"] = image_id(state["candidate"])
            if not state["candidateId"]:
                raise InstallError(f"runtime image build did not load {tag} into the local Docker daemon")
        for tag, state in transaction.items():
            tag_image(state["candidate"], tag, "runtime image promotion")
            if image_id(tag) != state["candidateId"]:
                raise InstallError(f"runtime image promotion verification failed for {tag}")
        return transaction
    except Exception as original:
        try:
            restore_images(transaction)
        except Exception as rollback:
            raise InstallError(f"image build failed ({original}); image rollback also failed ({rollback})") from original
        raise

def capture_runtime_diagnostics(plan: dict[str, Any]) -> None:
    """Preserve bounded private logs before the transactional rollback."""
    directory = Path("/var/lib/allscan-reimagined/bridge-setup/diagnostics")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = []
    for name in runtime_names(plan):
        state = docker_run(
            ["docker", "inspect", "-f", "{{json .State}}", name],
            capture_output=True, text=True,
        )
        logs = docker_run(
            ["docker", "logs", "--tail", "80", name],
            capture_output=True, text=True,
        )
        lines.extend((f"=== {name} state ===", state.stdout or state.stderr,
                      f"=== {name} logs ===", logs.stdout, logs.stderr))
    password = ""
    output = "\n".join(lines)
    if password:
        output = output.replace(password, "[REDACTED]")
    atomic(directory / "latest.txt", output.encode(), 0o600)


def verify_runtime(plan: dict[str, Any]) -> None:
    names = runtime_names(plan)
    for name in names:
        result = docker_run(["docker", "inspect", "-f", "{{.State.Running}}", name], capture_output=True, text=True)
        if result.returncode or result.stdout.strip() != "true":
            capture_runtime_diagnostics(plan)
            raise InstallError(f"{name} did not start")
    last_health = ""
    for _ in range(30):
        health = docker_run(
            ["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", runtime_names(plan)[2]],
            capture_output=True, text=True,
        )
        last_health = health.stdout.strip()
        if health.returncode == 0 and last_health == "healthy":
            break
        time.sleep(1)
    else:
        capture_runtime_diagnostics(plan)
        logs = docker_run(["docker", "logs", "--tail", "100", runtime_names(plan)[2]],
                          capture_output=True, text=True)
        if "TGIF authentication rejected by master" in logs.stdout + logs.stderr:
            raise InstallError("TGIF rejected the hotspot security key for this DMR ID")
        raise InstallError(f"TGIF/URF runtime health check did not pass: {last_health or 'unknown'}")
    node = plan["bridgeNode"]
    last_asterisk_error = ""
    for _ in range(20):
        result = subprocess.run(["asterisk", "-rx", f"rpt stats {node}"], capture_output=True, text=True)
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            return
        last_asterisk_error = (result.stderr or result.stdout).strip()
        time.sleep(0.5)
    capture_runtime_diagnostics(plan)
    detail = f": {last_asterisk_error[-500:]}" if last_asterisk_error else ""
    raise InstallError(f"Asterisk did not register URF transport node {node}{detail}")

def install(root: Path, settings, password: str, *, expected_digest: str,
            manage_services: bool = False, fail_after: int | None = None) -> dict[str, Any]:
    if manage_services and root != Path("/"):
        raise InstallError("service management is only valid for the real host")
    password = validate_password(password)
    plan = planner.plan(root, settings)
    if plan["digest"] != expected_digest:
        raise InstallError("preview is stale; preview again")
    outputs = file_spec(root, plan, password)
    identity = settings.bridge_id
    service = service_name(identity)
    record = f"{STATE}/{identity}.json"
    existing = rooted(root, record)
    old_record = json.loads(existing.read_text()) if existing.is_file() else None
    if old_record and old_record.get("bridgeType") != "dmr":
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
    atomic(backup / "manifest.json", (json.dumps({k: {"existed": v is not None, "sha256": hashlib.sha256(v[0]).hexdigest() if v else None} for k,v in before.items()}, indent=2, sort_keys=True) + "\n").encode(), 0o600)
    prior_images: dict[str, dict[str, str]] = {}
    service_was_enabled = False
    service_was_active = False
    changed = 0
    asterisk_changed = False
    try:
        for logical, (data, mode) in outputs.items():
            if before[logical] == (data, mode):
                continue
            atomic(rooted(root, logical), data, mode)
            changed += 1
            asterisk_changed |= logical in (planner.RPT, planner.MODULES)
            if fail_after is not None and changed >= fail_after:
                raise InstallError("injected apply failure")
        net_inactive = (str(plan["settings"].get("bridge_role") or "standard") == "net"
                        and json.loads(rooted(root, planner.CONFIG).read_text()).get("netBridgeMode") != "dmr")
        if manage_services:
            service_was_enabled = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", service], check=False
            ).returncode == 0
            service_was_active = subprocess.run(
                ["systemctl", "is-active", "--quiet", service], check=False
            ).returncode == 0
            prior_images = build_images()
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            if net_inactive:
                subprocess.run(["systemctl", "disable", "--now", service], check=True,
                               capture_output=True, text=True)
            else:
                if asterisk_changed:
                    subprocess.run(["systemctl", "restart", "asterisk.service"], check=True, capture_output=True, text=True)
                subprocess.run(["systemctl", "enable", service], check=True, capture_output=True, text=True)
                subprocess.run(["systemctl", "restart" if service_was_active else "start", service],
                               check=True, capture_output=True, text=True)
                verify_runtime(plan)
        owned = sorted(path for path in outputs if path not in (planner.CONFIG, planner.RPT, planner.MODULES))
        record_data = {"schema": 1, "bridgeType": "dmr", "runtime": "urf-tgif", "bridgeId": identity,
                       "bridgeNode": plan["bridgeNode"], "ports": plan["ports"], "service": service,
                       "provisioningVerified": manage_services and not net_inactive, "liveAudioVerified": False,
                       "committedAt": old_record.get("committedAt", int(time.time())) if old_record else int(time.time()),
                       "ownedFiles": owned}
        atomic(existing, (json.dumps(record_data, indent=2, sort_keys=True) + "\n").encode(), 0o600)
        cleanup_image_transaction(prior_images)
        return {"ok": True, "digest": plan["digest"], "bridgeId": identity, "bridgeNode": plan["bridgeNode"],
                "service": service, "changedFiles": changed, "backup": str(backup),
                "provisioningVerified": manage_services and not net_inactive, "liveAudioVerified": False}
    except Exception as original:
        rollback_errors = []
        if manage_services:
            subprocess.run(["systemctl", "disable", "--now", service],
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
                if original is not None and (not target.is_file() or target.read_bytes() != original[0] or stat.S_IMODE(target.stat().st_mode) != original[1]):
                    raise InstallError(f"rollback verification failed: {logical}")
        except Exception as exc:
            rollback_errors.append(str(exc))
        if manage_services:
            subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if asterisk_changed:
                subprocess.run(["systemctl", "restart", "asterisk.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                restore_images(prior_images)
                if service_was_enabled:
                    subprocess.run(["systemctl", "enable", service], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if service_was_active:
                    subprocess.run(["systemctl", "start", service], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                rollback_errors.append(str(exc))
        if rollback_errors:
            raise InstallError(
                f"installation failed ({original}); rollback also failed ({'; '.join(rollback_errors)})"
            ) from original
        raise
