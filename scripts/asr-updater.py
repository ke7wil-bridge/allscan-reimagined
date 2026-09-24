#!/usr/bin/env python3
"""Fixed-operation, root-owned ASR update worker. No caller-supplied paths or URLs."""

from __future__ import annotations

import datetime as dt
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

RELEASE_ROOT = Path("/opt/allscan-reimagined/current")
WEB_ROOT = Path("/var/www/html" if Path("/var/www/html/allscan").is_dir() else "/srv/http")
JOB_ROOT = Path("/run/allscan-reimagined/update-jobs")
GATE = Path("/run/lock/allscan-reimagined-updater.lock")
INSTALL_LOCK = Path("/run/lock/allscan-reimagined-rollback.lock")
ROLLBACK_JOBS = Path("/run/allscan-reimagined/rollback-jobs")
BACKUPS = Path("/root/allscan-reimagined-backups")
WORK_ROOT = Path("/var/lib/allscan-reimagined")
API = "https://api.github.com/repos/ke7wil-bridge/allscan-reimagined/releases?per_page=20"
JOB_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.[0-9]+(?:\.[0-9]+)*)?$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
STATES = {"queued", "preflight", "downloading", "verifying", "staging",
          "backup", "installing", "health", "restoring", "complete", "failed"}
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 768 * 1024 * 1024


class UpdateError(Exception):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def owned_jobs() -> None:
    group = next((grp.getgrnam(g).gr_gid for g in ("www-data", "apache", "http")
                  if group_exists(g)), -1)
    JOB_ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    meta = JOB_ROOT.lstat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0:
        raise UpdateError("Update status directory is not root-owned")
    os.chmod(JOB_ROOT, 0o750)
    if group >= 0:
        os.chown(JOB_ROOT, 0, group)


def group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def status(job: str, state: str, **extra: str) -> dict:
    if not JOB_RE.fullmatch(job) or state not in STATES:
        raise UpdateError("Invalid job state")
    # Explicit allowlist: never serialize subprocess output, paths, or config.
    data = {"ok": state != "failed", "jobId": job, "state": state, "updatedAt": now()}
    for key in ("currentVersion", "availableVersion", "message"):
        if key in extra:
            value = extra[key]
            if key.endswith("Version") and not VERSION_RE.fullmatch(value):
                raise UpdateError("Invalid version in status")
            if key == "message" and value not in MESSAGES:
                raise UpdateError("Invalid status message")
            data[key] = value
    path = JOB_ROOT / (job + ".json")
    fd, temp = tempfile.mkstemp(prefix=".update-", dir=JOB_ROOT)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o640)
        if group_exists("www-data"):
            os.chown(temp, 0, grp.getgrnam("www-data").gr_gid)
        elif group_exists("apache"):
            os.chown(temp, 0, grp.getgrnam("apache").gr_gid)
        elif group_exists("http"):
            os.chown(temp, 0, grp.getgrnam("http").gr_gid)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
    return data


MESSAGES = {
    "Update complete.", "Update failed; previous installation restored.",
    "Update failed; recovery needs attention.", "Update could not start.",
}


def read_status(job: str) -> dict:
    if not JOB_RE.fullmatch(job):
        raise UpdateError("Invalid job ID")
    try:
        data = json.loads((JOB_ROOT / (job + ".json")).read_text())
    except (OSError, ValueError) as exc:
        raise UpdateError("Update job not found") from exc
    if not isinstance(data, dict) or data.get("jobId") != job or data.get("state") not in STATES:
        raise UpdateError("Invalid update status")
    return {k: data[k] for k in ("ok", "jobId", "state", "updatedAt",
            "currentVersion", "availableVersion", "message") if k in data}


def active_jobs(root: Path) -> bool:
    if not root.is_dir():
        return False
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("state") in {
                "queued", "running", "preflight", "downloading", "verifying",
                "staging", "backup", "installing", "health", "restoring"}:
            return True
    return False


def current_version() -> str:
    api = RELEASE_ROOT / "server/asr-api.php"
    try:
        match = re.search(r"const\s+ASR_VERSION\s*=\s*['\"]([^'\"]+)['\"]", api.read_text())
    except OSError:
        match = None
    if not match or not VERSION_RE.fullmatch(match.group(1)):
        raise UpdateError("Current ASR installation is invalid")
    return match.group(1)


def fetch(url: str, accept: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": "AllScan-Reimagined/updater"})
    with urllib.request.urlopen(request, timeout=20) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise UpdateError("Release exceeds size limit")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise UpdateError("Release exceeds size limit")
    return body


def release_for_update(installed: str) -> dict:
    try:
        releases = json.loads(fetch(API, "application/vnd.github+json", 4 * 1024 * 1024))
    except (OSError, ValueError) as exc:
        raise UpdateError("Release check unavailable") from exc
    if not isinstance(releases, list):
        raise UpdateError("Invalid release list")
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name", ""))
        version = tag.removeprefix("v")
        if VERSION_RE.fullmatch(version):
            candidates.append((version_key(version), version, release))
    if not candidates:
        raise UpdateError("No published release")
    _, version, release = max(candidates)
    if version_key(version) <= version_key(installed):
        raise UpdateError("ASR is up to date")
    name = f"allscan-reimagined-{version}.tar.gz"
    prefix = f"https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/{release['tag_name']}/"
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("Invalid release assets")
    urls = {}
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") in (name, name + ".sha256"):
            expected = prefix + asset["name"]
            if asset.get("browser_download_url") == expected:
                urls[asset["name"]] = expected
    if len(urls) != 2:
        raise UpdateError("Verified release assets unavailable")
    checksum = fetch(urls[name + ".sha256"], "text/plain", 65536).decode("ascii")
    match = re.fullmatch(r"\s*([a-fA-F0-9]{64})\s+\*?" + re.escape(name) + r"\s*", checksum)
    if not match:
        raise UpdateError("Invalid release checksum")
    return {"version": version, "url": urls[name], "sha256": match.group(1).lower()}


def version_key(version: str) -> tuple:
    core, *suffix = version.split("-", 1)
    numbers = tuple(int(x) for x in core.split("."))
    if not suffix:
        return (*numbers, 1, 0, ())
    channel, sequence = suffix[0].split(".", 1)
    return (*numbers, 0, {"alpha": 1, "beta": 2, "rc": 3}[channel],
            tuple(int(x) for x in sequence.split(".")))


def archive_valid(path: Path, version: str) -> None:
    prefix = f"allscan-reimagined-{version}"
    seen = set()
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 20000:
                raise UpdateError("Too many release files")
            for member in members:
                parts = PurePosixPath(member.name).parts
                canonical = "/".join(parts)
                if (not parts or parts[0] != prefix or
                        member.name.rstrip("/") != canonical or
                        any(p in ("..", ".") for p in parts)):
                    raise UpdateError("Unsafe release member")
                if canonical in seen or not (member.isfile() or member.isdir()):
                    raise UpdateError("Unsafe release file type")
                seen.add(canonical)
                total += member.size
                if member.size > 128 * 1024 * 1024 or total > MAX_EXPANDED:
                    raise UpdateError("Release expands beyond limit")
            for required in ("install.sh", "package.json", "payload/server/asr-api.php",
                             "payload/scripts/asr-reapply.sh", "payload/web/index.html"):
                if prefix + "/" + required not in seen:
                    raise UpdateError("Incomplete release package")
            api = archive.extractfile(prefix + "/payload/server/asr-api.php")
            manifest = archive.extractfile(prefix + "/package.json")
            installer = archive.extractfile(prefix + "/install.sh")
            if api is None or manifest is None or installer is None:
                raise UpdateError("Incomplete release package")
            if f"const ASR_VERSION = '{version}';".encode() not in api.read(1024 * 1024):
                raise UpdateError("Release version mismatch")
            if json.loads(manifest.read(1024 * 1024)).get("version") != version:
                raise UpdateError("Release manifest version mismatch")
            if b"$SCRIPT_DIR/package.json" not in installer.read(1024 * 1024):
                raise UpdateError("Installer does not use release manifest")
    except (tarfile.TarError, OSError) as exc:
        raise UpdateError("Invalid release archive") from exc



def stage_release(package: Path, destination: Path, version: str) -> Path:
    """Extract only regular files and directories with fixed safe permissions."""
    archive_valid(package, version)
    root = "allscan-reimagined-" + version
    with tarfile.open(package, "r:gz") as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                os.chmod(target, 0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                source = archive.extractfile(member)
                if source is None:
                    raise UpdateError("Release member cannot be read")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                os.chmod(target, 0o644)
    return destination / root

def preflight() -> dict:
    installed = current_version()
    release = release_for_update(installed)
    version = release["version"]
    stock = WEB_ROOT / "allscan/include/common.php"
    try:
        match = re.search(r'^\$AllScanVersion\s*=\s*"([^"]+)"', stock.read_text(), re.M)
        config = json.loads(Path("/etc/allscan-reimagined/config.json").read_text())
    except (OSError, ValueError) as exc:
        raise UpdateError("Stock backend or ASR configuration is unavailable") from exc
    if not match or not isinstance(config, dict) or not (WEB_ROOT / "asr/asr-api.php").is_file():
        raise UpdateError("Installed configuration is incomplete")
    if not (RELEASE_ROOT / "compat" / ("allscan-" + match.group(1))).is_dir():
        raise UpdateError("Current stock backend is unsupported")
    if not (WEB_ROOT / "allscan").is_dir():
        raise UpdateError("Stock AllScan is missing")
    if shutil.which("systemctl") is None or shutil.which("php") is None:
        raise UpdateError("Required services are unavailable")
    if subprocess.run(["systemctl", "is-active", "--quiet", "apache2.service"], check=False).returncode:
        raise UpdateError("Web service is not active")
    if not BACKUPS.is_dir() or not os.access(BACKUPS, os.W_OK):
        raise UpdateError("Rollback backup storage is unavailable")
    if shutil.disk_usage(BACKUPS).free < 2 * 1024 ** 3:
        raise UpdateError("Insufficient free backup space")
    if shutil.disk_usage(RELEASE_ROOT).free < 2 * 1024 ** 3:
        raise UpdateError("Insufficient free release space")
    # Avoid any stock backend upgrade, which belongs to the interactive installer.
    official = fetch("https://raw.githubusercontent.com/davidgsd/AllScan/main/include/common.php",
                     "text/plain", 2 * 1024 * 1024).decode("utf-8")
    latest = re.search(r'^\$AllScanVersion\s*=\s*"([^"]+)"', official, re.M)
    if not latest or latest.group(1) != match.group(1):
        raise UpdateError("Stock AllScan requires an interactive upgrade")
    if (WEB_ROOT / "allscan/asr-api.php").exists():
        raise UpdateError("Legacy stock overlay requires interactive migration")
    return {"ok": True, "installedVersion": installed, "availableVersion": version,
            "compatible": True, "diskSpace": "sufficient",
            "services": "ready", "configuration": "valid", "backup": "ready",
            "restartRequired": True, "rebootRequired": False, "release": release}


def guard() -> object:
    GATE.parent.mkdir(parents=True, exist_ok=True)
    handle = GATE.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise UpdateError("Another maintenance operation is running") from exc
    return handle


def install_busy() -> bool:
    with INSTALL_LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def queue() -> dict:
    if os.geteuid() != 0:
        raise UpdateError("Root required")
    with guard():
        if active_jobs(JOB_ROOT) or active_jobs(ROLLBACK_JOBS) or install_busy():
            raise UpdateError("Another maintenance operation is running")
        owned_jobs()
        job = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        status(job, "queued")
        result = subprocess.run(["systemctl", "start", "--no-block",
                                 f"allscan-reimagined-update@{job}.service"],
                                capture_output=True, check=False)
        if result.returncode:
            status(job, "failed", message="Update could not start.")
            raise UpdateError("Update job could not start")
        return read_status(job)


def backup_since(before: set[str]) -> str | None:
    candidates = sorted((p for p in BACKUPS.iterdir() if p.is_dir()
                         and re.fullmatch(r"[0-9]{8}-[0-9]{6}", p.name)
                         and p.name not in before and (p / "manifest.json").is_file()), reverse=True)
    return candidates[0].name if candidates else None



def healthy(version: str) -> bool:
    try:
        if current_version() != version or not (WEB_ROOT / "asr/index.html").is_file():
            return False
        if subprocess.run(["php", "-l", str(WEB_ROOT / "asr/asr-api.php")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          timeout=15, check=False).returncode:
            return False
        probe = RELEASE_ROOT / "scripts/asr-loopback-validate.py"
        if subprocess.run(
            ["python3", str(probe), "--expect", "html", "--contains",
             "assets/index-", "http://127.0.0.1/asr/"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, check=False).returncode:
            return False
        return True
    except (OSError, UpdateError, subprocess.TimeoutExpired):
        return False

def recover(previous: str, before: set[str]) -> bool:
    try:
        if healthy(previous):
            return True
    except UpdateError:
        pass
    backup = backup_since(before)
    if not backup:
        return False
    result = subprocess.run(["/usr/local/sbin/allscan-reimagined-rollback", "rollback", backup],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    try:
        return result.returncode == 0 and healthy(previous)
    except UpdateError:
        return False



def install_release(staged: Path, job: str) -> int:
    environment = os.environ.copy()
    environment["ASR_UPDATE_JOB_ID"] = job
    log_fd = os.open(JOB_ROOT / (job + ".log"),
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(log_fd, 0o600)
        process = subprocess.Popen(
            ["bash", str(staged / "install.sh"), "--browser-update"],
            cwd=staged, stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
            env=environment, start_new_session=True)
        try:
            return process.wait(timeout=1800)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
    finally:
        os.close(log_fd)

def run(job: str) -> int:
    if os.geteuid() != 0 or read_status(job)["state"] != "queued":
        raise UpdateError("Job is not queued")
    # Queueing briefly holds this gate while systemd starts the worker.
    gate_handle = None
    for _ in range(100):
        try:
            gate_handle = guard()
            break
        except UpdateError:
            time.sleep(0.05)
    if gate_handle is None:
        raise UpdateError("Another maintenance operation is running")
    previous_sigterm = signal.signal(
        signal.SIGTERM,
        lambda _signum, _frame: (_ for _ in ()).throw(UpdateError("Update interrupted")))
    with gate_handle:
        work = None
        previous = None
        before = set()
        try:
            if active_jobs(ROLLBACK_JOBS) or install_busy():
                raise UpdateError("Another maintenance operation is running")
            status(job, "preflight")
            checks = preflight()
            previous = checks["installedVersion"]
            release = checks["release"]
            before = {p.name for p in BACKUPS.iterdir() if p.is_dir()}
            work = Path(tempfile.mkdtemp(prefix="asr-update-", dir=WORK_ROOT))
            package = work / "release.tar.gz"
            status(job, "downloading", currentVersion=previous, availableVersion=release["version"])
            payload = fetch(release["url"], "application/octet-stream", MAX_ARCHIVE)
            package.write_bytes(payload)
            status(job, "verifying")
            if hashlib.sha256(payload).hexdigest() != release["sha256"]:
                raise UpdateError("Release checksum mismatch")
            status(job, "staging")
            staged = stage_release(package, work, release["version"])
            stock_version = re.search(
                r'^\$AllScanVersion\s*=\s*"([^"]+)"',
                (WEB_ROOT / "allscan/include/common.php").read_text(), re.M)
            if (not stock_version or not
                    (staged / "payload/compat" / ("allscan-" + stock_version.group(1))).is_dir()):
                raise UpdateError("Release does not support the installed stock backend")
            status(job, "backup")
            result_code = install_release(staged, job)
            if result_code:
                raise UpdateError("Installer failed")
            status(job, "health")
            if not healthy(release["version"]):
                raise UpdateError("Installed application health check failed")
            return_code = 0
            status(job, "complete", currentVersion=previous,
                   availableVersion=release["version"], message="Update complete.")
        except Exception as exc:
            # Only the root-only journal contains detailed diagnostics.
            try:
                with (JOB_ROOT / (job + ".log")).open("a") as log:
                    log.write(f"{now()} {type(exc).__name__}: {str(exc)[:500]}\n")
                os.chmod(JOB_ROOT / (job + ".log"), 0o600)
            except OSError:
                pass
            status(job, "restoring")
            restored = previous is not None and recover(previous, before)
            status(job, "failed", message=("Update failed; previous installation restored."
                   if restored else "Update failed; recovery needs attention."))
            return_code = 1
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
        return return_code


def main(argv: list[str]) -> int:
    try:
        if argv == ["--queue-update"]:
            print(json.dumps(queue()))
        elif argv == ["--preflight-json"]:
            if os.geteuid() != 0:
                raise UpdateError("Root required")
            with guard():
                if active_jobs(JOB_ROOT) or active_jobs(ROLLBACK_JOBS) or install_busy():
                    raise UpdateError("Another maintenance operation is running")
                result = preflight()
                result.pop("release")
                print(json.dumps(result))
        elif len(argv) == 2 and argv[0] == "--status-json":
            print(json.dumps(read_status(argv[1])))
        elif len(argv) == 2 and argv[0] == "run-job":
            return run(argv[1])
        elif len(argv) == 3 and argv[0] == "internal-progress":
            if os.geteuid() != 0 or argv[2] not in {"installing", "restoring", "health"}:
                raise UpdateError("Invalid progress operation")
            if read_status(argv[1])["state"] not in {"backup", "installing", "restoring", "health"}:
                raise UpdateError("Update is not installing")
            print(json.dumps(status(argv[1], argv[2])))
        else:
            raise UpdateError("Invalid updater operation")
        return 0
    except UpdateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
