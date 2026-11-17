#!/usr/bin/env python3
"""Validated control plane for independently installed ASR bridge adapters."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
RUNTIME_ROOT = Path("/run/asr-standalone-admin")
GLOBAL_BAN_ROOT = Path(os.environ.get("ASR_GLOBAL_BAN_ROOT", "/var/lib/allscan-reimagined/admin"))
BLACKLIST = GLOBAL_BAN_ROOT / "global-bans.list"
TIMED_BANS = GLOBAL_BAN_ROOT / "asr-timed-bans.json"
AUDIT_LOG = GLOBAL_BAN_ROOT / "asr-admin-audit.jsonl"
ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}")
IDENTITY_RE = re.compile(r"[A-Z0-9][A-Z0-9_.\/-]{0,14}")
ACTOR_RE = re.compile(r"[A-Za-z0-9_.@+-]{1,80}")
ADAPTER_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
SUPPORTED_MODES = {"dmr", "ysf", "p25", "nxdn", "m17"}
MAX_JSON = 64 * 1024
FRESH_SECONDS = 30
RESULT_TIMEOUT = 12.0
TRUSTED_UID = 0
class AdminError(RuntimeError):
    pass


def emit(**payload: Any) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def read_json(path: Path, *, trusted: bool = False) -> Any:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise AdminError(f"Unsafe administration file: {path.name}")
        if trusted and (details.st_uid != TRUSTED_UID or details.st_mode & 0o022):
            raise AdminError(f"Untrusted administration file: {path.name}")
        if details.st_size < 2 or details.st_size > MAX_JSON:
            raise AdminError(f"Invalid administration file size: {path.name}")
        raw = os.read(descriptor, MAX_JSON + 1)
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise AdminError(f"Invalid administration JSON: {path.name}") from exc


def atomic_json(path: Path, payload: Any, mode: int = 0o640) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
def bridge_config(bridge_id: str) -> dict[str, Any]:
    if not ID_RE.fullmatch(bridge_id):
        raise AdminError("Invalid standalone bridge ID.")
    payload = read_json(CONFIG_PATH)
    if not isinstance(payload, dict) or not isinstance(payload.get("bridges"), list):
        raise AdminError("ASR bridge configuration is unavailable.")
    for bridge in payload["bridges"]:
        if not isinstance(bridge, dict) or bridge.get("id") != bridge_id:
            continue
        mode = str(bridge.get("mode") or bridge.get("type") or bridge_id).lower()
        mode = next((item for item in SUPPORTED_MODES if mode.startswith(item)), "")
        if not mode or bridge.get("cardType", "standard") != "standard":
            raise AdminError("Bridge is not an administrable standalone digital card.")
        return {**bridge, "_mode": mode}
    raise AdminError("Configured standalone bridge was not found.")


def bridge_runtime(bridge_id: str) -> Path:
    root = RUNTIME_ROOT.resolve(strict=True)
    directory = (root / bridge_id).resolve(strict=True)
    if root not in directory.parents:
        raise AdminError("Standalone administration path escaped its runtime root.")
    details = directory.stat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != TRUSTED_UID or details.st_mode & 0o022:
        raise AdminError("Standalone administration directory is untrusted.")
    return directory


def manifest_for(bridge: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    bridge_id, mode = str(bridge["id"]), str(bridge["_mode"])
    directory = bridge_runtime(bridge_id)
    path = directory / "capabilities.json"
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise AdminError("Standalone capability manifest cannot be a symlink.")
    manifest = read_json(path, trusted=True)
    now = int(time.time())
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise AdminError("Unsupported standalone capability manifest.")
    if manifest.get("bridgeId") != bridge_id or manifest.get("mode") != mode:
        raise AdminError("Standalone capability manifest targets the wrong bridge.")
    heartbeat = manifest.get("heartbeatEpoch")
    if manifest.get("healthy") is not True or not isinstance(heartbeat, int):
        raise AdminError("Standalone bridge adapter is unhealthy.")
    if abs(now - heartbeat) > FRESH_SECONDS or abs(now - int(details.st_mtime)) > FRESH_SECONDS:
        raise AdminError("Standalone bridge adapter heartbeat is stale.")
    if not ADAPTER_RE.fullmatch(str(manifest.get("adapter", ""))):
        raise AdminError("Standalone bridge adapter identity is invalid.")
    return directory, manifest


def policy_payload() -> dict[str, Any]:
    try:
        blacklist = BLACKLIST.read_bytes()
        timed_raw = TIMED_BANS.read_bytes()
        timed = json.loads(timed_raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AdminError("Global Ban policy is unavailable.") from exc
    rules = []
    for raw in blacklist.decode("utf-8").splitlines():
        token = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if token and not token.startswith("#"):
            rules.append(token.upper())
    if not isinstance(timed, dict):
        raise AdminError("Global timed-ban policy is invalid.")
    digest = hashlib.sha256(b"asr-global-ban-v1\0" + blacklist + b"\0" + timed_raw).hexdigest()
    return {"rules": sorted(set(rules)), "timedBans": timed, "digest": digest}


def supports_policy_reconcile(manifest: dict[str, Any]) -> bool:
    """Accept a declared ban contract before its first policy sync.

    An adapter cannot truthfully advertise active Ban/Unban support until it
    has applied the current policy digest.  The contract itself is enough to
    bootstrap that first reconciliation; the public API still requires the
    matching applied digest before it exposes the controls.
    """
    return (
        manifest.get("banContract") == "global-timed-v1"
        and all(isinstance(manifest.get(field), bool)
                for field in ("banClient", "unbanClient", "listBans"))
    )


def append_audit(action: str, actor: str, **details: Any) -> None:
    record = {"timestamp": int(time.time()), "action": action, "actor": actor, **details}
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(AUDIT_LOG, flags, 0o640)
    try:
        os.write(descriptor, (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
def kick_terms_valid(manifest: dict[str, Any]) -> bool:
    """Instant-reconnect contracts, or a bounded reconnect hold (disconnect-hold-v1)."""
    contract = manifest.get("kickContract")
    if contract in {"disconnect-until-reconnect", "local-media-block-until-reconnect"}:
        return manifest.get("kickAllowsImmediateReconnect") is True
    hold = manifest.get("kickHoldSeconds")
    return (contract == "disconnect-hold-v1" and manifest.get("kickAllowsImmediateReconnect") is False
            and isinstance(hold, int) and not isinstance(hold, bool) and 30 <= hold <= 3600)


def request(bridge: dict[str, Any], action: str, actor: str, **payload: Any) -> dict[str, Any]:
    directory, manifest = manifest_for(bridge)
    required = {
        "kick": (
            manifest.get("kickClient") is True
            and kick_terms_valid(manifest)
            and (manifest.get("kickContract") != "local-media-block-until-reconnect"
                 or manifest.get("enforcementEvidence") == "relay-observed-v1")
            and manifest.get("kickRequiresCurrentSession") is True
        ),
        "reconcile": (
            supports_policy_reconcile(manifest)
        ),
    }
    if not required.get(action, False):
        raise AdminError(f"Standalone bridge adapter does not support {action}.")
    nonce = secrets.token_hex(16)
    request_path = directory / f"request-{nonce}.json"
    result_path = directory / f"result-{nonce}.json"
    body = {
        "schema": 1, "nonce": nonce, "bridgeId": bridge["id"],
        "mode": bridge["_mode"], "action": action, "actor": actor,
        "requestedEpoch": int(time.time()), **payload,
    }
    lock_path = directory / ".asr-admin.lock"
    lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        atomic_json(request_path, body)
        deadline = time.monotonic() + RESULT_TIMEOUT
        while time.monotonic() < deadline:
            if result_path.exists():
                result = read_json(result_path, trusted=True)
                break
            time.sleep(0.1)
        else:
            raise AdminError("Standalone bridge adapter did not acknowledge the request.")
    finally:
        with contextlib.suppress(FileNotFoundError):
            request_path.unlink()
        os.close(lock)
    try:
        if not isinstance(result, dict) or result.get("schema") != 1:
            raise AdminError("Standalone bridge adapter returned an invalid result.")
        expected = {"nonce": nonce, "bridgeId": bridge["id"], "mode": bridge["_mode"], "action": action}
        if any(result.get(key) != value for key, value in expected.items()):
            raise AdminError("Standalone bridge adapter result did not match the request.")
        if result.get("ok") is not True or result.get("verified") is not True:
            raise AdminError(str(result.get("error") or "Standalone bridge action was not verified."))
        return result
    finally:
        with contextlib.suppress(FileNotFoundError):
            result_path.unlink()


def kick(bridge_id: str, identity: str, actor: str, client_id: str = "") -> dict[str, Any]:
    identity = identity.strip().upper()
    if not IDENTITY_RE.fullmatch(identity):
        raise AdminError("Invalid bridge client identity.")
    bridge = bridge_config(bridge_id)
    if bridge["_mode"] == "dmr" and (not client_id.isdigit() or not 0 < int(client_id) < 2**24):
        raise AdminError("A current DMR client ID is required.")
    result = request(bridge, "kick", actor, identity=identity, clientId=client_id)
    expected_contract = str(manifest_for(bridge)[1].get("kickContract") or "")
    if (result.get("identity") != identity or result.get("enforcement") != expected_contract
            or (bridge["_mode"] == "dmr" and result.get("clientId") != client_id)):
        raise AdminError("Standalone Kick acknowledgment did not match the attested enforcement contract.")
    removed = int(result.get("removed") or 0)
    if removed < 1:
        raise AdminError("Client was no longer connected or could not be kicked.")
    append_audit("kick", actor, bridgeId=bridge_id, callsign=identity,
                 clientId=client_id, removed=removed)
    return {"ok": True, "verified": True, "action": "kick", "bridgeId": bridge_id,
            "callsign": identity, "clientId": client_id, "removed": removed,
            "enforcement": result["enforcement"]}


def reconcile(bridge_id: str, actor: str = "system") -> dict[str, Any]:
    bridge = bridge_config(bridge_id)
    policy = policy_payload()
    result = request(bridge, "reconcile", actor, policy=policy)
    if result.get("appliedPolicyDigest") != policy["digest"]:
        raise AdminError("Standalone bridge did not apply the current Global Ban policy.")
    return {"bridgeId": bridge_id, "mode": bridge["_mode"],
            "appliedPolicyDigest": policy["digest"],
            "removed": int(result.get("removed") or 0)}
def reconcile_all(actor: str = "system") -> dict[str, Any]:
    try:
        config = read_json(CONFIG_PATH)
    except (OSError, AdminError):
        raise AdminError("ASR bridge configuration is unavailable.")
    bridge_ids = [
        str(item.get("id"))
        for item in config.get("bridges", [])
        if isinstance(item, dict) and ID_RE.fullmatch(str(item.get("id", "")))
        and item.get("cardType", "standard") == "standard"
        and any(str(item.get("mode") or item.get("type") or item.get("id", "")).lower().startswith(mode) for mode in SUPPORTED_MODES)
    ]
    bridge_ids = list(dict.fromkeys(bridge_ids))
    applied = []
    for bridge_id in bridge_ids:
        try:
            bridge = bridge_config(bridge_id)
            _, manifest = manifest_for(bridge)
        except FileNotFoundError:
            # Standalone protocols only participate when a verified native
            # adapter is installed. Unsupported bridges remain visible but do
            # not falsely advertise or block global administration.
            continue
        if not supports_policy_reconcile(manifest):
            continue
        applied.append(reconcile(bridge_id, actor))
    skipped = sorted(set(bridge_ids) - {item["bridgeId"] for item in applied})
    # A missing or incapable adapter is not proof of global enforcement.
    # The caller must not advertise successful reconciliation of all bridges.
    return {"ok": True, "verified": not skipped, "action": "reconcile-all",
            "applied": applied, "skipped": skipped,
            "policyDigest": policy_payload()["digest"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["kick", "reconcile", "reconcile-all"])
    parser.add_argument("bridge_id", nargs="?")
    parser.add_argument("identity", nargs="?")
    parser.add_argument("--actor", default="system")
    parser.add_argument("--client-id", default="")
    args = parser.parse_args()
    if not ACTOR_RE.fullmatch(args.actor):
        raise AdminError("Invalid administrator identity.")
    if args.action == "kick":
        if not args.bridge_id or not args.identity:
            raise AdminError("Kick requires BRIDGE_ID and IDENTITY.")
        emit(**kick(args.bridge_id, args.identity, args.actor, args.client_id))
    elif args.action == "reconcile":
        if not args.bridge_id:
            raise AdminError("Reconcile requires BRIDGE_ID.")
        emit(ok=True, verified=True, action="reconcile", **reconcile(args.bridge_id, args.actor))
    else:
        emit(**reconcile_all(args.actor))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit(ok=False, error=str(exc))
        raise SystemExit(1)
