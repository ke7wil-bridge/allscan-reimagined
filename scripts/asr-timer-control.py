#!/usr/bin/env python3
"""Constrained, audited systemd timer administration for ASR.

The web server may invoke this program through sudo. It accepts no shell
fragments or arbitrary paths: commands use fixed argv arrays, unit names are
validated, and writes are limited to ASR Admin-owned timer files.
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
ASR_PREFIX = "allscan-reimagined-"
MANAGED_PREFIX = "allscan-reimagined-admin-"
CRITICAL = {
    "apt-daily.timer", "apt-daily-upgrade.timer", "systemd-tmpfiles-clean.timer",
    "fstrim.timer", "logrotate.timer", "man-db.timer", "fwupd-refresh.timer",
    "systemd-journal-flush.timer", "systemd-journal-rotate.timer",
}
MUTATING_ACTIONS = {"enable", "disable", "start", "stop", "restart", "trigger"}


class ControlError(RuntimeError):
    pass


def validate_unit(name: str, *, managed: bool = False) -> str:
    if not isinstance(name, str) or not UNIT_RE.fullmatch(name) or ".." in name or "/" in name:
        raise ControlError("Invalid timer unit name")
    if managed and not name.startswith(MANAGED_PREFIX):
        raise ControlError("Only ASR Admin-owned timers may be created, edited, deleted, or rolled back")
    return name


def validate_service(name: str) -> str:
    if not isinstance(name, str) or not SERVICE_RE.fullmatch(name) or ".." in name or "/" in name:
        raise ControlError("Invalid service unit name")
    if not name.startswith(ASR_PREFIX):
        raise ControlError("Supported timers may trigger only ASR-owned services")
    return name


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
    account = os.environ.get("SUDO_USER", "")
    if not account or account == "root" or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", account):
        raise ControlError("The invoking user account is unavailable for user-timer inspection")
    try:
        uid = pwd.getpwnam(account).pw_uid
    except KeyError as exc:
        raise ControlError("The invoking user account does not exist") from exc
    return ["/usr/sbin/runuser", "-u", account, "--", "/usr/bin/env", f"XDG_RUNTIME_DIR=/run/user/{uid}", *argv]


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
    result: list[dict[str, Any]] = []
    properties = "Id,LoadState,ActiveState,SubState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec,Result,Triggers,Description"
    for row in by_name.values():
        name = row.get("unit_file", "") if isinstance(row, dict) else ""
        try:
            validate_unit(name)
        except ControlError:
            continue
        shown = systemctl("show", name, f"--property={properties}", user=user, check=False)
        props = parse_show(shown.stdout) if shown.returncode == 0 else {}
        services = [item for item in props.get("Triggers", "").split() if SERVICE_RE.fullmatch(item)]
        service_props: dict[str, str] = {}
        if len(services) == 1:
            service_show = systemctl("show", services[0], "--property=ActiveState,SubState,Result,ExecMainStatus", user=user, check=False)
            if service_show.returncode == 0:
                service_props = parse_show(service_show.stdout)
        failed = service_props.get("ActiveState") == "failed" or service_props.get("Result", "") not in ("", "success")
        result.append({
            "name": name, "scope": "user" if user else "system",
            "unitFileState": row.get("state", "unknown"), "preset": row.get("preset", "unknown"),
            "loadState": props.get("LoadState", "unknown"), "activeState": props.get("ActiveState", "unknown"),
            "subState": props.get("SubState", "unknown"), "next": props.get("NextElapseUSecRealtime", ""),
            "previous": props.get("LastTriggerUSec", ""), "result": props.get("Result", ""),
            "service": props.get("Triggers", ""), "description": props.get("Description", ""),
            "serviceState": "/".join(filter(None, [service_props.get("ActiveState", ""), service_props.get("SubState", "")])),
            "serviceResult": service_props.get("Result", ""), "serviceExecStatus": service_props.get("ExecMainStatus", ""),
            "failure": failed,
            "managed": name.startswith(MANAGED_PREFIX),
            "protected": name in CRITICAL or not name.startswith(ASR_PREFIX),
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
        "Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,SourcePath,"
        "DropInPaths,Triggers,NextElapseUSecRealtime,NextElapseUSecMonotonic,"
        "LastTriggerUSec,Result,Description"
    )
    props = parse_show(systemctl("show", name, f"--property={properties}", user=user).stdout)
    definition = systemctl("cat", name, user=user, check=False)
    service_names = [item for item in props.get("Triggers", "").split() if SERVICE_RE.fullmatch(item)]
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
    return {
        "name": name, "scope": "user" if user else "system", "properties": props,
        "serviceProperties": service_properties,
        "definition": definition.stdout[:30000] if definition.returncode == 0 else "",
        "history": history.stdout[-30000:] if history.returncode == 0 else "",
        "historyError": (history.stderr or "")[:500] if history.returncode else "",
        "managed": name.startswith(MANAGED_PREFIX),
        "protected": name in CRITICAL or not name.startswith(ASR_PREFIX),
    }


def render_timer(name: str, calendar: str, service: str, description: str, persistent: bool) -> str:
    validate_unit(name, managed=True)
    validate_service(service)
    if not CALENDAR_RE.fullmatch(calendar or "") or "\n" in calendar or "\r" in calendar:
        raise ControlError("Invalid OnCalendar expression")
    if not DESCRIPTION_RE.fullmatch(description or "") or "\n" in description or "\r" in description:
        raise ControlError("Invalid timer description")
    run([SYSTEMD_ANALYZE, "calendar", calendar])
    return "\n".join([
        "# Managed by AllScan Reimagined Admin Scheduling", "[Unit]", f"Description={description}", "",
        "[Timer]", f"OnCalendar={calendar}", f"Persistent={'true' if persistent else 'false'}",
        "AccuracySec=1min", f"Unit={service}", "", "[Install]", "WantedBy=timers.target", "",
    ])


def confirmation(action: str, unit: str) -> str:
    return f"{action.upper()} {unit}"


def require_confirmation(action: str, unit: str, supplied: str) -> None:
    expected = confirmation(action, unit)
    if supplied != expected:
        raise ControlError(f"Type {expected} to confirm")


def audit(actor: str, action_name: str, unit: str, ok: bool, detail: str = "") -> None:
    record = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "actor": validate_actor(actor),
        "action": action_name, "unit": unit, "ok": bool(ok), "detail": detail[:500],
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


def backup_unit(unit: str) -> dict[str, Any]:
    validate_unit(unit, managed=True)
    secure_directory(BACKUP_DIR, 0o700)
    path = UNIT_DIR / unit
    if path.is_symlink():
        raise ControlError("Refusing to back up a symlinked managed timer")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = BACKUP_DIR / f"{unit}.{stamp}.json"
    content = path.read_text(encoding="utf-8") if path.is_file() else None
    state = systemctl("is-enabled", unit, check=False).stdout.strip()
    payload = {"unit": unit, "content": content, "enabled": state == "enabled", "created": stamp}
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(payload, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return {"id": target.name, **payload}


def atomic_write(path: Path, content: str) -> None:
    if path.parent != UNIT_DIR or path.is_symlink():
        raise ControlError("Unsafe managed timer path")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".timer", dir=UNIT_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        verify = run([SYSTEMD_ANALYZE, "verify", temporary], check=False)
        if verify.returncode:
            raise ControlError((verify.stderr or verify.stdout).strip()[:500] or "Timer validation failed")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_backup(payload: dict[str, Any], *, reload: bool = True) -> None:
    unit = validate_unit(payload.get("unit", ""), managed=True)
    path = UNIT_DIR / unit
    content = payload.get("content")
    if content is None:
        if path.exists() and not path.is_symlink():
            path.unlink()
    elif isinstance(content, str):
        atomic_write(path, content)
    else:
        raise ControlError("Invalid backup content")
    if reload:
        systemctl("daemon-reload")
    systemctl("enable" if payload.get("enabled") else "disable", unit, check=False)


def apply_timer(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    require_confirmation("apply", unit, args.confirm)
    content = render_timer(unit, args.calendar, args.service, args.description, args.persistent)
    prior = backup_unit(unit)
    try:
        atomic_write(UNIT_DIR / unit, content)
        systemctl("daemon-reload")
    except Exception:
        restore_backup(prior, reload=True)
        raise
    return {"unit": unit, "definition": content, "backup": prior["id"]}


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    require_confirmation("rollback", unit, args.confirm)
    backup_name = args.backup or ""
    if not re.fullmatch(re.escape(unit) + r"\.\d{8}T\d{6}\.\d{6}Z\.json", backup_name):
        raise ControlError("Invalid backup ID")
    path = BACKUP_DIR / backup_name
    if path.parent != BACKUP_DIR or path.is_symlink() or not path.is_file():
        raise ControlError("Backup not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("unit") != unit:
        raise ControlError("Backup does not match timer")
    current = backup_unit(unit)
    restore_backup(payload)
    return {"unit": unit, "restored": backup_name, "safetyBackup": current["id"]}


def list_backups(unit: str) -> list[dict[str, Any]]:
    unit = validate_unit(unit, managed=True)
    if BACKUP_DIR.is_symlink() or not BACKUP_DIR.is_dir():
        return []
    result: list[dict[str, Any]] = []
    pattern = re.compile(re.escape(unit) + r"\.\d{8}T\d{6}\.\d{6}Z\.json")
    for path in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not pattern.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("unit") != unit:
            continue
        result.append({"id": path.name, "created": payload.get("created", ""),
                       "enabled": bool(payload.get("enabled")), "existed": payload.get("content") is not None})
        if len(result) >= 50:
            break
    return result


def action(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit)
    if args.action not in MUTATING_ACTIONS:
        raise ControlError("Action not allowed")
    if unit in CRITICAL:
        raise ControlError("Critical timer is protected")
    if not unit.startswith(ASR_PREFIX):
        raise ControlError("Unrelated system timers are read-only")
    require_confirmation(args.action, unit, args.confirm)
    if args.action == "trigger":
        raw = inspect_timer(unit)["properties"].get("Triggers", "")
        services = [item for item in raw.split() if SERVICE_RE.fullmatch(item)]
        if len(services) != 1:
            raise ControlError("Timer does not identify exactly one associated service")
        service = validate_service(services[0])
        systemctl("start", service)
        return {"unit": unit, "action": args.action, "service": service}
    systemctl(args.action, unit)
    return {"unit": unit, "action": args.action}


def delete_timer(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    require_confirmation("delete", unit, args.confirm)
    saved = backup_unit(unit)
    path = UNIT_DIR / unit
    if path.is_symlink():
        raise ControlError("Refusing to delete a symlinked timer")
    systemctl("disable", "--now", unit, check=False)
    if path.exists():
        path.unlink()
    systemctl("daemon-reload")
    return {"unit": unit, "backup": saved["id"]}


def plan(args: argparse.Namespace) -> dict[str, Any]:
    unit = validate_unit(args.unit, managed=True)
    content = render_timer(unit, args.calendar, args.service, args.description, args.persistent)
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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=["list", "inspect", "backups", "preview", "apply", "action", "delete", "rollback"])
    result.add_argument("--scope", choices=["system", "user", "all"], default="system")
    result.add_argument("--unit", default="")
    result.add_argument("--action", choices=sorted(MUTATING_ACTIONS))
    result.add_argument("--calendar", default="")
    result.add_argument("--service", default="")
    result.add_argument("--description", default="")
    result.add_argument("--persistent", action="store_true")
    result.add_argument("--confirm", default="")
    result.add_argument("--backup", default="")
    result.add_argument("--actor", default="unknown")
    return result


def main() -> int:
    args = parser().parse_args()
    mutating = args.operation in {"apply", "action", "delete", "rollback"}
    actor = validate_actor(args.actor)
    try:
        if mutating:
            audit(actor, "attempt-" + (args.operation if args.operation != "action" else str(args.action)), args.unit, True)
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
                data = list_backups(args.unit)
            elif args.operation == "preview":
                data = plan(args)
            elif args.operation == "apply":
                data = apply_timer(args)
            elif args.operation == "action":
                data = action(args)
            elif args.operation == "delete":
                data = delete_timer(args)
            else:
                data = rollback(args)
        if mutating:
            audit(actor, args.operation if args.operation != "action" else str(args.action), args.unit, True)
        print(json.dumps({"ok": True, "data": data}, sort_keys=True))
        return 0
    except (ControlError, OSError, ValueError, json.JSONDecodeError) as exc:
        if mutating:
            try:
                audit(actor, args.operation if args.operation != "action" else str(args.action), args.unit, False, str(exc))
            except Exception:
                pass
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
