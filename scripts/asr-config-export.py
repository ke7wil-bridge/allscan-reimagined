#!/usr/bin/env python3
"""Explicit, local-only, sensitive ASR configuration export (#127). No restore."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import stat
import tarfile

FILES = ("config.json", "secrets.json", "station-map-cache.json")
SCHEMA = 1


def export(source: Path, destination: Path, version: str) -> dict:
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Configuration directory is missing or a symlink")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; refusing overwrite")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Destination parent must be an existing real directory")
    if not version or len(version) > 128:
        raise ValueError("Invalid version")
    records = {}
    data = {}
    for name in FILES:
        path = source / name
        if path.is_symlink():
            raise ValueError("Refusing symlink: " + name)
        if not path.exists():
            continue
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Invalid config file: " + name)
        content = path.read_bytes()
        if name.endswith(".json"):
            json.loads(content)
        data[name] = content
        records[name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    if "config.json" not in data:
        raise ValueError("ASR config.json is required")
    manifest = {"schema": SCHEMA, "createdUtc": datetime.now(timezone.utc).isoformat(),
                "asrVersion": version, "architecture": platform.machine(),
                "system": platform.system(), "files": records,
                "containsSensitiveData": True, "restoreSupported": False}
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, content in [("manifest.json", payload), *data.items()]:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = stat.S_IRUSR | stat.S_IWUSR
                info.mtime = 0
                archive.addfile(info, io.BytesIO(content))
        os.chmod(destination, 0o600)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"ok": True, "archive": str(destination), "files": sorted(data),
            "containsSensitiveData": True, "restoreSupported": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ASR config to a local mode-0600 archive. Treat as sensitive.")
    parser.add_argument("--source", type=Path, default=Path("/etc/allscan-reimagined"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        result = export(args.source, args.output, args.version)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
