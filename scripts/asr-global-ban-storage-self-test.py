#!/usr/bin/env python3
"""Regression checks for Docker and native Global Ban storage selection."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    runtime = base / "run" / "urf-wil-config"
    native = base / "var" / "lib" / "allscan-reimagined" / "admin"
    native.mkdir(parents=True)
    for index, relative in enumerate((
        "scripts/asr-urf-admin.py",
        "scripts/asr-asl-ban.py",
        "scripts/asr-standalone-admin.py",
    )):
        module = load(f"asr_global_ban_storage_{index}", relative)
        assert module.global_ban_root(runtime, native) == native, f"{relative} did not fall back to native persistent storage"
        runtime.mkdir(parents=True, exist_ok=True)
        assert module.global_ban_root(runtime, native) == runtime, f"{relative} did not prefer the Docker/runtime mount"
        runtime.rmdir()

reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
assert 'GLOBAL_BAN_DIR="$DATA_DIR/admin"' in reapply
assert "allscan-reimagined-global-ban.conf" in reapply
assert "L+ /run/urf-wil-config" in reapply
assert "systemd-tmpfiles --create" in reapply

api = (ROOT / "asr-api.php").read_text(encoding="utf-8")
assert "is_dir('/run/urf-wil-config') ? '/run/urf-wil-config' : '/var/lib/allscan-reimagined/admin'" in api

print("global ban storage self-test: ok")
