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
    "asr-bridge-runtime-sources.py", "asr-bridge-setup-digital.py",
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


def install(profile) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("container host backend installation requires root")
    mqtt_secrets = profile.asr_config / "bridge-mqtt-secrets.json"
    if mqtt_secrets.exists() or mqtt_secrets.is_symlink():
        info = mqtt_secrets.stat(follow_symlinks=False)
        if (mqtt_secrets.is_symlink() or not mqtt_secrets.is_file()
                or info.st_uid != 0 or info.st_nlink != 1
                or (info.st_mode & 0o777) != 0o600):
            raise RuntimeError("existing MQTT credential file is unsafe")
        # Some older layered installs assigned the web GID despite mode 0600.
        # Normalize metadata without reading or replacing credential content.
        os.chown(mqtt_secrets, 0, 0)
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
    except Exception:
        if previous_runtime.exists() and not runtime_target.exists():
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
    atomic(SOCKET_TMPFILES,
           f"d /run/allscan-reimagined-host 0750 root {group} -\n", 0o644)
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
ProtectHome=true
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
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
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
        watch_command = f"{helper} --watch" if mode == "ysf" else f"{helper} watch --interval 2"
        atomic(Path("/etc/systemd/system") / f"allscan-reimagined-{mode}-net-live.service", f"""[Unit]
Description=ASR {mode.upper()} managed bridge status
After=network-online.target

[Service]
Type=simple
Environment=ASR_CONTAINER_PROVISIONING_PROFILE={PROFILE}
Environment=ASR_CONTAINER_WATCH_CONFIG=/run/allscan-reimagined-bridge-rpc/config.json
Environment=ASR_CONTAINER_WATCH_SECRETS=/run/allscan-reimagined-bridge-rpc/bridge-mqtt-secrets.json
ExecStart={watch_command}
User={SERVICE_USER}
Group={SERVICE_USER}
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
BindReadOnlyPaths={profile.asterisk_config} {profile.asr_config} {PROFILE}
ReadWritePaths={run_dir} /var/log/allscan-reimagined
InaccessiblePaths=/var/run/docker.sock
CapabilityBoundingSet=
AmbientCapabilities=

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
