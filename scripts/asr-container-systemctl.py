#!/usr/bin/env python3
"""Constrained systemctl adapter used only inside the root host broker."""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = re.compile(r"(?:allscan-reimagined-(?:m17-bridge@[a-z][a-z0-9_-]{1,31}|urf-tgif-[a-z][a-z0-9_-]{1,31})|(?:p25gateway|nxdngateway|ysfgateway|mmdvm-bridge|analog-bridge|md380-emu|asr-mqtt)-[a-z][a-z0-9_-]{1,31})\.service")


def backend():
    spec = importlib.util.spec_from_file_location("asr_backend_systemctl", HERE / "asr-provisioning-backend.py")
    if not spec or not spec.loader:
        raise RuntimeError("backend unavailable")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


def main() -> int:
    args = sys.argv[1:]
    profile = backend().load_profile()
    if profile is None:
        raise SystemExit("container profile unavailable")
    if args == ["restart", "asterisk.service"]:
        return subprocess.run(["/usr/bin/docker", "restart", profile.asterisk_container]).returncode
    if args == ["daemon-reload"]:
        return subprocess.run(["/usr/bin/systemctl", *args]).returncode
    verbs = {"enable", "start", "restart", "disable", "is-active", "is-enabled"}
    filtered = [arg for arg in args if arg not in {"--now", "--quiet"}]
    if len(filtered) != 2 or filtered[0] not in verbs or not SERVICE.fullmatch(filtered[1]):
        raise SystemExit("systemctl operation is not allowed")
    return subprocess.run(["/usr/bin/systemctl", *args]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
