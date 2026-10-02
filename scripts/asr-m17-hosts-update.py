#!/usr/bin/env python3
"""Fetch and validate the official M17 reflector catalog."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

URL = "https://m17-project.github.io/hostfiles/M17Hosts.json"
DESTINATION = Path("/var/lib/allscan-reimagined/m17/M17Hosts.json")
DESIGNATOR_RE = re.compile(r"^M17-[A-Z0-9]{3}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
MAX_BYTES = 4 * 1024 * 1024


def validated(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("M17 reflector catalog has an invalid size")
    payload = json.loads(raw)
    entries = payload.get("reflectors", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list) or not entries or len(entries) > 4096:
        raise ValueError("M17 reflector catalog has an invalid reflector list")
    seen: set[str] = set()
    usable: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("M17 reflector catalog contains an invalid entry")
        designator = str(entry.get("designator", "")).upper()
        # The upstream document also includes URF records.  This client accepts
        # M17 designators only, and currently has an IPv4 socket boundary.
        if not DESIGNATOR_RE.fullmatch(designator):
            continue
        host = str(entry.get("dns") or entry.get("ipv4") or "")
        if not host:
            continue
        modules = entry.get("modules")
        encrypted = entry.get("encrypted", [])
        port = entry.get("port")
        if (designator in seen
                or not HOST_RE.fullmatch(host) or ".." in host
                or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
                or not isinstance(modules, list) or not modules
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Z]", value) for value in modules)
                or not isinstance(encrypted, list)
                or any(not isinstance(value, str) or not re.fullmatch(r"[A-Z]", value) for value in encrypted)):
            raise ValueError(f"M17 reflector catalog entry is invalid: {designator or 'unknown'}")
        seen.add(designator)
        usable.append(entry)
    if not usable:
        raise ValueError("M17 reflector catalog has no usable IPv4 reflectors")
    payload["reflectors"] = usable
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n"


def install(raw: bytes, destination: Path = DESTINATION) -> None:
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    parent = destination.parent.stat()
    if destination.parent.is_symlink() or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise RuntimeError("M17 reflector catalog directory is unsafe")
    fd, temporary = tempfile.mkstemp(prefix=".M17Hosts.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(validated(raw)); handle.flush(); os.fsync(handle.fileno())
        os.chown(temporary, 0, 0); os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        sample = {"reflectors": [{"designator": "M17-TST", "dns": "m17.example.org", "ipv4": None,
                  "modules": ["A", "B"], "encrypted": [], "port": 17000}]}
        assert json.loads(validated(json.dumps(sample).encode()))["reflectors"][0]["designator"] == "M17-TST"
        for bad in ({"reflectors": []}, {"reflectors": [{**sample["reflectors"][0], "modules": ["AA"]}]}):
            try: validated(json.dumps(bad).encode())
            except ValueError: pass
            else: raise AssertionError("invalid M17 catalog was accepted")
        print("M17 hosts update self-test passed")
        return 0
    if os.geteuid() != 0:
        raise SystemExit("M17 catalog update must run as root")
    request = urllib.request.Request(URL, headers={"User-Agent": "AllScan-Reimagined/8 M17 catalog"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
    install(raw)
    print(json.dumps({"ok": True, "destination": str(DESTINATION)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
