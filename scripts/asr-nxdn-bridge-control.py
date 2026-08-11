#!/usr/bin/env python3
"""NXDN entry point for the fail-closed G4KLX MQTT bridge controller."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import stat
import sys


SOURCE_CORE_PATH = Path(__file__).resolve().with_name("asr-p25-bridge-control.py")
INSTALLED_CORE_PATH = Path(__file__).resolve().with_name("allscan-reimagined-p25-bridge-control")
CORE_PATH = SOURCE_CORE_PATH if SOURCE_CORE_PATH.is_file() else INSTALLED_CORE_PATH


def load_core():
    try:
        parent = CORE_PATH.parent.lstat()
        info = CORE_PATH.lstat()
    except OSError as exc:
        raise SystemExit("NXDN bridge controller core is unavailable.") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or (os.geteuid() == 0 and (
            parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022
        ))
    ):
        raise SystemExit("NXDN bridge controller directory has unsafe ownership or permissions.")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SystemExit("NXDN bridge controller core is not a regular file.")
    if os.geteuid() == 0 and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
        raise SystemExit("NXDN bridge controller core has unsafe ownership or permissions.")
    module_name = "asr_digital_bridge_control_core"
    loader = SourceFileLoader(module_name, str(CORE_PATH))
    module_spec = importlib.util.spec_from_loader(module_name, loader)
    if module_spec is None or module_spec.loader is None:
        raise SystemExit("NXDN bridge controller core could not be loaded.")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


core = load_core()

NXDN_SPEC = core.ModeSpec(
    mode="nxdn",
    label="NXDN",
    gateway_dir="NXDNGateway",
    gateway_ini="NXDNGateway.ini",
    run_dir=Path("/run/allscan-reimagined-nxdn-bridge-control"),
    audit_log=Path("/var/log/allscan-reimagined/nxdn-bridge-control.jsonl"),
    reserved=frozenset({*range(1, 11), 20, 9999}),
    emulator_allowed=True,
)


if __name__ == "__main__":
    raise SystemExit(core.main(NXDN_SPEC))
