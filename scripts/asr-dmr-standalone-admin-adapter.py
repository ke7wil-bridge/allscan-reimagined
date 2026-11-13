#!/usr/bin/env python3
"""Administration adapter for one ASR-managed direct DMR bridge."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,31}")
NONCE_RE = re.compile(r"[a-f0-9]{32}")
IDENTITY_RE = re.compile(r"[A-Z0-9][A-Z0-9_.\/-]{0,14}\*?")


def atomic(path: Path, payload: Any, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True); handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, mode); os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError): os.unlink(temporary)


def read(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError):
        return default


def control_payload(path: Path) -> dict[str, Any]:
    value = read(path, {})
    return value if isinstance(value, dict) else {}


def current_rows(roster: Path) -> list[dict[str, Any]]:
    payload = read(roster, {})
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("clients") if isinstance(payload.get("clients"), list) else payload.get("urf_dmr", [])
    return [row for row in rows if isinstance(row, dict)]


def enforcement_payload(path: Path) -> dict[str, Any]:
    payload = read(path, {})
    return payload if isinstance(payload, dict) else {}


def enforcement_ready(path: Path, now: int | None = None) -> bool:
    payload = enforcement_payload(path)
    return valid_enforcement(payload, now)


def valid_enforcement(payload: dict[str, Any], now: int | None = None) -> bool:
    epoch = payload.get("heartbeatEpoch")
    return (payload.get("schema") == 1
            and payload.get("contract") == "dmr-local-media-v1"
            and isinstance(epoch, int)
            and abs((int(time.time()) if now is None else now) - epoch) <= 5)


def wait_for(path: Path, predicate, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = enforcement_payload(path)
        if predicate(payload):
            return payload
        time.sleep(0.05)
    raise ValueError("DMR relay did not verify local media enforcement")


def active_policy_rules(policy: dict[str, Any]) -> list[str]:
    rules = [str(value).strip().upper() for value in policy.get("rules", [])]
    timed = policy.get("timedBans", {})
    now = int(time.time())
    if isinstance(timed, dict):
        for identity, details in timed.items():
            expiry = details.get("expiresAt", details.get("expires", 0)) if isinstance(details, dict) else details
            try:
                if int(expiry) > now: rules.append(str(identity).strip().upper())
            except (TypeError, ValueError):
                continue
    return sorted({rule for rule in rules if IDENTITY_RE.fullmatch(rule)})


def apply_policy(control: Path, policy: dict[str, Any], enforcement: Path | None = None) -> str:
    digest = str(policy.get("digest") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("global ban policy digest is invalid")
    rules = active_policy_rules(policy)
    payload = control_payload(control)
    payload["banned_ids"] = sorted(int(rule) for rule in rules if rule.isdigit())
    payload["banned_callsigns"] = sorted(rule for rule in rules if not rule.isdigit() and not rule.endswith("*"))
    payload["banned_prefixes"] = sorted(rule[:-1] for rule in rules if rule.endswith("*"))
    payload["policy_digest"] = digest
    payload.setdefault("kicked_ids", {})
    atomic(control, payload)
    if enforcement is not None:
        wait_for(enforcement, lambda item: valid_enforcement(item)
                 and item.get("appliedPolicyDigest") == digest)
    return digest


def kick(identity: str, roster: Path, control: Path, enforcement: Path, token: str) -> int:
    identity = identity.upper()
    matched = []
    for row in current_rows(roster):
        dmr_id = str(row.get("dmrid") or row.get("id") or "")
        callsign = str(row.get("callsign") or "").upper()
        if identity in {dmr_id, callsign} and dmr_id.isdigit(): matched.append(int(dmr_id))
    matched = sorted(set(matched))
    if not matched:
        raise ValueError("client is no longer connected")
    if len(matched) != 1:
        raise ValueError("client identity is ambiguous in the current roster")
    payload = control_payload(control); kicked = payload.get("kicked_ids", {})
    if not isinstance(kicked, dict): kicked = {}
    epoch = int(time.time())
    for dmr_id in matched: kicked[str(dmr_id)] = epoch
    tokens = payload.get("kick_tokens", {})
    if not isinstance(tokens, dict): tokens = {}
    tokens[str(matched[0])] = token
    payload["kicked_ids"] = kicked; payload["kick_tokens"] = tokens; atomic(control, payload)
    wait_for(enforcement, lambda item: valid_enforcement(item)
             and isinstance(item.get("lastKick"), dict)
             and item["lastKick"].get("dmrid") == str(matched[0])
             and item["lastKick"].get("token") == token)
    return len(matched)


def capability(bridge_id: str, digest: str, enforcement: Path | None = None) -> dict[str, Any]:
    ready = enforcement is not None and enforcement_ready(enforcement)
    return {"schema": 1, "bridgeId": bridge_id, "mode": "dmr",
            "adapter": "direct-dmr-policy-v2", "healthy": ready,
            "enforcementEvidence": "relay-observed-v1",
            "heartbeatEpoch": int(time.time()), "listClients": True,
            "kickClient": ready, "kickContract": "local-media-block-until-reconnect",
            "kickRequiresCurrentSession": True, "kickAllowsImmediateReconnect": True,
            "banClient": ready, "unbanClient": ready, "listBans": ready,
            "banContract": "global-timed-v1", "appliedPolicyDigest": digest}


def handle(request_path: Path, bridge_id: str, roster: Path, control: Path, enforcement: Path, digest: str) -> str:
    request = read(request_path, {})
    nonce = str(request.get("nonce") or "")
    action = str(request.get("action") or "")
    if (not NONCE_RE.fullmatch(nonce) or request.get("schema") != 1
            or request.get("bridgeId") != bridge_id or request.get("mode") != "dmr"):
        raise ValueError("administration request is invalid")
    result = {"schema": 1, "nonce": nonce, "bridgeId": bridge_id, "mode": "dmr",
              "action": action, "ok": True, "verified": True}
    if action == "kick":
        identity = str(request.get("identity") or "").upper()
        if not IDENTITY_RE.fullmatch(identity) or identity.endswith("*"):
            raise ValueError("kick identity is invalid")
        result.update({"identity": identity, "removed": kick(identity, roster, control, enforcement, nonce),
                       "enforcement": "local-media-block-until-reconnect"})
    elif action == "reconcile":
        digest = apply_policy(control, request.get("policy") if isinstance(request.get("policy"), dict) else {}, enforcement)
        result.update({"appliedPolicyDigest": digest, "removed": 0})
    else:
        raise ValueError("unsupported administration action")
    atomic(request_path.parent / f"result-{nonce}.json", result, 0o600)
    return digest


def run(bridge_id: str, roster: Path, control: Path, runtime: Path, creation_id: str) -> None:
    runtime.mkdir(parents=True, exist_ok=True); os.chmod(runtime, 0o750)
    atomic(runtime / ".asr-bridge-owner.json", {"schema": 1, "ownership": "asr",
           "createdBy": "allscan-reimagined-bridge-installer", "creationId": creation_id,
           "bridgeId": bridge_id}, 0o640)
    digest = "0" * 64
    enforcement = control.with_name("dmr-admin-enforcement.json")
    while True:
        atomic(runtime / "capabilities.json", capability(bridge_id, digest, enforcement), 0o644)
        for request_path in runtime.glob("request-*.json"):
            try:
                digest = handle(request_path, bridge_id, roster, control, enforcement, digest)
            except Exception as error:
                nonce = request_path.stem.removeprefix("request-")
                if NONCE_RE.fullmatch(nonce):
                    atomic(runtime / f"result-{nonce}.json", {"schema": 1, "nonce": nonce,
                        "bridgeId": bridge_id, "mode": "dmr", "action": str(read(request_path, {}).get("action") or ""),
                        "ok": False, "verified": False, "error": str(error)[:240]}, 0o600)
            finally:
                request_path.unlink(missing_ok=True)
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("bridge_id"); parser.add_argument("roster", type=Path); parser.add_argument("control", type=Path); parser.add_argument("creation_id")
    args = parser.parse_args()
    if (not ID_RE.fullmatch(args.bridge_id) or not args.roster.is_absolute() or not args.control.is_absolute()
            or not re.fullmatch(r"[a-f0-9]{32}", args.creation_id)):
        raise SystemExit("invalid direct DMR administration configuration")
    run(args.bridge_id, args.roster, args.control, Path("/run/asr-standalone-admin") / args.bridge_id, args.creation_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
