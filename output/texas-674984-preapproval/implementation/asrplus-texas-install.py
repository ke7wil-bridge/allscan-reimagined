#!/usr/bin/env python3
"""Guarded, idempotent Texas 674984 installer and rollback workflow.

The default action is a read-only preflight. Mutations require --apply. Tests
use --root with a disposable filesystem; no production endpoint is contacted.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time


REPO = Path(__file__).resolve().parents[1]
OPS = REPO / "ops/asrplus/texas"
NODE = "674984"
MANAGED = "; ASRPLUS-TEXAS-674984-MANAGED"
TARGETS = {
    "usr/local/sbin/asrplus-agent": REPO / "scripts/asrplus-agent.py",
    "usr/local/sbin/asrplus-texas-control": REPO / "scripts/asrplus-texas-control.py",
    "usr/local/lib/asrplus/asrplus_command_integrity.py": REPO / "scripts/asrplus_command_integrity.py",
    "etc/systemd/system/asrplus-agent.service": OPS / "asrplus-texas-agent.service",
    "etc/systemd/system/asrplus-agent.timer": REPO / "ops/asrplus/asrplus-agent.timer",
    "etc/systemd/system/asrplus-agent.path": REPO / "ops/asrplus/asrplus-agent.path",
    "etc/systemd/system/asrplus-talker-watcher.service": OPS / "asrplus-texas-talker-watcher.service",
    "usr/lib/sysusers.d/asrplus-texas.conf": OPS / "asrplus-agent.sysusers",
}


class InstallError(RuntimeError):
    pass


def rooted(root: Path, relative: str) -> Path:
    return root / relative.lstrip("/")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_values(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InstallError("values must be a JSON object")
    allowed = {"kentuckyAddress", "texasSourceIp", "texasPublicKey", "keyId"}
    if set(payload) - allowed:
        raise InstallError("unknown values key")
    return payload


def validate_values(values: dict) -> None:
    address = str(values.get("kentuckyAddress", ""))
    source = str(values.get("texasSourceIp", ""))
    public_key = str(values.get("texasPublicKey", ""))
    key_id = str(values.get("keyId", ""))
    if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", address):
        raise InstallError("invalid Kentucky address")
    try:
        ipaddress.ip_address(source)
    except ValueError as error:
        raise InstallError("invalid Texas source IP") from error
    if not re.fullmatch(r"(?:ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]{8,}(?: [^\r\n]{1,80})?", public_key):
        raise InstallError("invalid Texas public key")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", key_id):
        raise InstallError("invalid HMAC key ID")


def render_config(values: dict) -> bytes:
    config = json.loads((OPS / "asrplus-agent.json").read_text())
    replacements = {
        "KENTUCKY_ADDRESS": str(values.get("kentuckyAddress", "KENTUCKY_ADDRESS")),
        "TEXAS_KEY_ID": str(values.get("keyId", "TEXAS_KEY_ID")),
    }
    raw = json.dumps(config, indent=2) + "\n"
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    return raw.encode()


def render_authorized_key(values: dict) -> bytes:
    raw = (OPS / "authorized_keys.template").read_text()
    raw = raw.replace("TEXAS_SOURCE_IP", str(values.get("texasSourceIp", "TEXAS_SOURCE_IP")))
    raw = raw.replace("TEXAS_PUBLIC_KEY", str(values.get("texasPublicKey", "TEXAS_PUBLIC_KEY")))
    return raw.encode()


def integrate_admission(original: str) -> str:
    if MANAGED in original:
        return original
    context = re.search(r"(?ms)^\[radio-secure\]\s*$.*?(?=^\[|\Z)", original)
    if not context:
        raise InstallError("radio-secure context not found")
    body = context.group(0)
    if "GROUP_COUNT(" in body or "GROUP(rptin)" in body:
        raise InstallError("existing radio-secure admission logic requires manual review")
    rpt = re.search(r"(?m)^[ \t]*same\s*=>\s*n(?:\([^)]*\))?,Rpt\(\$\{EXTEN\}(?:,[^)]*)?\)\s*$", body)
    if not rpt:
        raise InstallError("radio-secure Rpt(${EXTEN}) admission point not found")
    managed = MANAGED + "\n" + (OPS / "radio-secure-capacity.conf").read_text().rstrip()
    updated = body[:rpt.start()] + managed + body[rpt.end():]
    return original[:context.start()] + updated + original[context.end():]


def preflight(root: Path, values: dict, require_ready: bool = False) -> dict:
    extensions = rooted(root, "etc/asterisk/extensions.conf")
    checks = {
        "rootExists": root.is_dir(),
        "osRelease": rooted(root, "etc/os-release").is_file(),
        "extensionsReadable": extensions.is_file(),
        "agentSources": all(source.is_file() for source in TARGETS.values()),
        "nodeIdentity": str(values.get("node", NODE)) == NODE,
    }
    try:
        integrate_admission(extensions.read_text(encoding="utf-8"))
        checks["admissionPatchable"] = True
    except (OSError, InstallError):
        checks["admissionPatchable"] = bool(extensions.is_file() and MANAGED in extensions.read_text(errors="ignore"))
    placeholders = [name for name in ("kentuckyAddress", "texasSourceIp", "texasPublicKey", "keyId")
                    if not str(values.get(name, "")).strip()]
    checks["hostValuesPresent"] = not placeholders
    if require_ready and (not all(checks.values()) or placeholders):
        raise InstallError("preflight failed: " + ", ".join(
            [key for key, value in checks.items() if not value] + placeholders))
    if require_ready:
        validate_values(values)
    return {"ok": all(value for key, value in checks.items() if key != "hostValuesPresent"),
            "checks": checks, "placeholders": placeholders, "node": NODE,
            "root": str(root)}


def make_backup(root: Path, paths: list[Path]) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{time.time_ns()}"
    backup = rooted(root, f"var/backups/asrplus-texas/{stamp}")
    backup.mkdir(parents=True, mode=0o700)
    records = []
    for path in paths:
        relative = str(path.relative_to(root))
        record = {"path": relative, "existed": path.exists()}
        if path.is_file():
            destination = backup / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            record |= {"sha256": sha256(path), "mode": stat.S_IMODE(path.stat().st_mode)}
        records.append(record)
    atomic_write(backup / "manifest.json", (json.dumps({"version": 1, "files": records}, indent=2) + "\n").encode(), 0o600)
    return backup


def install(root: Path, values: dict) -> dict:
    preflight(root, values, require_ready=True)
    extensions = rooted(root, "etc/asterisk/extensions.conf")
    destinations = [rooted(root, path) for path in TARGETS] + [
        rooted(root, "etc/asrplus-agent.json"),
        rooted(root, "var/lib/asrplus-texas/kentucky-authorized-key.fragment"), extensions,
        rooted(root, "var/lib/asrplus-texas/central-registration.json"),
    ]
    backup = make_backup(root, destinations)
    written = []
    fail_after = (int(os.environ.get("ASRPLUS_TEST_FAIL_AFTER", "0") or 0)
                  if root != Path("/") else 0)
    try:
        for relative, source in TARGETS.items():
            destination = rooted(root, relative)
            mode = (0o644 if relative.startswith(("etc/systemd", "usr/lib/sysusers", "usr/local/lib"))
                    else 0o755)
            atomic_write(destination, source.read_bytes(), mode)
            written.append(relative)
            if fail_after and len(written) >= fail_after:
                raise InstallError("injected partial installation failure")
        atomic_write(rooted(root, "etc/asrplus-agent.json"), render_config(values), 0o640)
        written.append("etc/asrplus-agent.json")
        atomic_write(rooted(root, "var/lib/asrplus-texas/kentucky-authorized-key.fragment"),
                     render_authorized_key(values), 0o600)
        written.append("var/lib/asrplus-texas/kentucky-authorized-key.fragment")
        atomic_write(extensions, integrate_admission(extensions.read_text()).encode(), 0o640)
        written.append("etc/asterisk/extensions.conf")
        registration = json.loads((OPS / "asrplus-central-hub.json").read_text())
        atomic_write(rooted(root, "var/lib/asrplus-texas/central-registration.json"),
                     (json.dumps(registration, indent=2) + "\n").encode(), 0o600)
        written.append("var/lib/asrplus-texas/central-registration.json")
    except Exception:
        rollback(root, backup)
        raise
    return {"ok": True, "backup": str(backup), "written": written,
            "telemetryOnly": True, "serviceActivationPerformed": False}


def rollback(root: Path, backup: Path) -> dict:
    backup = backup.resolve()
    backup_root = rooted(root, "var/backups/asrplus-texas").resolve()
    if not backup.is_relative_to(backup_root):
        raise InstallError("backup is outside the Texas backup root")
    manifest = json.loads((backup / "manifest.json").read_text())
    validated = []
    for record in manifest.get("files", []):
        relative = Path(str(record.get("path", "")))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise InstallError("unsafe backup manifest path")
        source = backup / "files" / relative
        if record.get("existed") and (not source.is_file() or sha256(source) != record.get("sha256")):
            raise InstallError(f"backup integrity failure: {relative}")
        validated.append((record, relative, source))
    restored = []
    for record, relative, source in validated:
        destination = rooted(root, str(relative))
        if record["existed"]:
            atomic_write(destination, source.read_bytes(), int(record["mode"]))
        elif destination.exists():
            if not destination.is_file():
                raise InstallError(f"refusing to remove non-file {destination}")
            destination.unlink()
        restored.append(record["path"])
    return {"ok": True, "restored": restored, "backup": str(backup)}


def verify(root: Path) -> dict:
    config_path = rooted(root, "etc/asrplus-agent.json")
    config = json.loads(config_path.read_text())
    extensions = rooted(root, "etc/asterisk/extensions.conf").read_text()
    checks = {
        "node": config.get("hub") == "texas" and config.get("node") == NODE,
        "requiredIntegrity": config.get("commandIntegrity", {}).get("mode") == "required",
        "telemetryOnly": config.get("controlPolling") is False,
        "capacity60": "> 60" in extensions and "GROUP_COUNT(${EXTEN}@rptin)" in extensions,
        "localExcluded": 'CHANNEL(peerip)}" = "127.0.0.1"' in extensions,
        "dedicatedAccount": "User=asrplus-agent" in rooted(root, "etc/systemd/system/asrplus-agent.service").read_text(),
        "dedicatedWatcher": "User=asrplus-agent" in rooted(root, "etc/systemd/system/asrplus-talker-watcher.service").read_text(),
        "centralCapacity": json.loads(rooted(root, "var/lib/asrplus-texas/central-registration.json").read_text()).get("capacity") == 60,
    }
    return {"ok": all(checks.values()), "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "install", "verify", "rollback", "uninstall", "emergency-isolate"))
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--values", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval-id")
    args = parser.parse_args()
    root = args.root.resolve()
    values = load_values(args.values)
    try:
        if args.command == "preflight":
            result = preflight(root, values)
        elif args.command == "verify":
            result = verify(root)
        else:
            if not args.apply:
                raise InstallError("mutation requires --apply")
            if root == Path("/") and not args.approval_id:
                raise InstallError("live-root mutation requires --approval-id")
            if args.command == "install":
                result = install(root, values)
            elif args.command in {"rollback", "uninstall"}:
                if not args.backup:
                    raise InstallError("rollback requires --backup")
                result = rollback(root, args.backup.resolve())
            else:
                config_path = rooted(root, "etc/asrplus-agent.json")
                config = json.loads(config_path.read_text())
                config["controlPolling"] = False
                atomic_write(config_path, (json.dumps(config, indent=2) + "\n").encode(), 0o640)
                result = {"ok": True, "controlPolling": False,
                          "serviceActivationPerformed": False}
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("ok") else 1
    except (InstallError, OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)[:300]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
