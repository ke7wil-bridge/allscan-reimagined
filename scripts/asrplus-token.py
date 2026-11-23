#!/usr/bin/env python3
"""Manage and authenticate scoped ASR Plus dashboard bearer tokens."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time


STORE_PATH = Path(os.environ.get("ASRPLUS_TOKEN_STORE", "/var/lib/asrplus/tokens.json"))
AUDIT_PATH = Path(os.environ.get("ASRPLUS_TOKEN_AUDIT", "/var/log/asrplus-token-audit.jsonl"))
LOCK_PATH = Path(os.environ.get("ASRPLUS_TOKEN_LOCK", "/run/lock/asrplus-token.lock"))
KNOWN_NODES = {
    "kentucky": "674982",
    "phoenix": "641894",
    "georgia": "674980",
    "texas": "674984",
}
PERMISSIONS = {
    "status:read",
    "control:connect",
    "control:disconnect",
    "control:ban",
    "control:unban",
}
TOKEN_RE = re.compile(r"^asrp_([a-f0-9]{16})_([A-Za-z0-9_-]{43})$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.@+-]{0,79}$")


class TokenError(ValueError):
    pass


def utc(epoch: int | None = None) -> str:
    value = int(time.time()) if epoch is None else epoch
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def load_store() -> dict:
    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "tokens": []}
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("tokens"), list):
        raise TokenError("token store format is invalid")
    return payload


def atomic_store(payload: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STORE_PATH.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".tokens.", dir=STORE_PATH.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, STORE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def audit(event: str, *, token_id: str = "", allowed: bool, reason: str = "", node: str = "",
          permission: str = "", source: str = "", method: str = "") -> None:
    record = {
        "utc": utc(), "event": event, "tokenId": token_id, "allowed": allowed,
        "reason": reason[:120], "node": node, "permission": permission,
        "source": source[:80], "method": method[:12],
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    os.chmod(AUDIT_PATH, 0o640)


def locked_store() -> tuple[object, dict]:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock, load_store()


def parse_csv(value: str) -> list[str]:
    return sorted({part.strip().lower() for part in value.split(",") if part.strip()})


def create(args: argparse.Namespace) -> dict:
    if not LABEL_RE.fullmatch(args.label):
        raise TokenError("label must contain only safe printable identity characters")
    nodes = parse_csv(args.nodes)
    permissions = parse_csv(args.permissions or "status:read")
    if not nodes or any(node not in KNOWN_NODES for node in nodes):
        raise TokenError("one or more node scopes are invalid")
    if not permissions or any(permission not in PERMISSIONS for permission in permissions):
        raise TokenError("one or more permissions are invalid")
    if args.expires_in < 300 or args.expires_in > 31_536_000:
        raise TokenError("expiration must be between 300 and 31536000 seconds")
    identifier = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    now = int(time.time())
    record = {
        "id": identifier, "label": args.label, "secretSha256": digest(secret),
        "nodes": nodes, "permissions": permissions, "createdAt": utc(now),
        "expiresAt": utc(now + args.expires_in), "expiresEpoch": now + args.expires_in,
        "revokedAt": None,
    }
    lock, store = locked_store()
    try:
        store["tokens"].append(record)
        atomic_store(store)
    finally:
        lock.close()
    audit("create", token_id=identifier, allowed=True, reason=args.label)
    return {"ok": True, "id": identifier, "token": f"asrp_{identifier}_{secret}",
            "nodes": nodes, "permissions": permissions, "expiresAt": record["expiresAt"]}


def revoke(args: argparse.Namespace) -> dict:
    if not re.fullmatch(r"[a-f0-9]{16}", args.id):
        raise TokenError("invalid token id")
    lock, store = locked_store()
    found = False
    try:
        for record in store["tokens"]:
            if record.get("id") == args.id:
                if record.get("revokedAt") is None:
                    record["revokedAt"] = utc()
                found = True
                break
        if not found:
            raise TokenError("token not found")
        atomic_store(store)
    finally:
        lock.close()
    audit("revoke", token_id=args.id, allowed=True)
    return {"ok": True, "id": args.id, "revoked": True}


def list_tokens(_args: argparse.Namespace) -> dict:
    records = []
    now = int(time.time())
    for item in load_store()["tokens"]:
        records.append({key: item.get(key) for key in (
            "id", "label", "nodes", "permissions", "createdAt", "expiresAt", "revokedAt"
        )} | {"expired": int(item.get("expiresEpoch") or 0) <= now})
    return {"ok": True, "tokens": records}


def authenticate(_args: argparse.Namespace) -> dict:
    try:
        request = json.loads(sys.stdin.buffer.read(16_385))
        if not isinstance(request, dict):
            raise TokenError("request must be an object")
        token = str(request.get("token", ""))
        node = str(request.get("node", "")).lower()
        permission = str(request.get("permission", "")).lower()
        method = str(request.get("method", "")).upper()
        source = str(request.get("source", ""))
        secure = request.get("secure") is True
        match = TOKEN_RE.fullmatch(token)
        identifier = match.group(1) if match else ""
        if not secure:
            raise TokenError("bearer tokens require HTTPS")
        if method not in {"GET", "POST"}:
            raise TokenError("request method is not allowed")
        if node not in KNOWN_NODES:
            raise TokenError("node scope is invalid")
        if permission not in PERMISSIONS:
            raise TokenError("permission is invalid")
        if not match:
            raise TokenError("token format is invalid")
        record = next((item for item in load_store()["tokens"] if item.get("id") == identifier), None)
        if not record or not hmac.compare_digest(str(record.get("secretSha256", "")), digest(match.group(2))):
            raise TokenError("token is invalid")
        if record.get("revokedAt") is not None:
            raise TokenError("token is revoked")
        if int(record.get("expiresEpoch") or 0) <= int(time.time()):
            raise TokenError("token is expired")
        if node not in record.get("nodes", []):
            raise TokenError("token is not authorized for this node")
        if permission not in record.get("permissions", []):
            raise TokenError("token lacks the required permission")
        audit("authenticate", token_id=identifier, allowed=True, node=node,
              permission=permission, source=source, method=method)
        return {"ok": True, "tokenId": identifier, "label": str(record.get("label", "")),
                "nodes": record["nodes"], "permissions": record["permissions"],
                "expiresAt": record["expiresAt"]}
    except Exception as error:
        identifier = locals().get("identifier", "")
        audit("authenticate", token_id=identifier, allowed=False, reason=str(error),
              node=locals().get("node", ""), permission=locals().get("permission", ""),
              source=locals().get("source", ""), method=locals().get("method", ""))
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    add = sub.add_parser("create")
    add.add_argument("--label", required=True)
    add.add_argument("--nodes", required=True, help="comma-separated hub IDs")
    add.add_argument("--permissions", default="status:read", help="comma-separated permissions")
    add.add_argument("--expires-in", type=int, default=86_400)
    add.set_defaults(handler=create)
    remove = sub.add_parser("revoke")
    remove.add_argument("id")
    remove.set_defaults(handler=revoke)
    listing = sub.add_parser("list")
    listing.set_defaults(handler=list_tokens)
    auth = sub.add_parser("authenticate")
    auth.set_defaults(handler=authenticate)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        if args.command != "authenticate" and os.geteuid() != 0:
            raise PermissionError("token administration requires root")
        print(json.dumps(args.handler(args), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:200]}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
