#!/usr/bin/env python3
"""Transactional installer for ASR-managed URF + TGIF/DMR."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import grp
import pwd
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
NATIVE_RUNTIME_ROOT = Path("/opt/allscan-reimagined-bridges/.runtime/urf-native")
NATIVE_STARTUP_ATTEMPTS = 90
NATIVE_STARTUP_POLL_SECONDS = 1.0
def service_name(bridge_id: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise InstallError("invalid bridge ID")
    return f"allscan-reimagined-urf-tgif-{bridge_id}.service"

def unit_path(bridge_id: str) -> str:
    return f"/etc/systemd/system/{service_name(bridge_id)}"

def native_service_names(bridge_id: str) -> tuple[str, str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise InstallError("invalid bridge ID")
    return (f"allscan-reimagined-urfd-{bridge_id}.service",
            f"allscan-reimagined-tcd-{bridge_id}.service",
            f"allscan-reimagined-tgif-{bridge_id}.service")

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
native_sources = load("urf_installer_native_sources", "asr-bridge-urf-native-runtime.py")
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
    network_dmr_id = plan["dmrNetworkId"]
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
def container_unit_text(plan: dict[str, Any]) -> str:
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

def native_units(plan: dict[str, Any]) -> dict[str, tuple[bytes, int]]:
    identity = plan["settings"]["bridge_id"]
    wrapper = service_name(identity)
    urfd, tcd, tgif = native_service_names(identity)
    runtime = plan["resources"]["root"]
    shared = str(NATIVE_RUNTIME_ROOT / "current")
    data = plan["resources"]["data"]
    run = plan["resources"]["tgifRun"]
    secret = plan["resources"]["tgifSecret"]
    settings, ports = plan["settings"], plan["ports"]
    common = """Restart=on-failure
RestartSec=2
User=asr-bridge
Group=asr-bridge
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictNamespaces=true
CapabilityBoundingSet=
AmbientCapabilities=
"""
    tcd_command = f"{shared}/bin/tcd-armhf {plan['resources']['tcdConfig']}"
    if platform.machine().lower() != "armv7l":
        tcd_command = (f"{shared}/bin/qemu-arm-static -L /usr/arm-linux-gnueabihf "
                       f"{shared}/bin/tcd-armhf {plan['resources']['tcdConfig']}")
    wrapper_text = f"""[Unit]
Description=ASR managed native URF + TGIF bridge ({identity})
After=network-online.target asterisk.service
Wants=network-online.target
Wants={urfd} {tcd} {tgif}

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true

[Install]
WantedBy=multi-user.target
"""
    def component(description: str, after: str, command: str, writable: str) -> str:
        return f"""[Unit]
Description={description}
After={after}
PartOf={wrapper}

[Service]
Type=simple
WorkingDirectory={runtime}
ExecStart={command}
{common}ReadWritePaths={writable}

[Install]
WantedBy=multi-user.target
"""
    environment = {
        "DMR_ID": settings["dmr_id"],
        "DMR_NETWORK_ID": plan["dmrNetworkId"],
        "CALLSIGN": settings["callsign"], "TGIF_HOST": "tgif.network", "TGIF_PORT": 62031,
        "TGIF_TG": settings["tgif_tg"], "TGIF_TARGET_FILE": f"{run}/net-target",
        "LOCAL_PORT": ports["tgif_local"], "MMDVM_PORT": ports["tgif_mmdvm"],
        "URF_HOST": "127.0.0.1", "URF_PORT": ports["dmr"],
        "URF_LOCAL_PORT": ports["urf_local"], "URF_TG": 4001,
        "DVSWITCH_TX": ports["dvswitch_tx"], "DVSWITCH_RX": ports["dvswitch_rx"],
        "HEALTH_DIR": run, "DMR_ROSTER_FILE": f"{run}/dmr-clients.json",
        "TGIF_PASSWORD_FILE": secret, "TGIF_ASSET_DIR": f"{shared}/tgif",
        "MMDVM_BINARY": f"{shared}/bin/MMDVM_Bridge",
    }
    env_lines = "".join(f"Environment={key}={value}\n" for key, value in environment.items())
    tgif_text = component(
        f"ASR TGIF adapter and MMDVM bridge ({identity})", f"network-online.target {urfd}",
        f"{shared}/tgif/run.sh", run,
    ).replace("[Service]\n", "[Service]\nSuccessExitStatus=143\n", 1).replace(
        "ExecStart=", env_lines + "ExecStart=", 1
    )
    return {
        unit_path(identity): (wrapper_text.encode(), 0o644),
        f"/etc/systemd/system/{urfd}": (component(
            f"ASR native URFD reflector ({identity})", "network-online.target asterisk.service",
            f"{shared}/bin/urfd {plan['resources']['urfConfig']}", data).replace(
                "[Service]\n", "[Service]\nRuntimeDirectory=urf-wil\nRuntimeDirectoryMode=0755\n", 1
            ).encode(), 0o644),
        f"/etc/systemd/system/{tcd}": (component(
            f"ASR native TCD transcoder ({identity})", f"network.target {urfd}",
            tcd_command, data).encode(), 0o644),
        f"/etc/systemd/system/{tgif}": (tgif_text.encode(), 0o644),
    }

def file_spec(root: Path, plan: dict[str, Any], password: str) -> dict[str, tuple[bytes, int]]:
    outputs = planner.integration_files(root, plan)
    if plan["backend"] == "container":
        outputs[plan["resources"]["compose"]] = (compose_yaml(plan).encode(), 0o644)
        outputs[unit_path(plan["settings"]["bridge_id"])] = (container_unit_text(plan).encode(), 0o644)
        secret_mode = 0o600
    else:
        outputs.update(native_units(plan))
        secret_mode = 0o640
    outputs[plan["resources"]["tgifSecret"]] = ((password + "\n").encode(), secret_mode)
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


def prepare_native_runtime() -> dict[str, Any]:
    """Build and atomically promote the shared, provenance-verified runtime."""
    if not RUNTIME_SOURCE.is_dir():
        raise InstallError("URF runtime source is not installed")
    current = NATIVE_RUNTIME_ROOT / "current"
    if NATIVE_RUNTIME_ROOT.is_symlink() or current.is_symlink():
        raise InstallError("native runtime staging path is unsafe")
    try:
        native_sources.verify(current, RUNTIME_SOURCE, sources)
        return {"changed": False}
    except native_sources.SourceError:
        pass
    NATIVE_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    transaction_id = f"{os.getpid()}-{time.time_ns():x}"
    candidate = NATIVE_RUNTIME_ROOT / f"candidate-{transaction_id}"
    backup = NATIVE_RUNTIME_ROOT / f"previous-{transaction_id}"
    transaction: dict[str, Any] = {
        "changed": True, "current": current, "candidate": candidate,
        "backup": backup, "hadPrior": current.exists(),
    }
    try:
        native_sources.install_packages()
        native_sources.build(candidate, RUNTIME_SOURCE, sources)
        native_sources.verify(candidate, RUNTIME_SOURCE, sources)
        if current.exists():
            os.replace(current, backup)
        os.replace(candidate, current)
        native_sources.verify(current, RUNTIME_SOURCE, sources)
        return transaction
    except Exception as original:
        try:
            if candidate.exists():
                shutil.rmtree(candidate)
            if backup.exists() and not current.exists():
                os.replace(backup, current)
        except Exception as rollback:
            raise InstallError(
                f"native runtime build failed ({original}); staging rollback failed ({rollback})"
            ) from original
        raise


def finish_native_runtime(transaction: dict[str, Any]) -> None:
    if transaction.get("changed") and Path(transaction["backup"]).exists():
        shutil.rmtree(transaction["backup"])


def restore_native_runtime(transaction: dict[str, Any]) -> None:
    if not transaction.get("changed"):
        return
    current, backup = Path(transaction["current"]), Path(transaction["backup"])
    failed = NATIVE_RUNTIME_ROOT / f"failed-{time.time_ns():x}"
    if current.exists():
        os.replace(current, failed)
    if transaction.get("hadPrior"):
        if not backup.exists():
            raise InstallError("native runtime rollback copy is missing")
        os.replace(backup, current)
        native_sources.verify(current, RUNTIME_SOURCE, sources)
    if failed.exists():
        shutil.rmtree(failed)


def ensure_native_runtime_paths(plan: dict[str, Any]) -> None:
    try:
        account = pwd.getpwnam("asr-bridge")
        web_group = grp.getgrnam("www-data")
    except KeyError as exc:
        raise InstallError("native bridge service or web account is unavailable") from exc
    for logical in (plan["resources"]["data"], plan["resources"]["tgifRun"]):
        path = rooted(Path("/"), logical)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise InstallError(f"native runtime writable path is unsafe: {logical}")
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
        group = web_group.gr_gid if logical == plan["resources"]["data"] else account.pw_gid
        os.chown(path, account.pw_uid, group)
        os.chmod(path, 0o750)
    secret = rooted(Path("/"), plan["resources"]["tgifSecret"])
    os.chown(secret, 0, account.pw_gid)
    os.chmod(secret, 0o640)

def capture_runtime_diagnostics(plan: dict[str, Any]) -> None:
    """Preserve bounded private logs before the transactional rollback."""
    directory = Path("/var/lib/allscan-reimagined/bridge-setup/diagnostics")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = []
    if plan["backend"] == "container":
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
    else:
        for name in (service_name(plan["settings"]["bridge_id"]),
                     *native_service_names(plan["settings"]["bridge_id"])):
            state = subprocess.run(
                ["systemctl", "show", name, "--property=ActiveState,SubState,Result"],
                capture_output=True, text=True,
            )
            logs = subprocess.run(
                ["journalctl", "--no-pager", "-u", name, "-n", "80"],
                capture_output=True, text=True,
            )
            lines.extend((f"=== {name} state ===", state.stdout or state.stderr,
                          f"=== {name} logs ===", logs.stdout, logs.stderr))
    secret = rooted(Path("/"), plan["resources"]["tgifSecret"])
    password = secret.read_text(encoding="utf-8").rstrip("\n") if secret.is_file() else ""
    output = "\n".join(lines)
    if password:
        output = output.replace(password, "[REDACTED]")
    atomic(directory / "latest.txt", output.encode(), 0o600)


def native_identity_state(run: Path) -> str:
    try:
        payload = json.loads((run / "urf-identity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("state", "")) if isinstance(payload, dict) else ""


def poll_native_runtime(run: Path, *, attempts: int = NATIVE_STARTUP_ATTEMPTS,
                        interval: float = NATIVE_STARTUP_POLL_SECONDS) -> tuple[bool, str, str]:
    """Wait for positive runtime signals while treating early identity rejects as transient."""
    last_error = ""
    identity_state = ""
    for attempt in range(attempts):
        try:
            pids = [int((run / name).read_text().strip()) for name in ("adapter.pid", "mmdvm.pid")]
            for pid in pids:
                os.kill(pid, 0)
            now = time.time()
            identity_state = native_identity_state(run) or identity_state
            markers_fresh = all(now - (run / name).stat().st_mtime <= 60
                                for name in ("urf-health", "tgif-health"))
            if identity_state == "authenticated" and markers_fresh:
                return True, "", identity_state
            last_error = ("waiting for authenticated URFWIL DMR login"
                          if identity_state != "authenticated"
                          else "health markers are not fresh")
        except (OSError, ValueError) as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False, last_error, identity_state


def reload_and_reset_failed(units: tuple[str, ...]) -> None:
    """Reload units, then independently clear only this transaction's failures."""
    subprocess.run(["systemctl", "daemon-reload"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for unit in units:
        subprocess.run(["systemctl", "reset-failed", unit], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def native_runtime_failure(identity_state: str, diagnostics: str, last_error: str) -> InstallError:
    if "TGIF authentication rejected by master" in diagnostics:
        return InstallError("TGIF rejected the hotspot security key for this DMR ID")
    if identity_state == "rejected" or (
            identity_state != "authenticated"
            and "URFWIL DMR login rejected for DMR ID" in diagnostics):
        return InstallError("URFD rejected the configured DMR identity; verify the DMR ID and callsign")
    return InstallError(
        f"TGIF/URF native runtime health check did not pass: {last_error or 'unknown'}")


def verify_container_runtime(plan: dict[str, Any]) -> None:
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


def verify_native_runtime(plan: dict[str, Any]) -> None:
    identity = plan["settings"]["bridge_id"]
    units = (service_name(identity), *native_service_names(identity))
    for unit in units:
        result = subprocess.run(["systemctl", "is-active", "--quiet", unit])
        if result.returncode:
            capture_runtime_diagnostics(plan)
            raise InstallError(f"native runtime unit {unit} did not start")
    urfd, tcd, _tgif = native_service_names(identity)
    for unit in (urfd, tcd):
        result = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit],
                                capture_output=True, text=True)
        try:
            pid = int(result.stdout.strip())
            os.kill(pid, 0)
        except (ValueError, OSError):
            capture_runtime_diagnostics(plan)
            raise InstallError(f"native runtime process for {unit} is not alive")
    run = rooted(Path("/"), plan["resources"]["tgifRun"])
    ready, last_error, identity_state = poll_native_runtime(run)
    if not ready:
        capture_runtime_diagnostics(plan)
        diagnostic = Path("/var/lib/allscan-reimagined/bridge-setup/diagnostics/latest.txt")
        text = diagnostic.read_text(encoding="utf-8", errors="replace") if diagnostic.is_file() else ""
        raise native_runtime_failure(identity_state, text, last_error)
    verify_asterisk_node(plan)


def verify_asterisk_node(plan: dict[str, Any]) -> None:
    node = plan["bridgeNode"]
    last_error = ""
    for _ in range(20):
        result = subprocess.run(["asterisk", "-rx", f"rpt stats {node}"],
                                capture_output=True, text=True)
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            return
        last_error = (result.stderr or result.stdout).strip()
        time.sleep(0.5)
    capture_runtime_diagnostics(plan)
    raise InstallError(f"Asterisk did not register URF transport node {node}"
                       f"{': ' + last_error[-500:] if last_error else ''}")

def install(root: Path, settings, password: str, *, expected_digest: str,
            manage_services: bool = False, fail_after: int | None = None) -> dict[str, Any]:
    if manage_services and root != Path("/"):
        raise InstallError("service management is only valid for the real host")
    password = validate_password(password)
    plan = planner.plan(root, settings)
    if plan["digest"] != expected_digest:
        raise InstallError("preview is stale; preview again")
    outputs = file_spec(root, plan, password)
    runtime_root_was_present = rooted(root, plan["resources"]["root"]).exists()
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
    native_transaction: dict[str, Any] = {"changed": False}
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
        for logical, (data, mode) in outputs.items():
            target = rooted(root, logical)
            if (not target.is_file() or target.read_bytes() != data
                    or stat.S_IMODE(target.stat().st_mode) != mode):
                raise InstallError(f"managed file verification failed: {logical}")
        net_inactive = (str(plan["settings"].get("bridge_role") or "standard") == "net"
                        and json.loads(rooted(root, planner.CONFIG).read_text()).get("netBridgeMode") != "dmr")
        if manage_services:
            service_was_enabled = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", service], check=False
            ).returncode == 0
            service_was_active = subprocess.run(
                ["systemctl", "is-active", "--quiet", service], check=False
            ).returncode == 0
            if plan["backend"] == "container":
                prior_images = build_images()
            else:
                native_transaction = prepare_native_runtime()
                ensure_native_runtime_paths(plan)
                secret_info = rooted(root, plan["resources"]["tgifSecret"]).stat()
                account = pwd.getpwnam("asr-bridge")
                if (secret_info.st_uid != 0 or secret_info.st_gid != account.pw_gid
                        or stat.S_IMODE(secret_info.st_mode) != 0o640):
                    raise InstallError("TGIF credential ownership verification failed")
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
                if plan["backend"] == "container":
                    verify_container_runtime(plan)
                else:
                    verify_native_runtime(plan)
        owned = sorted(path for path in outputs if path not in (planner.CONFIG, planner.RPT, planner.MODULES))
        record_data = {"schema": 2, "bridgeType": "dmr", "runtime": "urf-tgif",
                       "backend": plan["backend"], "bridgeId": identity,
                       "bridgeNode": plan["bridgeNode"], "ports": plan["ports"], "service": service,
                       "services": plan["services"],
                       "provisioningVerified": manage_services and not net_inactive, "liveAudioVerified": False,
                       "committedAt": old_record.get("committedAt", int(time.time())) if old_record else int(time.time()),
                       "ownedFiles": owned}
        atomic(existing, (json.dumps(record_data, indent=2, sort_keys=True) + "\n").encode(), 0o600)
        cleanup_image_transaction(prior_images)
        finish_native_runtime(native_transaction)
        return {"ok": True, "digest": plan["digest"], "bridgeId": identity, "bridgeNode": plan["bridgeNode"],
                "service": service, "changedFiles": changed, "backup": str(backup),
                "provisioningVerified": manage_services and not net_inactive, "liveAudioVerified": False}
    except Exception as original:
        rollback_errors = []
        if manage_services:
            subprocess.run(["systemctl", "disable", "--now", service],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for logical, prior in reversed(list(before.items())):
                target = rooted(root, logical)
                if prior is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic(target, prior[0], prior[1])
                    if owners[logical] is not None:
                        os.chown(target, *owners[logical])
            for logical, prior in before.items():
                target = rooted(root, logical)
                if prior is None and target.exists():
                    raise InstallError(f"rollback verification failed: {logical}")
                if prior is not None and (not target.is_file() or target.read_bytes() != prior[0] or stat.S_IMODE(target.stat().st_mode) != prior[1]):
                    raise InstallError(f"rollback verification failed: {logical}")
        except Exception as exc:
            rollback_errors.append(str(exc))
        if manage_services:
            reset_units = (service,)
            if plan["backend"] == "native":
                reset_units += native_service_names(identity)
            reload_and_reset_failed(reset_units)
            if asterisk_changed:
                subprocess.run(["systemctl", "restart", "asterisk.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if plan["backend"] == "container":
                    restore_images(prior_images)
                else:
                    restore_native_runtime(native_transaction)
                if service_was_enabled:
                    subprocess.run(["systemctl", "enable", service], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if service_was_active:
                    subprocess.run(["systemctl", "start", service], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                rollback_errors.append(str(exc))
        if plan["backend"] == "native" and not runtime_root_was_present:
            try:
                runtime_root = rooted(root, plan["resources"]["root"])
                if runtime_root.is_dir() and not runtime_root.is_symlink():
                    shutil.rmtree(runtime_root)
            except Exception as exc:
                rollback_errors.append(str(exc))
        if rollback_errors:
            raise InstallError(
                f"installation failed ({original}); rollback also failed ({'; '.join(rollback_errors)})"
            ) from original
        raise
