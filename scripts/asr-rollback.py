#!/usr/bin/env python3
"""Validated backup discovery and manual rollback for AllScan Reimagined."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


SCHEMA_VERSION = 2
SUPPORTED_SCHEMAS = (1, 2)
BACKUP_ROOT = Path("/root/allscan-reimagined-backups")
RELEASE_ROOT = Path("/opt/allscan-reimagined/releases")
CURRENT_LINK = Path("/opt/allscan-reimagined/current")
LOCK_PATH = Path("/run/lock/allscan-reimagined-rollback.lock")
JOB_ROOT = Path("/run/allscan-reimagined/rollback-jobs")
BACKUP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}$")
JOB_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$")
RUNTIME_FILES = (
    "bridge-live.json",
    "connected-clients.json",
    "zello-status-data.json",
)
RUNTIME_DIRS = ("img", "asr-user-content")
STABLE_WEB_FILES = ("asr-settings/rollback-status.php",)
REQUIRED_RELEASE_FILES = (
    "web/index.html",
    "server/asr-api.php",
    "scripts/asr-reapply.sh",
    "scripts/asr-integrity-check.sh",
)
ARCHIVE_LIMITS = {
    "webroot": {"members": 20000, "file_bytes": 128 * 1024 * 1024, "total_bytes": 512 * 1024 * 1024},
    "release": {"members": 10000, "file_bytes": 64 * 1024 * 1024, "total_bytes": 256 * 1024 * 1024},
}


class RollbackError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def version_label(version: str) -> str:
    match = re.fullmatch(
        r"([0-9]+\.[0-9]+\.[0-9]+)-beta\.([0-9]+(?:\.[0-9]+)*)",
        version,
    )
    if match:
        return f"v{match.group(1)} Beta {match.group(2)}"
    return f"v{version}" if version != "not-installed" else "Not installed"


def detect_version(api_file: Path) -> str:
    try:
        text = api_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "not-installed"
    match = re.search(r"const\s+ASR_VERSION\s*=\s*['\"]([^'\"]+)['\"]\s*;", text)
    if match:
        value = match.group(1)
        if VERSION_RE.fullmatch(value):
            return value
    label_match = re.search(
        r"const\s+ASR_VERSION_LABEL\s*=\s*['\"]v([0-9]+\.[0-9]+\.[0-9]+)"
        r"\s+Beta\s+([0-9]+(?:\.[0-9]+)*)['\"]\s*;",
        text,
        flags=re.IGNORECASE,
    )
    if label_match:
        value = f"{label_match.group(1)}-beta.{label_match.group(2)}"
        if VERSION_RE.fullmatch(value):
            return value
    return "unknown"


def detect_backend(common_file: Path) -> str:
    try:
        text = common_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    match = re.search(r'^\$AllScanVersion\s*=\s*"([^"]+)"\s*;', text, re.MULTILINE)
    value = match.group(1) if match else "unknown"
    return value if re.fullmatch(r"v[0-9]+\.[0-9]+", value) else "unknown"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise RollbackError(f"Unsafe archive member: {name}")
    return path


def validate_archive(path: Path, archive_kind: str) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise RollbackError(f"Missing or empty {archive_kind} archive")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise RollbackError(f"Empty {archive_kind} archive")
            names: set[str] = set()
            link_names: set[str] = set()
            total_bytes = 0
            limits = ARCHIVE_LIMITS["webroot" if archive_kind == "legacy-webroot" else archive_kind]
            expected_root = "allscan" if archive_kind == "legacy-webroot" else "asr"
            if len(members) > limits["members"]:
                raise RollbackError(f"Too many members in {archive_kind} archive")
            for member in members:
                member_path = safe_member_name(member.name)
                normalized = str(member_path).removeprefix("./")
                if not normalized:
                    continue
                if normalized in names:
                    raise RollbackError(f"Duplicate member in {archive_kind} archive")
                names.add(normalized)
                if member.isfile():
                    if member.size < 0 or member.size > limits["file_bytes"]:
                        raise RollbackError(f"Oversized file in {archive_kind} archive")
                    total_bytes += member.size
                    if total_bytes > limits["total_bytes"]:
                        raise RollbackError(f"Expanded {archive_kind} archive is too large")
                if member.ischr() or member.isblk() or member.isfifo():
                    raise RollbackError(f"Special file in {archive_kind} archive")
                if archive_kind in {"webroot", "legacy-webroot"}:
                    parts = tuple(part for part in member_path.parts if part != ".")
                    if not parts or parts[0] != expected_root:
                        raise RollbackError(
                            f"Webroot archive contains files outside {expected_root}/"
                        )
                if member.islnk():
                    raise RollbackError(f"Hard link in {archive_kind} archive")
                if member.issym():
                    allowed = (
                        archive_kind in {"webroot", "legacy-webroot"}
                        and normalized == f"{expected_root}/favorites.ini"
                        and member.linkname == "/etc/allscan/favorites.ini"
                    )
                    if not allowed:
                        raise RollbackError(f"Unexpected link in {archive_kind} archive")
                    link_names.add(normalized)
            for link_name in link_names:
                prefix = f"{link_name}/"
                if any(name.startswith(prefix) for name in names):
                    raise RollbackError("Archive contains a member below a symbolic link")
            if archive_kind in {"webroot", "legacy-webroot"} and not any(
                name == expected_root or name.startswith(f"{expected_root}/") for name in names
            ):
                raise RollbackError(f"Webroot archive has no {expected_root}/ directory")
            if archive_kind == "release":
                for required in REQUIRED_RELEASE_FILES:
                    if required not in names:
                        raise RollbackError(f"Release archive is missing {required}")
    except (tarfile.TarError, OSError) as exc:
        raise RollbackError(f"Invalid {archive_kind} archive: {exc}") from exc


def artifact_record(backup_dir: Path, filename: str, kind: str) -> dict[str, Any]:
    path = backup_dir / filename
    validate_archive(path, kind)
    return {
        "file": filename,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def write_manifest(
    backup_dir: Path,
    version: str,
    *,
    created_at: str | None = None,
) -> dict[str, Any]:
    backup_id = backup_dir.name
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise RollbackError("Backup directory name is not an exact timestamp ID")
    if version not in {"not-installed", "unknown"} and not VERSION_RE.fullmatch(version):
        raise RollbackError("Invalid pre-update ASR version")
    artifacts: dict[str, Any] = {}
    webroot = backup_dir / "asr-webroot.tar.gz"
    legacy_webroot = backup_dir / "allscan-webroot.tar.gz"
    release = backup_dir / "asr-release.tar.gz"
    if webroot.is_file():
        artifacts["webroot"] = artifact_record(
            backup_dir, "asr-webroot.tar.gz", "webroot"
        )
        schema = 2
    elif legacy_webroot.is_file():
        artifacts["webroot"] = artifact_record(
            backup_dir, "allscan-webroot.tar.gz", "legacy-webroot"
        )
        schema = 1
    else:
        schema = 2
    if release.is_file():
        artifacts["release"] = artifact_record(
            backup_dir, "asr-release.tar.gz", "release"
        )
    eligible = (
        VERSION_RE.fullmatch(version) is not None
        and "webroot" in artifacts
        and "release" in artifacts
    )
    manifest = {
        "schema": schema,
        "id": backup_id,
        "created_at": created_at or utc_now(),
        "pre_update_version": version,
        "pre_update_label": version_label(version),
        "rollback_eligible": eligible,
        "artifacts": artifacts,
    }
    temporary = backup_dir / ".manifest.json.tmp"
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, backup_dir / "manifest.json")
    os.chmod(backup_dir / "manifest.json", 0o600)
    return manifest


def load_valid_manifest(
    backup_dir: Path,
    *,
    verify_checksums: bool = True,
    verify_archives: bool = True,
) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RollbackError("Missing or invalid manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") not in SUPPORTED_SCHEMAS:
        raise RollbackError("Unsupported manifest schema")
    if manifest.get("id") != backup_dir.name or not BACKUP_ID_RE.fullmatch(backup_dir.name):
        raise RollbackError("Manifest ID does not match its backup directory")
    version = manifest.get("pre_update_version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise RollbackError("Manifest version is invalid")
    if manifest.get("rollback_eligible") is not True:
        raise RollbackError("Backup is not rollback eligible")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RollbackError("Manifest artifacts are invalid")
    schema = int(manifest["schema"])
    webroot_filename = "asr-webroot.tar.gz" if schema == 2 else "allscan-webroot.tar.gz"
    webroot_kind = "webroot" if schema == 2 else "legacy-webroot"
    for key, filename in (
        ("webroot", webroot_filename),
        ("release", "asr-release.tar.gz"),
    ):
        record = artifacts.get(key)
        if not isinstance(record, dict) or record.get("file") != filename:
            raise RollbackError(f"Manifest {key} record is invalid")
        expected = record.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RollbackError(f"Manifest {key} checksum is invalid")
        artifact = backup_dir / filename
        expected_bytes = record.get("bytes")
        if (
            not artifact.is_file()
            or not isinstance(expected_bytes, int)
            or expected_bytes <= 0
            or artifact.stat().st_size != expected_bytes
        ):
            raise RollbackError(f"{key.capitalize()} archive size does not match")
        if verify_checksums and sha256_file(artifact) != expected:
            raise RollbackError(f"{key.capitalize()} archive checksum does not match")
        if verify_archives:
            validate_archive(artifact, webroot_kind if key == "webroot" else key)
    return manifest


def find_webroot() -> Path:
    for root in (Path("/var/www/html"), Path("/srv/http")):
        if (root / "allscan").is_dir():
            return root
    raise RollbackError("AllScan installation not found")


def current_version() -> str:
    return detect_version(CURRENT_LINK / "server/asr-api.php")


def list_backups(backup_root: Path = BACKUP_ROOT, current: str | None = None) -> list[dict[str, str]]:
    current = current if current is not None else current_version()
    results: list[dict[str, str]] = []
    versions: set[str] = set()
    if not backup_root.is_dir():
        return results
    for backup_dir in sorted(backup_root.iterdir(), key=lambda item: item.name, reverse=True):
        if not backup_dir.is_dir() or not BACKUP_ID_RE.fullmatch(backup_dir.name):
            continue
        try:
            manifest = load_valid_manifest(
                backup_dir, verify_checksums=False, verify_archives=False
            )
        except RollbackError:
            continue
        version = str(manifest["pre_update_version"])
        if version == current or version in versions:
            continue
        versions.add(version)
        results.append(
            {
                "id": backup_dir.name,
                "version": version,
                "label": str(manifest.get("pre_update_label") or version_label(version)),
                "createdAt": str(manifest.get("created_at") or ""),
                "path": str(backup_dir),
            }
        )
        if len(results) == 5:
            break
    return results


def next_backup_dir(backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    candidate_time = dt.datetime.now()
    for offset in range(120):
        backup_id = (candidate_time + dt.timedelta(seconds=offset)).strftime("%Y%m%d-%H%M%S")
        candidate = backup_root / backup_id
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise RollbackError("Could not allocate a unique backup timestamp")


def prune_backups(backup_root: Path = BACKUP_ROOT, retention: int = 10) -> None:
    if not backup_root.is_dir():
        return
    backups = sorted(
        (
            item
            for item in backup_root.iterdir()
            if item.is_dir() and BACKUP_ID_RE.fullmatch(item.name)
        ),
        key=lambda item: item.name,
        reverse=True,
    )
    for expired in backups[retention:]:
        shutil.rmtree(expired)


def atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    os.chmod(path, mode)


def load_job(job_id: str) -> dict[str, Any]:
    if not JOB_ID_RE.fullmatch(job_id):
        raise RollbackError("Job ID is invalid")
    status_path = JOB_ROOT / f"{job_id}.json"
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RollbackError("Rollback job was not found") from exc
    if not isinstance(payload, dict) or payload.get("jobId") != job_id:
        raise RollbackError("Rollback job status is invalid")
    return payload


def queue_rollback(backup_id: str) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RollbackError("Rollback queueing must run as root")
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise RollbackError("Rollback ID must exactly match YYYYMMDD-HHMMSS")
    allowed = {item["id"] for item in list_backups()}
    if backup_id not in allowed:
        raise RollbackError("Requested backup is not one of the five available previous versions")
    backup_dir = BACKUP_ROOT / backup_id
    if int(load_valid_manifest(backup_dir, verify_checksums=False, verify_archives=False)["schema"]) == 1:
        raise RollbackError(
            "Legacy schema-1 rollback cannot be queued from the web UI; "
            "run the rollback helper interactively with --confirm-legacy-overlay."
        )
    JOB_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(JOB_ROOT, 0o700)
    job_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
    payload: dict[str, Any] = {
        "ok": True,
        "jobId": job_id,
        "backupId": backup_id,
        "state": "queued",
        "queuedAt": utc_now(),
    }
    atomic_json(JOB_ROOT / f"{job_id}.json", payload)
    unit = f"allscan-reimagined-rollback@{job_id}.service"
    result = subprocess.run(
        ["systemctl", "start", "--no-block", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        payload["state"] = "failed"
        payload["finishedAt"] = utc_now()
        payload["error"] = "The rollback job could not be started."
        atomic_json(JOB_ROOT / f"{job_id}.json", payload)
        raise RollbackError(payload["error"])
    return payload


def run_job(job_id: str) -> int:
    if os.geteuid() != 0:
        raise RollbackError("Rollback jobs must run as root")
    payload = load_job(job_id)
    if payload.get("state") != "queued":
        raise RollbackError("Rollback job is not queued")
    payload["state"] = "running"
    payload["startedAt"] = utc_now()
    atomic_json(JOB_ROOT / f"{job_id}.json", payload)
    try:
        result = rollback(str(payload["backupId"]))
        payload["state"] = "succeeded"
        payload["finishedAt"] = utc_now()
        payload["result"] = result
        atomic_json(JOB_ROOT / f"{job_id}.json", payload)
        return 0
    except Exception as exc:
        payload["state"] = "failed"
        payload["finishedAt"] = utc_now()
        payload["error"] = rollback_failure_message(exc)
        atomic_json(JOB_ROOT / f"{job_id}.json", payload)
        return 1


def rollback_failure_message(exc: Exception) -> str:
    prefix = "Rollback failed. The previous installation was restored."
    detail = re.sub(r"\s+", " ", str(exc)).strip()
    return f"{prefix} Reason: {detail[:400]}" if detail else prefix


def rollback_reapply_environment(
    protected_config_helper: Path | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment["ASR_ROLLBACK_MODE"] = "1"
    environment["ASR_INSTALL_LOCK_HELD"] = "1"
    environment.pop("ASR_PROTECTED_CONFIG_HELPER", None)
    if protected_config_helper is not None:
        environment["ASR_PROTECTED_CONFIG_HELPER"] = str(protected_config_helper)
    return environment


def run_tar_create(arguments: list[str], destination: Path) -> None:
    command = ["tar", "--ignore-failed-read", "--warning=no-file-changed", *arguments]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode not in (0, 1) or not destination.is_file() or destination.stat().st_size == 0:
        raise RollbackError(
            "Backup archive creation failed"
            + (f": {result.stderr.strip()}" if result.stderr.strip() else "")
        )
    os.chmod(destination, 0o600)


def safety_webroot_exclude_arguments() -> list[str]:
    """Return the exact volatile/generated paths omitted from safety backups."""
    return [
        "--exclude=asr/bridge-live.json",
        "--exclude=asr/connected-clients.json",
        "--exclude=asr/asr-connected-clients.json",
        "--exclude=asr/zello-status-data.json",
        "--exclude=asr/astdb.txt",
        "--exclude=asr/astdb.txt.*",
        "--exclude=asr/backup-*",
        "--exclude=asr/*.bak",
        "--exclude=asr/*.bak.*",
        "--exclude=asr/._*",
        "--exclude=asr/.DS_Store",
    ]


def create_safety_backup(web_root: Path) -> Path:
    backup_dir = next_backup_dir(BACKUP_ROOT)
    try:
        active_asr = web_root / "asr"
        if not active_asr.is_dir():
            raise RollbackError("Active /asr tree is missing")
        web_archive = backup_dir / "asr-webroot.tar.gz"
        run_tar_create(
            [
                *safety_webroot_exclude_arguments(),
                "-czf",
                str(web_archive),
                "-C",
                str(web_root),
                "asr",
            ],
            web_archive,
        )
        master = CURRENT_LINK.resolve(strict=True)
        if RELEASE_ROOT.resolve() not in master.parents:
            raise RollbackError("Current ASR master is outside the release directory")
        release_archive = backup_dir / "asr-release.tar.gz"
        run_tar_create(
            ["-czf", str(release_archive), "-C", str(master), "."],
            release_archive,
        )
        version = detect_version(master / "server/asr-api.php")
        manifest = write_manifest(backup_dir, version)
        if manifest["rollback_eligible"] is not True:
            raise RollbackError("Current installation could not be captured safely")
        return backup_dir
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def copy_preserved_runtime(active: Path, staged: Path) -> None:
    for name in RUNTIME_FILES:
        source = active / name
        destination = staged / name
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)
    for name in RUNTIME_DIRS:
        source = active / name
        destination = staged / name
        if source.is_dir() and not source.is_symlink():
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            shutil.copytree(source, destination, symlinks=True)
    for name in STABLE_WEB_FILES:
        source = active / name
        destination = staged / name
        if source.is_file() and not source.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination, follow_symlinks=False)


def runtime_source_for_schema(web_root: Path, active_web: Path, schema: int) -> Path:
    return web_root / "asr" if schema == 1 else active_web


def extract_archive(archive_path: Path, destination: Path, kind: str) -> None:
    validate_archive(archive_path, kind)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive_path, "r:gz") as archive:
        options: dict[str, Any] = {"numeric_owner": True}
        if "filter" in inspect.signature(archive.extractall).parameters:
            options["filter"] = "fully_trusted"
        archive.extractall(destination, **options)


def run_checked(command: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, check=False, text=True, capture_output=True, env=env)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RollbackError(
            f"Command failed: {' '.join(command)}" + (f": {detail}" if detail else "")
        )


def reconcile_ysf_net_wiring(release_root: Path) -> None:
    """Remove only ASR-owned YSF wiring when the restored release predates it."""
    if (release_root / "scripts/asr-ysf-bridge-control.py").is_file():
        return
    subprocess.run(
        [
            "systemctl", "disable", "--now",
            "allscan-reimagined-ysf-net-live.service",
            "allscan-reimagined-ysf-hosts-refresh.timer",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for path in (
        Path("/etc/systemd/system/allscan-reimagined-ysf-net-live.service"),
        Path("/etc/systemd/system/allscan-reimagined-ysf-hosts-refresh.service"),
        Path("/etc/systemd/system/allscan-reimagined-ysf-hosts-refresh.timer"),
        Path("/usr/local/sbin/allscan-reimagined-ysf-bridge-control"),
    ):
        path.unlink(missing_ok=True)
    shutil.rmtree(
        "/run/allscan-reimagined-ysf-bridge-control", ignore_errors=True
    )
    subprocess.run(
        ["systemctl", "daemon-reload"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def rewrite_owned_policy_without(
    path: Path,
    retired_markers: tuple[str, ...],
    *,
    validate_sudoers: bool = False,
) -> None:
    """Remove exact ASR-owned capability lines without broad policy edits."""
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise RollbackError(f"Refusing to rewrite unexpected policy path: {path}")
    metadata = path.stat()
    original = path.read_text(encoding="utf-8")
    retained = "".join(
        line for line in original.splitlines(keepends=True)
        if not any(marker in line for marker in retired_markers)
    )
    if retained == original:
        return
    temporary = path.with_name(f".{path.name}.rollback-digital-{os.getpid()}")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            metadata.st_mode & 0o777,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(retained)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, metadata.st_mode & 0o777)
        if validate_sudoers:
            result = subprocess.run(
                ["visudo", "-cf", str(temporary)],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise RollbackError(
                    f"Rollback sudoers cleanup validation failed: {result.stderr.strip()}"
                )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reconcile_next_digital_wiring(release_root: Path) -> None:
    """Remove ASR-owned P25/NXDN/M17 helpers when the restored release predates them."""
    scripts = release_root / "scripts"
    expected = {
        "asr-p25-bridge-control.py": Path("/usr/local/sbin/allscan-reimagined-p25-bridge-control"),
        "asr-nxdn-bridge-control.py": Path("/usr/local/sbin/allscan-reimagined-nxdn-bridge-control"),
        "asr-m17-bridge-control.py": Path("/usr/local/sbin/allscan-reimagined-m17-bridge-control"),
        "asr-m17-usrp-connector.py": Path("/usr/local/sbin/allscan-reimagined-m17-usrp-connector"),
    }
    missing = [name for name in expected if not (scripts / name).is_file()]
    if not missing:
        return
    if any(name.startswith("asr-m17-") for name in missing):
        wants_dir = Path("/etc/systemd/system/multi-user.target.wants")
        for unit_link in wants_dir.glob("allscan-reimagined-m17-bridge@*.service"):
            if not unit_link.is_symlink():
                continue
            subprocess.run(
                ["systemctl", "disable", "--now", unit_link.name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        Path("/etc/systemd/system/allscan-reimagined-m17-bridge@.service").unlink(missing_ok=True)
        shutil.rmtree("/run/allscan-reimagined-m17", ignore_errors=True)
    for mode, script_name in (
        ("p25", "asr-p25-bridge-control.py"),
        ("nxdn", "asr-nxdn-bridge-control.py"),
    ):
        if script_name not in missing:
            continue
        subprocess.run(
            ["systemctl", "disable", "--now", f"allscan-reimagined-{mode}-bridge-status.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        Path(f"/etc/systemd/system/allscan-reimagined-{mode}-bridge-status.service").unlink(missing_ok=True)
    for name in missing:
        expected[name].unlink(missing_ok=True)
    if "asr-p25-bridge-control.py" in missing:
        shutil.rmtree("/run/allscan-reimagined-p25-bridge-control", ignore_errors=True)
    if "asr-nxdn-bridge-control.py" in missing:
        shutil.rmtree("/run/allscan-reimagined-nxdn-bridge-control", ignore_errors=True)
    policy_markers = tuple(
        marker
        for script_name, markers in {
            "asr-p25-bridge-control.py": (
                "/run/allscan-reimagined-p25-bridge-control",
                "allscan-reimagined-p25-bridge-control",
            ),
            "asr-nxdn-bridge-control.py": (
                "/run/allscan-reimagined-nxdn-bridge-control",
                "allscan-reimagined-nxdn-bridge-control",
            ),
            "asr-m17-bridge-control.py": (
                "/run/allscan-reimagined-m17",
                "allscan-reimagined-m17-bridge-control",
            ),
        }.items()
        if script_name in missing
        for marker in markers
    )
    if policy_markers:
        rewrite_owned_policy_without(
            Path("/etc/tmpfiles.d/allscan-reimagined.conf"), policy_markers
        )
        rewrite_owned_policy_without(
            Path("/etc/sudoers.d/allscan-reimagined"),
            policy_markers,
            validate_sudoers=True,
        )
    subprocess.run(
        ["systemctl", "daemon-reload"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reconcile_fixed_bridge_recovery_wiring(
    release_root: Path,
    *,
    systemd_dir: Path = Path("/etc/systemd/system"),
    sbin_dir: Path = Path("/usr/local/sbin"),
    runner: Any = subprocess.run,
) -> None:
    """Remove ASR-owned fixed-link recovery when the restored release predates it."""
    if (release_root / "scripts/asr-fixed-bridge-recovery.py").is_file():
        return
    runner(
        [
            "systemctl", "disable", "--now",
            "allscan-reimagined-fixed-bridge-recovery.timer",
            "allscan-reimagined-fixed-bridge-recovery.service",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for path in (
        systemd_dir / "allscan-reimagined-fixed-bridge-recovery.service",
        systemd_dir / "allscan-reimagined-fixed-bridge-recovery.timer",
        sbin_dir / "allscan-reimagined-fixed-bridge-recovery",
    ):
        path.unlink(missing_ok=True)
    runner(
        ["systemctl", "daemon-reload"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reconcile_standard_bridge_status_wiring(
    release_root: Path,
    *,
    systemd_dir: Path = Path("/etc/systemd/system"),
    sbin_dir: Path = Path("/usr/local/sbin"),
    run_dir: Path = Path("/run/allscan-reimagined-standard-bridge-status"),
    tmpfiles_path: Path = Path("/etc/tmpfiles.d/allscan-reimagined.conf"),
    runner: Any = subprocess.run,
) -> None:
    """Remove Beta 7.4 Standard status wiring from older rollback targets."""
    if (release_root / "scripts/asr_bridge_status.py").is_file():
        return
    runner(
        [
            "systemctl", "disable", "--now",
            "allscan-reimagined-standard-bridge-status.service",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for path in (
        systemd_dir / "allscan-reimagined-standard-bridge-status.service",
        sbin_dir / "allscan-reimagined-standard-bridge-status",
        sbin_dir / "asr_bridge_status.py",
    ):
        path.unlink(missing_ok=True)
    shutil.rmtree(run_dir, ignore_errors=True)
    rewrite_owned_policy_without(
        tmpfiles_path,
        ("/run/allscan-reimagined-standard-bridge-status",),
    )
    runner(
        ["systemctl", "daemon-reload"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reconcile_bridge_lifecycle_wiring(
    release_root: Path,
    *,
    systemd_dir: Path = Path("/etc/systemd/system"),
    sbin_dir: Path = Path("/usr/local/sbin"),
    sudoers_path: Path = Path("/etc/sudoers.d/allscan-reimagined"),
    sound_dir: Path = Path("/usr/share/asterisk/sounds/en/custom/allscan-reimagined"),
    runner: Any = subprocess.run,
) -> None:
    """Retire executables/units when the restored release predates lifecycle support.

    Ownership manifests and tombstones are intentionally preserved. They are
    protected state needed if a later ASR version resumes an incomplete delete.
    """
    scripts = release_root / "scripts"
    lifecycle_present = (scripts / "asr-bridge-lifecycle.py").is_file()
    startup_present = (scripts / "asr-startup-bridge-summary.py").is_file()
    changed = False
    if not startup_present:
        runner(
            ["systemctl", "disable", "--now", "allscan-reimagined-startup-bridge-summary.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (systemd_dir / "allscan-reimagined-startup-bridge-summary.service").unlink(missing_ok=True)
        (sbin_dir / "allscan-reimagined-startup-bridge-summary").unlink(missing_ok=True)
        marker = sound_dir / ".asr-startup-summary-owner.json"
        expected_marker = {
            "schema": 1,
            "createdBy": "allscan-reimagined",
            "purpose": "startup-bridge-summary",
        }
        try:
            payload = json.loads(marker.read_text(encoding="utf-8")) \
                if sound_dir.is_dir() and not sound_dir.is_symlink() \
                and marker.is_file() and not marker.is_symlink() else None
        except (OSError, ValueError):
            payload = None
        if payload == expected_marker:
            target = sound_dir / "startup-bridge-summary.gsm"
            allowed = {marker, target}
            contents = set(sound_dir.iterdir())
            if contents.issubset(allowed) and (not target.exists() or (target.is_file() and not target.is_symlink())):
                target.unlink(missing_ok=True)
                marker.unlink()
                sound_dir.rmdir()
        changed = True
    if not lifecycle_present:
        (sbin_dir / "allscan-reimagined-bridge-lifecycle").unlink(missing_ok=True)
        rewrite_owned_policy_without(
            sudoers_path,
            ("allscan-reimagined-bridge-lifecycle",),
            validate_sudoers=sudoers_path == Path("/etc/sudoers.d/allscan-reimagined"),
        )
        changed = True
    if changed:
        runner(
            ["systemctl", "daemon-reload"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def ensure_manager_wiring() -> None:
    helper = Path("/usr/local/sbin/allscan-reimagined-rollback")
    if helper.is_file():
        os.chown(helper, 0, 0)
        os.chmod(helper, 0o755)
    web_group = next(
        (
            name
            for name in ("www-data", "apache", "http")
            if subprocess.run(
                ["getent", "group", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        ),
        None,
    )
    if not web_group:
        raise RollbackError("Web-server group not found")
    sudoers = Path("/etc/sudoers.d/allscan-reimagined")
    previous = sudoers.read_bytes() if sudoers.is_file() else b""
    lines = previous.decode("utf-8", errors="strict").splitlines()
    rollback_prefix = f"{web_group} ALL=(root) NOPASSWD: {helper} "
    filtered_lines = [line for line in lines if not line.startswith(rollback_prefix)]
    changed = filtered_lines != lines
    lines = filtered_lines
    required = (
        f"{web_group} ALL=(root) NOPASSWD: {helper} --list-json",
        f"{web_group} ALL=(root) NOPASSWD: {helper} --queue-rollback [0-9]*",
        f"{web_group} ALL=(root) NOPASSWD: {helper} --status-json [0-9]*",
    )
    for line in required:
        if line not in lines:
            lines.append(line)
            changed = True
    if not changed:
        return
    temporary = sudoers.with_name(f".{sudoers.name}.rollback-{os.getpid()}")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o440)
    result = subprocess.run(
        ["visudo", "-cf", str(temporary)], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise RollbackError(f"Rollback sudoers validation failed: {result.stderr.strip()}")
    os.replace(temporary, sudoers)


def rollback(backup_id: str, *, confirm_legacy_overlay: bool = False) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RollbackError("Rollback must run as root")
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise RollbackError("Rollback ID must exactly match YYYYMMDD-HHMMSS")
    backup_dir = BACKUP_ROOT / backup_id
    if backup_dir.parent != BACKUP_ROOT or not backup_dir.is_dir():
        raise RollbackError("Requested rollback backup was not found")
    manifest = load_valid_manifest(backup_dir)
    schema = int(manifest["schema"])
    if schema == 1 and not confirm_legacy_overlay:
        raise RollbackError(
            "Legacy schema-1 rollback restores the old in-place /allscan overlay. "
            "Repeat with --confirm-legacy-overlay to confirm this architecture change."
        )
    allowed = {item["id"] for item in list_backups()}
    if backup_id not in allowed:
        raise RollbackError("Requested backup is not one of the five available previous versions")

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        web_root = find_webroot()
        active_web = web_root / ("asr" if schema == 2 else "allscan")
        old_master = CURRENT_LINK.resolve(strict=True)
        if RELEASE_ROOT.resolve() not in old_master.parents:
            raise RollbackError("Current ASR master is outside the release directory")
        installed_reapply = Path("/usr/local/sbin/allscan-reimagined-reapply")
        if not installed_reapply.is_file():
            raise RollbackError("Stable ASR reapply manager is missing")
        safety_backup = create_safety_backup(web_root)
        stable_fd, stable_name = tempfile.mkstemp(
            prefix="asr-rollback-reapply-", dir=str(LOCK_PATH.parent)
        )
        os.close(stable_fd)
        stable_reapply = Path(stable_name)
        shutil.copy2(installed_reapply, stable_reapply)
        os.chmod(stable_reapply, 0o700)
        stable_protected_config_helper: Path | None = None
        installed_protected_config_helper = (
            old_master / "scripts/asr-protected-config-metadata.py"
        )
        if installed_protected_config_helper.is_file():
            helper_fd, helper_name = tempfile.mkstemp(
                prefix="asr-rollback-protected-config-", dir=str(LOCK_PATH.parent)
            )
            os.close(helper_fd)
            stable_protected_config_helper = Path(helper_name)
            shutil.copy2(
                installed_protected_config_helper,
                stable_protected_config_helper,
            )
            os.chmod(stable_protected_config_helper, 0o700)
        version = str(manifest["pre_update_version"])
        safe_version = version.replace("/", "_")
        release_target = RELEASE_ROOT / f"{safe_version}.rollback-{backup_id}"
        web_stage_parent = Path(
            tempfile.mkdtemp(prefix=".asr-rollback-web-", dir=str(web_root))
        )
        release_stage_parent = Path(
            tempfile.mkdtemp(prefix=".asr-rollback-release-", dir=str(RELEASE_ROOT))
        )
        web_extract = web_stage_parent / "payload"
        release_extract = release_stage_parent / "payload"
        old_web = web_root / f".asr-rollback-previous-{os.getpid()}"
        legacy_asr_old = web_root / f".asr-legacy-rollback-previous-{os.getpid()}"
        link_temp = CURRENT_LINK.parent / f".current.rollback-{os.getpid()}"
        release_installed = False
        active_moved = False
        web_swapped = False
        legacy_asr_moved = False
        link_swapped = False
        reapply_units_to_restore: list[str] = []
        previous_signal_handlers = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }
        def rollback_interrupted(signum: int, _frame: Any) -> None:
            raise RollbackError(f"Rollback interrupted by signal {signum}")
        signal.signal(signal.SIGTERM, rollback_interrupted)
        signal.signal(signal.SIGINT, rollback_interrupted)
        try:
            webroot_filename = (
                "asr-webroot.tar.gz" if schema == 2 else "allscan-webroot.tar.gz"
            )
            webroot_kind = "webroot" if schema == 2 else "legacy-webroot"
            webroot_name = "asr" if schema == 2 else "allscan"
            extract_archive(backup_dir / webroot_filename, web_extract, webroot_kind)
            staged_web = web_extract / webroot_name
            if not staged_web.is_dir():
                raise RollbackError("Staged webroot is incomplete")
            runtime_source = runtime_source_for_schema(web_root, active_web, schema)
            copy_preserved_runtime(runtime_source, staged_web)
            staged_backend = detect_backend(staged_web / "include/common.php")
            current_backend = detect_backend(web_root / "allscan/include/common.php")
            backend_version = staged_backend if schema == 1 else current_backend
            if backend_version == "unknown" or (
                schema == 2 and staged_backend != current_backend
            ):
                raise RollbackError(
                    "Selected backup does not match the installed AllScan backend"
                )

            extract_archive(
                backup_dir / "asr-release.tar.gz", release_extract, "release"
            )
            for required in REQUIRED_RELEASE_FILES:
                if not (release_extract / required).is_file():
                    raise RollbackError(f"Staged release is missing {required}")
            staged_version = detect_version(release_extract / "server/asr-api.php")
            if staged_version != version:
                raise RollbackError("Staged release version does not match its manifest")
            compat_dir = release_extract / "compat" / f"allscan-{backend_version}"
            if not (compat_dir / "include/common.php").is_file():
                raise RollbackError(
                    f"Selected release has no exact compatibility layer for {backend_version}"
                )
            if schema == 1 and "ASR_WEB_DIR" in (
                release_extract / "scripts/asr-reapply.sh"
            ).read_text(encoding="utf-8", errors="replace"):
                raise RollbackError(
                    "Schema-1 backup does not contain a legacy in-place reapply manager"
                )
            if release_target.exists():
                raise RollbackError("Rollback release target already exists")
            os.replace(release_extract, release_target)
            release_installed = True

            for unit in (
                "allscan-reimagined-reapply.path",
                "allscan-reimagined-reapply.timer",
            ):
                if subprocess.run(
                    ["systemctl", "is-active", "--quiet", unit],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode == 0:
                    reapply_units_to_restore.append(unit)
            subprocess.run(
                [
                    "systemctl",
                    "stop",
                    "allscan-reimagined-reapply.path",
                    "allscan-reimagined-reapply.timer",
                    "allscan-reimagined-reapply.service",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            os.symlink(release_target, link_temp)
            os.replace(link_temp, CURRENT_LINK)
            link_swapped = True
            if schema == 1 and (web_root / "asr").exists():
                os.replace(web_root / "asr", legacy_asr_old)
                legacy_asr_moved = True
            os.replace(active_web, old_web)
            active_moved = True
            os.replace(staged_web, active_web)
            web_swapped = True
            shutil.rmtree(web_stage_parent, ignore_errors=True)

            if schema == 2:
                environment = rollback_reapply_environment(
                    stable_protected_config_helper
                )
                run_checked(["bash", str(stable_reapply)], env=environment)
            else:
                environment = rollback_reapply_environment()
                run_checked(
                    ["bash", str(release_target / "scripts/asr-reapply.sh")],
                    env=environment,
                )
            reconcile_ysf_net_wiring(release_target)
            reconcile_next_digital_wiring(release_target)
            reconcile_fixed_bridge_recovery_wiring(release_target)
            reconcile_standard_bridge_status_wiring(release_target)
            reconcile_bridge_lifecycle_wiring(release_target)
            ensure_manager_wiring()
            if detect_version(active_web / "asr-api.php") != version:
                raise RollbackError("Served ASR version did not match after rollback")
            run_checked(["php", "-l", str(active_web / "asr-api.php")])
            if shutil.which("apache2ctl"):
                run_checked(["apache2ctl", "configtest"])
            run_checked(["systemctl", "reload", "apache2"])
            shutil.rmtree(old_web, ignore_errors=True)
            if legacy_asr_moved:
                shutil.rmtree(legacy_asr_old, ignore_errors=True)
            if schema == 1:
                # The legacy release's own manager has restored its old
                # /allscan watcher. Do not restart the schema-2 /asr units.
                reapply_units_to_restore.clear()
            prune_backups()
            return {
                "ok": True,
                "version": version,
                "label": str(manifest.get("pre_update_label") or version_label(version)),
                "backup_id": backup_id,
                "safety_backup_id": safety_backup.name,
                "schema": schema,
            }
        except Exception:
            if web_swapped:
                failed_web = web_root / f".asr-rollback-failed-{os.getpid()}"
                if active_web.exists():
                    os.replace(active_web, failed_web)
                if old_web.exists():
                    os.replace(old_web, active_web)
                shutil.rmtree(failed_web, ignore_errors=True)
            elif active_moved and old_web.exists():
                os.replace(old_web, active_web)
            if legacy_asr_moved and legacy_asr_old.exists():
                if (web_root / "asr").exists():
                    shutil.rmtree(web_root / "asr", ignore_errors=True)
                os.replace(legacy_asr_old, web_root / "asr")
            if link_swapped:
                if link_temp.exists() or link_temp.is_symlink():
                    link_temp.unlink()
                os.symlink(old_master, link_temp)
                os.replace(link_temp, CURRENT_LINK)
            if release_installed:
                shutil.rmtree(release_target, ignore_errors=True)
            if stable_reapply.is_file():
                environment = rollback_reapply_environment(
                    stable_protected_config_helper
                )
                subprocess.run(
                    ["bash", str(stable_reapply)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=environment,
                )
            try:
                ensure_manager_wiring()
            except (RollbackError, OSError):
                pass
            subprocess.run(
                ["systemctl", "reload", "apache2"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            prune_backups()
            raise
        finally:
            if link_temp.exists() or link_temp.is_symlink():
                link_temp.unlink()
            shutil.rmtree(web_stage_parent, ignore_errors=True)
            if release_stage_parent.exists():
                shutil.rmtree(release_stage_parent, ignore_errors=True)
            stable_reapply.unlink(missing_ok=True)
            if stable_protected_config_helper is not None:
                stable_protected_config_helper.unlink(missing_ok=True)
            if reapply_units_to_restore:
                subprocess.run(
                    ["systemctl", "start", *reapply_units_to_restore],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            for signum, handler in previous_signal_handlers.items():
                signal.signal(signum, handler)


def self_test() -> None:
    def expect_rollback_error(action: Any, message: str) -> None:
        try:
            action()
        except RollbackError:
            return
        raise AssertionError(message)

    with tempfile.TemporaryDirectory(prefix="asr-rollback-self-test-") as temporary:
        root = Path(temporary)
        stable_helper = root / "stable-protected-config-helper.py"
        rollback_environment = rollback_reapply_environment(stable_helper)
        assert rollback_environment["ASR_ROLLBACK_MODE"] == "1"
        assert rollback_environment["ASR_INSTALL_LOCK_HELD"] == "1"
        assert rollback_environment["ASR_PROTECTED_CONFIG_HELPER"] == str(stable_helper)
        assert "ASR_PROTECTED_CONFIG_HELPER" not in rollback_reapply_environment()
        failure_message = rollback_failure_message(
            RollbackError("missing\nprotected-config helper")
        )
        assert "previous installation was restored" in failure_message
        assert "missing protected-config helper" in failure_message
        legacy_release = root / "legacy-release"
        legacy_release.mkdir()
        systemd_dir = root / "systemd"
        sbin_dir = root / "sbin"
        systemd_dir.mkdir()
        sbin_dir.mkdir()
        fixed_paths = (
            systemd_dir / "allscan-reimagined-fixed-bridge-recovery.service",
            systemd_dir / "allscan-reimagined-fixed-bridge-recovery.timer",
            sbin_dir / "allscan-reimagined-fixed-bridge-recovery",
        )
        for path in fixed_paths:
            path.write_text("test\n", encoding="utf-8")
        reconciliation_commands: list[list[str]] = []

        def record_reconciliation(command: list[str], **_: Any) -> None:
            reconciliation_commands.append(command)

        reconcile_fixed_bridge_recovery_wiring(
            legacy_release,
            systemd_dir=systemd_dir,
            sbin_dir=sbin_dir,
            runner=record_reconciliation,
        )
        assert not any(path.exists() for path in fixed_paths)
        assert reconciliation_commands[0][0:3] == ["systemctl", "disable", "--now"]
        assert reconciliation_commands[-1] == ["systemctl", "daemon-reload"]

        current_release = root / "current-release"
        (current_release / "scripts").mkdir(parents=True)
        (current_release / "scripts/asr-fixed-bridge-recovery.py").write_text(
            "test\n", encoding="utf-8"
        )
        reconciliation_commands.clear()
        reconcile_fixed_bridge_recovery_wiring(
            current_release,
            systemd_dir=systemd_dir,
            sbin_dir=sbin_dir,
            runner=record_reconciliation,
        )
        assert reconciliation_commands == []

        standard_systemd = root / "standard-systemd"
        standard_sbin = root / "standard-sbin"
        standard_run = root / "standard-run"
        standard_systemd.mkdir()
        standard_sbin.mkdir()
        standard_run.mkdir()
        standard_paths = (
            standard_systemd / "allscan-reimagined-standard-bridge-status.service",
            standard_sbin / "allscan-reimagined-standard-bridge-status",
            standard_sbin / "asr_bridge_status.py",
            standard_run / "bridge-live.json",
        )
        for path in standard_paths:
            path.write_text("test\n", encoding="utf-8")
        standard_tmpfiles = root / "standard-tmpfiles.conf"
        standard_tmpfiles.write_text(
            "d /run/allscan-reimagined-standard-bridge-status 0755 root root -\n"
            "d /run/keep 0755 root root -\n",
            encoding="utf-8",
        )
        reconciliation_commands.clear()
        reconcile_standard_bridge_status_wiring(
            legacy_release,
            systemd_dir=standard_systemd,
            sbin_dir=standard_sbin,
            run_dir=standard_run,
            tmpfiles_path=standard_tmpfiles,
            runner=record_reconciliation,
        )
        assert not any(path.exists() for path in standard_paths)
        assert not standard_run.exists()
        assert "standard-bridge-status" not in standard_tmpfiles.read_text(encoding="utf-8")
        assert "/run/keep" in standard_tmpfiles.read_text(encoding="utf-8")
        assert reconciliation_commands[0][0:3] == ["systemctl", "disable", "--now"]
        assert reconciliation_commands[-1] == ["systemctl", "daemon-reload"]

        (current_release / "scripts/asr_bridge_status.py").write_text(
            "test\n", encoding="utf-8"
        )
        reconciliation_commands.clear()
        reconcile_standard_bridge_status_wiring(
            current_release,
            systemd_dir=standard_systemd,
            sbin_dir=standard_sbin,
            run_dir=standard_run,
            tmpfiles_path=standard_tmpfiles,
            runner=record_reconciliation,
        )
        assert reconciliation_commands == []
        lifecycle_systemd = root / "lifecycle-systemd"
        lifecycle_sbin = root / "lifecycle-sbin"
        lifecycle_systemd.mkdir()
        lifecycle_sbin.mkdir()
        lifecycle_unit = lifecycle_systemd / "allscan-reimagined-startup-bridge-summary.service"
        lifecycle_helper = lifecycle_sbin / "allscan-reimagined-bridge-lifecycle"
        startup_helper = lifecycle_sbin / "allscan-reimagined-startup-bridge-summary"
        for path in (lifecycle_unit, lifecycle_helper, startup_helper):
            path.write_text("test\n", encoding="utf-8")
        lifecycle_sudoers = root / "lifecycle-sudoers"
        lifecycle_sudoers.write_text(
            "www-data ALL=(root) NOPASSWD: /usr/local/sbin/allscan-reimagined-bridge-lifecycle preview-all\n"
            "www-data ALL=(root) NOPASSWD: /usr/local/sbin/keep\n",
            encoding="utf-8",
        )
        lifecycle_commands: list[list[str]] = []
        lifecycle_sound = root / "lifecycle-sounds"
        lifecycle_sound.mkdir()
        (lifecycle_sound / ".asr-startup-summary-owner.json").write_text(
            json.dumps({
                "schema": 1, "createdBy": "allscan-reimagined",
                "purpose": "startup-bridge-summary",
            }), encoding="utf-8",
        )
        (lifecycle_sound / "startup-bridge-summary.gsm").write_text("test\n", encoding="utf-8")
        preserved_state = root / "lifecycle-state"
        for name in ("bridge-ownership", "bridge-tombstones", "bridge-deletion-queue"):
            directory = preserved_state / name
            directory.mkdir(parents=True)
            (directory / "evidence.json").write_text("{}\n", encoding="utf-8")

        def record_lifecycle(command: list[str], **_: Any) -> None:
            lifecycle_commands.append(command)

        reconcile_bridge_lifecycle_wiring(
            legacy_release,
            systemd_dir=lifecycle_systemd,
            sbin_dir=lifecycle_sbin,
            sudoers_path=lifecycle_sudoers,
            sound_dir=lifecycle_sound,
            runner=record_lifecycle,
        )
        assert not any(path.exists() for path in (lifecycle_unit, lifecycle_helper, startup_helper))
        assert "bridge-lifecycle" not in lifecycle_sudoers.read_text(encoding="utf-8")
        assert not lifecycle_sound.exists()
        assert all((preserved_state / name / "evidence.json").exists()
                   for name in ("bridge-ownership", "bridge-tombstones", "bridge-deletion-queue"))
        assert lifecycle_commands[-1] == ["systemctl", "daemon-reload"]
        tmpfiles_policy = root / "allscan-reimagined.conf"
        tmpfiles_policy.write_text(
            "d /run/allscan-reimagined 1775 root www-data -\n"
            "d /run/allscan-reimagined-p25-bridge-control 2750 root www-data -\n"
            "d /run/allscan-reimagined-m17 0755 root root -\n",
            encoding="utf-8",
        )
        rewrite_owned_policy_without(
            tmpfiles_policy,
            (
                "/run/allscan-reimagined-p25-bridge-control",
                "/run/allscan-reimagined-m17",
            ),
        )
        assert tmpfiles_policy.read_text(encoding="utf-8") == (
            "d /run/allscan-reimagined 1775 root www-data -\n"
        )
        backups = root / "backups"
        for index, version in enumerate(
            ("1.0.0-beta.6.0", "1.0.0-beta.5.11", "1.0.0-beta.5.10")
        ):
            backup_dir = backups / f"2026072{3 - index}-120000"
            backup_dir.mkdir(parents=True)
            web_source = root / f"web-{index}" / "asr"
            release_source = root / f"release-{index}"
            web_source.mkdir(parents=True)
            (web_source / "index.html").write_text("ok\n", encoding="utf-8")
            for required in REQUIRED_RELEASE_FILES:
                target = release_source / required
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    (
                        f"<?php const ASR_VERSION = '{version}';\n"
                        if required == "server/asr-api.php"
                        else "test\n"
                    ),
                    encoding="utf-8",
                )
            with tarfile.open(backup_dir / "asr-webroot.tar.gz", "w:gz") as archive:
                archive.add(web_source, arcname="asr")
            with tarfile.open(backup_dir / "asr-release.tar.gz", "w:gz") as archive:
                for child in release_source.iterdir():
                    archive.add(child, arcname=child.name)
            write_manifest(backup_dir, version)
            assert (backup_dir / "manifest.json").stat().st_mode & 0o777 == 0o600

        listed = list_backups(backups, current="1.0.0-beta.6.0")
        assert [item["version"] for item in listed] == [
            "1.0.0-beta.5.11",
            "1.0.0-beta.5.10",
        ]
        assert version_label("1.0.0-beta.5.11") == "v1.0.0 Beta 5.11"
        assert set(listed[0]) == {"id", "version", "label", "createdAt", "path"}

        # Fast listing deliberately avoids hashing large archives, but the
        # mutating rollback preflight must reject same-size tampering.
        tamper_root = root / "tamper-backups"
        tampered = tamper_root / "20260721-120000"
        shutil.copytree(backups / "20260721-120000", tampered)
        tampered_archive = tampered / "asr-release.tar.gz"
        tampered_bytes = bytearray(tampered_archive.read_bytes())
        tampered_bytes[len(tampered_bytes) // 2] ^= 1
        tampered_archive.write_bytes(tampered_bytes)
        assert list_backups(
            tamper_root, current="1.0.0-beta.5.11"
        ), "same-size damage should remain cheap to list"
        expect_rollback_error(
            lambda: load_valid_manifest(tampered),
            "same-size archive tampering passed rollback preflight",
        )

        damaged = backups / "20260722-120000" / "asr-release.tar.gz"
        damaged.write_bytes(b"damaged")
        listed = list_backups(backups, current="1.0.0-beta.6.0")
        assert [item["version"] for item in listed] == ["1.0.0-beta.5.10"]

        expect_rollback_error(
            lambda: safe_member_name("../escape"),
            "parent traversal was accepted",
        )
        linked_release = root / "linked-release.tar.gz"
        with tarfile.open(linked_release, "w:gz") as archive:
            link = tarfile.TarInfo("web/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "index.html"
            archive.addfile(link)
        expect_rollback_error(
            lambda: validate_archive(linked_release, "release"),
            "release archive symbolic link was accepted",
        )

        absolute_release = root / "absolute-release.tar.gz"
        with tarfile.open(absolute_release, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("/etc/escape"))
        expect_rollback_error(
            lambda: validate_archive(absolute_release, "release"),
            "absolute archive member was accepted",
        )

        hardlinked_release = root / "hardlinked-release.tar.gz"
        with tarfile.open(hardlinked_release, "w:gz") as archive:
            link = tarfile.TarInfo("web/hard-link")
            link.type = tarfile.LNKTYPE
            link.linkname = "web/index.html"
            archive.addfile(link)
        expect_rollback_error(
            lambda: validate_archive(hardlinked_release, "release"),
            "release archive hard link was accepted",
        )

        special_release = root / "special-release.tar.gz"
        with tarfile.open(special_release, "w:gz") as archive:
            fifo = tarfile.TarInfo("web/fifo")
            fifo.type = tarfile.FIFOTYPE
            archive.addfile(fifo)
        expect_rollback_error(
            lambda: validate_archive(special_release, "release"),
            "release archive special file was accepted",
        )

        outside_webroot = root / "outside-webroot.tar.gz"
        with tarfile.open(outside_webroot, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("outside/index.html"))
        expect_rollback_error(
            lambda: validate_archive(outside_webroot, "webroot"),
            "webroot archive member outside allscan/ was accepted",
        )

        duplicate_release = root / "duplicate-release.tar.gz"
        with tarfile.open(duplicate_release, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("web/index.html"))
            archive.addfile(tarfile.TarInfo("web/index.html"))
        expect_rollback_error(
            lambda: validate_archive(duplicate_release, "release"),
            "duplicate archive member was accepted",
        )

        favorites_webroot = root / "favorites-webroot.tar.gz"
        with tarfile.open(favorites_webroot, "w:gz") as archive:
            directory = tarfile.TarInfo("asr")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            favorite = tarfile.TarInfo("asr/favorites.ini")
            favorite.type = tarfile.SYMTYPE
            favorite.linkname = "/etc/allscan/favorites.ini"
            archive.addfile(favorite)
        validate_archive(favorites_webroot, "webroot")

        safety_source_root = root / "safety-source"
        safety_source = safety_source_root / "asr"
        safety_source.mkdir(parents=True)
        (safety_source / "index.html").write_text("current\n", encoding="utf-8")
        (safety_source / "favorites.ini").symlink_to(
            "/etc/allscan/favorites.ini"
        )
        for name in (
            "astdb.txt",
            "astdb.txt.before-local-labels",
            "astdb.txt.bak-local-labels-20260809-120000",
        ):
            (safety_source / name).symlink_to("/var/lib/asterisk/astdb.txt")

        def create_test_safety_archive(destination: Path) -> None:
            environment = dict(os.environ)
            environment["COPYFILE_DISABLE"] = "1"
            subprocess.run(
                [
                    "tar",
                    *safety_webroot_exclude_arguments(),
                    "-czf",
                    str(destination),
                    "-C",
                    str(safety_source_root),
                    "asr",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

        generated_aliases = root / "generated-aliases-webroot.tar.gz"
        create_test_safety_archive(generated_aliases)
        validate_archive(generated_aliases, "webroot")
        with tarfile.open(generated_aliases, "r:gz") as archive:
            generated_names = {
                str(PurePosixPath(member.name)).removeprefix("./")
                for member in archive.getmembers()
            }
        assert "asr/favorites.ini" in generated_names
        assert not any(
            name == "asr/astdb.txt" or name.startswith("asr/astdb.txt.")
            for name in generated_names
        ), "generated ASTDB aliases entered the safety backup"

        (safety_source / "operator-created-link").symlink_to("/etc/passwd")
        arbitrary_link = root / "arbitrary-link-webroot.tar.gz"
        create_test_safety_archive(arbitrary_link)
        with tarfile.open(arbitrary_link, "r:gz") as archive:
            arbitrary_members = {
                str(PurePosixPath(member.name)).removeprefix("./"): member
                for member in archive.getmembers()
            }
        assert arbitrary_members["asr/operator-created-link"].issym()
        assert arbitrary_members["asr/operator-created-link"].linkname == "/etc/passwd"
        expect_rollback_error(
            lambda: validate_archive(arbitrary_link, "webroot"),
            "arbitrary webroot symbolic link was accepted",
        )

        legacy_root = root / "legacy" / "20260720-120000"
        legacy_root.mkdir(parents=True)
        legacy_source = root / "legacy-source" / "allscan"
        legacy_source.mkdir(parents=True)
        (legacy_source / "index.html").write_text("legacy\n", encoding="utf-8")
        with tarfile.open(legacy_root / "allscan-webroot.tar.gz", "w:gz") as archive:
            archive.add(legacy_source, arcname="allscan")
        shutil.copy2(
            backups / "20260721-120000" / "asr-release.tar.gz",
            legacy_root / "asr-release.tar.gz",
        )
        legacy_manifest = write_manifest(legacy_root, "1.0.0-beta.5.9")
        assert legacy_manifest["schema"] == 1
        validate_archive(legacy_root / "allscan-webroot.tar.gz", "legacy-webroot")
        expect_rollback_error(
            lambda: validate_archive(
                legacy_root / "allscan-webroot.tar.gz", "webroot"
            ),
            "legacy allscan/ archive passed schema-2 validation",
        )

        active = root / "active"
        staged = root / "staged"
        active.mkdir()
        staged.mkdir()
        for name in RUNTIME_FILES:
            (active / name).write_text(f"{name}\n", encoding="utf-8")
        for name in RUNTIME_DIRS:
            (active / name).mkdir()
            (active / name / "preserved.txt").write_text(
                f"{name}\n", encoding="utf-8"
            )
            (staged / name).mkdir()
            (staged / name / "obsolete.txt").write_text(
                "replace me\n", encoding="utf-8"
            )
        for name in STABLE_WEB_FILES:
            source = active / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("stable\n", encoding="utf-8")
        copy_preserved_runtime(active, staged)
        for name in RUNTIME_FILES:
            assert (staged / name).read_text(encoding="utf-8") == f"{name}\n"
        for name in RUNTIME_DIRS:
            assert (staged / name / "preserved.txt").is_file()
            assert not (staged / name / "obsolete.txt").exists()
        for name in STABLE_WEB_FILES:
            assert (staged / name).read_text(encoding="utf-8") == "stable\n"
        assert runtime_source_for_schema(root, root / "allscan", 1) == root / "asr"
        assert runtime_source_for_schema(root, root / "asr", 2) == root / "asr"

        retention_root = root / "retention"
        retention_root.mkdir()
        for index in range(12):
            (retention_root / f"20260723-1200{index:02d}").mkdir()
        (retention_root / "keep-unrelated").mkdir()
        prune_backups(retention_root, retention=10)
        retained = sorted(
            item.name
            for item in retention_root.iterdir()
            if BACKUP_ID_RE.fullmatch(item.name)
        )
        assert retained == [
            f"20260723-1200{index:02d}" for index in range(2, 12)
        ]
        assert (retention_root / "keep-unrelated").is_dir()

        first_install = root / "first-install" / "20260724-120000"
        first_install.mkdir(parents=True)
        first_manifest = write_manifest(first_install, "not-installed")
        assert first_manifest["rollback_eligible"] is False
        assert list_backups(first_install.parent, current="not-installed") == []

        invalid_backup = root / "invalid-backup-name"
        invalid_backup.mkdir()
        expect_rollback_error(
            lambda: write_manifest(invalid_backup, "1.0.0-beta.5.11"),
            "invalid backup directory name was accepted",
        )

        legacy_api = root / "legacy-asr-api.php"
        legacy_api.write_text(
            "<?php const ASR_VERSION_LABEL = 'v1.0.0 Beta 5.11';\n",
            encoding="utf-8",
        )
        assert detect_version(legacy_api) == "1.0.0-beta.5.11"
        invalid_api = root / "invalid-asr-api.php"
        invalid_api.write_text(
            "<?php const ASR_VERSION = '../../escape';\n", encoding="utf-8"
        )
        assert detect_version(invalid_api) == "unknown"
    print("ASR rollback self-test: ok")


def main() -> int:
    if sys.argv[1:] == ["--list-json"]:
        sys.argv[1:] = ["list", "--json"]
    elif len(sys.argv[1:]) == 2 and sys.argv[1] == "--rollback":
        sys.argv[1:] = ["rollback", sys.argv[2]]
    elif len(sys.argv[1:]) == 2 and sys.argv[1] == "--queue-rollback":
        sys.argv[1:] = ["queue-rollback", sys.argv[2]]
    elif len(sys.argv[1:]) == 2 and sys.argv[1] == "--status-json":
        sys.argv[1:] = ["status-json", sys.argv[2]]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List valid previous versions")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON")
    rollback_parser = subparsers.add_parser("rollback", help="Roll back to a backup ID")
    rollback_parser.add_argument("backup_id")
    rollback_parser.add_argument(
        "--confirm-legacy-overlay",
        action="store_true",
        help="Confirm restoring a schema-1 backup into /allscan",
    )
    queue_parser = subparsers.add_parser(
        "queue-rollback", help="Queue a managed rollback job"
    )
    queue_parser.add_argument("backup_id")
    status_parser = subparsers.add_parser(
        "status-json", help="Read a managed rollback job status"
    )
    status_parser.add_argument("job_id")
    run_job_parser = subparsers.add_parser("run-job", help=argparse.SUPPRESS)
    run_job_parser.add_argument("job_id")
    finalize_parser = subparsers.add_parser(
        "finalize-backup", help=argparse.SUPPRESS
    )
    finalize_parser.add_argument("backup_dir")
    finalize_parser.add_argument("version")
    detect_parser = subparsers.add_parser("detect-version", help=argparse.SUPPRESS)
    detect_parser.add_argument("api_file")
    subparsers.add_parser("self-test", help="Run non-mutating internal tests")
    args = parser.parse_args()

    try:
        if args.command == "self-test":
            self_test()
            return 0
        if args.command == "list":
            if os.geteuid() != 0:
                raise RollbackError("Backup listing must run as root")
            backups = list_backups()
            payload = {
                "ok": True,
                "current_version": current_version(),
                "backups": backups,
            }
            if args.json:
                print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            else:
                for item in backups:
                    print(f"{item['id']}\t{item['label']}\t{item['createdAt']}")
            return 0
        if args.command == "finalize-backup":
            if os.geteuid() != 0:
                raise RollbackError("Backup finalization must run as root")
            backup_dir = Path(args.backup_dir)
            if backup_dir.parent != BACKUP_ROOT or not backup_dir.is_dir():
                raise RollbackError("Backup directory is outside the approved root")
            write_manifest(backup_dir, args.version)
            return 0
        if args.command == "detect-version":
            print(detect_version(Path(args.api_file)))
            return 0
        if args.command == "rollback":
            result = rollback(
                args.backup_id,
                confirm_legacy_overlay=args.confirm_legacy_overlay,
            )
            print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            return 0
        if args.command == "queue-rollback":
            result = queue_rollback(args.backup_id)
            print(json.dumps(result, separators=(",", ":"), sort_keys=True))
            return 0
        if args.command == "status-json":
            if os.geteuid() != 0:
                raise RollbackError("Rollback status must run as root")
            payload = load_job(args.job_id)
            print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            return 0
        if args.command == "run-job":
            return run_job(args.job_id)
    except BlockingIOError:
        print("ERROR: Another ASR rollback is already running.", file=sys.stderr)
        return 1
    except (RollbackError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
