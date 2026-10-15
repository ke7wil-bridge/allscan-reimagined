#!/usr/bin/env python3
"""Verify API/ASTAPI source authority across native and layered deployments."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRITICAL_COMPAT = (
	"astapi/asrAmiGuard.php",
    "astapi/server.php",
    "astapi/asrTalkerIdentity.php",
    "include/asrRuntime.php",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_equal(left: Path, right: Path) -> None:
    if left.read_bytes() != right.read_bytes():
        raise RuntimeError(f"authoritative source copies differ: {left.relative_to(ROOT)} != {right.relative_to(ROOT)}")


def docker_bytes(container: str, path: str) -> bytes:
    result = subprocess.run(
        ["docker", "exec", container, "cat", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(f"cannot read {path} in {container}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def static_checks() -> None:
    require_equal(ROOT / "asr-api.php", ROOT / "server/asr-api.php")
    for relative in CRITICAL_COMPAT:
        require_equal(ROOT / "compat/allscan-v1.02" / relative,
                      ROOT / "compat/allscan-v1.03" / relative)
    entrypoint = (ROOT / "docker/layered-entrypoint.sh").read_text()
    reapply = (ROOT / "scripts/asr-reapply.sh").read_text()
    if 'COMPAT_DIR="/opt/asr-source/compat/allscan-${BACKEND_VERSION:-unknown}"' not in entrypoint:
        raise RuntimeError("layered entrypoint does not select an exact detected compatibility version")
    if "compat/allscan-v1.02/\" \"$ASR_DIR" in entrypoint:
        raise RuntimeError("layered entrypoint can still hard-code the v1.02 compatibility tree")
    for runtime_name in ("bridge-live.json", "zello-talkers.json", "zello-relay.json"):
        if f"--exclude={runtime_name}" not in entrypoint:
            raise RuntimeError(f"layered recreation can delete persistent runtime state: {runtime_name}")
    if 'compat_dir="$MASTER_DIR/compat/allscan-${backend_version:-unknown}"' not in reapply:
        raise RuntimeError("native reapply does not select an exact detected compatibility version")
    for source in (ROOT / "compat/allscan-v1.02/astapi/server.php",
                   ROOT / "compat/allscan-v1.03/astapi/server.php"):
        text = source.read_text()
        if "asrSanitizeTalkerFeed($payload['current']" not in text:
            raise RuntimeError(f"{source.relative_to(ROOT)} does not sanitize shared ASTAPI cache output")


def live_checks(container: str) -> None:
    common = docker_bytes(container, "/var/www/html/allscan/include/common.php").decode(errors="replace")
    match = re.search(r'^\$AllScanVersion = "([^"]+)";', common, re.MULTILINE)
    if not match:
        raise RuntimeError("live stock AllScan version is unavailable")
    version = match.group(1)
    compat = ROOT / f"compat/allscan-{version}"
    if not compat.is_dir():
        raise RuntimeError(f"repository has no exact compatibility layer for live AllScan {version}")

    expected_api = (ROOT / "asr-api.php").read_bytes()
    for path in ("/opt/asr-source/asr-api.php", "/var/www/html/asr/asr-api.php"):
        actual = docker_bytes(container, path)
        if digest(actual) != digest(expected_api):
            raise RuntimeError(f"live API source diverges at {path}")
    for relative in CRITICAL_COMPAT:
        expected = (compat / relative).read_bytes()
        for prefix in (f"/opt/asr-source/compat/allscan-{version}", "/var/www/html/asr"):
            path = f"{prefix}/{relative}"
            actual = docker_bytes(container, path)
            if digest(actual) != digest(expected):
                raise RuntimeError(f"live compatibility source diverges at {path}")
    print(f"PASS: live API/ASTAPI matches repository compatibility layer {version}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--container", default="allscan-reimagined-layered-allscan-1")
    args = parser.parse_args()
    static_checks()
    print("PASS: repository deployment source authority")
    if args.live:
        live_checks(args.container)


if __name__ == "__main__":
    main()
