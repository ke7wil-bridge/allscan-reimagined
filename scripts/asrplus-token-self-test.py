#!/usr/bin/env python3
"""Security contract tests for scoped ASR Plus dashboard tokens."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/asrplus-token.py"


def load_module(root: Path):
    os.environ["ASRPLUS_TOKEN_STORE"] = str(root / "tokens.json")
    os.environ["ASRPLUS_TOKEN_AUDIT"] = str(root / "audit.jsonl")
    os.environ["ASRPLUS_TOKEN_LOCK"] = str(root / "token.lock")
    spec = importlib.util.spec_from_file_location("asrplus_token", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authenticate(module, request: dict) -> tuple[bool, dict]:
    payload = json.dumps(request).encode()
    stream = SimpleNamespace(buffer=SimpleNamespace(read=lambda _limit: payload))
    with patch.object(module.sys, "stdin", stream):
        try:
            return True, module.authenticate(SimpleNamespace())
        except Exception as error:
            return False, {"error": str(error)}


with tempfile.TemporaryDirectory() as directory:
    parent = Path(directory) / "shared"
    parent.mkdir(mode=0o755)
    root = parent / "tokens"
    root.mkdir(mode=0o700)
    token = load_module(root)
    assert token.KNOWN_NODES["ky2"] == "674980" and "georgia" not in token.KNOWN_NODES
    assert "ky2" in token.parse_csv("ky2") and "georgia" not in token.KNOWN_NODES
    created = token.create(SimpleNamespace(
        label="KJ5IRQ review", nodes="texas", permissions="status:read", expires_in=3600
    ))
    bearer = created["token"]
    store_text = (root / "tokens.json").read_text()
    store = json.loads(store_text)
    assert bearer not in store_text
    assert "secretSha256" in store["tokens"][0]
    assert (root / "tokens.json").stat().st_mode & 0o777 == 0o600
    assert (root.stat().st_mode & 0o777) == 0o700

    for invalid in (
        SimpleNamespace(label="Bad node", nodes="unknown", permissions="status:read", expires_in=3600),
        SimpleNamespace(label="Bad permission", nodes="texas", permissions="control:shell", expires_in=3600),
        SimpleNamespace(label="Bad expiry", nodes="texas", permissions="status:read", expires_in=299),
    ):
        try:
            token.create(invalid)
            raise AssertionError("invalid token definition was accepted")
        except token.TokenError:
            pass

    base = {"token": bearer, "node": "texas", "permission": "status:read",
            "method": "GET", "secure": True, "source": "127.0.0.1"}
    ok, claims = authenticate(token, base)
    assert ok and claims["tokenId"] == created["id"]

    ok, denied = authenticate(token, base | {"node": "kentucky"})
    assert not ok and "not authorized" in denied["error"]
    ok, denied = authenticate(token, base | {"permission": "control:connect", "method": "POST"})
    assert not ok and "lacks" in denied["error"]
    ok, denied = authenticate(token, base | {"secure": False})
    assert not ok and "HTTPS" in denied["error"]
    ok, denied = authenticate(token, base | {"token": bearer[:-1] + ("A" if bearer[-1] != "A" else "B")})
    assert not ok and "invalid" in denied["error"]

    control = token.create(SimpleNamespace(
        label="Texas control", nodes="texas",
        permissions="status:read,control:connect,control:disconnect", expires_in=3600
    ))
    ok, claims = authenticate(token, base | {"token": control["token"],
        "permission": "control:connect", "method": "POST"})
    assert ok and "control:disconnect" in claims["permissions"]

    token.revoke(SimpleNamespace(id=control["id"]))
    ok, denied = authenticate(token, base | {"token": control["token"]})
    assert not ok and "revoked" in denied["error"]

    expired = token.create(SimpleNamespace(
        label="Expiry test", nodes="texas", permissions="status:read", expires_in=300
    ))
    with patch.object(token.time, "time", return_value=int(time.time()) + 301):
        ok, denied = authenticate(token, base | {"token": expired["token"]})
    assert not ok and "expired" in denied["error"]

    audit = [json.loads(line) for line in (root / "audit.jsonl").read_text().splitlines()]
    assert any(row["allowed"] is True and row["event"] == "authenticate" for row in audit)
    assert any(row["allowed"] is False and row["reason"] for row in audit)
    assert all("token" not in row and "secret" not in row for row in audit)

api = (ROOT / "ops/asrplus/api.php").read_text()
sudoers = (ROOT / "ops/asrplus/asrplus-token.sudoers").read_text().strip()
assert "Bearer" in api and "asrplus_secure_request" in api
assert "asrplus_filter_status" in api and "control:connect" in api
assert "'ban-list', 'ban-result'" not in api
assert sudoers == "www-data ALL=(root) NOPASSWD: /usr/local/sbin/asrplus-token authenticate"
assert " create" not in sudoers and " revoke" not in sudoers
assert 'args.command != "authenticate" and os.geteuid() != 0' in SOURCE.read_text()
subprocess.run(["php", "-l", str(ROOT / "ops/asrplus/api.php")], check=True,
               stdout=subprocess.DEVNULL)

# Execute the actual PHP filter against a synthetic multi-hub payload.
functions = api[api.index("function asrplus_canonical_hub"):api.index("$method = strtoupper")]
php = functions + """
$p = asrplus_filter_status(['ok'=>true,'generatedEpoch'=>123,'hubs'=>[['id'=>'masterhub'],['id'=>'arizona-hub']],'talkers'=>['other hub'],'telemetry'=>['other hub']], 'phoenix');
if (array_keys($p) !== ['ok','generatedEpoch','hubs'] || $p['hubs'] !== [['id'=>'arizona-hub']]) exit(1);
if (asrplus_filter_status(['hubs'=>[]], 'phoenix')['ok'] !== false) exit(2);
"""
subprocess.run(["php", "-r", php], check=True)
print("ASR Plus scoped token security contract: PASS")
