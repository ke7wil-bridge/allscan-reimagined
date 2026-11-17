#!/usr/bin/env python3
"""Regression checks for the native ASR Global Ban policy store (#177)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = "/var/lib/allscan-reimagined/admin"


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


with tempfile.TemporaryDirectory() as temporary:
    os.environ["ASR_GLOBAL_BAN_ROOT"] = temporary
    try:
        # Every policy consumer resolves the same store and list name.
        for index, relative in enumerate(("scripts/asr-global-ban.py", "scripts/asr-asl-ban.py",
                                          "scripts/asr-standalone-admin.py")):
            module = load(f"asr_global_ban_storage_{index}", relative)
            assert str(module.GLOBAL_BAN_ROOT) == temporary, f"{relative} ignored the policy store root"
        ban = sys.modules["asr_global_ban_storage_0"]
        assert ban.BLACKLIST == str(Path(temporary) / "global-bans.list")
        assert sys.modules["asr_global_ban_storage_1"].GLOBAL == Path(temporary) / "global-bans.list"
        assert sys.modules["asr_global_ban_storage_2"].BLACKLIST == Path(temporary) / "global-bans.list"
        ban.write_rules(["KE7WIL", "N0*", "KE7WIL"])
        assert ban.read_rules() == ["KE7WIL", "N0*"]
        assert (Path(temporary) / "global-bans.list").read_text().startswith("# Managed by AllScan Reimagined Global Ban\n")
        timed = ban.set_ban_expiration({}, "KE7WIL", "1h", now=1000)
        ban.write_timed_bans(timed)
        assert ban.read_timed_bans() == {"KE7WIL": {"createdAt": 1000, "expiresAt": 4600}}
        try:
            ban.assert_ban_allowed("RFCKRD0")
            raise AssertionError("protected service identity was bannable")
        except ValueError:
            pass
        # The local node, bridge nodes and Net node 1999 are never bannable,
        # directly or through a covering prefix rule.
        config = Path(temporary) / "config.json"
        config.write_text(json.dumps({"node": "674982", "callsign": "KN4EWT",
                                      "bridges": [{"id": "dmr", "node": "1001"}, {"id": "zello", "node": "1006"}]}))
        ban.CONFIG = str(config)
        for rule in ("674982", "6749*", "1001", "1006", "1999", "1*", "KN4EWT", "KN4E*"):
            try:
                ban.assert_ban_allowed(rule)
                raise AssertionError(f"protected infrastructure identity was bannable: {rule}")
            except ValueError:
                pass
        for rule in ("N0ASRT", "674983", "2*"):
            ban.assert_ban_allowed(rule)
    finally:
        os.environ.pop("ASR_GLOBAL_BAN_ROOT", None)

udp_admin = (ROOT / "scripts/asr-native-udp-admin.py").read_text(encoding="utf-8")
assert f'"ASR_GLOBAL_POLICY_DIR", "{STORE}"' in udp_admin and 'POLICY_DIR / "global-bans.list"' in udp_admin
unit = (ROOT / "ops/native-udp-admin/allscan-reimagined-native-admin@.service").read_text(encoding="utf-8")
assert f"ReadOnlyPaths={STORE}" in unit and "urf" not in unit.lower()
# The runtime directory must survive a reboot clearing /run (#134 Kentucky deploy).
assert "RuntimeDirectory=asr-standalone-admin/%i" in unit and "ReadWritePaths=/run/asr-standalone-admin" not in unit

reapply = (ROOT / "scripts/asr-reapply.sh").read_text(encoding="utf-8")
assert 'GLOBAL_BAN_DIR="$DATA_DIR/admin"' in reapply
# Existing bans migrate once from the retired URF list name.
assert 'cp -p "$GLOBAL_BAN_DIR/urfd.blacklist" "$GLOBAL_BAN_DIR/global-bans.list"' in reapply
assert 'cp -p "$legacy_dir/urfd.blacklist" "$GLOBAL_BAN_DIR/global-bans.list"' in reapply
# Read-only aliases remain for software outside ASR.
assert 'ln -sfn global-bans.list "$GLOBAL_BAN_DIR/urfd.blacklist"' in reapply
assert "L+ /run/urf-wil-config" in reapply and "systemd-tmpfiles --create" in reapply
assert "/usr/local/sbin/allscan-reimagined-global-ban" in reapply
assert "allscan-reimagined-urf-admin " not in reapply

docker_reapply = (ROOT / "docker/layered-reapply.sh").read_text(encoding="utf-8")
assert "install_helper asr-global-ban.py allscan-reimagined-global-ban" in docker_reapply
assert "GLOBAL_BAN_DIR=/var/lib/allscan-reimagined/admin" in docker_reapply

api = (ROOT / "asr-api.php").read_text(encoding="utf-8")
assert f"const ASR_GLOBAL_BAN_ROOT = '{STORE}';" in api
assert "ASR_GLOBAL_BAN_ROOT . '/global-bans.list'" in api
assert "urf-wil-config" not in api

zello = (ROOT / "scripts/asr-bridge-setup-zello-install.py").read_text(encoding="utf-8")
assert "- /var/lib/allscan-reimagined/admin:/run/asr-global:ro" in zello
assert 'ZELLO_GLOBAL_BANS: "/run/asr-global/global-bans.list"' in zello
assert 'ZELLO_SESSION_KICKS: "/run/asr-global/asr-zello-kicks.json"' in zello
assert f"ExecStartPre=/usr/bin/test -f {STORE}/global-bans.list" in zello

admin = sys.modules["asr_global_ban_storage_0"]
assert admin.global_enforcement_verified({"status": "applied"}, {"verified": True, "applied": [], "skipped": []})
assert not admin.global_enforcement_verified(
    {"status": "applied", "applied": {"unmappedGlobalRules": ["N0MISSING"]}},
    {"verified": True, "applied": [], "skipped": []})
for native in ({"verified": False, "skipped": ["p25"]}, {"verified": False, "unavailable": True}, {"verified": True, "skipped": ["nxdn"]}):
    assert not admin.global_enforcement_verified({"status": "applied"}, native)
assert not admin.global_enforcement_verified({"status": "pending"}, {"verified": True, "skipped": []})
assert json.loads(json.dumps(admin.ban_payload(["KE7WIL"], {}, now=1))) == [
    {"rule": "KE7WIL", "createdAt": 0, "expiresAt": 0, "remainingSeconds": 0}]

print("global ban storage self-test: ok")
