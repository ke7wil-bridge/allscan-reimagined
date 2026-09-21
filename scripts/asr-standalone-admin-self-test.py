#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts/asr-standalone-admin.py"
spec = importlib.util.spec_from_file_location("standalone_admin", HELPER_PATH)
admin = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(admin)


def write_json(path, payload, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(mode)


def check(value, message):
    if not value:
        raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix="asr-standalone-admin-test-") as temporary:
    base = Path(temporary)
    runtime = base / "runtime"
    bridge_dir = runtime / "ysf-local"
    bridge_dir.mkdir(parents=True)
    bridge_dir.chmod(0o755)
    config = base / "config.json"
    blacklist = base / "urfd.blacklist"
    timed = base / "asr-timed-bans.json"
    audit = base / "audit.jsonl"
    write_json(config, {"bridges": [{
        "id": "ysf-local", "mode": "ysf", "node": "1201",
        "cardType": "standard", "urfReflector": False,
    }]})
    blacklist.write_text("# managed\nN0CALL*\n", encoding="utf-8")
    timed.write_text("{}\n", encoding="utf-8")

    admin.CONFIG_PATH = config
    admin.RUNTIME_ROOT = runtime
    admin.TRUSTED_UID = os.geteuid()
    admin.BLACKLIST = blacklist
    admin.TIMED_BANS = timed
    admin.AUDIT_LOG = audit
    now = int(time.time())
    manifest = {
        "schema": 1, "bridgeId": "ysf-local", "mode": "ysf",
        "adapter": "test-adapter", "healthy": True, "heartbeatEpoch": now,
        "listClients": True, "kickClient": True,
        "kickContract": "disconnect-until-reconnect",
        "kickRequiresCurrentSession": True, "kickAllowsImmediateReconnect": True,
        "banClient": True, "unbanClient": True, "listBans": True,
        "banContract": "global-timed-v1", "appliedPolicyDigest": "0" * 64,
    }
    manifest_path = bridge_dir / "capabilities.json"
    write_json(manifest_path, manifest)
    stop = threading.Event()

    def adapter():
        while not stop.is_set():
            for request_path in bridge_dir.glob("request-*.json"):
                request = json.loads(request_path.read_text(encoding="utf-8"))
                result = {
                    "schema": 1, "nonce": request["nonce"],
                    "bridgeId": request["bridgeId"], "mode": request["mode"],
                    "action": request["action"], "ok": True, "verified": True,
                }
                if request["action"] == "kick":
                    result.update({
                        "identity": request["identity"], "removed": 1,
                        "enforcement": "disconnect-until-reconnect",
                    })
                else:
                    digest = request["policy"]["digest"]
                    result["appliedPolicyDigest"] = digest
                    current = json.loads(manifest_path.read_text(encoding="utf-8"))
                    current["appliedPolicyDigest"] = digest
                    current["heartbeatEpoch"] = int(time.time())
                    write_json(manifest_path, current)
                result_path = bridge_dir / f"result-{request['nonce']}.json"
                write_json(result_path, result)
            time.sleep(0.01)

    worker = threading.Thread(target=adapter, daemon=True)
    worker.start()
    try:
        kicked = admin.kick("ysf-local", "N7TEST", "admin")
        check(kicked["removed"] == 1 and kicked["verified"], "verified Kick failed")
        reconciled = admin.reconcile_all("admin")
        digest = admin.policy_payload()["digest"]
        check(reconciled["applied"][0]["appliedPolicyDigest"] == digest, "policy digest was not acknowledged")
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        check(current["appliedPolicyDigest"] == digest, "adapter manifest did not publish applied digest")

        current["heartbeatEpoch"] = now - 120
        write_json(manifest_path, current)
        try:
            admin.kick("ysf-local", "N7TEST", "admin")
            raise AssertionError("stale adapter was accepted")
        except admin.AdminError as error:
            check("stale" in str(error).lower(), "stale adapter failure was not explicit")

        current["heartbeatEpoch"] = int(time.time())
        current["bridgeId"] = "wrong-bridge"
        write_json(manifest_path, current)
        try:
            admin.kick("ysf-local", "N7TEST", "admin")
            raise AssertionError("wrong-bridge manifest was accepted")
        except admin.AdminError as error:
            check("wrong bridge" in str(error).lower(), "bridge binding failure was not explicit")
        check(audit.is_file(), "Kick audit was not written")
    finally:
        stop.set()
        worker.join(timeout=1)

print("Standalone bridge administration self-test: ok")
