#!/usr/bin/env python3
"""Verified, host-side Docker updater reached only through the ASR broker."""
from __future__ import annotations

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
import subprocess
import sys
import tarfile
import tempfile
import time
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


def preflight(include_release: bool = False) -> dict[str, Any]:
    selected = profile()
    backend().validate_active_profile(selected)
    container = inspect(selected.web_container)
    meta = compose_metadata(container)
    meta = approved_compose(meta)
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
    data = {"ok": state != "failed", "jobId": job, "state": state, "updatedAt": now(), **extra}
    temporary = STATE / f".{job}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE / f"{job}.json")
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


def active_jobs() -> bool:
    if not STATE.exists():
        return False
    for path in STATE.glob("[0-9]*.json"):
        try:
            if json.loads(path.read_text()).get("state") in ACTIVE:
                return True
        except (OSError, ValueError):
            continue
    return False


def guard():
    GATE.parent.mkdir(parents=True, exist_ok=True)
    handle = GATE.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise UpdateError("Another maintenance operation is running") from exc
    return handle


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


def create_backup(job: str, meta: dict[str, Any], mounts: list[dict[str, str]]) -> Path:
    backup = BACKUPS / job
    backup.mkdir(parents=True, mode=0o700)
    recorded_mounts = []
    for mount in mounts:
        info = Path(mount["source"]).stat()
        recorded_mounts.append({**mount, "uid": info.st_uid, "gid": info.st_gid,
                                "mode": info.st_mode & 0o7777})
    manifest = {"schema": 1, "jobId": job, "createdAt": now(), "compose": meta,
                "mounts": recorded_mounts}
    for index, mount in enumerate(recorded_mounts):
        with tarfile.open(backup / f"volume-{index}.tar.gz", "w:gz") as archive:
            # Runtime links are recreated by the entrypoint. Excluding links
            # keeps a web-writable symlink from escaping the restore root.
            archive.add(mount["source"], arcname="data", recursive=True,
                        filter=lambda member: None if member.issym() or member.islnk() else member)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(backup / "manifest.json", 0o600)
    return backup


def restore_backup(backup: Path, image: str, version: str) -> bool:
    staged: list[tuple[Path, Path, Path]] = []
    replaced: list[tuple[Path, Path]] = []
    preserve_displaced = False
    try:
        manifest = json.loads((backup / "manifest.json").read_text())
        meta = manifest["compose"]
        mounts = manifest["mounts"]
        # Validate and fully decompress every volume before touching live data.
        for index, mount in enumerate(mounts):
            root = Path(mount["source"])
            if (root.is_symlink() or not root.is_dir() or len(root.parts) < 3
                    or not all(isinstance(mount.get(key), int) for key in ("uid", "gid", "mode"))):
                raise UpdateError("Rollback volume is unsafe")
            temporary = Path(tempfile.mkdtemp(prefix=".asr-restore-", dir=root.parent))
            displaced = Path(tempfile.mkdtemp(prefix=".asr-displaced-", dir=root.parent))
            staged.append((root, temporary, displaced))
            with tarfile.open(backup / f"volume-{index}.tar.gz", "r:gz") as archive:
                safe_archive_members(archive, temporary)
                archive.extractall(temporary, filter="fully_trusted", numeric_owner=True)
            if not (temporary / "data").is_dir():
                raise UpdateError("Rollback archive lacks volume root")
        if run(["/usr/bin/docker", "stop", meta["webContainer"]], timeout=120).returncode:
            raise UpdateError("Rollback container could not stop")
        for root, temporary, displaced in staged:
            for child in root.iterdir():
                os.replace(child, displaced / child.name)
            replaced.append((root, displaced))
            for child in (temporary / "data").iterdir():
                os.replace(child, root / child.name)
            mount = mounts[len(replaced) - 1]
            os.chown(root, mount["uid"], mount["gid"])
            os.chmod(root, mount["mode"])
        result = docker_compose(meta, image, "up", "-d", "--no-deps", "--force-recreate",
                                meta["service"], timeout=300)
        return result.returncode == 0 and healthy(profile().web_container, version)
    except (OSError, ValueError, KeyError, UpdateError, tarfile.TarError, subprocess.TimeoutExpired):
        # A failed filesystem replacement must not leave a mixture of old and
        # restored volume contents. Put every touched volume back as it was.
        rollback_ok = True
        for root, displaced in reversed(replaced):
            try:
                for child in root.iterdir():
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


def run_job(job: str) -> int:
    if read_status(job)["state"] != "queued":
        raise UpdateError("Job is not queued")
    with guard():
        temporary: Path | None = None
        backup: Path | None = None
        previous_image = ""
        previous_version = ""
        web_container = ""
        stop_attempted = False
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
            target_image = f"allscan-reimagined:{release['version']}"
            status(job, "building")
            build = run(["/usr/bin/docker", "build", "--pull=false", "--network=default",
                         "-f", str(dockerfile), "-t", target_image, str(staged)], timeout=1800)
            if build.returncode:
                raise UpdateError("Container image build failed")
            status(job, "backup")
            web_container = profile().web_container
            stop_attempted = True
            if run(["/usr/bin/docker", "stop", web_container], timeout=120).returncode:
                raise UpdateError("ASR container could not stop for a consistent backup")
            backup = create_backup(job, meta, mounts)
            status(job, "installing")
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
                status(job, "failed", message=("Update failed; previous container and volumes restored."
                       if restored else "Update failed; recovery needs attention."),
                       failureReason=reason, restoration="succeeded" if restored else "failed",
                       manualInterventionRequired=not restored)
            return 1
        finally:
            if temporary:
                shutil.rmtree(temporary, ignore_errors=True)


def recover() -> dict[str, Any]:
    with guard():
        stale = []
        for path in STATE.glob("[0-9]*.json") if STATE.exists() else []:
            try:
                data = json.loads(path.read_text())
                if data.get("state") in ACTIVE and data.get("state") != "queued":
                    stale.append(data)
            except (OSError, ValueError):
                continue
        if not stale:
            return {"ok": True, "status": "nothing_to_recover"}
        raise UpdateError("Interrupted container update requires host recovery from its recorded backup")


def diagnostic(job: str) -> dict[str, Any]:
    data = read_status(job)
    lines = [f"ASR container update job: {job}", f"State: {data['state']}",
             f"Updated: {data['updatedAt']}", f"Message: {data.get('message', '')}",
             f"Failure: {data.get('failureReason', '')}",
             f"Restoration: {data.get('restoration', 'not_needed')}",
             f"Manual intervention required: {'yes' if data.get('manualInterventionRequired') else 'no'}"]
    return {"ok": True, "jobId": job, "report": "\n".join(lines) + "\n"}


def main(argv: list[str]) -> int:
    try:
        if argv == ["approve-compose"]:
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
    except (UpdateError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
