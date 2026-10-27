#!/usr/bin/env python3
"""Discover and install the optional container-host provisioning boundary."""
from __future__ import annotations

import argparse
import ctypes.util
import grp
import importlib.util
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEST = Path("/usr/local/libexec/allscan-reimagined")
PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")
SOCKET_UNIT = Path("/etc/systemd/system/allscan-reimagined-container-provisioning.socket")
SERVICE_UNIT = Path("/etc/systemd/system/allscan-reimagined-container-provisioning@.service")
ASTERISK_SOCKET_UNIT = Path("/etc/systemd/system/allscan-reimagined-container-asterisk.socket")
ASTERISK_SERVICE_UNIT = Path("/etc/systemd/system/allscan-reimagined-container-asterisk@.service")
SOCKET_TMPFILES = Path("/etc/tmpfiles.d/allscan-reimagined-container-provisioning.conf")

HOST_FILES = (
    "asr-provisioning-backend.py", "asr-container-host-broker.py",
    "asr-container-netns-exec.py", "asr-container-systemctl.py",
    "asr-container-asterisk.py", "asr-container-bridge-recovery.py", "asr-bridge-setup-core.py",
    "asr-m17-hosts-update.py",
    "asr-bridge-setup-m17.py", "asr-bridge-setup-helper.py",
    "asr-bridge-runtime-sources.py", "asr-bridge-urf-native-runtime.py", "asr-bridge-setup-digital.py",
    "asr-bridge-setup-digital-render.py", "asr-bridge-setup-digital-install.py",
    "asr-bridge-setup-urf.py", "asr-bridge-setup-urf-install.py",
)
CONTROL_FILES = (
    "asr-managed-dmr-net-control.py", "asr-ysf-bridge-control.py",
    "asr-p25-bridge-control.py", "asr-nxdn-bridge-control.py",
    "asr-m17-bridge-control.py", "asr-m17-usrp-connector.py",
    "asr-net-bridge-mode-control.py",
)
SUPPORT_FILES = ("asr_bridge_status.py",)
SERVICE_USER = "asr-bridge"
DOCKER_STATE_DIRECTORIES = (
    Path("/var/cache/allscan-reimagined/docker-home"),
    Path("/var/cache/allscan-reimagined/docker-config"),
    Path("/var/cache/allscan-reimagined/buildx"),
)


def backend():
    spec = importlib.util.spec_from_file_location("asr_backend_installer", HERE / "asr-provisioning-backend.py")
    if not spec or not spec.loader:
        raise RuntimeError("backend module unavailable")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


def atomic(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content); handle.flush(); os.fchmod(handle.fileno(), mode); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_bytes(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content); handle.flush(); os.fchmod(handle.fileno(), mode); os.fsync(handle.fileno())
        os.chown(temporary, 0, 0); os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def profile_json(profile) -> str:
    return json.dumps({
        "schema": 1, "backend": "container-host",
        "asteriskContainer": profile.asterisk_container,
        "asteriskConfig": str(profile.asterisk_config),
        "asrConfig": str(profile.asr_config), "clientUid": profile.client_uid,
        "clientGid": profile.client_gid,
        "hostProc": str(profile.host_proc),
        "runtimeHostRoot": str(profile.runtime_host_root),
    }, indent=2, sort_keys=True) + "\n"


def install_m17_runtime() -> None:
    if ctypes.util.find_library("codec2"):
        return
    release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            release[key] = value.strip().strip('"')
    distro = release.get("ID")
    version = release.get("VERSION_ID", "")
    package = None
    if distro == "debian":
        package = {"12": "libcodec2-1.0", "13": "libcodec2-1.2"}.get(version.split(".", 1)[0])
    elif distro == "ubuntu" and (version == "26.04" or version.startswith("26.04.")):
        package = "libcodec2-1.2"
    if package is None:
        raise RuntimeError("M17 host runtime requires Debian 12/13 or Ubuntu 26.04 libcodec2")
    result = subprocess.run(
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends",
         "--no-upgrade", "--no-remove", package], capture_output=True, text=True,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode or not ctypes.util.find_library("codec2"):
        raise RuntimeError(f"M17 codec runtime installation failed: {result.stderr[-500:]}")


def ensure_service_account() -> None:
    try:
        account = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        subprocess.run(["groupadd", "--system", SERVICE_USER], check=True)
        subprocess.run(["useradd", "--system", "--no-create-home",
                        "--shell", "/usr/sbin/nologin", "--gid", SERVICE_USER,
                        SERVICE_USER], check=True)
        account = pwd.getpwnam(SERVICE_USER)
    group = grp.getgrnam(SERVICE_USER)
    if account.pw_uid == 0 or account.pw_gid != group.gr_gid:
        raise RuntimeError("container bridge service account is unsafe")


def migrate_managed_log_ownership(account: pwd.struct_passwd) -> None:
    """Allow the dedicated service account to resume its own pre-migration logs."""
    log_root = Path("/var/log/mmdvm")
    allowed = re.compile(
        r"(?:YSFGateway|P25Gateway|NXDNGateway|MMDVM_Bridge)_[a-z][a-z0-9_-]{1,31}-[0-9]{4}-[0-9]{2}-[0-9]{2}\.log"
    )
    for candidate in log_root.iterdir():
        if not allowed.fullmatch(candidate.name):
            continue
        info = candidate.lstat()
        if (not candidate.is_file() or candidate.is_symlink() or info.st_nlink != 1
                or info.st_uid not in {0, account.pw_uid}):
            raise RuntimeError(f"managed bridge log is unsafe: {candidate.name}")
        os.chown(candidate, account.pw_uid, account.pw_gid)
        os.chmod(candidate, 0o640)


def ensure_runtime_root(path: Path) -> None:
    current = Path("/")
    for component in path.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise RuntimeError("container runtime root contains a symbolic link")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != 0:
        raise RuntimeError("container runtime root is unsafe")
    os.chown(path, 0, 0)
    os.chmod(path, 0o755)


def ensure_private_root_directories(paths: tuple[Path, ...]) -> None:
    """Create root-only state without accepting links or unsafe ancestors."""
    for path in paths:
        current = Path("/")
        for component in path.parts[1:]:
            current /= component
            if current.exists():
                info = current.lstat()
                if current.is_symlink() or not current.is_dir():
                    raise RuntimeError(f"private state path contains an unsafe component: {path}")
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise RuntimeError(f"private state path has an unsafe ancestor: {path}")
        if path.exists() and (not path.is_dir() or path.stat().st_uid != 0):
            raise RuntimeError(f"private state directory is unsafe: {path}")
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 0, 0)
        os.chmod(path, 0o700)


def managed_urf_images_required(profile) -> bool:
    config_path = profile.asr_config / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ASR configuration could not be read for managed image repair") from exc
    return any(
        isinstance(bridge, dict)
        and bridge.get("cardType") == "dmr_net"
        and bridge.get("backendMode", "managed") != "display_only"
        for bridge in config.get("bridges", [])
    )


def repair_managed_urf_images(profile) -> bool:
    """Restore missing local URF images on hosts that actually use DMR Net Bridge."""
    if not managed_urf_images_required(profile):
        return False
    source = DEST / "asr-bridge-setup-urf-install.py"
    spec = importlib.util.spec_from_file_location("asr_urf_image_repair", source)
    if not spec or not spec.loader:
        raise RuntimeError("managed URF image repair module is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous_profile = os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE")
    os.environ["ASR_CONTAINER_PROVISIONING_PROFILE"] = str(PROFILE)
    try:
        spec.loader.exec_module(module)
        return bool(module.ensure_managed_images())
    finally:
        if previous_profile is None:
            os.environ.pop("ASR_CONTAINER_PROVISIONING_PROFILE", None)
        else:
            os.environ["ASR_CONTAINER_PROVISIONING_PROFILE"] = previous_profile


def mqtt_secrets_metadata_is_safe(file_info, parent_info, profile) -> bool:
    legacy_owner = file_info.st_uid == profile.client_uid and file_info.st_gid == profile.client_gid
    parent_group_safe = not (parent_info.st_mode & 0o020) or parent_info.st_gid == profile.client_gid
    return (
        stat.S_ISREG(file_info.st_mode)
        and file_info.st_nlink == 1
        and stat.S_IMODE(file_info.st_mode) == 0o600
        and (file_info.st_uid == 0 or legacy_owner)
        and stat.S_ISDIR(parent_info.st_mode)
        and parent_info.st_uid == 0
        and not parent_info.st_mode & 0o002
        and parent_group_safe
    )


def normalize_mqtt_secrets(profile) -> None:
    """Atomically migrate the exact legacy web-owned credential file to root."""
    path = profile.asr_config / "bridge-mqtt-secrets.json"
    if not path.exists() and not path.is_symlink():
        return
    parent = path.parent
    parent_info = parent.lstat()
    if parent.is_symlink():
        raise RuntimeError("MQTT credential directory is unsafe")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(parent, directory_flags)
    descriptor = -1
    try:
        descriptor = os.open(path.name, file_flags, dir_fd=directory_fd)
        info = os.fstat(descriptor)
        if not mqtt_secrets_metadata_is_safe(info, parent_info, profile):
            raise RuntimeError("existing MQTT credential file is unsafe")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            content = handle.read(1_048_577)
        if len(content) > 1_048_576:
            raise RuntimeError("existing MQTT credential file is unsafe")
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("existing MQTT credential file is invalid") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("bridges"), dict):
            raise RuntimeError("existing MQTT credential file is invalid")
        atomic_bytes(path, content, 0o600)
        migrated = path.lstat()
        if (path.is_symlink() or not path.is_file() or migrated.st_uid != 0
                or migrated.st_gid != 0 or migrated.st_nlink != 1
                or stat.S_IMODE(migrated.st_mode) != 0o600):
            raise RuntimeError("MQTT credential ownership migration failed")
    except OSError as exc:
        raise RuntimeError("existing MQTT credential file is unsafe") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)


def mosquitto_credential_metadata_is_safe(file_info, mosquitto_uid: int,
                                           mosquitto_gid: int) -> bool:
    return (
        stat.S_ISREG(file_info.st_mode)
        and file_info.st_nlink == 1
        and stat.S_IMODE(file_info.st_mode) == 0o640
        and file_info.st_uid in {0, mosquitto_uid}
        and file_info.st_gid == mosquitto_gid
    )


def normalize_mosquitto_credentials() -> None:
    """Migrate ASR-owned broker credentials to Mosquitto's required owner."""
    directory = Path("/etc/mosquitto")
    directory_info = directory.lstat()
    if (directory.is_symlink() or not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != 0 or directory_info.st_mode & 0o022):
        raise RuntimeError("Mosquitto credential directory is unsafe")
    account = pwd.getpwnam("mosquitto")
    for pattern in ("asr-*.passwords", "asr-*.acl"):
        for path in directory.glob(pattern):
            info = path.lstat()
            if not mosquitto_credential_metadata_is_safe(
                    info, account.pw_uid, account.pw_gid):
                raise RuntimeError(f"existing Mosquitto credential file is unsafe: {path}")
            if info.st_uid != account.pw_uid:
                os.chown(path, account.pw_uid, account.pw_gid)
            migrated = path.lstat()
            if (migrated.st_uid != account.pw_uid
                    or migrated.st_gid != account.pw_gid
                    or stat.S_IMODE(migrated.st_mode) != 0o640):
                raise RuntimeError(f"Mosquitto credential ownership migration failed: {path}")


def disconnected_ysf_net_config(content: str) -> tuple[str, bool]:
    """Clear only the live Startup target in a YSF Gateway Network section."""
    lines = content.splitlines(keepends=True)
    section = ""
    matches: list[int] = []
    for index, line in enumerate(lines):
        section_match = re.match(r"^\s*\[([^]]+)]\s*(?:[#;].*)?(?:\r?\n)?$", line)
        if section_match:
            section = section_match.group(1).strip().lower()
            continue
        if section == "network" and re.match(r"^\s*Startup\s*=", line, re.IGNORECASE):
            matches.append(index)
    if len(matches) != 1:
        raise RuntimeError("managed YSF Net Bridge configuration is invalid")
    index = matches[0]
    match = re.match(r"^(\s*)Startup\s*=([^\r\n]*)(\r?\n)?$", lines[index], re.IGNORECASE)
    if not match:
        raise RuntimeError("managed YSF Net Bridge configuration is invalid")
    changed = bool(match.group(2).strip())
    if changed:
        lines[index] = f"{match.group(1)}Startup={match.group(3) or ''}"
    return "".join(lines), changed


def normalize_managed_ysf_net_startup(profile) -> None:
    """Prevent legacy Net Bridge gateways from auto-linking on mode startup."""
    config_path = profile.asr_config / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ASR configuration could not be read for YSF startup migration") from exc
    candidates: set[Path] = set()
    allowed = re.compile(r"^/opt/YSFGateway_[a-z][a-z0-9_-]{1,31}/YSFGateway\.ini$")
    for bridge in config.get("bridges", []):
        if (not isinstance(bridge, dict) or bridge.get("cardType") != "ysf_net"
                or bridge.get("backendMode", "managed") == "display_only"):
            continue
        value = bridge.get("ysfGatewayConfig")
        if not isinstance(value, str) or not allowed.fullmatch(value):
            raise RuntimeError("managed YSF Net Bridge configuration path is unsafe")
        candidates.add(Path(value))
    for path in candidates:
        parent_info = path.parent.lstat()
        info = path.lstat()
        if (path.parent.is_symlink() or not path.parent.is_dir()
                or parent_info.st_uid != 0 or parent_info.st_gid != 0
                or stat.S_IMODE(parent_info.st_mode) != 0o755
                or path.is_symlink() or not path.is_file() or info.st_nlink != 1
                or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 1_048_576):
            raise RuntimeError(f"managed YSF Net Bridge configuration is unsafe: {path}")
        content = path.read_text(encoding="utf-8")
        updated, changed = disconnected_ysf_net_config(content)
        if changed:
            atomic_bytes(path, updated.encode("utf-8"), 0o644)


def managed_dmr_live_mounts(content: str, runtime_root: Path) -> tuple[str, bool]:
    """Expose the managed URF event/PTT files at the path used by its binary."""
    data_mount = f"      - {runtime_root}/data:/data\n"
    live_mount = f"      - {runtime_root}/data:/run/urf-wil\n"
    if content.count(data_mount) != 1:
        raise RuntimeError("managed DMR compose data mount is invalid")
    if content.count(live_mount) > 1:
        raise RuntimeError("managed DMR compose live-state mount is invalid")
    if live_mount in content:
        return content, False
    return content.replace(data_mount, data_mount + live_mount), True


def normalize_managed_dmr_live_state_mounts(profile) -> None:
    """Migrate existing managed DMR compose files without rebuilding settings."""
    try:
        config = json.loads((profile.asr_config / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ASR configuration could not be read for managed DMR migration") from exc
    for bridge in config.get("bridges", []):
        if (not isinstance(bridge, dict) or bridge.get("cardType") != "dmr_net"
                or bridge.get("backendMode", "managed") == "display_only"):
            continue
        bridge_id = str(bridge.get("id", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
            raise RuntimeError("managed DMR bridge ID is invalid")
        runtime_root = Path("/opt/allscan-reimagined-bridges/urf") / bridge_id
        path = runtime_root / "compose.yml"
        info = path.lstat()
        parent = path.parent.lstat()
        if (path.is_symlink() or not path.is_file() or info.st_nlink != 1
                or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o644 or info.st_size > 1_048_576
                or path.parent.is_symlink() or not path.parent.is_dir()
                or parent.st_uid != 0 or parent.st_gid != 0
                or parent.st_mode & 0o022):
            raise RuntimeError(f"managed DMR compose file is unsafe: {path}")
        updated, changed = managed_dmr_live_mounts(
            path.read_text(encoding="utf-8"), runtime_root
        )
        if changed:
            atomic_bytes(path, updated.encode("utf-8"), 0o644)


def managed_m17_external_bind(config: dict) -> bool:
    """Allow managed M17 signaling to reach non-loopback reflectors."""
    changed = False
    for bridge in config.get("bridges", []):
        if (not isinstance(bridge, dict) or bridge.get("mode") != "m17"
                or bridge.get("backendMode", "managed") == "display_only"):
            continue
        if bridge.get("m17BindAddress") == "127.0.0.1":
            bridge["m17BindAddress"] = "0.0.0.0"
            changed = True
    return changed


def normalize_managed_m17_bind_address(profile) -> None:
    """Migrate the legacy loopback-only M17 network socket atomically."""
    path = profile.asr_config / "config.json"
    info = path.lstat()
    if (path.is_symlink() or not path.is_file() or info.st_nlink != 1
            or info.st_uid != 0 or info.st_mode & 0o002 or info.st_size > 1_048_576):
        raise RuntimeError("ASR configuration is unsafe for M17 bind migration")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ASR configuration could not be read for M17 bind migration") from exc
    if not managed_m17_external_bind(config):
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".config.json.m17-bind.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fchown(handle.fileno(), 0, info.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(profile) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("container host backend installation requires root")
    normalize_mqtt_secrets(profile)
    normalize_managed_ysf_net_startup(profile)
    normalize_managed_dmr_live_state_mounts(profile)
    normalize_managed_m17_bind_address(profile)
    source_spec = importlib.util.spec_from_file_location(
        "asr_container_runtime_sources", HERE / "asr-bridge-runtime-sources.py"
    )
    if not source_spec or not source_spec.loader:
        raise RuntimeError("digital runtime source module is unavailable")
    source_module = importlib.util.module_from_spec(source_spec)
    sys.modules[source_spec.name] = source_module
    source_spec.loader.exec_module(source_module)
    source_module.install_packages(build=True)
    install_m17_runtime()
    ensure_service_account()
    ensure_runtime_root(profile.runtime_host_root)
    ensure_private_root_directories(DOCKER_STATE_DIRECTORIES)
    for directory in (Path("/etc/mosquitto"), Path("/var/lib/mmdvm"),
                      Path("/var/log/mmdvm"), Path("/var/log/allscan-reimagined"),
                      Path("/var/lib/allscan-reimagined"),
                      Path("/run/allscan-reimagined-m17"),
                      Path("/run/allscan-reimagined-bridge-rpc"),
                      Path("/run/allscan-reimagined-ysf-bridge-control"),
                      Path("/run/allscan-reimagined-p25-bridge-control"),
                      Path("/run/allscan-reimagined-nxdn-bridge-control")):
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise RuntimeError(f"required host directory is unsafe: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
    normalize_mosquitto_credentials()
    service_account = pwd.getpwnam(SERVICE_USER)
    for directory in (Path("/var/log/mmdvm"), Path("/var/log/allscan-reimagined"),
                      Path("/run/allscan-reimagined-m17")):
        os.chown(directory, service_account.pw_uid, service_account.pw_gid)
        os.chmod(directory, 0o750)
    # M17 control actions run through the root-only broker.  Keep their audit
    # parent root-owned so the append-only security checks cannot be bypassed
    # by the shared service account that owns the general bridge log directory.
    m17_audit_directory = Path("/var/log/allscan-reimagined/m17")
    m17_audit_directory.mkdir(mode=0o750, exist_ok=True)
    os.chown(m17_audit_directory, 0, 0)
    os.chmod(m17_audit_directory, 0o750)
    m17_data_directory = Path("/var/lib/allscan-reimagined/m17")
    m17_data_directory.mkdir(mode=0o755, exist_ok=True)
    os.chown(m17_data_directory, 0, 0)
    os.chmod(m17_data_directory, 0o755)
    os.chown("/run/allscan-reimagined-bridge-rpc", 0, 0)
    os.chmod("/run/allscan-reimagined-bridge-rpc", 0o755)
    for mode in ("ysf", "p25", "nxdn"):
        run_dir = Path("/run") / f"allscan-reimagined-{mode}-bridge-control"
        os.chown(run_dir, service_account.pw_uid, profile.client_gid)
        os.chmod(run_dir, 0o2750)
    backend().refresh_watch_snapshots(profile, SERVICE_USER)
    migrate_managed_log_ownership(service_account)
    stage_root = Path("/var/cache/allscan-reimagined/bridge-runtime")
    stage_root.mkdir(parents=True, exist_ok=True)
    if stage_root.is_symlink() or stage_root.stat().st_uid != 0:
        raise RuntimeError("digital runtime cache root is unsafe")
    for mode in ("ysf", "p25", "nxdn"):
        stage = stage_root / mode
        try:
            source_module.verify(stage, mode)
        except source_module.SourceError:
            if stage.exists():
                if stage.is_symlink() or not stage.is_dir():
                    raise RuntimeError("digital runtime cache is unsafe")
                shutil.rmtree(stage)
            with tempfile.TemporaryDirectory(prefix=f".{mode}-", dir=stage_root) as temporary:
                prepared = Path(temporary) / "runtime"
                source_module.prepare(mode, prepared, False)
                source_module.verify(prepared, mode)
                os.replace(prepared, stage)
    if DEST.exists() and (DEST.is_symlink() or DEST.stat().st_uid != 0 or DEST.stat().st_mode & 0o022):
        raise RuntimeError("host helper directory is unsafe")
    DEST.mkdir(parents=True, exist_ok=True)
    for name in HOST_FILES:
        source = HERE / name
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"required host backend file is missing: {name}")
        atomic_bytes(DEST / name, source.read_bytes(), 0o755)
    subprocess.run([str(DEST / "asr-m17-hosts-update.py")], check=True)
    for name in CONTROL_FILES:
        source = HERE / name
        target = Path("/usr/local/sbin") / f"allscan-reimagined-{name.removeprefix('asr-').removesuffix('.py')}"
        if target.is_symlink():
            raise RuntimeError(f"unsafe control helper target: {target}")
        atomic_bytes(target, source.read_bytes(), 0o755)
    for name in SUPPORT_FILES:
        source = HERE / name
        target = Path("/usr/local/sbin") / name
        if not source.is_file() or source.is_symlink() or target.is_symlink():
            raise RuntimeError(f"unsafe or missing support module: {name}")
        atomic_bytes(target, source.read_bytes(), 0o644)
    runtime_source = HERE.parent / "runtime" / "urf"
    runtime_target = Path("/usr/local/share/allscan-reimagined/runtime/urf")
    if not runtime_source.is_dir() or runtime_source.is_symlink():
        raise RuntimeError("managed URF runtime source is unavailable")
    temporary_runtime = runtime_target.with_name("urf.new")
    if temporary_runtime.exists():
        if temporary_runtime.is_symlink() or not temporary_runtime.is_dir():
            raise RuntimeError("managed URF temporary runtime is unsafe")
        shutil.rmtree(temporary_runtime)
    temporary_runtime.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(runtime_source, temporary_runtime)
    previous_runtime = runtime_target.with_name("urf.previous")
    if previous_runtime.exists():
        if previous_runtime.is_symlink() or not previous_runtime.is_dir() or previous_runtime.stat().st_uid != 0:
            raise RuntimeError("managed URF previous runtime is unsafe")
        shutil.rmtree(previous_runtime)
    if runtime_target.exists():
        if runtime_target.is_symlink() or not runtime_target.is_dir() or runtime_target.stat().st_uid != 0:
            raise RuntimeError("managed URF runtime target is unsafe")
        os.replace(runtime_target, previous_runtime)
    try:
        os.replace(temporary_runtime, runtime_target)
        repair_managed_urf_images(profile)
    except Exception:
        if previous_runtime.exists():
            if runtime_target.exists():
                if runtime_target.is_symlink() or not runtime_target.is_dir() or runtime_target.stat().st_uid != 0:
                    raise RuntimeError("managed URF runtime rollback target is unsafe")
                shutil.rmtree(runtime_target)
            os.replace(previous_runtime, runtime_target)
        raise
    if previous_runtime.exists():
        shutil.rmtree(previous_runtime)
    bin_dir = DEST / "container-host-bin"
    bin_dir.mkdir(mode=0o755, exist_ok=True)
    for name, source in (("systemctl", "asr-container-systemctl.py"),
                         ("asterisk", "asr-container-asterisk.py")):
        target = bin_dir / name
        target.unlink(missing_ok=True)
        target.symlink_to(DEST / source)
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    atomic(PROFILE, profile_json(profile), 0o600)
    group = grp.getgrgid(profile.client_gid).gr_name
    runtime_directories = "".join(
        f"d /run/allscan-reimagined-{mode}-bridge-control 2750 {SERVICE_USER} {group} -\n"
        for mode in ("ysf", "p25", "nxdn")
    )
    atomic(SOCKET_TMPFILES,
           f"d /run/allscan-reimagined-host 0750 root {group} -\n" + runtime_directories,
           0o644)
    subprocess.run(["systemd-tmpfiles", "--create", str(SOCKET_TMPFILES)], check=True)
    atomic(SOCKET_UNIT, f"""[Unit]
Description=ASR container provisioning socket

[Socket]
ListenStream=/run/allscan-reimagined-host/bridge-setup.sock
SocketMode=0660
SocketUser=root
SocketGroup={group}
DirectoryMode=0750
RemoveOnStop=true
Accept=yes
MaxConnections=4

[Install]
WantedBy=sockets.target
""", 0o644)
    atomic(SERVICE_UNIT, f"""[Unit]
Description=ASR container provisioning request

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/allscan-reimagined/asr-container-host-broker.py
StandardInput=socket
StandardOutput=socket
StandardError=journal
User=root
Group=root
Environment=HOME=/var/cache/allscan-reimagined/docker-home
Environment=DOCKER_CONFIG=/var/cache/allscan-reimagined/docker-config
Environment=BUILDX_CONFIG=/var/cache/allscan-reimagined/buildx
NoNewPrivileges=false
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
BindPaths={profile.asterisk_config} {profile.asr_config} {profile.runtime_host_root}
ReadWritePaths={profile.asterisk_config} {profile.asr_config} {profile.runtime_host_root} /etc/systemd/system /etc/mosquitto /opt /var/cache/allscan-reimagined /var/lib/allscan-reimagined /var/lib/mmdvm /var/log/allscan-reimagined /var/log/mmdvm /run
TimeoutStartSec=1200
TasksMax=128
MemoryMax=2G
""", 0o644)
    atomic(ASTERISK_SOCKET_UNIT, f"""[Unit]
Description=ASR constrained Asterisk command socket

[Socket]
ListenStream=/run/allscan-reimagined-bridge-rpc/asterisk.sock
SocketMode=0660
SocketUser=root
SocketGroup={SERVICE_USER}
DirectoryMode=0755
RemoveOnStop=true
Accept=yes
MaxConnections=4

[Install]
WantedBy=sockets.target
""", 0o644)
    atomic(ASTERISK_SERVICE_UNIT, f"""[Unit]
Description=ASR constrained Asterisk command request

[Service]
Type=oneshot
Environment=ASR_CONTAINER_PROVISIONING_PROFILE={PROFILE}
ExecStart={DEST}/asr-container-asterisk.py --broker
StandardInput=socket
StandardOutput=socket
StandardError=journal
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
BindReadOnlyPaths={profile.asterisk_config} {profile.asr_config} {PROFILE} /var/run/docker.sock
RestrictAddressFamilies=AF_UNIX
TimeoutStartSec=30
TasksMax=16
MemoryMax=128M
""", 0o644)
    atomic(Path("/etc/systemd/system/allscan-reimagined-m17-bridge@.service"), f"""[Unit]
Description=Run isolated ASR M17 bridge instance %i
After=network-online.target {profile.asterisk_container}.service
Wants=network-online.target

[Service]
Type=simple
Environment=ASR_CONTAINER_PROVISIONING_PROFILE={PROFILE}
ExecStart={DEST}/asr-container-netns-exec.py --container {profile.asterisk_container} --proc-root {profile.host_proc} -- /usr/local/sbin/allscan-reimagined-m17-usrp-connector --bridge %i --run
Restart=on-failure
RestartPreventExitStatus=78
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
RuntimeDirectory=allscan-reimagined-m17
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
BindReadOnlyPaths={profile.asterisk_config} {profile.asr_config} {PROFILE}
ReadWritePaths=/run/allscan-reimagined-m17 /var/log/allscan-reimagined /var/lib/allscan-reimagined/m17
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
""", 0o644)
    atomic(Path("/etc/systemd/system/allscan-reimagined-container-recovery.service"), f"""[Unit]
Description=Recover ASR bridges after Asterisk container namespace replacement
After=docker.service

[Service]
Type=oneshot
Environment=ASR_CONTAINER_PROVISIONING_PROFILE={PROFILE}
ExecStart={DEST}/asr-container-bridge-recovery.py
""", 0o644)
    atomic(Path("/etc/systemd/system/allscan-reimagined-container-recovery.timer"), """[Unit]
Description=Monitor Asterisk container namespace for ASR bridges

[Timer]
OnBootSec=20s
OnUnitActiveSec=15s
AccuracySec=2s

[Install]
WantedBy=timers.target
""", 0o644)
    atomic(Path("/etc/systemd/system/allscan-reimagined-m17-hosts-update.service"), f"""[Unit]
Description=Refresh the validated M17 reflector catalog
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={DEST}/asr-m17-hosts-update.py
User=root
Group=root
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/allscan-reimagined/m17
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
""", 0o644)
    atomic(Path("/etc/systemd/system/allscan-reimagined-m17-hosts-update.timer"), """[Unit]
Description=Refresh the M17 reflector catalog daily

[Timer]
OnBootSec=5m
OnUnitActiveSec=1d
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
""", 0o644)
    for mode in ("ysf", "p25", "nxdn"):
        helper = Path("/usr/local/sbin") / f"allscan-reimagined-{mode}-bridge-control"
        run_dir = Path("/run") / f"allscan-reimagined-{mode}-bridge-control"
        run_dir.mkdir(parents=True, exist_ok=True)
        if mode == "ysf":
            watch_command = f"{helper} --watch"
            watch_user = SERVICE_USER
            watch_group = group
            watch_prefix = ""
            socket_guard = "InaccessiblePaths=/var/run/docker.sock"
        else:
            # P25/NXDN MQTT brokers intentionally listen only on loopback inside
            # the Asterisk container network namespace. Enter that namespace,
            # then drop to the bridge service account before running the watcher.
            watch_command = (
                f"{DEST}/asr-container-netns-exec.py --container {profile.asterisk_container} "
                f"--proc-root {profile.host_proc} --user {SERVICE_USER} -- {helper} watch --interval 2"
            )
            # RuntimeDirectory ownership follows User/Group.  Keep it owned by
            # the dropped service account and web-reader group, while the '+'
            # prefix starts only the audited setns launcher as root.
            watch_user = SERVICE_USER
            watch_group = group
            watch_prefix = "+"
            socket_guard = ""
        # The proven namespace launcher needs the host's normal root capability
        # set only until setns succeeds.  It immediately setuid/setgid drops to
        # asr-bridge, and NoNewPrivileges prevents regaining capabilities.
        capability_guard = ("" if mode != "ysf" else
                            "CapabilityBoundingSet=\nAmbientCapabilities=\n")
        atomic(Path("/etc/systemd/system") / f"allscan-reimagined-{mode}-net-live.service", f"""[Unit]
Description=ASR {mode.upper()} managed bridge status
After=network-online.target

[Service]
Type=simple
Environment=ASR_CONTAINER_PROVISIONING_PROFILE={PROFILE}
Environment=ASR_CONTAINER_WATCH_CONFIG=/run/allscan-reimagined-bridge-rpc/config.json
Environment=ASR_CONTAINER_WATCH_SECRETS=/run/allscan-reimagined-bridge-rpc/bridge-mqtt-secrets.json
ExecStart={watch_prefix}{watch_command}
User={watch_user}
Group={watch_group}
SupplementaryGroups={SERVICE_USER}
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RuntimeDirectory={run_dir.name}
RuntimeDirectoryMode=2750
RuntimeDirectoryPreserve=yes
BindReadOnlyPaths={profile.asterisk_config} {profile.asr_config} {PROFILE}
ReadWritePaths={run_dir} /var/log/allscan-reimagined
{socket_guard}
{capability_guard}

[Install]
WantedBy=multi-user.target
""", 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    # This command is an explicit administrator reapply boundary.  Restart the
    # host-side entry points so newly installed validation and sandbox policy is
    # effective immediately; no web request can invoke this installer.
    subprocess.run(["systemctl", "enable", SOCKET_UNIT.name], check=True)
    subprocess.run(["systemctl", "restart", SOCKET_UNIT.name], check=True)
    subprocess.run(["systemctl", "enable", ASTERISK_SOCKET_UNIT.name], check=True)
    subprocess.run(["systemctl", "restart", ASTERISK_SOCKET_UNIT.name], check=True)
    subprocess.run(["systemctl", "enable", "allscan-reimagined-container-recovery.timer"], check=True)
    subprocess.run(["systemctl", "restart", "allscan-reimagined-container-recovery.timer"], check=True)
    subprocess.run(["systemctl", "enable", "allscan-reimagined-m17-hosts-update.timer"], check=True)
    subprocess.run(["systemctl", "restart", "allscan-reimagined-m17-hosts-update.timer"], check=True)
    for mode in ("ysf", "p25", "nxdn"):
        unit = f"allscan-reimagined-{mode}-net-live.service"
        subprocess.run(["systemctl", "enable", unit], check=True)
        subprocess.run(["systemctl", "restart", unit], check=True)
    # Reapply updated helpers and unit policy only to instances whose root-owned
    # installation records prove that they are owned by ASR.
    recovery_env = os.environ.copy()
    recovery_env["ASR_CONTAINER_PROVISIONING_PROFILE"] = str(PROFILE)
    subprocess.run([str(DEST / "asr-container-bridge-recovery.py"), "--restart-owned"],
                   check=True, env=recovery_env)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("detect", "install"))
    parser.add_argument("--web-container", required=True)
    parser.add_argument("--asterisk-container", required=True)
    parser.add_argument("--client-user", default="www-data")
    parser.add_argument("--host-proc", type=Path, default=Path("/proc"))
    parser.add_argument("--runtime-host-root", type=Path,
                        default=Path("/opt/allscan-reimagined-bridges"))
    args = parser.parse_args()
    try:
        account = pwd.getpwnam(args.client_user)
        profile = backend().discover(args.web_container, args.asterisk_container,
                                     client_uid=account.pw_uid, client_gid=account.pw_gid,
                                     host_proc=args.host_proc,
                                     runtime_host_root=args.runtime_host_root)
        if args.command == "install":
            install(profile)
        print(profile_json(profile), end="")
        return 0
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
