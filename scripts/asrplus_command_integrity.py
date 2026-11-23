#!/usr/bin/env python3
"""Cryptographic command and acknowledgment envelope for ASR Plus agents."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any


PROTOCOL_VERSION = 1
DEFAULT_TTL_SECONDS = 120
MAX_TTL_SECONDS = 300
MAX_CLOCK_SKEW_SECONDS = 30
COMMAND_ID = re.compile(r"^[a-f0-9]{32}$")
NONCE = re.compile(r"^[a-f0-9]{32}$")
KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HUB = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
NODE = re.compile(r"^[0-9]{3,10}$")


class IntegrityError(ValueError):
    """A command, acknowledgment, key, or integrity configuration is invalid."""


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                      sort_keys=True).encode("utf-8")


def _unsigned_command(command: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in command.items()
            if key not in {"commandDigest", "signature"}}


def command_digest(command: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(_unsigned_command(command))).hexdigest()


def _command_mac(command: dict[str, Any], key: bytes) -> str:
    body = canonical_json(_unsigned_command(command))
    return hmac.new(key, b"asrplus-command-v1\0" + body, hashlib.sha256).hexdigest()


def _ack_mac(acknowledgment: dict[str, Any], key: bytes) -> str:
    body = canonical_json({key_name: value for key_name, value in acknowledgment.items()
                           if key_name != "ackSignature"})
    return hmac.new(key, b"asrplus-ack-v1\0" + body, hashlib.sha256).hexdigest()


def load_key(path_value: str | Path) -> bytes:
    path = Path(path_value)
    if path.is_symlink():
        raise IntegrityError("command key must not be a symbolic link")
    try:
        info = path.stat()
        raw = path.read_bytes().strip()
    except OSError as error:
        raise IntegrityError("command key is unavailable") from error
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise IntegrityError("command key permissions must be owner-only")
    if re.fullmatch(rb"[a-fA-F0-9]{64}", raw):
        raw = bytes.fromhex(raw.decode("ascii"))
    if len(raw) < 32:
        raise IntegrityError("command key must contain at least 256 bits")
    return raw


def load_central_hub(config_path: str | Path, hub: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        definition = payload["hubs"][hub]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise IntegrityError("hub integrity configuration is unavailable") from error
    if not isinstance(definition, dict):
        raise IntegrityError("hub integrity configuration is invalid")
    key_id = str(definition.get("keyId", ""))
    if not KEY_ID.fullmatch(key_id):
        raise IntegrityError("hub command key ID is invalid")
    mode = str(definition.get("mode", "required"))
    if mode not in {"optional", "required"}:
        raise IntegrityError("hub integrity mode is invalid")
    return {"key": load_key(str(definition.get("keyFile", ""))),
            "keyId": key_id, "mode": mode}


def load_agent_config(config: dict[str, Any]) -> dict[str, Any]:
    definition = config.get("commandIntegrity")
    if not isinstance(definition, dict):
        raise IntegrityError("agent command integrity configuration is unavailable")
    key_id = str(definition.get("keyId", ""))
    mode = str(definition.get("mode", "required"))
    if not KEY_ID.fullmatch(key_id) or mode not in {"optional", "required"}:
        raise IntegrityError("agent command integrity configuration is invalid")
    return {"key": load_key(str(definition.get("keyFile", ""))),
            "keyId": key_id, "mode": mode}


def sign_command(command: dict[str, Any], key: bytes, key_id: str, *,
                 now: int | None = None, ttl: int = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    issued = int(time.time()) if now is None else int(now)
    if not KEY_ID.fullmatch(key_id):
        raise IntegrityError("command key ID is invalid")
    if ttl < 1 or ttl > MAX_TTL_SECONDS:
        raise IntegrityError("command lifetime is invalid")
    signed = dict(command)
    signed.update({
        "protocolVersion": PROTOCOL_VERSION,
        "issuedEpoch": issued,
        "expiresEpoch": issued + ttl,
        "nonce": secrets.token_hex(16),
        "keyId": key_id,
    })
    signed.pop("commandDigest", None)
    signed.pop("signature", None)
    signed["commandDigest"] = command_digest(signed)
    signed["signature"] = _command_mac(signed, key)
    return signed


def verify_command(command: dict[str, Any], key: bytes, key_id: str, hub: str,
                   local_node: str, *, now: int | None = None,
                   allow_expired: bool = False) -> str:
    current = int(time.time()) if now is None else int(now)
    if not isinstance(command, dict):
        raise IntegrityError("command must be an object")
    if command.get("protocolVersion") != PROTOCOL_VERSION:
        raise IntegrityError("command protocol version is invalid")
    if not COMMAND_ID.fullmatch(str(command.get("id", ""))):
        raise IntegrityError("command ID is invalid")
    if str(command.get("hub", "")) != hub or str(command.get("localNode", "")) != local_node:
        raise IntegrityError("command hub identity mismatch")
    if not HUB.fullmatch(hub) or not NODE.fullmatch(local_node):
        raise IntegrityError("configured hub identity is invalid")
    if str(command.get("keyId", "")) != key_id:
        raise IntegrityError("command key ID mismatch")
    if not NONCE.fullmatch(str(command.get("nonce", ""))):
        raise IntegrityError("command nonce is invalid")
    issued = int(command.get("issuedEpoch") or 0)
    expires = int(command.get("expiresEpoch") or 0)
    if issued <= 0 or expires <= issued or expires - issued > MAX_TTL_SECONDS:
        raise IntegrityError("command validity window is invalid")
    if issued > current + MAX_CLOCK_SKEW_SECONDS:
        raise IntegrityError("command issue time is in the future")
    expected_digest = command_digest(command)
    if not hmac.compare_digest(str(command.get("commandDigest", "")), expected_digest):
        raise IntegrityError("command digest is invalid")
    if not hmac.compare_digest(str(command.get("signature", "")), _command_mac(command, key)):
        raise IntegrityError("command signature is invalid")
    if expires < current and not allow_expired:
        raise IntegrityError("command is expired")
    return expected_digest


def legacy_digest(command: dict[str, Any]) -> str:
    return hashlib.sha256(b"asrplus-legacy-v1\0" + canonical_json(command)).hexdigest()


def sign_acknowledgment(result: dict[str, Any], command: dict[str, Any], key: bytes,
                        key_id: str, *, now: int | None = None) -> dict[str, Any]:
    completed = int(time.time()) if now is None else int(now)
    acknowledgment = dict(result)
    acknowledgment.update({
        "protocolVersion": PROTOCOL_VERSION,
        "commandId": str(command.get("id", "")),
        "commandDigest": str(command.get("commandDigest", "")),
        "hub": str(command.get("hub", "")),
        "completedEpoch": completed,
        "ackNonce": secrets.token_hex(16),
        "keyId": key_id,
    })
    acknowledgment["ackSignature"] = _ack_mac(acknowledgment, key)
    return acknowledgment


def verify_acknowledgment(acknowledgment: dict[str, Any], command: dict[str, Any],
                          key: bytes, key_id: str, hub: str, *,
                          now: int | None = None) -> str:
    current = int(time.time()) if now is None else int(now)
    if not isinstance(acknowledgment, dict) or acknowledgment.get("protocolVersion") != PROTOCOL_VERSION:
        raise IntegrityError("acknowledgment protocol version is invalid")
    if str(acknowledgment.get("commandId", "")) != str(command.get("id", "")):
        raise IntegrityError("acknowledgment command ID mismatch")
    if str(acknowledgment.get("commandDigest", "")) != str(command.get("commandDigest", "")):
        raise IntegrityError("acknowledgment command digest mismatch")
    if str(acknowledgment.get("hub", "")) != hub or str(command.get("hub", "")) != hub:
        raise IntegrityError("acknowledgment hub identity mismatch")
    if str(acknowledgment.get("keyId", "")) != key_id:
        raise IntegrityError("acknowledgment key ID mismatch")
    if not NONCE.fullmatch(str(acknowledgment.get("ackNonce", ""))):
        raise IntegrityError("acknowledgment nonce is invalid")
    completed = int(acknowledgment.get("completedEpoch") or 0)
    issued = int(command.get("issuedEpoch") or 0)
    if completed < issued - MAX_CLOCK_SKEW_SECONDS or completed > current + MAX_CLOCK_SKEW_SECONDS:
        raise IntegrityError("acknowledgment timestamp is invalid")
    if completed > int(command.get("expiresEpoch") or 0) + MAX_CLOCK_SKEW_SECONDS:
        raise IntegrityError("acknowledgment is outside the command validity window")
    if not hmac.compare_digest(str(acknowledgment.get("ackSignature", "")),
                               _ack_mac(acknowledgment, key)):
        raise IntegrityError("acknowledgment signature is invalid")
    return hashlib.sha256(canonical_json({key_name: value for key_name, value in acknowledgment.items()
                                         if key_name != "ackSignature"})).hexdigest()


def generate_key() -> str:
    """Return a 256-bit hex key for an operator to store directly in a 0600 file."""
    return secrets.token_hex(32)


__all__ = [
    "DEFAULT_TTL_SECONDS", "IntegrityError", "legacy_digest", "load_agent_config",
    "load_central_hub", "load_key", "sign_acknowledgment", "sign_command",
    "verify_acknowledgment", "verify_command",
]
