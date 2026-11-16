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
        assert module.global_ban_root(runtime, native) == native, f"{relative} did not prefer the ASR-owned persistent policy store"
        runtime.rmdir()
        # Native store must remain authoritative even when the old runtime
        # directory exists; removing that runtime must not lose ban state.
        (native / "urfd.blacklist").write_text("KE7WIL\n", encoding="utf-8")
        runtime.mkdir(parents=True)
        (runtime / "urfd.blacklist").write_text("OTHER\n", encoding="utf-8")
        chosen = module.global_ban_root(runtime, native)
        assert (chosen / "urfd.blacklist").read_text(encoding="utf-8") == "KE7WIL\n"
        runtime.joinpath("urfd.blacklist").unlink()
        runtime.rmdir()

reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
assert 'GLOBAL_BAN_DIR="$DATA_DIR/admin"' in reapply
assert "allscan-reimagined-global-ban.conf" in reapply
assert "L+ /run/urf-wil-config" in reapply
assert "systemd-tmpfiles --create" in reapply

api = (ROOT / "asr-api.php").read_text(encoding="utf-8")
assert "is_dir('/var/lib/allscan-reimagined/admin') ? '/var/lib/allscan-reimagined/admin' : '/run/urf-wil-config'" in api

print("global ban storage self-test: ok")

zello = (ROOT / "scripts/asr-bridge-setup-zello-install.py").read_text(encoding="utf-8")
assert "- /var/lib/allscan-reimagined/admin:/run/asr-global:ro" in zello

assert "ExecStartPre=/usr/bin/test -f /var/lib/allscan-reimagined/admin/urfd.blacklist" in zello
assert "ExecStartPre=/bin/sh -c 'if [ ! -e /run/urf-wil-config ]" not in zello

admin = load("asr_global_ban_verification_test", "scripts/asr-urf-admin.py")
assert admin.global_enforcement_verified({"status": "applied"}, {"verified": True, "applied": [], "skipped": []})
assert not admin.global_enforcement_verified(
    {"status": "applied", "applied": {"unmappedGlobalRules": ["N0MISSING"]}},
    {"verified": True, "applied": [], "skipped": []})
for native in ({"verified": False, "skipped": ["p25"]}, {"verified": False, "unavailable": True}, {"verified": True, "skipped": ["nxdn"]}):
    assert not admin.global_enforcement_verified({"status": "applied"}, native)
assert not admin.global_enforcement_verified({"status": "pending"}, {"verified": True, "skipped": []})
