#!/usr/bin/env python3
"""Constrained, audited systemd timer administration for ASR.

The web server may invoke this program through sudo. It accepts no shell
fragments or arbitrary paths: commands use fixed argv arrays, unit names are
validated, vendor definitions are never replaced, and schedule writes are
limited to an ASR-owned drop-in or an ASR Admin-owned timer file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_ANALYZE = "/usr/bin/systemd-analyze"
JOURNALCTL = "/usr/bin/journalctl"
UNIT_DIR = Path("/etc/systemd/system")
BACKUP_DIR = Path("/var/lib/allscan-reimagined/timer-backups")
AUDIT_LOG = Path("/var/log/allscan-reimagined/timer-audit.jsonl")
LOCK_FILE = Path("/run/lock/allscan-reimagined-timer-control.lock")

UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,240}\.timer$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,240}\.service$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9_.@+ -]{1,80}$")
CALENDAR_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9*,:./ _~+\-]{0,159}$")
DESCRIPTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:_()/'\-]{0,159}$")
TIMESPAN_RE = re.compile(
    r"^[0-9]+(?:\.[0-9]+)?(?:\s*(?:us|ms|s|sec|second|seconds|min|minute|minutes|h|hour|hours|d|day|days|w|week|weeks|month|months|y|year|years))(?:\s+[0-9]+(?:\.[0-9]+)?\s*(?:us|ms|s|sec|second|seconds|min|minute|minutes|h|hour|hours|d|day|days|w|week|weeks|month|months|y|year|years))*$",
    re.IGNORECASE,
)
ASR_PREFIX = "allscan-reimagined-"
MANAGED_PREFIX = "allscan-reimagined-admin-"
DROPIN_NAME = "90-allscan-reimagined-schedule.conf"
SCHEDULE_TYPES = {
    "calendar": "OnCalendar",
    "boot": "OnBootSec",
    "startup": "OnStartupSec",
    "active": "OnActiveSec",
    "unit-active": "OnUnitActiveSec",
    "unit-inactive": "OnUnitInactiveSec",
}
CRITICAL = {
    "apt-daily.timer", "apt-daily-upgrade.timer", "systemd-tmpfiles-clean.timer",
    "fstrim.timer", "logrotate.timer", "man-db.timer", "fwupd-refresh.timer",
    "systemd-journal-flush.timer", "systemd-journal-rotate.timer",
    # ASR's own safety timers: stopping these silently freezes self-repair
    # (reapply/integrity) or leaves timed Global Bans in force indefinitely.
    "allscan-reimagined-reapply.timer", "allscan-reimagined-reapply-delayed.timer",
    "allscan-reimagined-global-ban-expire.timer",
}
MUTATING_ACTIONS = {"enable", "disable", "start", "stop", "restart", "trigger"}
CRITICAL_SERVICES = {
    "asterisk.service", "apache2.service", "dbus.service", "ssh.service", "sshd.service",
    "systemd-journald.service", "systemd-logind.service", "systemd-udevd.service",
}
CRITICAL_DEPENDENCIES = {
    "emergency.target", "rescue.target", "shutdown.target", "initrd.target",
    "systemd-journald.service", "systemd-logind.service", "systemd-udevd.service",
}


class ControlError(RuntimeError):
    pass


def validate_unit(name: str, *, managed: bool = False) -> str:
    if not isinstance(name, str) or not UNIT_RE.fullmatch(name) or ".." in name or "/" in name:
        raise ControlError("Invalid timer unit name")
    if managed and not name.startswith(MANAGED_PREFIX):
        raise ControlError("Only ASR Admin-owned timers may be created, edited, deleted, or rolled back")
    return name


def validate_service(name: str, *, asr_only: bool = True) -> str:
    if not isinstance(name, str) or not SERVICE_RE.fullmatch(name) or ".." in name or "/" in name:
        raise ControlError("Invalid service unit name")
    if asr_only and not name.startswith(ASR_PREFIX):
        raise ControlError("Supported timers may trigger only ASR-owned services")
    return name


def invoking_account() -> pwd.struct_passwd:
    account = os.environ.get("SUDO_USER", "")
    if not account or account == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", account):
        raise ControlError("The invoking user account is unavailable for user-timer management")
    try:
        return pwd.getpwnam(account)
    except KeyError as exc:
        raise ControlError("The invoking user account does not exist") from exc


def validate_actor(actor: str) -> str:
    if not ACTOR_RE.fullmatch(actor or ""):
        raise ControlError("Invalid audit actor")
    return actor


def secure_directory(path: Path, mode: int) -> None:
    if path.is_symlink():
        raise ControlError(f"Unsafe symlinked directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or (os.geteuid() == 0 and info.st_uid != 0):
        raise ControlError(f"Unsafe privileged directory: {path}")
    os.chmod(path, mode)


def run(argv: list[str], *, timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(str(exc)) from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().replace("\x00", "")[:500]
        raise ControlError(detail or f"Command failed with status {result.returncode}")
    return result


def systemctl(*args: str, user: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    if not user:
        return run([SYSTEMCTL, "--system", "--no-pager", *args], check=check)
    return run(as_invoking_user([SYSTEMCTL, "--user", "--no-pager", *args]), check=check)


def parse_show(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def as_invoking_user(argv: list[str]) -> list[str]:
    account = invoking_account()
    return ["/usr/sbin/runuser", "-u", account.pw_name, "--", "/usr/bin/env", f"XDG_RUNTIME_DIR=/run/user/{account.pw_uid}", *argv]


def validate_schedule(schedule_type: str, value: str) -> tuple[str, str]:
    directive = SCHEDULE_TYPES.get(schedule_type)
    if not directive:
        raise ControlError("Unsupported timer schedule type")
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise ControlError("Invalid timer schedule")
    value = value.strip()
    if schedule_type == "calendar":
        if not CALENDAR_RE.fullmatch(value):
            raise ControlError("Invalid OnCalendar expression")
        run([SYSTEMD_ANALYZE, "calendar", value])
    else:
        if not TIMESPAN_RE.fullmatch(value):
            raise ControlError("Invalid monotonic timer interval")
        run([SYSTEMD_ANALYZE, "timespan", value])
    return directive, value


def triggered_services(properties: dict[str, str]) -> list[str]:
    return [item for item in properties.get("Triggers", "").split() if SERVICE_RE.fullmatch(item)]


def protection_reason(name: str, *, user: bool = False, properties: dict[str, str] | None = None) -> str:
    if name in CRITICAL:
        return "critical timer"
    props = properties if properties is not None else parse_show(systemctl(
        "show", name,
        "--property=Id,Names,LoadState,Triggers,Requires,Requisite,BindsTo,PartOf,Transient,FragmentPath,UnitFileState",
        user=user, check=False,
    ).stdout)
    if props.get("LoadState") != "loaded":
        return "timer is not loaded or could not be verified"
    identities = set(props.get("Names", "").split()) | {props.get("Id", ""), name}
    if identities & CRITICAL:
        return "critical timer identity"
    if props.get("Id") and props["Id"] != name:
        return "timer alias is not independently mutable"
    if props.get("Transient") == "yes" or props.get("FragmentPath", "").startswith("/run/") or props.get("UnitFileState") in {"generated", "transient", "alias"}:
        return "generated, transient or aliased timer"
    services = triggered_services(props)
    if any(service in CRITICAL_SERVICES for service in services):
        return "timer triggers a protected service"
    dependencies: set[str] = set()
    for key in ("Requires", "Requisite", "BindsTo", "PartOf"):
        dependencies.update(props.get(key, "").split())
    if dependencies & CRITICAL_DEPENDENCIES:
        return "timer participates in a critical dependency chain"
    return ""


def format_usec(value: Any) -> str:
    """Render a systemd epoch-microsecond timestamp like `systemctl show` does."""
    try:
        usec = int(value)
    except (TypeError, ValueError):
        return ""
    if usec <= 0:
        return ""
    return time.strftime("%a %Y-%m-%d %H:%M:%S %Z", time.localtime(usec / 1_000_000))


def runtime_schedule(*, user: bool = False) -> dict[str, dict[str, str]]:
    """Next/last elapse for every loaded timer, calendar and monotonic alike.

    `NextElapseUSecRealtime` is empty for OnBootSec/OnUnitActiveSec timers;
    `systemctl list-timers` merges both clocks into one wall-clock time.
    """
    listed = systemctl("list-timers", "--all", "--output=json", user=user, check=False)
    if listed.returncode != 0:
        return {}
    try:
        rows = json.loads(listed.stdout)
    except json.JSONDecodeError:
        return {}
    result: dict[str, dict[str, str]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("unit"):
            result[str(row["unit"])] = {"next": format_usec(row.get("next")), "last": format_usec(row.get("last"))}
    return result


def timer_rows(*, user: bool = False) -> list[dict[str, Any]]:
    files = systemctl("list-unit-files", "--type=timer", "--all", "--output=json", user=user)
    try:
        rows = json.loads(files.stdout)
    except json.JSONDecodeError as exc:
        raise ControlError("systemctl returned invalid timer inventory") from exc
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            by_name[str(row.get("unit_file", ""))] = row
    runtime = systemctl("list-timers", "--all", "--output=json", user=user, check=False)
    if runtime.returncode == 0:
        try:
            runtime_rows = json.loads(runtime.stdout)
        except json.JSONDecodeError:
            runtime_rows = []
        for row in runtime_rows if isinstance(runtime_rows, list) else []:
            name = str(row.get("unit", "")) if isinstance(row, dict) else ""
            if name and name not in by_name:
                by_name[name] = {"unit_file": name, "state": "generated", "preset": "-"}
    schedule = runtime_schedule(user=user)
    result: list[dict[str, Any]] = []
    properties = "Id,Names,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,SourcePath,Transient,NextElapseUSecRealtime,LastTriggerUSec,Result,Triggers,Requires,Requisite,BindsTo,PartOf,Description"
    for row in by_name.values():
        name = row.get("unit_file", "") if isinstance(row, dict) else ""
        try:
            validate_unit(name)
        except ControlError:
            continue
        shown = systemctl("show", name, f"--property={properties}", user=user, check=False)
        props = parse_show(shown.stdout) if shown.returncode == 0 else {}
        services = triggered_services(props)
        service_props: dict[str, str] = {}
        if len(services) == 1:
            service_show = systemctl("show", services[0], "--property=ActiveState,SubState,Result,ExecMainStatus", user=user, check=False)
            if service_show.returncode == 0:
                service_props = parse_show(service_show.stdout)
        failed = service_props.get("ActiveState") == "failed" or service_props.get("Result", "") not in ("", "success")
        protected_reason = protection_reason(name, user=user, properties=props)
        fragment = props.get("FragmentPath", "")
        editable = not protected_reason and props.get("LoadState") != "not-found" and props.get("Transient") != "yes" and not fragment.startswith("/run/")
        result.append({
            "name": name, "scope": "user" if user else "system",
            "unitFileState": row.get("state", "unknown"), "preset": row.get("preset", "unknown"),
            "loadState": props.get("LoadState", "unknown"), "activeState": props.get("ActiveState", "unknown"),
            "subState": props.get("SubState", "unknown"), "next": props.get("NextElapseUSecRealtime", "") or schedule.get(name, {}).get("next", ""),
            "previous": props.get("LastTriggerUSec", "") or schedule.get(name, {}).get("last", ""), "result": props.get("Result", ""),
            "service": props.get("Triggers", ""), "description": props.get("Description", ""),
            "serviceState": "/".join(filter(None, [service_props.get("ActiveState", ""), service_props.get("SubState", "")])),
            "serviceResult": service_props.get("Result", ""), "serviceExecStatus": service_props.get("ExecMainStatus", ""),
            "failure": failed,
            "managed": name.startswith(MANAGED_PREFIX),
            "protected": bool(protected_reason), "protectionReason": protected_reason,
            "editable": editable,
        })
    return sorted(result, key=lambda item: (item["scope"], item["name"]))


def inventory(scope: str) -> dict[str, Any]:
    data: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    scopes = ("system", "user") if scope == "all" else (scope,)
    for current in scopes:
        try:
            data.extend(timer_rows(user=current == "user"))
        except ControlError as exc:
            errors[current] = str(exc)
    if not data and errors:
        raise ControlError("; ".join(f"{key}: {value}" for key, value in errors.items()))
    return {"timers": data, "errors": errors}


def inspect_timer(name: str, *, user: bool = False) -> dict[str, Any]:
    validate_unit(name)
    properties = (
        "Id,Names,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,SourcePath,"
        "DropInPaths,Triggers,NextElapseUSecRealtime,NextElapseUSecMonotonic,"
        "LastTriggerUSec,Result,Description,Transient,Requires,Requisite,BindsTo,PartOf"
    )
    props = parse_show(systemctl("show", name, f"--property={properties}", user=user).stdout)
    definition = systemctl("cat", name, user=user, check=False)
    service_names = triggered_services(props)
    service_properties: dict[str, str] = {}
    if len(service_names) == 1:
        service_show = systemctl("show", service_names[0], "--property=ActiveState,SubState,Result,ExecMainStatus,ExecMainCode", user=user, check=False)
        if service_show.returncode == 0:
            service_properties = parse_show(service_show.stdout)
    unit_option = "--user-unit" if user else "--unit"
    history_argv = [JOURNALCTL, unit_option, name]
    if len(service_names) == 1:
        history_argv.extend([unit_option, service_names[0]])
    history_argv.extend(["--no-pager", "--lines=60", "--output=short-iso"])
    history = run(as_invoking_user(history_argv) if user else history_argv, check=False)
    schedule_next = runtime_schedule(user=user).get(name, {}).get("next", "")
    if schedule_next and not props.get("NextElapseUSecRealtime"):
        props["NextElapseUSecRealtime"] = schedule_next
    protected_reason = protection_reason(name, user=user, properties=props)
    fragment = props.get("FragmentPath", "")
    return {
        "name": name, "scope": "user" if user else "system", "properties": props,
        "serviceProperties": service_properties,
        "definition": definition.stdout[:30000] if definition.returncode == 0 else "",
        "history": history.stdout[-30000:] if history.returncode == 0 else "",
        "historyError": (history.stderr or "")[:500] if history.returncode else "",
        "managed": name.startswith(MANAGED_PREFIX),
        "protected": bool(protected_reason), "protectionReason": protected_reason,
        "editable": not protected_reason and props.get("LoadState") != "not-found" and props.get("Transient") != "yes" and not fragment.startswith("/run/"),
    }


def render_timer(name: str, schedule_type: str, schedule: str, service: str, description: str, persistent: bool) -> str:
    validate_unit(name, managed=True)
    validate_service(service)
    directive, schedule = validate_schedule(schedule_type, schedule)
    if not DESCRIPTION_RE.fullmatch(description or "") or "\n" in description or "\r" in description:
        raise ControlError("Invalid timer description")
    timer_lines = ["[Timer]", f"{directive}={schedule}"]
    if schedule_type == "calendar":
        timer_lines.append(f"Persistent={'true' if persistent else 'false'}")
    timer_lines.extend(["AccuracySec=1min", f"Unit={service}"])
    return "\n".join([
        "# Managed by AllScan Reimagined Admin Scheduling", "[Unit]", f"Description={description}", "",
        *timer_lines, "", "[Install]", "WantedBy=timers.target", "",
    ])


def render_override(name: str, schedule_type: str, schedule: str, persistent: bool) -> str:
    validate_unit(name)
    directive, schedule = validate_schedule(schedule_type, schedule)
    lines = ["# Managed by AllScan Reimagined Admin Scheduling", "[Timer]"]
    for candidate in SCHEDULE_TYPES.values():
        lines.append(f"{candidate}=")
    lines.append(f"{directive}={schedule}")
    lines.append("Persistent=" + ("true" if persistent else "false") if schedule_type == "calendar" else "Persistent=false")
    return "\n".join([*lines, ""])


def confirmation(action: str, unit: str) -> str:
    return f"{action.upper()} {unit}"


def require_confirmation(action: str, unit: str, supplied: str) -> None:
    expected = confirmation(action, unit)
    if supplied != expected:
        raise ControlError(f"Type {expected} to confirm")


def audit(actor: str, action_name: str, unit: str, ok: bool, detail: str = "", *, scope: str = "system") -> None:
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "actor": validate_actor(actor),
        "action": action_name, "scope": scope, "unit": unit, "ok": bool(ok), "detail": detail[:500],
    }
    secure_directory(AUDIT_LOG.parent, 0o750)
    fd = os.open(AUDIT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o640)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.geteuid() == 0 and info.st_uid != 0):
            raise ControlError("Unsafe timer audit log")
        os.fchmod(fd, 0o640)
        os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


MAX_DEFINITION = 256 * 1024


def user_file(unit: str, mode: str, operation: str, content: str | None = None) -> str | None:
    """Perform user filesystem operations in a permanently unprivileged child.

    Traverse with directory descriptors, never follow links, and never reopen
    a checked pathname in the privileged parent.
    """
    validate_unit(unit, managed=mode == "direct")
    if mode not in {"direct", "override"} or operation not in {"read", "write", "remove"}:
        raise ControlError("Invalid user definition operation")
    account = invoking_account()
    if account.pw_uid == 0 or account.pw_gid == 0:
        raise ControlError("Privileged account is not a user timer account")
    home = Path(account.pw_dir)
    if not home.is_absolute() or home == Path("/") or ".." in home.parts:
        raise ControlError("Unsafe user home")
    if content is not None and len(content.encode("utf-8")) > MAX_DEFINITION:
        raise ControlError("Timer definition exceeds size limit")
    reader, writer = os.pipe()
    try:
        pid = os.fork()
    except OSError:
        os.close(reader)
        os.close(writer)
        raise
    if pid == 0:
        os.close(reader)
        try:
            if os.geteuid() == 0:
                os.initgroups(account.pw_name, account.pw_gid)
                os.setgid(account.pw_gid)
                os.setuid(account.pw_uid)
            if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
                raise ControlError("Could not drop filesystem privileges")
            result = _user_file_child(home, account.pw_uid, unit, mode, operation, content)
            reply = {"ok": True, "content": result}
        except BaseException as exc:
            reply = {"ok": False, "error": str(exc)[:500]}
        try:
            with os.fdopen(writer, "w", encoding="utf-8") as stream:
                json.dump(reply, stream, ensure_ascii=False)
        finally:
            os._exit(0)
    os.close(writer)
    try:
        with os.fdopen(reader, "r", encoding="utf-8") as stream:
            raw = stream.read(MAX_DEFINITION * 6 + 4096)
    finally:
        _, status = os.waitpid(pid, 0)
    if status != 0:
        raise ControlError("User definition worker failed")
    reply = json.loads(raw)
    if not reply.get("ok"):
        raise ControlError(reply.get("error", "User definition operation failed"))
    return reply.get("content")


def _user_file_child(home: Path, uid: int, unit: str, mode: str, operation: str, content: str | None) -> str | None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    temporary = None
    try:
        components = list(home.parts[1:]) + [".config", "systemd", "user"]
        if mode == "override":
            components.append(unit + ".d")
        for index, component in enumerate(components):
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if operation != "write":
                    return None
                if index < len(home.parts) - 1:
                    raise ControlError("User home does not exist")
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            if index >= len(home.parts) - 2:
                info = os.fstat(fd)
                if info.st_uid != uid or info.st_mode & 0o022:
                    raise ControlError("Unsafe user timer directory ownership or permissions")
        name = unit if mode == "direct" else DROPIN_NAME
        try:
            existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            with os.fdopen(existing, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or info.st_mode & 0o022:
                    raise ControlError("Unsafe user timer definition")
                if operation == "read":
                    data = stream.read(MAX_DEFINITION + 1)
                    if len(data) > MAX_DEFINITION:
                        raise ControlError("Timer definition exceeds size limit")
                    return data.decode("utf-8")
        if operation == "read":
            return None
        if operation == "remove":
            if existing is not None:
                os.unlink(name, dir_fd=fd)
            return None
        if not isinstance(content, str):
            raise ControlError("Missing timer definition")
        import secrets
        temporary = ".asr-" + secrets.token_hex(16) + ".tmp"
        output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        temporary = None
        os.fsync(fd)
        return None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
        os.close(fd)


def definition_content(unit: str, *, user: bool, mode: str) -> str | None:
    if user:
        return user_file(unit, mode, "read")
    path, _, _ = storage_path(unit, user=False, mode=mode)
    if path.is_symlink() or (path.exists() and not stat.S_ISREG(path.lstat().st_mode)):
        raise ControlError("Unsafe timer definition")
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_definition(unit: str, content: str | None, *, user: bool, mode: str) -> None:
    if user:
        user_file(unit, mode, "remove" if content is None else "write", content)
        return
    path, uid, gid = storage_path(unit, user=False, mode=mode, create=content is not None)
    if path.is_symlink():
        raise ControlError("Unsafe timer definition path")
    if content is None:
        if path.exists():
            path.unlink()
        if mode == "override" and path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    else:
        atomic_write(path, content, uid=uid, gid=gid, verify=mode == "direct")


def storage_path(unit: str, *, user: bool, mode: str, create: bool = False) -> tuple[Path, int, int]:
    validate_unit(unit, managed=mode == "direct")
    if mode not in {"direct", "override"}:
        raise ControlError("Invalid timer storage mode")
    if user:
        raise ControlError("User paths must be accessed through the unprivileged filesystem worker")
    else:
        root, owner = UNIT_DIR, (0, 0)
    if mode == "direct":
        return root / unit, *owner
    directory = root / f"{unit}.d"
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise ControlError("Unsafe timer drop-in directory")
    if create and not directory.exists():
        directory.mkdir(mode=0o755)
        os.chown(directory, *owner)
    return directory / DROPIN_NAME, *owner


def backup_unit(unit: str, *, user: bool, mode: str) -> dict[str, Any]:
    validate_unit(unit, managed=mode == "direct")
    secure_directory(BACKUP_DIR, 0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    scope = "user" if user else "system"
    target = BACKUP_DIR / f"{scope}.{unit}.{stamp}.json"
    content = definition_content(unit, user=user, mode=mode)
    state = systemctl("is-enabled", unit, user=user, check=False).stdout.strip()
    active = systemctl("is-active", unit, user=user, check=False).stdout.strip()
    payload = {"unit": unit, "scope": scope, "mode": mode, "content": content,
               "enabled": state == "enabled", "active": active == "active", "created": stamp,
               "accountUid": invoking_account().pw_uid if user else None}
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(payload, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"id": target.name, **payload}


def atomic_write(path: Path, content: str, *, uid: int, gid: int, verify: bool) -> None:
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ControlError("Unsafe timer definition path")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".timer" if verify else ".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.chown(temporary, uid, gid)
        if verify:
            checked = run([SYSTEMD_ANALYZE, "verify", temporary], check=False)
            if checked.returncode:
                raise ControlError((checked.stderr or checked.stdout).strip()[:500] or "Timer validation failed")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_backup(payload: dict[str, Any], *, reload: bool = True) -> None:
    user = payload.get("scope") == "user"
    mode = payload.get("mode", "")
    unit = validate_unit(payload.get("unit", ""), managed=mode == "direct")
    if user and payload.get("accountUid") != invoking_account().pw_uid:
        raise ControlError("Backup belongs to another user account")
    content = payload.get("content")
    if content is not None and not isinstance(content, str):
        raise ControlError("Invalid backup content")
    if mode == "direct" and content is None and definition_content(unit, user=user, mode=mode) is not None:
        systemctl("disable", "--now", unit, user=user)
    write_definition(unit, content, user=user, mode=mode)
    if reload:
        systemctl("daemon-reload", user=user)
    if mode == "direct" and content is None:
        # No unit remains to enable or start.
        return
    systemctl("enable" if payload.get("enabled") else "disable", unit, user=user)
    systemctl("start" if payload.get("active") else "stop", unit, user=user)


def require_absent_timer(unit: str, *, user: bool) -> None:
    shown = systemctl("show", unit, "--property=Id,LoadState,FragmentPath", user=user, check=False)
    props = parse_show(shown.stdout) if shown.returncode == 0 else {}
    if props.get("LoadState") == "loaded":
        raise ControlError("Existing vendor timer must be edited through a managed override")
    if props.get("LoadState") != "not-found" or props.get("Id") != unit or props.get("FragmentPath"):
        raise ControlError("Cannot establish that timer is absent")


def apply_timer(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    require_confirmation("apply", unit, args.confirm)
    user = args.scope == "user"
    content = render_timer(unit, args.schedule_type, args.schedule, args.service, args.description, args.persistent)
    existing = definition_content(unit, user=user, mode="direct")
    if existing is None:
        require_absent_timer(unit, user=user)
    else:
        details = inspect_timer(unit, user=user)
        if details["protected"] or not details["editable"]:
            raise ControlError("Protected or uneditable timer cannot be replaced")
    prior = backup_unit(unit, user=user, mode="direct")
    try:
        write_definition(unit, content, user=user, mode="direct")
        systemctl("daemon-reload", user=user)
    except Exception:
        restore_backup(prior, reload=True)
        raise
    return {"unit": unit, "definition": content, "backup": prior["id"]}


def override_timer(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit)
    user = args.scope == "user"
    require_confirmation("override", unit, args.confirm)
    details = inspect_timer(unit, user=user)
    if details["protected"]:
        raise ControlError("Protected timer cannot be edited: " + details["protectionReason"])
    if not details["editable"]:
        raise ControlError("Generated or transient timer cannot be edited")
    content = render_override(unit, args.schedule_type, args.schedule, args.persistent)
    prior = backup_unit(unit, user=user, mode="override")
    try:
        write_definition(unit, content, user=user, mode="override")
        systemctl("daemon-reload", user=user)
    except Exception:
        restore_backup(prior, reload=True)
        raise
    return {"unit": unit, "definition": content, "backup": prior["id"]}


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit)
    require_confirmation("rollback", unit, args.confirm)
    backup_name = args.backup or ""
    scope = args.scope
    if not re.fullmatch(re.escape(scope) + r"\." + re.escape(unit) + r"\.\d{8}T\d{6}\.\d{6}Z\.json", backup_name):
        raise ControlError("Invalid backup ID")
    path = BACKUP_DIR / backup_name
    if path.parent != BACKUP_DIR or path.is_symlink() or not path.is_file():
        raise ControlError("Backup not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("unit") != unit or payload.get("scope") != scope:
        raise ControlError("Backup does not match timer")
    if scope == "user" and payload.get("accountUid") != invoking_account().pw_uid:
        raise ControlError("Backup belongs to another user account")
    if unit in CRITICAL:
        raise ControlError("Protected timer cannot be rolled back")
    mode = payload.get("mode")
    user = scope == "user"
    if mode not in {"direct", "override"}:
        raise ControlError("Invalid backup mode")
    if mode == "direct" and definition_content(unit, user=user, mode=mode) is None:
        require_absent_timer(unit, user=user)
    else:
        details = inspect_timer(unit, user=user)
        if details["protected"] or not details["editable"]:
            raise ControlError("Protected or uneditable timer cannot be rolled back")
    current = backup_unit(unit, user=user, mode=mode)
    try:
        restore_backup(payload)
    except Exception as original:
        try:
            restore_backup(current)
        except Exception as recovery:
            raise ControlError(f"Rollback failed; recovery also failed: {original}; {recovery}") from recovery
        raise
    return {"unit": unit, "restored": backup_name, "safetyBackup": current["id"]}


def list_backups(unit: str, *, user: bool) -> list[dict[str, Any]]:
    unit = validate_unit(unit)
    if BACKUP_DIR.is_symlink() or not BACKUP_DIR.is_dir():
        return []
    result: list[dict[str, Any]] = []
    scope = "user" if user else "system"
    pattern = re.compile(re.escape(scope) + r"\." + re.escape(unit) + r"\.\d{8}T\d{6}\.\d{6}Z\.json")
    for path in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not pattern.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("unit") != unit or payload.get("scope") != scope:
            continue
        if user and payload.get("accountUid") != invoking_account().pw_uid:
            continue
        result.append({"id": path.name, "created": payload.get("created", ""),
                       "enabled": bool(payload.get("enabled")), "active": bool(payload.get("active")),
                       "mode": payload.get("mode", ""), "existed": payload.get("content") is not None})
        if len(result) >= 50:
            break
    return result


def action(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit)
    user = args.scope == "user"
    if args.action not in MUTATING_ACTIONS:
        raise ControlError("Action not allowed")
    details = inspect_timer(unit, user=user)
    if details["protected"]:
        raise ControlError("Protected timer cannot be changed: " + details["protectionReason"])
    require_confirmation(args.action, unit, args.confirm)
    if args.action == "trigger":
        raw = details["properties"].get("Triggers", "")
        services = [item for item in raw.split() if SERVICE_RE.fullmatch(item)]
        if len(services) != 1:
            raise ControlError("Timer does not identify exactly one associated service")
        service = validate_service(services[0], asr_only=False)
        if service in CRITICAL_SERVICES:
            raise ControlError("Associated service is protected")
        systemctl("start", service, user=user)
        return {"unit": unit, "action": args.action, "service": service}
    systemctl(args.action, unit, user=user)
    return {"unit": unit, "action": args.action}


def delete_timer(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    user = args.scope == "user"
    require_confirmation("delete", unit, args.confirm)
    details = inspect_timer(unit, user=user)
    if details["protected"] or not details["editable"]:
        raise ControlError("Protected or uneditable timer cannot be deleted")
    if definition_content(unit, user=user, mode="direct") is None:
        raise ControlError("Only an ASR Admin-owned timer file can be deleted")
    saved = backup_unit(unit, user=user, mode="direct")
    systemctl("disable", "--now", unit, user=user)
    try:
        write_definition(unit, None, user=user, mode="direct")
        systemctl("daemon-reload", user=user)
    except Exception:
        restore_backup(saved)
        raise
    return {"unit": unit, "backup": saved["id"]}


def plan(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    content = render_timer(unit, args.schedule_type, args.schedule, args.service, args.description, args.persistent)
    fd, temporary = tempfile.mkstemp(prefix=".asr-timer-preview-", suffix=".timer")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        verified = run([SYSTEMD_ANALYZE, "verify", temporary], check=False)
        if verified.returncode:
            raise ControlError((verified.stderr or verified.stdout).strip()[:500] or "Timer validation failed")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"unit": unit, "definition": content, "confirmation": confirmation("apply", unit)}


def plan_override(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit)
    user = args.scope == "user"
    details = inspect_timer(unit, user=user)
    if details["protected"]:
        raise ControlError("Protected timer cannot be edited: " + details["protectionReason"])
    if not details["editable"]:
        raise ControlError("Generated or transient timer cannot be edited")
    content = render_override(unit, args.schedule_type, args.schedule, args.persistent)
    return {"unit": unit, "definition": content, "confirmation": confirmation("override", unit)}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=["list", "inspect", "backups", "preview", "apply", "preview-override", "override", "action", "delete", "rollback"])
    result.add_argument("--scope", choices=["system", "user", "all"], default="system")
    result.add_argument("--unit", default="")
    result.add_argument("--action", choices=sorted(MUTATING_ACTIONS))
    result.add_argument("--calendar", default="")
    result.add_argument("--schedule-type", choices=sorted(SCHEDULE_TYPES), default="calendar")
    result.add_argument("--schedule", default="")
    result.add_argument("--service", default="")
    result.add_argument("--description", default="")
    result.add_argument("--persistent", action="store_true")
    result.add_argument("--confirm", default="")
    result.add_argument("--backup", default="")
    result.add_argument("--actor", default="unknown")
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.schedule and args.calendar:
        args.schedule = args.calendar
    mutating = args.operation in {"apply", "override", "action", "delete", "rollback"}
    actor = validate_actor(args.actor)
    try:
        if args.scope == "all" and args.operation != "list":
            raise ControlError("Operation requires one scope")
        if mutating:
            audit(actor, "attempt-" + (args.operation if args.operation != "action" else str(args.action)), args.unit, True, scope=args.scope)
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
            lock_info = os.fstat(lock.fileno())
            if not stat.S_ISREG(lock_info.st_mode) or (os.geteuid() == 0 and lock_info.st_uid != 0):
                raise ControlError("Unsafe timer-control lock file")
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX if mutating else fcntl.LOCK_SH)
            if args.operation == "list":
                data = inventory(args.scope)
            elif args.operation == "inspect":
                if args.scope == "all":
                    raise ControlError("Inspect requires one scope")
                data = inspect_timer(args.unit, user=args.scope == "user")
            elif args.operation == "backups":
                if args.scope == "all":
                    raise ControlError("Backups require one scope")
                data = list_backups(args.unit, user=args.scope == "user")
            elif args.operation == "preview":
                data = plan(args)
            elif args.operation == "apply":
                data = apply_timer(args)
            elif args.operation == "preview-override":
                data = plan_override(args)
            elif args.operation == "override":
                data = override_timer(args)
            elif args.operation == "action":
                data = action(args)
            elif args.operation == "delete":
                data = delete_timer(args)
            else:
                data = rollback(args)
        if mutating:
            audit(actor, args.operation if args.operation != "action" else str(args.action), args.unit, True,
                  json.dumps(data, sort_keys=True, default=str), scope=args.scope)
        print(json.dumps({"ok": True, "data": data}, sort_keys=True))
        return 0
    except (ControlError, OSError, ValueError, json.JSONDecodeError) as exc:
        if mutating:
            try:
                audit(actor, args.operation if args.operation != "action" else str(args.action), args.unit, False, str(exc), scope=args.scope)
            except Exception:
                pass
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
