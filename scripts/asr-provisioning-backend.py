#!/usr/bin/env python3
"""Provisioning backend selection and container-host path helpers.

Native ASL3 is deliberately the default.  A container profile is honored only
inside the root-only host broker and is never inferred from Docker's mere
presence.
"""
from __future__ import annotations

import json
import grp
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROFILE_ENV = "ASR_CONTAINER_PROVISIONING_PROFILE"
CONTAINER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
HOST_PATH_RE = re.compile(r"/[A-Za-z0-9_./-]+")


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContainerProfile:
    asterisk_container: str
    asterisk_config: Path
    asr_config: Path
    client_uid: int
    client_gid: int
    host_proc: Path
    runtime_host_root: Path

    @classmethod
    def parse(cls, value: Any) -> "ContainerProfile":
        if not isinstance(value, dict) or set(value) != {
            "schema", "backend", "asteriskContainer", "asteriskConfig",
            "asrConfig", "clientUid", "clientGid", "hostProc", "runtimeHostRoot",
        }:
            raise BackendError("container provisioning profile has an invalid shape")
        if value.get("schema") != 1 or value.get("backend") != "container-host":
            raise BackendError("unsupported container provisioning profile")
        name = value.get("asteriskContainer")
        if not isinstance(name, str) or not CONTAINER_RE.fullmatch(name):
            raise BackendError("invalid Asterisk container identity")
        paths: list[Path] = []
        for key in ("asteriskConfig", "asrConfig"):
            raw = value.get(key)
            if (not isinstance(raw, str) or not HOST_PATH_RE.fullmatch(raw)
                    or ".." in Path(raw).parts):
                raise BackendError(f"invalid {key} path")
            path = Path(raw)
            if len(path.parts) < 3 or path.is_symlink() or not path.is_dir():
                raise BackendError(f"{key} is not a safe directory")
            paths.append(path.resolve())
        if paths[0] == paths[1] or paths[0] in paths[1].parents or paths[1] in paths[0].parents:
            raise BackendError("container state mounts overlap")
        uid = value.get("clientUid")
        gid = value.get("clientGid")
        if (not isinstance(uid, int) or not 1 <= uid <= 65535
                or not isinstance(gid, int) or not 1 <= gid <= 65535):
            raise BackendError("invalid broker client identity")
        host_proc_raw = value.get("hostProc")
        if not isinstance(host_proc_raw, str) or not HOST_PATH_RE.fullmatch(host_proc_raw):
            raise BackendError("invalid host proc path")
        host_proc = Path(host_proc_raw)
        if not (host_proc / "self/status").is_file():
            raise BackendError("host proc filesystem is unavailable")
        runtime_raw = value.get("runtimeHostRoot")
        if (not isinstance(runtime_raw, str) or not HOST_PATH_RE.fullmatch(runtime_raw)
                or ".." in Path(runtime_raw).parts):
            raise BackendError("invalid host runtime root")
        runtime = Path(runtime_raw)
        if len(runtime.parts) < 3 or runtime.is_symlink():
            raise BackendError("host runtime root is unsafe")
        if any(runtime == item or runtime in item.parents or item in runtime.parents for item in paths):
            raise BackendError("host provisioning roots overlap")
        return cls(name, paths[0], paths[1], uid, gid, host_proc.resolve(), runtime)


def load_profile(path: Path | None = None) -> ContainerProfile | None:
    raw = str(path) if path else os.environ.get(PROFILE_ENV, "")
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise BackendError("container provisioning profile is unsafe")
    info = candidate.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise BackendError("container provisioning profile must be root-owned and immutable")
    try:
        return ContainerProfile.parse(json.loads(candidate.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise BackendError("container provisioning profile is invalid") from exc


def refresh_watch_snapshots(profile: ContainerProfile, group_name: str = "asr-bridge") -> None:
    """Publish read-only runtime inputs without exposing the writable ASR tree."""
    if os.geteuid() != 0:
        raise BackendError("watch snapshots require root")
    target_dir = Path("/run/allscan-reimagined-bridge-rpc")
    info = target_dir.lstat()
    if (not target_dir.is_dir() or target_dir.is_symlink() or info.st_uid != 0
            or info.st_mode & 0o022):
        raise BackendError("watch snapshot directory is unsafe")
    try:
        gid = grp.getgrnam(group_name).gr_gid
    except KeyError as exc:
        raise BackendError("bridge service group is unavailable") from exc
    sources = {
        "config.json": profile.asr_config / "config.json",
        "bridge-mqtt-secrets.json": profile.asr_config / "bridge-mqtt-secrets.json",
    }
    for name, source in sources.items():
        if not source.exists() and name == "bridge-mqtt-secrets.json":
            continue
        source_info = source.lstat()
        # config.json is maintained by the provisioned web identity.  Trust
        # only that exact identity (or root), and still reject writable group/
        # other bits so another container account cannot replace its contents.
        if (source.is_symlink() or not source.is_file()
                or source_info.st_uid not in {0, profile.client_uid}
                or source_info.st_nlink != 1 or source_info.st_mode & 0o022):
            raise BackendError(f"watch snapshot source is unsafe: {name}")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=target_dir)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(source.read_bytes())
                handle.flush()
                os.fchmod(handle.fileno(), 0o640)
                os.fchown(handle.fileno(), 0, gid)
                os.fsync(handle.fileno())
            os.replace(temporary, target_dir / name)
        finally:
            Path(temporary).unlink(missing_ok=True)


def map_path(root: Path, logical: str) -> Path:
    if not logical.startswith("/") or ".." in Path(logical).parts:
        raise BackendError("unsafe logical provisioning path")
    profile = load_profile()
    if root != Path("/") or profile is None:
        return root / logical.lstrip("/")
    mappings = (
        ("/etc/asterisk", profile.asterisk_config),
        ("/etc/allscan-reimagined", profile.asr_config),
        ("/opt/allscan-reimagined-bridges", profile.runtime_host_root),
    )
    for prefix, target in mappings:
        if logical == prefix:
            candidate = target
            relative = Path()
        elif logical.startswith(prefix + "/"):
            relative = Path(logical[len(prefix) + 1:])
            candidate = target / relative
        else:
            continue
        current = target
        if current.is_symlink():
            raise BackendError("mapped provisioning root is a symbolic link")
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise BackendError("symbolic link in mapped provisioning path")
        try:
            candidate.relative_to(target)
        except ValueError as exc:
            raise BackendError("mapped provisioning path escaped its root") from exc
        return candidate
    # Unmapped paths are fixed application-owned host paths.  They retain the
    # native installer's path-specific validation; common host files such as
    # /etc/os-release are legitimately symlinks on supported systems.
    return Path(logical)


def docker_inspect(name: str, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> dict[str, Any]:
    if not CONTAINER_RE.fullmatch(name):
        raise BackendError("invalid container identity")
    result = runner(
        ["docker", "inspect", name], capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise BackendError(f"container {name} is not inspectable")
    try:
        rows = json.loads(result.stdout)
    except ValueError as exc:
        raise BackendError("Docker inspection returned invalid JSON") from exc
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise BackendError("Docker inspection was ambiguous")
    return rows[0]


def mounted_source(container: dict[str, Any], destination: str) -> Path:
    matches = [m for m in container.get("Mounts", [])
               if isinstance(m, dict) and m.get("Destination") == destination
               and m.get("RW") is True and m.get("Type") in {"bind", "volume"}]
    if len(matches) != 1:
        raise BackendError(f"container has no unique writable {destination} mount")
    source = matches[0].get("Source")
    if (not isinstance(source, str) or not HOST_PATH_RE.fullmatch(source)
            or len(Path(source).parts) < 3):
        raise BackendError(f"container {destination} mount source is unsafe")
    path = Path(source)
    if path.is_symlink() or not path.is_dir():
        raise BackendError(f"container {destination} mount source is unavailable")
    return path.resolve()


def validate_asterisk_runtime(name: str,
                              runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    probe = runner(
        ["docker", "exec", name, "sh", "-c",
         "test $(cat /proc/1/comm) = asterisk && /usr/sbin/asterisk -rx 'module show like chan_usrp.so' | grep -q chan_usrp.so"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode:
        raise BackendError("container is not an active chan_usrp Asterisk runtime")


def discover(web_container: str, asterisk_container: str,
             runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
             client_uid: int = 33, client_gid: int = 33, host_proc: Path = Path("/proc"),
             runtime_host_root: Path = Path("/opt/allscan-reimagined-bridges")) -> ContainerProfile:
    """Fail-closed discovery of the two writable, persistent state roots."""
    if web_container == asterisk_container:
        raise BackendError("web and Asterisk containers must be distinct")
    web = docker_inspect(web_container, runner)
    asterisk = docker_inspect(asterisk_container, runner)
    if web.get("State", {}).get("Running") is not True or asterisk.get("State", {}).get("Running") is not True:
        raise BackendError("provisioning containers must be running")
    config = mounted_source(asterisk, "/etc/asterisk")
    asr = mounted_source(web, "/etc/allscan-reimagined")
    if config == asr or config in asr.parents or asr in config.parents:
        raise BackendError("container state mounts overlap")
    for path in (config / "rpt.conf", config / "modules.conf", asr / "config.json"):
        if path.is_symlink() or not path.is_file():
            raise BackendError(f"required persistent state is unavailable: {path.name}")
    validate_asterisk_runtime(asterisk_container, runner)
    if not (host_proc / "self/status").is_file():
        raise BackendError("host proc filesystem is unavailable")
    if (not runtime_host_root.is_absolute() or not HOST_PATH_RE.fullmatch(str(runtime_host_root))
            or len(runtime_host_root.parts) < 3
            or ".." in runtime_host_root.parts or runtime_host_root.is_symlink()
            or any(runtime_host_root == item or runtime_host_root in item.parents or item in runtime_host_root.parents
                   for item in (config, asr))):
        raise BackendError("host runtime root is unsafe")
    if not 1 <= client_gid <= 65535:
        raise BackendError("invalid broker client GID")
    return ContainerProfile(asterisk_container, config, asr, client_uid, client_gid,
                            host_proc.resolve(), runtime_host_root)


def validate_active_profile(profile: ContainerProfile,
                            runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
    """Revalidate mutable container identities and mounts for every request."""
    asterisk = docker_inspect(profile.asterisk_container, runner)
    if asterisk.get("State", {}).get("Running") is not True:
        raise BackendError("Asterisk container is not running")
    if mounted_source(asterisk, "/etc/asterisk") != profile.asterisk_config:
        raise BackendError("Asterisk container configuration mount changed")
    for path in (profile.asterisk_config / "rpt.conf",
                 profile.asterisk_config / "modules.conf",
                 profile.asr_config / "config.json"):
        if path.is_symlink() or not path.is_file():
            raise BackendError("persistent provisioning state changed")
    validate_asterisk_runtime(profile.asterisk_container, runner)
    result = runner(["docker", "ps", "--filter", "status=running", "--format", "{{.Names}}"],
                    capture_output=True, text=True, check=False)
    if result.returncode:
        raise BackendError("running containers could not be enumerated")
    matches: list[str] = []
    for name in result.stdout.splitlines():
        try:
            item = docker_inspect(name.strip(), runner)
            if mounted_source(item, "/etc/allscan-reimagined") == profile.asr_config:
                matches.append(name.strip())
        except BackendError:
            continue
    if len(matches) != 1:
        raise BackendError("ASR configuration mount is no longer uniquely active")


def asterisk_module_available(module: str = "chan_usrp.so") -> bool:
    profile = load_profile()
    if profile is None:
        candidates = list(Path("/usr/lib").glob(f"asterisk/modules/{module}"))
        candidates += list(Path("/usr/lib").glob(f"*/asterisk/modules/{module}"))
        return bool(candidates)
    result = subprocess.run(
        ["docker", "exec", profile.asterisk_container, "sh", "-c",
         'test -f "/usr/lib/asterisk/modules/$1" || find /usr/lib -path "*/asterisk/modules/$1" -type f -print -quit | grep -q .',
         "asr-module-check", module],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    return result.returncode == 0


def network_prefix(user: str = "") -> list[str]:
    profile = load_profile()
    if profile is None:
        return []
    prefix = ["/usr/local/libexec/allscan-reimagined/asr-container-netns-exec.py",
              "--container", profile.asterisk_container, "--proc-root", str(profile.host_proc)]
    if user:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            raise BackendError("invalid service account")
        prefix += ["--user", user]
    return [*prefix, "--"]


def systemd_exec(command: list[str], user: str = "") -> str:
    if not command or any(not isinstance(item, str) or not item or any(c in item for c in "\r\n\0") for item in command):
        raise BackendError("invalid service command")
    return " ".join([*network_prefix(user), *command])


def docker_network_mode() -> str:
    profile = load_profile()
    return f"container:{profile.asterisk_container}" if profile else "host"
