#!/usr/bin/env python3
"""Fail-closed selection of native or supported container provisioning."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

BROKER_SOCKET = Path("/run/allscan-reimagined-host/bridge-setup.sock")


def in_container(root: Path = Path("/")) -> bool:
    if (root / ".dockerenv").exists():
        return True
    cgroup = root / "proc/1/cgroup"
    text = cgroup.read_text(errors="replace") if cgroup.is_file() else ""
    return any(marker in text for marker in ("/docker/", "/containerd/", "/kubepods/"))


def unix_socket(path: Path) -> bool:
    try:
        return path.is_socket()
    except OSError:
        return False


def detect(root: Path = Path("/")) -> dict:
    if in_container(root):
        broker = root / str(BROKER_SOCKET).lstrip("/")
        if unix_socket(broker):
            return {"backend": "container-client", "supported": True,
                    "reason": "narrow host provisioning socket is available"}
        return {"backend": "unsupported", "supported": False,
                "reason": "containerized ASR has no authenticated host provisioning socket"}
    rpt = root / "etc/asterisk/rpt.conf"
    if root != Path("/"):
        systemd = (root / "run/systemd/system").is_dir()
        asterisk = (root / "usr/sbin/asterisk").is_file()
    else:
        systemd = bool(shutil.which("systemctl") and subprocess.run(
            ["systemctl", "is-system-running"], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        ).returncode in {0, 1})
        asterisk = bool(shutil.which("asterisk"))
    if rpt.is_file() and not rpt.is_symlink() and systemd and asterisk:
        return {"backend": "native-asl3", "supported": True,
                "reason": "native Asterisk configuration and lifecycle are present"}
    return {"backend": "unsupported", "supported": False,
            "reason": "active Asterisk architecture could not be determined safely"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/"))
    args = parser.parse_args()
    result = detect(args.root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["supported"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
