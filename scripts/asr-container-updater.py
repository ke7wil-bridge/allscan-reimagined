#!/usr/bin/env python3
"""Verified, host-side Docker updater reached only through the ASR broker."""
from __future__ import annotations

import ast
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

HERE = Path(__file__).resolve().parent
PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")
STATE = Path("/var/lib/allscan-reimagined/container-updates")
BACKUPS = Path("/var/backups/allscan-reimagined-container")
WORK = Path("/var/cache/allscan-reimagined/container-updates")
DOCKER_HOME = Path("/var/cache/allscan-reimagined/docker-home")
DOCKER_CONFIG = Path("/var/cache/allscan-reimagined/docker-config")
DOCKER_BUILDX = Path("/var/cache/allscan-reimagined/buildx")
GATE = Path("/run/lock/allscan-reimagined-container-updater.lock")
JOB_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-(?:alpha|beta|rc)\.[0-9]+(?:\.[0-9]+)*")
ACTIVE = {"queued", "preflight", "downloading", "verifying", "staging", "building", "backup", "installing", "health", "restoring"}
TERMINAL = {"complete", "failed"}
PERSISTENT_DESTINATIONS = {
    "/var/www/html", "/etc/allscan", "/etc/allscan-reimagined", "/var/lib/allscan-reimagined",
}
EPHEMERAL_WRITABLE_DESTINATIONS = {"/run/asr-standalone-admin"}
# A change in the host worker/unit/tmpfiles contract requires an explicit,
# qualified migration, rather than silently copying an incompatible installer.
HOST_UPDATER_INFRASTRUCTURE_SCHEMA = 1
RECOVERY_UNIT = "allscan-reimagined-container-update-recovery.service"
RECOVERY_UNIT_TEXT = """[Unit]
Description=Recover an interrupted ASR container update
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
Environment=ASR_CONTAINER_PROVISIONING_PROFILE=/etc/allscan-reimagined/container-provisioning.json
ExecStart=/usr/local/libexec/allscan-reimagined/asr-container-updater.py recover-interrupted
User=root
Group=root
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=full
ReadWritePaths=/usr/local/libexec/allscan-reimagined /usr/local/sbin
BindReadOnlyPaths=/etc/allscan-reimagined/container-provisioning.json
TimeoutStartSec=15min

[Install]
WantedBy=multi-user.target
"""


HOST_DEST = Path("/usr/local/libexec/allscan-reimagined")
CONTROL_DEST = Path("/usr/local/sbin")
HOST_FILES = ('asr-provisioning-backend.py', 'asr-container-host-broker.py', 'asr-container-updater.py', 'asr-updater.py', 'asr-container-netns-exec.py', 'asr-container-systemctl.py', 'asr-container-asterisk.py', 'asr-container-bridge-recovery.py', 'asr-container-standard-link.py', 'asr-fixed-bridge-recovery.py', 'asr-node-diagnostics.py', 'asr-bridge-setup-core.py', 'asr-m17-hosts-update.py', 'asr-bridge-setup-m17.py', 'asr-bridge-setup-helper.py', 'asr-bridge-runtime-sources.py', 'asr-bridge-setup-digital.py', 'asr-bridge-setup-digital-render.py', 'asr-bridge-setup-digital-install.py', 'asr-bridge-endpoint-collisions.py', 'asr-bridge-lifecycle.py', 'asr-bridge-setup-dmr.py', 'asr-bridge-setup-dmr-render.py', 'asr-bridge-setup-dmr-install.py', 'asr-dmr-standalone-admin-adapter.py')
CONTROL_FILES = ('asr-managed-dmr-net-control.py', 'asr-ysf-bridge-control.py', 'asr-p25-bridge-control.py', 'asr-nxdn-bridge-control.py', 'asr-m17-bridge-control.py', 'asr-m17-usrp-connector.py', 'asr-net-bridge-mode-control.py')
SUPPORT_FILES = ('asr_bridge_status.py',)


class UpdateError(RuntimeError):
    pass


def native_updater():
    path = HERE / "asr-updater.py"
    spec = importlib.util.spec_from_file_location("asr_native_release_verifier", path)
    if not spec or not spec.loader:
        raise UpdateError("Release verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backend():
    path = HERE / "asr-provisioning-backend.py"
    spec = importlib.util.spec_from_file_location("asr_container_update_backend", path)
    if not spec or not spec.loader:
        raise UpdateError("Container profile support is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(argv: list[str], *, timeout: int = 120, env: dict[str, str] | None = None,
        check: bool = False) -> subprocess.CompletedProcess[str]:
    if env is None and argv and argv[0] == "/usr/bin/docker":
        env = docker_environment()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                            env=env, check=False)
    if check and result.returncode:
        raise UpdateError("A required Docker operation failed")
    return result


def docker_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(DOCKER_HOME),
        "DOCKER_CONFIG": str(DOCKER_CONFIG),
        "BUILDX_CONFIG": str(DOCKER_BUILDX),
    }


def inspect(name: str) -> dict[str, Any]:
    result = run(["/usr/bin/docker", "inspect", name])
    try:
        rows = json.loads(result.stdout)
    except ValueError as exc:
        raise UpdateError("Docker inspection failed") from exc
    if result.returncode or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise UpdateError("Docker inspection failed")
    return rows[0]


def profile():
    value = backend().load_profile(PROFILE)
    if value is None:
        raise UpdateError("Container update profile is unavailable")
    return value


def installed_version(web_container: str) -> str:
    result = run(["/usr/bin/docker", "exec", web_container, "php", "-r",
                  '$v=json_decode(file_get_contents("/opt/asr-source/package.json"),true)["version"]??"";echo $v;'])
    value = result.stdout.strip()
    if result.returncode or VERSION_RE.fullmatch(value) is None:
        raise UpdateError("Installed ASR version is unavailable")
    return value


def compose_metadata(container: dict[str, Any], *, approval: bool = False) -> dict[str, Any]:
    labels = container.get("Config", {}).get("Labels", {})
    if not isinstance(labels, dict):
        raise UpdateError("ASR container is not managed by Docker Compose")
    required = {
        "project": labels.get("com.docker.compose.project"),
        "service": labels.get("com.docker.compose.service"),
        "workingDir": labels.get("com.docker.compose.project.working_dir"),
        "configFiles": labels.get("com.docker.compose.project.config_files"),
    }
    if (not all(isinstance(value, str) and value for value in required.values())
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", required["project"]) is None
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", required["service"]) is None):
        raise UpdateError("ASR Compose identity is incomplete")
    working = Path(required["workingDir"])
    files = [Path(item) for item in required["configFiles"].split(",")]
    if (not working.is_absolute() or ".." in working.parts or len(working.parts) < 3
            or not files or any(not item.is_absolute() or ".." in item.parts for item in files)):
        raise UpdateError("ASR Compose configuration is unsafe")
    if approval:
        combined = "\n".join(item.read_text(encoding="utf-8") for item in files)
        if "ASR_IMAGE" not in combined or "ASR_UPDATE_MODE" not in combined:
            raise UpdateError("Compose must declare ASR_IMAGE and ASR_UPDATE_MODE before browser updates")
    image = container.get("Config", {}).get("Image")
    image_id = container.get("Image")
    if (not isinstance(image, str) or not image or not isinstance(image_id, str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None):
        raise UpdateError("Installed ASR image identity is unavailable")
    web_name = str(container.get("Name", "")).lstrip("/")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", web_name) is None:
        raise UpdateError("ASR container identity is invalid")
    return {**required, "workingDir": str(working), "configFiles": [str(item) for item in files],
            "webContainer": web_name,
            "image": image, "imageId": image_id}


def persistent_mounts(container: dict[str, Any]) -> list[dict[str, str]]:
    mounts: list[dict[str, str]] = []
    for item in container.get("Mounts", []):
        if not isinstance(item, dict) or item.get("RW") is not True:
            continue
        destination = item.get("Destination")
        source = item.get("Source")
        if destination in EPHEMERAL_WRITABLE_DESTINATIONS:
            continue
        if destination not in PERSISTENT_DESTINATIONS or not isinstance(source, str):
            raise UpdateError("ASR has an unsupported writable mount")
        path = Path(source)
        if (not path.is_absolute() or len(path.parts) < 3 or ".." in path.parts
                or path.is_symlink() or not path.is_dir()):
            raise UpdateError("ASR persistent storage is unsafe")
        mounts.append({"destination": destination, "source": str(path.resolve())})
    if {item["destination"] for item in mounts} != PERSISTENT_DESTINATIONS:
        raise UpdateError("ASR persistent volume set is incomplete")
    return sorted(mounts, key=lambda item: item["destination"])


def docker_compose(meta: dict[str, Any], image: str, *arguments: str,
                   timeout: int = 300) -> subprocess.CompletedProcess[str]:
    if "approvedModel" in meta:
        model = json.loads(json.dumps(meta["approvedModel"]))
        model["services"][meta["service"]]["image"] = image
        # JSON is valid Compose YAML. Escape interpolation in the already-rendered model.
        def escape(value):
            if isinstance(value, str):
                return value.replace("$", "$$")
            if isinstance(value, list):
                return [escape(item) for item in value]
            if isinstance(value, dict):
                return {key: escape(item) for key, item in value.items()}
            return value
        with tempfile.TemporaryDirectory(prefix="approved-compose-", dir=WORK) as directory:
            config = Path(directory) / "compose.json"
            config.write_text(json.dumps(escape(model)))
            environment = docker_environment()
            return run(["/usr/bin/docker", "compose", "--project-directory", directory,
                        "--project-name", meta["project"], "--env-file", "/dev/null",
                        "-f", str(config), *arguments], timeout=timeout,
                       env=environment)
    argv = ["/usr/bin/docker", "compose", "--project-directory", meta["workingDir"]]
    for config in meta["configFiles"]:
        argv += ["-f", config]
    argv += [*arguments]
    environment = docker_environment()
    environment.update({"ASR_IMAGE": image, "ASR_UPDATE_MODE": "container",
                        "COMPOSE_PROJECT_NAME": meta["project"]})
    return run(argv, timeout=timeout, env=environment)


def validate_compose_model(meta: dict[str, Any]) -> dict[str, Any]:
    sentinel = "allscan-reimagined:asr-update-preflight"
    rendered = docker_compose(meta, sentinel, "config", "--format", "json")
    try:
        model = json.loads(rendered.stdout)
        service = model["services"][meta["service"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpdateError("Compose configuration could not be rendered") from exc
    environment = service.get("environment", {})
    if (rendered.returncode or service.get("image") != sentinel
            or not isinstance(environment, dict)
            or environment.get("ASR_UPDATE_MODE") != "container"):
        raise UpdateError("Compose does not honor the selected ASR image and container update mode")
    return model


def approved_compose(meta: dict[str, Any]) -> dict[str, Any]:
    path = STATE / "approved-compose.json"
    if path.is_symlink() or not path.is_file():
        raise UpdateError("Root administrator must approve the Compose snapshot")
    st = path.stat()
    if st.st_uid != 0 or st.st_mode & 0o022:
        raise UpdateError("Approved Compose snapshot is unsafe")
    model = json.loads(path.read_text())
    if model.get("name") != meta["project"] or meta["service"] not in model.get("services", {}):
        raise UpdateError("Approved Compose identity differs from running container")
    return {**meta, "approvedModel": model}


def approve_compose() -> dict[str, Any]:
    if os.geteuid() != 0:
        raise UpdateError("Root administrator approval required")
    selected = profile()
    backend().validate_active_profile(selected)
    meta = compose_metadata(inspect(selected.web_container), approval=True)
    model = validate_compose_model(meta)
    # Explicit administrator action only; this operation is not exposed by the broker.
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=STATE, delete=False) as handle:
        json.dump(model, handle)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE / "approved-compose.json")
    return {"ok": True, "project": meta["project"], "service": meta["service"]}


def release_check(web_container: str) -> dict[str, Any]:
    installed = installed_version(web_container)
    verifier = native_updater()
    try:
        release = verifier.release_for_update(installed)
    except verifier.UpdateError as exc:
        if str(exc) != "ASR is up to date":
            raise UpdateError(str(exc)) from exc
        return {"ok": True, "installedVersion": installed, "availableVersion": installed,
                "updateAvailable": False, "updateMode": "container"}
    return {"ok": True, "installedVersion": installed, "availableVersion": release["version"],
            "updateAvailable": True, "updateMode": "container", "release": release}


def validate_restart_policy(container: dict, meta: dict) -> None:
    policy = container.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "no") or "no"
    model_policy = meta["approvedModel"]["services"][meta["service"]].get("restart", "no")
    if policy not in {"no", "unless-stopped"} or model_policy not in {"no", "unless-stopped"}:
        raise UpdateError("Container restart policy must be no or unless-stopped for safe updates")
    if policy != model_policy:
        raise UpdateError("Approved Compose restart policy differs from running container")


def preflight(include_release: bool = False) -> dict[str, Any]:
    selected = profile()
    backend().validate_active_profile(selected)
    container = inspect(selected.web_container)
    meta = compose_metadata(container)
    meta = approved_compose(meta)
    # A manually stopped service must stay stopped across a daemon/host reboot
    # while its helpers are being replaced. Do not support "always" policies.
    validate_restart_policy(container, meta)
    # Both fresh installations and the explicit legacy bootstrap install this
    # service. Refuse browser upgrades before downtime if it is not enabled.
    if run(["/usr/bin/systemctl", "is-enabled", "--quiet", RECOVERY_UNIT], timeout=15).returncode:
        raise UpdateError("Host updater recovery service is not enabled; administrator migration required")
    mounts = persistent_mounts(container)
    check = release_check(selected.web_container)
    if not check["updateAvailable"]:
        raise UpdateError("ASR is up to date")
    if run(["/usr/bin/docker", "compose", "version"]).returncode:
        raise UpdateError("Docker Compose is unavailable")
    if shutil.disk_usage(BACKUPS).free < 2 * 1024 ** 3 or shutil.disk_usage(WORK).free < 2 * 1024 ** 3:
        raise UpdateError("Insufficient free space for image build and rollback backup")
    result = {"ok": True, "installedVersion": check["installedVersion"],
              "availableVersion": check["availableVersion"], "compatible": True,
              "diskSpace": "sufficient", "services": "ready", "configuration": "valid",
              "backup": "ready", "restartRequired": True, "rebootRequired": False,
              "updateMode": "container"}
    if include_release:
        result.update({"release": check["release"], "compose": meta, "mounts": mounts})
    return result


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def status(job: str, state: str, **extra: Any) -> dict[str, Any]:
    if JOB_RE.fullmatch(job) is None or state not in ACTIVE | TERMINAL:
        raise UpdateError("Invalid update status")
    STATE.mkdir(parents=True, exist_ok=True)
    data = {"ok": state != "failed", "jobId": job, "state": state,
            "transactionSchema": 1, "updatedAt": now(), **extra}
    atomic_host_file(STATE / f"{job}.json",
                     (json.dumps(data, separators=(",", ":")) + "\n").encode(), 0o600)
    return data


def read_status(job: str) -> dict[str, Any]:
    if JOB_RE.fullmatch(job) is None:
        raise UpdateError("Invalid update job ID")
    try:
        data = json.loads((STATE / f"{job}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("Update job was not found") from exc
    if not isinstance(data, dict) or data.get("jobId") != job or data.get("state") not in ACTIVE | TERMINAL:
        raise UpdateError("Update status is invalid")
    return data


def requires_recovery(data: dict) -> bool:
    return data["state"] in ACTIVE or (data["state"] == "failed"
                                        and data.get("manualInterventionRequired") is True)


def active_jobs() -> bool:
    if not STATE.exists():
        return False
    return any(requires_recovery(read_status(path.stem))
               for path in STATE.glob("[0-9]*.json") if JOB_RE.fullmatch(path.stem))


def guard(*, wait_seconds: float = 0):
    GATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(GATE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o022):
        os.close(fd)
        raise UpdateError("Unsafe container update lock")
    handle = os.fdopen(fd, "a+")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError as exc:
            if time.monotonic() >= deadline:
                handle.close()
                raise UpdateError("Another maintenance operation is running") from exc
            time.sleep(0.05)


@contextmanager
def maintenance_writer_guard():
    # Same configuration-writer locks as independent Standard Bridge controls.
    # Already holding GATE; take the remaining locks nonblocking to avoid
    # deadlocks with existing setup or lifecycle operations.
    names = ("allscan-reimagined-rollback.lock",
             "allscan-reimagined-bridge-setup.lock",
             "allscan-reimagined-bridge-lifecycle.lock",
             "allscan-reimagined-m17-qualification.lock",
             "allscan-reimagined-net-bridge-mode.lock")
    with ExitStack() as stack:
        for name in names:
            fd = os.open(GATE.parent / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022):
                raise UpdateError("Unsafe ASR configuration writer lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise UpdateError("ASR bridge maintenance is active; retry update later") from exc
        yield


def queue() -> dict[str, Any]:
    with guard():
        if active_jobs():
            raise UpdateError("Another maintenance operation is running")
        job = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        status(job, "queued")
        result = run(["/usr/bin/systemctl", "start", "--no-block",
                      f"allscan-reimagined-container-update@{job}.service"])
        if result.returncode:
            status(job, "failed", message="Update could not start.",
                   failureReason="The host update worker could not start.",
                   restoration="not_needed", manualInterventionRequired=False)
            raise UpdateError("Update job could not start")
        return read_status(job)


def safe_archive_members(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        parts = PurePosixPath(member.name).parts
        if (not parts or member.name.startswith("/") or any(part in {"", ".", ".."} for part in parts)
                or member.issym() or member.islnk() or not (member.isfile() or member.isdir())):
            raise UpdateError("Rollback backup contains an unsafe entry")
        target = destination.joinpath(*parts).resolve()
        if target != root and root not in target.parents:
            raise UpdateError("Rollback backup escaped its volume")


def host_inventory(host_files=HOST_FILES, control_files=CONTROL_FILES, support_files=SUPPORT_FILES) -> dict[str, tuple[str, Path, int]]:
    # Fixed destinations; never accept a host path from an archive or request.
    groups = (host_files, control_files, support_files)
    names = [name for group in groups for name in group]
    reserved = {"asr-container-host-install.py", "asr-tgif-dmr-proxy.py", "asr-tgif-standalone-relay.py"}
    if (any(not isinstance(name, str) or not re.fullmatch(r"asr[-_][a-z0-9_-]+\.py", name) for name in names)
            or len(names) != len(set(names)) or reserved.intersection(names)
            or any(not name.startswith("asr-") for name in control_files)):
        raise UpdateError("Invalid release host inventory")
    if not all(set(required) <= set(actual) for required, actual in zip((HOST_FILES, CONTROL_FILES, SUPPORT_FILES), groups)):
        raise UpdateError("Release host inventory removed a required helper")
    entries = {}
    for name in (*host_files, "asr-container-host-install.py"):
        entries[name] = ("payload/scripts/" + name, HOST_DEST / name, 0o755)
    for name in control_files:
        entries[name] = ("payload/scripts/" + name, CONTROL_DEST / ("allscan-reimagined-" + name.removeprefix("asr-").removesuffix(".py")), 0o755)
    for name in support_files:
        entries[name] = ("payload/scripts/" + name, CONTROL_DEST / name, 0o644)
    entries["asr-tgif-dmr-proxy.py"] = ("payload/runtime/dmr/asr-tgif-dmr-proxy.py", HOST_DEST / "asr-tgif-dmr-proxy.py", 0o755)
    entries["asr-tgif-standalone-relay.py"] = ("payload/scripts/bridge-relays/asr-tgif-standalone-relay.py", HOST_DEST / "bridge-relays/asr-tgif-standalone-relay.py", 0o755)
    if len({target for _, target, _ in entries.values()}) != len(entries):
        raise UpdateError("Release host inventory contains duplicate destinations")
    return entries


def release_inventory(staged: Path, *, local_checkout: bool = False):
    source = staged / ("scripts/asr-container-host-install.py" if local_checkout else "payload/scripts/asr-container-host-install.py")
    if source.is_symlink() or not source.is_file():
        raise UpdateError("Verified release lacks host installer")
    # Read the release's literal inventory without executing its Python.
    groups = {}
    schema = []
    for node in ast.parse(source.read_text()).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "HOST_UPDATER_INFRASTRUCTURE_SCHEMA"):
            schema.append(ast.literal_eval(node.value))
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {"HOST_FILES", "CONTROL_FILES", "SUPPORT_FILES"}:
            if node.targets[0].id in groups:
                raise UpdateError("Duplicate release host inventory group")
            value = ast.literal_eval(node.value)
            if not isinstance(value, (list, tuple)):
                raise UpdateError("Invalid release host inventory")
            groups[node.targets[0].id] = tuple(value)
    if schema != [HOST_UPDATER_INFRASTRUCTURE_SCHEMA]:
        raise UpdateError("Release requires a different host updater infrastructure migration")
    if set(groups) != {"HOST_FILES", "CONTROL_FILES", "SUPPORT_FILES"}:
        raise UpdateError("Release host inventory is incomplete")
    return host_inventory(*(groups[key] for key in ("HOST_FILES", "CONTROL_FILES", "SUPPORT_FILES")))


def host_payload(staged: Path, inventory=None, *, local_checkout: bool = False) -> dict[str, bytes]:
    result = {}
    for name, (relative, target, _) in (inventory or host_inventory()).items():
        source = staged / (relative.removeprefix("payload/") if local_checkout else relative)
        if source.is_symlink() or not source.is_file():
            raise UpdateError("Verified release lacks host helper: " + name)
        # Installed helper parents must already exist and be privileged.
        for directory in (target.parent, *target.parent.parents):
            if directory.is_symlink() or not directory.is_dir():
                raise UpdateError("Host helper destination is unsafe")
            info = directory.stat()
            # Only inspect the immediate managed directory; /tmp and test
            # fixtures can have different ownership/permissions.
            if directory == target.parent and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
                raise UpdateError("Host helper destination is not protected")
        host_existing(target)
        result[name] = source.read_bytes()
        # Catch invalid Python before the current container is stopped.
        try:
            compile(result[name], name, "exec")
        except (ValueError, SyntaxError) as exc:
            raise UpdateError("Invalid release host helper: " + name) from exc
    return result


def host_existing(target: Path) -> bool:
    if target.is_symlink():
        raise UpdateError("Unsafe host helper symlink")
    if not target.exists():
        return False
    info = target.stat()
    if not target.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o022 or info.st_nlink != 1:
        raise UpdateError("Unsafe installed host helper")
    return True


def sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_host_file(target: Path, content: bytes, mode: int) -> None:
    host_existing(target)
    fd, temporary = tempfile.mkstemp(prefix=".asr-host-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        sync_directory(target.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def backup_host_helpers(backup: Path, inventory=None) -> None:
    directory = backup / "host-helpers"
    directory.mkdir(mode=0o700)
    inventory = inventory or host_inventory()
    manifest = {}
    for name, (_, target, _) in inventory.items():
        if not host_existing(target):
            manifest[name] = None
            continue
        content = target.read_bytes()
        atomic_host_file(directory / name, content, 0o600)
        manifest[name] = {"mode": target.stat().st_mode & 0o777, "sha256": hashlib.sha256(content).hexdigest()}
    # A complete manifest is the commit point for a usable host backup.
    locations = {name: ("host" if target.parent == HOST_DEST else "control" if target.parent == CONTROL_DEST and name != "asr_bridge_status.py" and mode == 0o755 else "support" if target.parent == CONTROL_DEST else "relay") for name, (_, target, mode) in inventory.items()}
    atomic_host_file(directory / "manifest.json", json.dumps({"schema": 1, "files": manifest, "locations": locations}, sort_keys=True).encode(), 0o600)
    sync_directory(backup)
    read_host_backup(backup)


def install_host_helpers(payload: dict[str, bytes], inventory=None) -> None:
    inventory = inventory or host_inventory()
    if set(payload) != set(inventory):
        raise UpdateError("Incomplete host helper payload")
    # Publish the broker last, after its dependencies are available.
    for name in sorted(inventory, key=lambda n: n == "asr-container-host-broker.py"):
        _, target, mode = inventory[name]
        atomic_host_file(target, payload[name], mode)
    for name, (_, target, mode) in inventory.items():
        if (not host_existing(target) or target.read_bytes() != payload[name]
                or target.stat().st_mode & 0o777 != mode):
            raise UpdateError("Host helper verification failed: " + name)


def read_host_backup(backup: Path):
    directory = backup / "host-helpers"
    manifest_file = directory / "manifest.json"
    if not directory.exists() and not directory.is_symlink():
        return None  # Legacy backups predate host-helper changes.
    if (directory.is_symlink() or not directory.is_dir()
            or directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o077):
        raise UpdateError("Host helper backup directory is unsafe")
    if not host_existing(manifest_file):
        raise UpdateError("Host helper backup manifest is unsafe")
    record = json.loads(manifest_file.read_text())
    if record.get("schema") != 1:
        raise UpdateError("Invalid host helper backup schema")
    manifest = record["files"]
    locations = record["locations"]
    special = {"asr-container-host-install.py", "asr-tgif-dmr-proxy.py", "asr-tgif-standalone-relay.py"}
    inventory = host_inventory(*(tuple(name for name, kind in locations.items() if kind == category and name not in special) for category in ("host", "control", "support")))
    if set(manifest) != set(inventory) or set(locations) != set(inventory):
        raise UpdateError("Invalid host helper backup inventory")
    contents = {}
    for name, record in manifest.items():
        if record is not None:
            source = directory / name
            if not host_existing(source):
                raise UpdateError("Missing host helper backup")
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != record["sha256"] or not isinstance(record["mode"], int) or record["mode"] & ~0o777 or record["mode"] & 0o022:
                raise UpdateError("Invalid host helper backup")
            contents[name] = content
    return inventory, manifest, contents


def restore_host_helpers(backup: Path) -> None:
    data = read_host_backup(backup)
    if data is None:
        return
    inventory, manifest, contents = data
    for name in sorted(inventory, key=lambda n: n == "asr-container-host-broker.py"):
        _, target, _ = inventory[name]
        if manifest[name] is None:
            if host_existing(target):
                target.unlink()
                sync_directory(target.parent)
        else:
            atomic_host_file(target, contents[name], manifest[name]["mode"])
    for name, (_, target, _) in inventory.items():
        if manifest[name] is None:
            if host_existing(target):
                raise UpdateError("Host rollback failed to remove newly installed helper")
        elif (not host_existing(target) or target.read_bytes() != contents[name]
                or target.stat().st_mode & 0o777 != manifest[name]["mode"]):
            raise UpdateError("Host rollback verification failed: " + name)


def create_backup(job: str, meta: dict[str, Any], mounts: list[dict[str, str]]) -> Path:
    backup = BACKUPS / job
    backup.mkdir(parents=True, mode=0o700)
    recorded_mounts = []
    for mount in mounts:
        info = Path(mount["source"]).stat()
        recorded_mounts.append({**mount, "uid": info.st_uid, "gid": info.st_gid,
                                "mode": info.st_mode & 0o7777})
    manifest = {"schema": 1, "jobId": job, "createdAt": now(), "compose": meta,
                "mounts": recorded_mounts, "volumeHashes": []}
    for index, mount in enumerate(recorded_mounts):
        with tarfile.open(backup / f"volume-{index}.tar.gz", "w:gz") as archive:
            # Runtime links are recreated by the entrypoint. Excluding links
            # keeps a web-writable symlink from escaping the restore root.
            archive.add(mount["source"], arcname="data", recursive=True,
                        filter=lambda member: None if member.issym() or member.islnk() else member)
        archive_path = backup / f"volume-{index}.tar.gz"
        with archive_path.open("rb") as handle:
            os.fsync(handle.fileno())
            manifest["volumeHashes"].append(hashlib.file_digest(handle, "sha256").hexdigest())
    atomic_host_file(backup / "manifest.json",
                     (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o600)
    sync_directory(BACKUPS)
    return backup


def stop_for_restore(web_container: str) -> bool:
    result = run(["/usr/bin/docker", "stop", web_container], timeout=120)
    if result.returncode == 0:
        return True
    inspected = run(["/usr/bin/docker", "container", "inspect", web_container], timeout=30)
    # Compose may have removed the old container before creating its replacement.
    # Only an exact Docker not-found response makes absence safe to proceed.
    return (inspected.returncode != 0
            and inspected.stderr.strip() == f"Error response from daemon: No such container: {web_container}")


def restore_backup(backup: Path, image: str, version: str) -> bool:
    staged: list[tuple[Path, Path, Path]] = []
    replaced: list[tuple[Path, Path, list[str]]] = []
    preserve_displaced = False
    restored_files = False
    try:
        manifest = json.loads((backup / "manifest.json").read_text())
        meta = manifest["compose"]
        mounts = manifest["mounts"]
        hashes = manifest.get("volumeHashes")
        if hashes is not None and (not isinstance(hashes, list) or len(hashes) != len(mounts)):
            raise UpdateError("Invalid rollback volume hash inventory")
        # Reject a damaged host backup before displacing any persistent volume.
        read_host_backup(backup)
        # Validate and fully decompress every volume before touching live data.
        for index, mount in enumerate(mounts):
            root = Path(mount["source"])
            if (root.is_symlink() or not root.is_dir() or len(root.parts) < 3
                    or not all(isinstance(mount.get(key), int) for key in ("uid", "gid", "mode"))):
                raise UpdateError("Rollback volume is unsafe")
            temporary = Path(tempfile.mkdtemp(prefix=".asr-restore-", dir=root.parent))
            displaced = Path(tempfile.mkdtemp(prefix=".asr-displaced-", dir=root.parent))
            staged.append((root, temporary, displaced))
            archive_path = backup / f"volume-{index}.tar.gz"
            if hashes is not None:
                with archive_path.open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != hashes[index]:
                        raise UpdateError("Rollback volume checksum mismatch")
            with tarfile.open(archive_path, "r:gz") as archive:
                safe_archive_members(archive, temporary)
                archive.extractall(temporary, filter="fully_trusted", numeric_owner=True)
            if not (temporary / "data").is_dir():
                raise UpdateError("Rollback archive lacks volume root")
        if not stop_for_restore(meta["webContainer"]):
            raise UpdateError("Rollback container could not stop")
        for root, temporary, displaced in staged:
            installed: list[str] = []
            replaced.append((root, displaced, installed))
            for child in root.iterdir():
                os.replace(child, displaced / child.name)
            for child in (temporary / "data").iterdir():
                os.replace(child, root / child.name)
                installed.append(child.name)
            mount = mounts[len(replaced) - 1]
            os.chown(root, mount["uid"], mount["gid"])
            os.chmod(root, mount["mode"])
        restore_host_helpers(backup)
        restored_files = True
        result = docker_compose(meta, image, "up", "-d", "--no-deps", "--force-recreate",
                                meta["service"], timeout=300)
        return result.returncode == 0 and healthy(profile().web_container, version)
    except (OSError, ValueError, KeyError, UpdateError, tarfile.TarError, subprocess.TimeoutExpired):
        # Once all original files are restored, never undo them if starting
        # or checking the original image fails. A later retry can use the backup.
        if restored_files:
            return False
        # A failed filesystem replacement must not leave a mixture of old and
        # restored volume contents. Put every touched volume back as it was.
        rollback_ok = True
        for root, displaced, installed in reversed(replaced):
            try:
                for name in installed:
                    child = root / name
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                for child in displaced.iterdir():
                    os.replace(child, root / child.name)
            except OSError:
                rollback_ok = False
        preserve_displaced = not rollback_ok
        return False
    finally:
        for _, temporary, displaced in staged:
            shutil.rmtree(temporary, ignore_errors=True)
            if not preserve_displaced:
                shutil.rmtree(displaced, ignore_errors=True)


def healthy(web_container: str, version: str | None) -> bool:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            container = inspect(web_container)
            state = container.get("State", {})
            health = state.get("Health", {}).get("Status")
            if state.get("Running") is True and health not in {"starting", "unhealthy"}:
                if version is not None and installed_version(web_container) != version:
                    time.sleep(2)
                    continue
                result = run(["/usr/bin/docker", "exec", web_container, "php", "-r",
                              '$v=@file_get_contents("http://127.0.0.1/asr/");exit(is_string($v)&&str_contains($v,"assets/index-")?0:1);'])
                if result.returncode == 0:
                    return True
        except (UpdateError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    return False


def transaction_path(job: str) -> Path:
    if JOB_RE.fullmatch(job) is None:
        raise UpdateError("Invalid update transaction ID")
    return STATE / f".{job}.transaction.json"


def write_transaction(job: str, meta: dict, previous_version: str, phase: str) -> None:
    if (phase not in {"prepared", "backup_ready"} or VERSION_RE.fullmatch(previous_version) is None
            or re.fullmatch(r"sha256:[a-f0-9]{64}", meta["imageId"]) is None):
        raise UpdateError("Invalid update transaction")
    record = {"schema": 1, "jobId": job, "phase": phase, "compose": meta,
              "previousVersion": previous_version}
    atomic_host_file(transaction_path(job), json.dumps(record, sort_keys=True).encode(), 0o600)


def read_transaction(job: str) -> dict:
    path = transaction_path(job)
    if not host_existing(path):
        raise UpdateError("Interrupted update has no verified transaction record")
    try:
        record = json.loads(path.read_text())
        if (record.get("schema") != 1 or record.get("jobId") != job
                or record.get("phase") not in {"prepared", "backup_ready"}
                or VERSION_RE.fullmatch(record.get("previousVersion", "")) is None
                or re.fullmatch(r"sha256:[a-f0-9]{64}", record["compose"]["imageId"]) is None
                or record["compose"]["webContainer"] != profile().web_container):
            raise UpdateError("Interrupted update transaction is invalid")
        return record
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise UpdateError("Interrupted update transaction is invalid") from exc


def run_job(job: str) -> int:
    with guard(wait_seconds=5):
        if read_status(job)["state"] != "queued":
            raise UpdateError("Job is not queued")
        temporary: Path | None = None
        backup: Path | None = None
        previous_image = ""
        previous_version = ""
        web_container = ""
        stop_attempted = False
        writers = ExitStack()
        try:
            status(job, "preflight")
            checks = preflight(include_release=True)
            release = checks.pop("release")
            meta = checks.pop("compose")
            mounts = checks.pop("mounts")
            previous_image = meta["imageId"]
            previous_version = checks["installedVersion"]
            temporary = Path(tempfile.mkdtemp(prefix="asr-container-update-", dir=WORK))
            package = temporary / "release.tar.gz"
            status(job, "downloading", currentVersion=previous_version,
                   availableVersion=release["version"])
            verifier = native_updater()
            payload = verifier.fetch(release["url"], "application/octet-stream", verifier.MAX_ARCHIVE)
            package.write_bytes(payload)
            status(job, "verifying")
            if hashlib.sha256(payload).hexdigest() != release["sha256"]:
                raise UpdateError("Release checksum mismatch")
            status(job, "staging")
            staged = verifier.stage_release(package, temporary / "stage", release["version"])
            dockerfile = staged / "container/Dockerfile.layered"
            if not dockerfile.is_file():
                raise UpdateError("Release has no verified container image definition")
            inventory = release_inventory(staged)
            host_files = host_payload(staged, inventory)
            target_image = f"allscan-reimagined:{release['version']}"
            status(job, "building")
            build = run(["/usr/bin/docker", "build", "--pull=false", "--network=default",
                         "-f", str(dockerfile), "-t", target_image, str(staged)], timeout=1800)
            if build.returncode:
                raise UpdateError("Container image build failed")
            writers.enter_context(maintenance_writer_guard())
            write_transaction(job, meta, previous_version, "prepared")
            status(job, "backup")
            web_container = profile().web_container
            stop_attempted = True
            if run(["/usr/bin/docker", "stop", web_container], timeout=120).returncode:
                raise UpdateError("ASR container could not stop for a consistent backup")
            candidate_backup = create_backup(job, meta, mounts)
            backup_host_helpers(candidate_backup, inventory)
            write_transaction(job, meta, previous_version, "backup_ready")
            backup = candidate_backup
            status(job, "installing")
            install_host_helpers(host_files, inventory)
            result = docker_compose(meta, target_image, "up", "-d", "--no-deps", "--force-recreate",
                                    meta["service"], timeout=300)
            if result.returncode:
                raise UpdateError("Updated ASR container could not start")
            status(job, "health")
            if not healthy(profile().web_container, release["version"]):
                raise UpdateError("Updated ASR container failed health checks")
            status(job, "complete", currentVersion=release["version"],
                   availableVersion=release["version"], message="Update complete.",
                   backupId=job)
            return 0
        except Exception as exc:
            reason = str(exc)[:240] if isinstance(exc, UpdateError) else "The container update failed unexpectedly."
            if backup is None:
                recovered = True
                if stop_attempted:
                    try:
                        recovered = (run(["/usr/bin/docker", "start", web_container], timeout=120).returncode == 0
                                     and healthy(web_container, previous_version))
                    except Exception:
                        recovered = False
                status(job, "failed", message="Update could not start." if recovered else "Original container recovery needs attention.",
                       failureReason=reason,
                       restoration=("succeeded" if recovered else "failed") if stop_attempted else "not_needed",
                       manualInterventionRequired=not recovered)
            else:
                status(job, "restoring")
                restored = restore_backup(backup, previous_image, previous_version)
                status(job, "failed", message=("Update failed; previous container, volumes and host helpers restored."
                       if restored else "Update failed; recovery needs attention."),
                       failureReason=reason, restoration="succeeded" if restored else "failed",
                       manualInterventionRequired=not restored)
            return 1
        finally:
            writers.close()
            if temporary:
                shutil.rmtree(temporary, ignore_errors=True)


def recover() -> dict[str, Any]:
    with guard(wait_seconds=5):
        stale = []
        for path in STATE.glob("[0-9]*.json") if STATE.exists() else []:
            if JOB_RE.fullmatch(path.stem):
                data = read_status(path.stem)
                if requires_recovery(data):
                    stale.append(data)
        if not stale:
            return {"ok": True, "status": "nothing_to_recover"}
        if len(stale) != 1:
            raise UpdateError("Multiple unfinished updates require administrator review")
        data = stale[0]
        job = data["jobId"]
        # A queued worker may not yet own the lock. Never race a systemd job.
        if data["state"] == "queued":
            unit = f"allscan-reimagined-container-update@{job}.service"
            result = run(["/usr/bin/systemctl", "show", "--property=ActiveState,Job", unit], timeout=15)
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if result.returncode or fields.get("ActiveState") not in {"inactive", "failed"} or fields.get("Job") != "":
                raise UpdateError("Queued update worker may still start")
            status(job, "failed", message="Queued update did not start.",
                   restoration="not_needed", manualInterventionRequired=False)
            return {"ok": True, "status": "not_needed", "jobId": job}
        if not transaction_path(job).exists():
            if (data.get("transactionSchema") == 1
                    and data["state"] in {"preflight", "downloading", "verifying", "staging", "building"}):
                status(job, "failed", message="Update interrupted before any host or container changes.",
                       restoration="not_needed", manualInterventionRequired=False)
                return {"ok": True, "status": "not_needed", "jobId": job}
            # An older worker may have already changed the host. Do not infer
            # safety from its status alone.
            raise UpdateError("Interrupted container update has no verified transaction record")
        record = read_transaction(job)
        with maintenance_writer_guard():
            return recover_transaction(job, record)


def recover_transaction(job: str, record: dict) -> dict[str, Any]:
    meta, version = record["compose"], record["previousVersion"]
    status(job, "restoring")
    try:
        if record["phase"] == "prepared":
            # Only a stop/backup was possible before the durable ready record.
            current = inspect(meta["webContainer"])
            if current.get("Image") != meta["imageId"]:
                raise UpdateError("Original container image identity changed")
            restored = (run(["/usr/bin/docker", "start", meta["webContainer"]], timeout=120).returncode == 0
                        and healthy(meta["webContainer"], version))
        else:
            backup = BACKUPS / job
            if read_host_backup(backup) is None:
                raise UpdateError("Interrupted update lacks verified host helper backup")
            manifest = json.loads((backup / "manifest.json").read_text())
            if (manifest.get("jobId") != job or manifest.get("compose") != meta
                    or not isinstance(manifest.get("volumeHashes"), list)):
                raise UpdateError("Interrupted update backup identity is invalid")
            restored = restore_backup(backup, meta["imageId"], version)
    except (OSError, ValueError, KeyError, TypeError, UpdateError, subprocess.TimeoutExpired):
        restored = False
    # Leave failed recovery retryable and block new updates until resolved.
    if not restored:
        status(job, "failed", message="Interrupted update recovery needs attention.",
               restoration="failed", manualInterventionRequired=True)
        raise UpdateError("Interrupted update recovery needs administrator attention")
    status(job, "failed", message="Interrupted update rolled back.",
           restoration="succeeded", manualInterventionRequired=False, currentVersion=version)
    return {"ok": True, "status": "restored", "jobId": job}

def diagnostic(job: str) -> dict[str, Any]:
    data = read_status(job)
    lines = [f"ASR container update job: {job}", f"State: {data['state']}",
             f"Updated: {data['updatedAt']}", f"Message: {data.get('message', '')}",
             f"Failure: {data.get('failureReason', '')}",
             f"Restoration: {data.get('restoration', 'not_needed')}",
             f"Manual intervention required: {'yes' if data.get('manualInterventionRequired') else 'no'}"]
    return {"ok": True, "jobId": job, "report": "\n".join(lines) + "\n"}


def bootstrap_host(checkout: Path) -> dict:
    """Explicit administrator migration for pre-#316 host updater installations."""
    if os.geteuid() != 0:
        raise UpdateError("Host bootstrap requires an administrator")
    with guard():
        if active_jobs():
            raise UpdateError("An update job is active")
        selected = profile()
        backend().validate_active_profile(selected)
        inventory = release_inventory(checkout, local_checkout=True)
        payload = host_payload(checkout, inventory, local_checkout=True)
        dropin_dir = Path("/etc/systemd/system/allscan-reimagined-container-update@.service.d")
        dropin = dropin_dir / "316-host-helpers.conf"
        tmpfiles = Path("/etc/tmpfiles.d/allscan-reimagined-standard-link.conf")
        failure_dropin = dropin_dir / "316-interrupted-recovery.conf"
        recovery_unit = Path("/etc/systemd/system") / RECOVERY_UNIT
        config = {
            dropin: b"[Service]\nReadWritePaths=/usr/local/libexec/allscan-reimagined /usr/local/sbin\n",
            failure_dropin: ("[Unit]\nOnFailure=" + RECOVERY_UNIT + "\n").encode(),
            recovery_unit: RECOVERY_UNIT_TEXT.encode(),
            tmpfiles: b"d /run/lock/allscan-reimagined-fixed-bridge 0755 root root -\nf /run/lock/allscan-reimagined-fixed-bridge/recovery.lock 0644 root root -\n",
        }
        dropin_dir.mkdir(mode=0o755, exist_ok=True)
        for target in config:
            if target.parent.is_symlink() or target.parent.stat().st_uid != 0 or target.parent.stat().st_mode & 0o022:
                raise UpdateError("Unsafe bootstrap configuration directory")
            if host_existing(target) and target.read_bytes() != config[target]:
                raise UpdateError("Existing bootstrap configuration differs")
        backup = BACKUPS / ("host-bootstrap-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4))
        backup.mkdir(mode=0o700)
        backup_host_helpers(backup, inventory)
        created = []
        enabled = run(["/usr/bin/systemctl", "is-enabled", RECOVERY_UNIT]).returncode == 0
        enable_attempted = False
        try:
            for target, content in config.items():
                if not host_existing(target):
                    atomic_host_file(target, content, 0o644)
                    created.append(target)
            run(["/usr/bin/systemd-tmpfiles", "--create", str(tmpfiles)], check=True)
            install_host_helpers(payload, inventory)
            run(["/usr/bin/systemctl", "daemon-reload"], check=True)
            # Read-only probe through the same web UID and constrained broker.
            probe = run(["/usr/bin/docker", "exec", "-u", str(selected.client_uid), selected.web_container,
                         "/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery", "--plan-json"], timeout=45)
            if probe.returncode:
                raise UpdateError("Host Standard Bridge broker probe failed: " + (probe.stderr or probe.stdout)[:160])
            plan = json.loads(probe.stdout)
            if not isinstance(plan, dict) or not all(isinstance(plan.get(key), list) for key in ("nativeManualHoldConflicts", "nativeManualHoldUnknown")):
                raise UpdateError("Host Standard Bridge broker returned an invalid plan")
            enable_attempted = True
            run(["/usr/bin/systemctl", "enable", RECOVERY_UNIT], check=True)
        except Exception:
            if enable_attempted and not enabled:
                run(["/usr/bin/systemctl", "disable", RECOVERY_UNIT])
            restore_host_helpers(backup)
            for target in reversed(created):
                target.unlink()
            run(["/usr/bin/systemctl", "daemon-reload"], check=True)
            raise
        return {"ok": True, "backup": str(backup), "webContainer": selected.web_container,
                "hostHelpers": len(inventory), "standardLinkProbe": "passed"}



def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "bootstrap-host":
            result = bootstrap_host(Path(argv[1]).resolve())
        elif argv == ["approve-compose"]:
            result = approve_compose()
        elif argv == ["check"]:
            selected = profile()
            backend().validate_active_profile(selected)
            result = release_check(selected.web_container)
            result.pop("release", None)
        elif argv == ["preflight"]:
            with guard():
                if active_jobs():
                    raise UpdateError("Another maintenance operation is running")
                result = preflight()
        elif argv == ["queue"]:
            result = queue()
        elif argv == ["recover"]:
            with guard():
                if active_jobs():
                    run(["/usr/bin/systemctl", "start", "--no-block",
                         RECOVERY_UNIT], check=True)
                    result = {"ok": True, "status": "recovery_queued"}
                else:
                    result = {"ok": True, "status": "nothing_to_recover"}
        elif argv == ["recover-interrupted"]:
            if os.geteuid() != 0:
                raise UpdateError("Host recovery requires an administrator")
            result = recover()
        elif len(argv) == 2 and argv[0] == "status":
            result = read_status(argv[1])
        elif len(argv) == 2 and argv[0] == "diagnostic":
            result = diagnostic(argv[1])
        elif len(argv) == 2 and argv[0] == "run-job":
            return run_job(argv[1])
        else:
            raise UpdateError("Invalid updater operation")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (UpdateError, OSError, ValueError, SyntaxError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
