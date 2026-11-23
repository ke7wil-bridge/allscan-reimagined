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
    # Match the production adapter's atomic result publication. A direct
    # write lets the request reader observe a zero-length partial JSON file.
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    # The lifecycle acceptance gate must reject unsupported or fabricated
    # protocol enforcement before any managed bridge is accepted.
    import importlib.util as _importlib_util
    import sys as _sys
    _spec = _importlib_util.spec_from_file_location("asr_lifecycle_contract_test", ROOT / "scripts/asr-bridge-lifecycle.py")
    _lifecycle = _importlib_util.module_from_spec(_spec)
    _sys.modules[_spec.name] = _lifecycle
    _spec.loader.exec_module(_lifecycle)
    _lifecycle.validate_admin_capabilities(manifest, "ysf-local", "ysf")
    for mode in ("ysf", "p25", "nxdn", "m17"):
        candidate = {**manifest, "mode": mode}
        _lifecycle.validate_admin_capabilities(candidate, "ysf-local", mode)
        for invalid in (
            {"kickClient": False},
            {"kickContract": "not-supported"},
            {"kickContract": "local-media-block-until-reconnect"},
            {"kickContract": "local-media-block-until-reconnect", "enforcementEvidence": "self-reported"},
            {"banContract": "unverified"},
            {"appliedPolicyDigest": "invalid"},
        ):
            try:
                _lifecycle.validate_admin_capabilities({**candidate, **invalid}, "ysf-local", mode)
            except _lifecycle.LifecycleError:
                pass
            else:
                raise AssertionError(f"{mode}: unsupported enforcement was accepted: {invalid}")
        _lifecycle.validate_admin_capabilities({**candidate, "kickContract": "local-media-block-until-reconnect", "enforcementEvidence": "relay-observed-v1"}, "ysf-local", mode)
    print("YSF/P25/NXDN/M17 administration acceptance contracts: PASS")
    manifest_path = bridge_dir / "capabilities.json"
    write_json(manifest_path, manifest)
    stop = threading.Event()

    handled = set()

    def adapter():
        while not stop.is_set():
            for request_path in runtime.glob("*/request-*.json"):
                active_manifest_path = request_path.parent / "capabilities.json"
                try:
                    request = json.loads(request_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
                # Answer each request once; re-handling a request the caller has
                # not yet removed rewrote the manifest heartbeat (flaky test).
                if request["nonce"] in handled:
                    continue
                handled.add(request["nonce"])
                result = {
                    "schema": 1, "nonce": request["nonce"],
                    "bridgeId": request["bridgeId"], "mode": request["mode"],
                    "action": request["action"], "ok": True, "verified": True,
                }
                if request["action"] == "kick":
                    result.update({
                        "identity": request["identity"], "clientId": request.get("clientId", ""), "removed": 1,
                        "enforcement": ("local-media-block-until-reconnect"
                                        if request["mode"] == "dmr" or request["identity"] == "N7LOCAL"
                                        else "disconnect-until-reconnect"),
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
        # A configured bridge without a live adapter must never produce a
        # globally verified policy result merely because it was skipped.
        write_json(config, {"bridges": [
            {"id": "ysf-local", "mode": "ysf", "node": "1201", "cardType": "standard", "urfReflector": False},
            {"id": "p25-missing", "mode": "p25", "node": "1202", "cardType": "standard", "urfReflector": False},
        ]})
        incomplete = admin.reconcile_all("admin")
        check(incomplete["verified"] is False and "p25-missing" in incomplete["skipped"],
              "missing native adapter was falsely reported as globally verified")
        write_json(config, {"bridges": [
            {"id": "ysf-local", "mode": "ysf", "node": "1201", "cardType": "standard", "urfReflector": False},
        ]})
        kicked = admin.kick("ysf-local", "N7TEST", "admin")
        check(kicked["removed"] == 1 and kicked["verified"], "verified Kick failed")
        reconciled = admin.reconcile_all("admin")
        digest = admin.policy_payload()["digest"]
        check(reconciled["applied"][0]["appliedPolicyDigest"] == digest, "policy digest was not acknowledged")
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        check(current["appliedPolicyDigest"] == digest, "adapter manifest did not publish applied digest")

        # DMR's relay-verified local media block is a distinct, valid Kick
        # contract. Reject an unattested contract and a mismatched response.
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        current.update({"kickContract": "local-media-block-until-reconnect",
                        "enforcementEvidence": "relay-observed-v1",
                        "heartbeatEpoch": int(time.time())})
        write_json(manifest_path, current)
        try:
            admin.kick("ysf-local", "N7TEST", "admin")
            raise AssertionError("mismatched local-media Kick acknowledgment accepted")
        except admin.AdminError as error:
            check("attested enforcement contract" in str(error),
                  "mismatched Kick enforcement was not rejected")
        kicked_local = admin.kick("ysf-local", "N7LOCAL", "admin")
        check(kicked_local["enforcement"] == "local-media-block-until-reconnect"
              and kicked_local["verified"], "verified local-media Kick failed")
        current["enforcementEvidence"] = "unverified"
        write_json(manifest_path, current)
        try:
            admin.kick("ysf-local", "N7TEST", "admin")
            raise AssertionError("unattested local-media Kick was accepted")
        except admin.AdminError as error:
            check("does not support kick" in str(error),
                  "unattested local-media Kick was not rejected")
        current.update({"kickContract": "disconnect-until-reconnect",
                        "enforcementEvidence": "relay-observed-v1"})
        write_json(manifest_path, current)

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

        # DMR Kick must carry the exact roster ID through the control helper;
        # callsign-only success is not an acceptable enforcement receipt.
        dmr_dir = runtime / "dmr-local"
        dmr_dir.mkdir(); dmr_dir.chmod(0o755)
        dmr_manifest = {**manifest, "bridgeId": "dmr-local", "mode": "dmr",
            "kickContract": "local-media-block-until-reconnect",
            "enforcementEvidence": "relay-observed-v1", "heartbeatEpoch": int(time.time())}
        write_json(dmr_dir / "capabilities.json", dmr_manifest)
        write_json(config, {"bridges": [{"id": "dmr-local", "mode": "dmr", "node": "1203",
            "cardType": "standard", "urfReflector": False}]})
        dmr_kick = admin.kick("dmr-local", "N1DMR", "admin", "1234567")
        check(dmr_kick["verified"] and dmr_kick["clientId"] == "1234567",
              "exact DMR ID was not preserved in the verified Kick result")
        try:
            admin.kick("dmr-local", "N1DMR", "admin")
            raise AssertionError("callsign-only DMR Kick was accepted")
        except admin.AdminError as error:
            check("DMR client ID" in str(error), "missing DMR ID failure was not explicit")

        # URF reflectors were retired (#177): a leftover aggregate card is not
        # administrable even when a mode-named native proxy is running, and it
        # is not counted toward Global Ban reconciliation.
        aggregate_dir = runtime / "ysf"
        aggregate_dir.mkdir()
        aggregate_dir.chmod(0o755)
        write_json(aggregate_dir / "capabilities.json", {
            **manifest, "bridgeId": "ysf", "mode": "ysf",
            "heartbeatEpoch": int(time.time()), "appliedPolicyDigest": "0" * 64,
        })
        write_json(config, {"bridges": [{
            "id": "urfnet", "mode": "urf", "cardType": "standard",
            "urfReflector": True, "modes": ["ysf"],
        }]})
        for bridge_id in ("ysf", "urfnet"):
            try:
                admin.kick(bridge_id, "N7URF", "admin")
                raise AssertionError(f"retired URF aggregate accepted a Kick via {bridge_id}")
            except admin.AdminError:
                pass
        reconciled = admin.reconcile_all("admin")
        check(reconciled["applied"] == [] and reconciled["skipped"] == [],
              "retired URF aggregate took part in Global Ban reconciliation")

    finally:
        stop.set()
        worker.join(timeout=1)

print("Standalone bridge administration self-test: ok")
