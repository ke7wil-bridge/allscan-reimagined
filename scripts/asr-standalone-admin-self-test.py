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
        "enforcementEvidence": "relay-observed-v1",
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
            for request_path in runtime.glob("*/request-*.json"):
                active_manifest_path = request_path.parent / "capabilities.json"
                try:
                    request = json.loads(request_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
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
                    current = json.loads(active_manifest_path.read_text(encoding="utf-8"))
                    current["appliedPolicyDigest"] = digest
                    current["heartbeatEpoch"] = int(time.time())
                    write_json(active_manifest_path, current)
                result_path = request_path.parent / f"result-{request['nonce']}.json"
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

        # A fresh adapter must be able to receive its first policy before it
        # advertises Ban/Unban as active to the web UI.
        current.update({
            "banClient": False, "unbanClient": False, "listBans": False,
            "appliedPolicyDigest": "", "heartbeatEpoch": int(time.time()),
        })
        write_json(manifest_path, current)
        bootstrapped = admin.reconcile_all("installer")
        check(bootstrapped["applied"][0]["appliedPolicyDigest"] == digest,
              "initial policy reconciliation was blocked by inactive capability flags")

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

        # A native URF aggregate uses mode-named runtime proxies even though
        # only one aggregate bridge is stored in config.json.
        aggregate_dir = runtime / "ysf"
        aggregate_dir.mkdir()
        aggregate_dir.chmod(0o755)
        aggregate_manifest = {
            **manifest, "bridgeId": "ysf", "mode": "ysf",
            "heartbeatEpoch": int(time.time()), "appliedPolicyDigest": "0" * 64,
        }
        unproved_manifest = dict(aggregate_manifest)
        unproved_manifest.pop("enforcementEvidence")
        write_json(aggregate_dir / "capabilities.json", unproved_manifest)
        write_json(config, {"bridges": [{
            "id": "urfnet", "mode": "urf", "cardType": "standard",
            "urfReflector": True, "modes": ["ysf"],
        }]})
        try:
            admin.kick("ysf", "N7URF", "admin")
            raise AssertionError("unproved standalone adapter was accepted")
        except admin.AdminError as error:
            check("does not support" in str(error).lower(),
                  "missing enforcement evidence was not rejected")
        write_json(aggregate_dir / "capabilities.json", aggregate_manifest)
        write_json(config, {"bridges": [{
            "id": "urfnet", "mode": "urf", "cardType": "standard",
            "urfReflector": True, "modes": ["ysf"],
        }]})
        kicked = admin.kick("ysf", "N7URF", "admin")
        check(kicked["bridgeId"] == "ysf" and kicked["removed"] == 1,
              "aggregate URF Kick did not use its native mode proxy")
        reconciled = admin.reconcile_all("admin")
        check([item["mode"] for item in reconciled["applied"]] == ["ysf"],
              "aggregate URF policy did not reconcile through its native proxy")
    finally:
        stop.set()
        worker.join(timeout=1)

# Every non-DMR mode must fail closed without an authenticated live adapter.
with tempfile.TemporaryDirectory(prefix="asr-native-mode-gates-") as temporary:
    base = Path(temporary)
    admin.CONFIG_PATH = base / "config.json"
    admin.RUNTIME_ROOT = base / "runtime"
    admin.RUNTIME_ROOT.mkdir()
    admin.BLACKLIST = base / "urfd.blacklist"
    admin.TIMED_BANS = base / "asr-timed-bans.json"
    admin.BLACKLIST.write_text("# empty\n", encoding="utf-8")
    admin.TIMED_BANS.write_text("{}\n", encoding="utf-8")
    for mode in ("ysf", "p25", "nxdn", "m17"):
        bridge_id = f"{mode}-local"
        write_json(admin.CONFIG_PATH, {"bridges": [{"id": bridge_id, "mode": mode, "cardType": "standard", "urfReflector": False}]})
        directory = admin.RUNTIME_ROOT / bridge_id
        directory.mkdir()
        directory.chmod(0o755)
        result = admin.reconcile_all("admin")
        check(not result["ok"] and not result["verified"] and bridge_id in result["skipped"],
              f"{mode} missing adapter falsely reported global enforcement")
        try:
            admin.kick(bridge_id, "N7TEST", "admin")
        except (admin.AdminError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"{mode} Kick accepted without a verified adapter")
        directory.rmdir()

    # A configured Zello card cannot disappear from global Ban verification.
    write_json(admin.CONFIG_PATH, {"bridges": [
        {"id": "zello-local", "mode": "zello", "cardType": "standard"},
    ]})
    zello_policy = admin.reconcile_all("admin")
    check(not zello_policy["ok"] and not zello_policy["verified"]
          and "zello" in zello_policy["skipped"],
          "configured Zello falsely reported global Ban enforcement")
    check(any(item["bridgeId"] == "zello" and item["reason"]
              for item in zello_policy["failed"]),
          "Zello global Ban failure did not include a reason")

    # Nonstandard/disabled aggregate cards must not invent enabled protocols.
    write_json(admin.CONFIG_PATH, {"bridges": [{
        "id": "inactive-urf", "urfReflector": True, "cardType": "custom",
        "modes": ["dmr", "ysf", "p25", "nxdn", "m17"],
    }]})
    inactive_policy = admin.reconcile_all("admin")
    check(not inactive_policy["skipped"] and not inactive_policy["failed"],
          "nonstandard aggregate card was incorrectly included in Ban enforcement")

    # Aggregate DMR must not be silently treated as globally reconciled.
    write_json(admin.CONFIG_PATH, {"bridges": [{
        "id": "urfnet", "mode": "urf", "cardType": "standard",
        "urfReflector": True, "modes": ["dmr"],
    }]})
    dmr_policy = admin.reconcile_all("admin")
    check(not dmr_policy["ok"] and not dmr_policy["verified"]
          and "dmr" in dmr_policy["skipped"],
          "aggregate DMR falsely reported global Ban enforcement")

    # Mixed coverage must never be reported as a globally verified Ban.
    write_json(admin.CONFIG_PATH, {"bridges": [
        {"id": "ysf-local", "mode": "ysf", "cardType": "standard", "urfReflector": False},
        {"id": "p25-local", "mode": "p25", "cardType": "standard", "urfReflector": False},
    ]})
    result = admin.reconcile_all("admin")
    check(not result["ok"] and not result["verified"]
          and set(result["skipped"]) == {"ysf-local", "p25-local"},
          "multi-mode missing enforcement falsely reported global Ban success")
    check({item["bridgeId"] for item in result["failed"]}
          == {"ysf-local", "p25-local"},
          "global Ban omitted per-bridge failure evidence")
    check(all(item["reason"] for item in result["failed"]),
          "global Ban failure evidence omitted a reason")
    kicked = admin.kick_all("n7test", "admin")
    check(kicked["callsign"] == "N7TEST" and not kicked["ok"] and not kicked["verified"]
          and not kicked["complete"], "global Kick falsely reported complete")
    check({item["bridgeId"] for item in kicked["failed"]}
          == {"ysf-local", "p25-local", "zello"},
          "global Kick did not disclose every unavailable mode")
    check(not kicked["applied"], "global Kick reported unsupported bridge as applied")
    # A successful native Kick on one bridge cannot mask failures elsewhere.
    original_kick = admin.kick
    try:
        def simulated_kick(bridge_id, identity, actor):
            if bridge_id == "ysf-local":
                return {"ok": True, "verified": True, "bridgeId": bridge_id,
                        "callsign": identity, "removed": 1}
            raise admin.AdminError("Adapter unavailable")
        admin.kick = simulated_kick
        partial = admin.kick_all("N7TEST", "admin")
        check(len(partial["applied"]) == 1 and partial["applied"][0]["mode"] == "ysf",
              "global Kick lost successful per-mode evidence")
        check(not partial["ok"] and not partial["verified"] and not partial["complete"],
              "partial global Kick falsely reported complete")
        check({item["mode"] for item in partial["failed"]} == {"p25", "zello"},
              "partial global Kick lost failed mode evidence")
    finally:
        admin.kick = original_kick
    # Unverified adapter responses must never count as successful global Kick.
    try:
        admin.kick = lambda bridge_id, identity, actor: {
            "ok": True, "verified": False, "removed": 1}
        unverified = admin.kick_all("N7TEST", "admin")
        check(not unverified["applied"] and not unverified["verified"],
              "global Kick trusted an unverified adapter response")
    finally:
        admin.kick = original_kick
    # Aggregate URF modes must also appear individually in failure evidence.
    write_json(admin.CONFIG_PATH, {"bridges": [{
        "id": "urfnet", "mode": "urf", "cardType": "standard",
        "urfReflector": True, "modes": ["dmr", "ysf", "p25", "nxdn", "m17"],
    }]})
    aggregate = admin.kick_all("N7TEST", "admin")
    check(not aggregate["verified"] and not aggregate["complete"],
          "aggregate global Kick falsely reported success")
    check({item["mode"] for item in aggregate["failed"]}
          == {"dmr", "ysf", "p25", "nxdn", "m17", "zello"},
          "aggregate global Kick omitted one or more mode failures")
    try:
        admin.kick_all("INVALID IDENTITY", "admin")
    except admin.AdminError:
        pass
    else:
        raise AssertionError("global Kick accepted an invalid identity")

print("Standalone bridge administration self-test: ok")
