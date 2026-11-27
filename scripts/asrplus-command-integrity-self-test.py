#!/usr/bin/env python3
"""End-to-end security tests for the ASR Plus remote command lifecycle."""

from __future__ import annotations

import importlib.util
import io
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
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import asrplus_command_integrity as integrity  # noqa: E402


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


endpoint = load("asrplus_remote_endpoint_test", "asrplus-remote-endpoint.py")
agent = load("asrplus_agent_test", "asrplus-agent.py")
control = load("asrplus_control_test", "asrplus-control.py")


for raw, expected in ((b"not-json", "Expecting value"), (b"{}x", "Extra data")):
    stream = SimpleNamespace(buffer=io.BytesIO(raw))
    with patch.object(endpoint.sys, "stdin", stream):
        try:
            endpoint.read_input(32)
            raise AssertionError("malformed endpoint input was accepted")
        except ValueError as error:
            assert expected in str(error)
stream = SimpleNamespace(buffer=io.BytesIO(b"x" * 34))
with patch.object(endpoint.sys, "stdin", stream):
    try:
        endpoint.read_input(32)
        raise AssertionError("oversized endpoint input was accepted")
    except ValueError as error:
        assert "too large" in str(error)


def key_file(root: Path, name: str = "command.key") -> tuple[Path, bytes]:
    path = root / name
    raw = bytes(range(32))
    path.write_text(raw.hex() + "\n", encoding="ascii")
    path.chmod(0o600)
    return path, raw


def command(hub: str = "phoenix", node: str = "641894", identifier: str = "a" * 32) -> dict:
    return {
        "id": identifier, "utc": "2026-11-23T00:00:00Z", "hub": hub,
        "localNode": node, "action": "connect", "node": "12345",
        "permanent": False, "actor": "security-test", "kind": "",
        "value": "", "duration": "", "reason": "",
    }


def central_fixture(root: Path, mode: str = "required") -> tuple[Path, bytes]:
    key_path, key = key_file(root)
    config = root / "integrity.json"
    config.write_text(json.dumps({"hubs": {"phoenix": {
        "mode": mode, "keyId": "phoenix-test-1", "keyFile": str(key_path),
    }}}))
    endpoint.QUEUE_ROOT = root / "queue"
    endpoint.RESULT_ROOT = root / "results"
    endpoint.ARCHIVE_ROOT = root / "archive"
    endpoint.INTEGRITY_CONFIG = config
    endpoint.SECURITY_AUDIT_PATH = root / "security-audit.jsonl"
    return config, key


# Key loading fails closed for missing, symbolic-link, short, and group-readable
# material. These checks cover both central and remote-agent configuration.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    valid, raw = key_file(root)
    assert integrity.load_key(valid) == raw
    insecure = root / "insecure.key"
    insecure.write_text(raw.hex() + "\n", encoding="ascii")
    insecure.chmod(0o640)
    short = root / "short.key"
    short.write_bytes(b"too-short")
    short.chmod(0o600)
    link = root / "linked.key"
    link.symlink_to(valid)
    for candidate, expected in (
        (root / "missing.key", "unavailable"),
        (insecure, "owner-only"),
        (short, "256 bits"),
        (link, "symbolic link"),
    ):
        try:
            integrity.load_key(candidate)
            raise AssertionError(f"unsafe command key accepted: {candidate.name}")
        except integrity.IntegrityError as error:
            assert expected in str(error)


# Cryptographic envelope: authorized command and acknowledgment succeed.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    path, key = key_file(root)
    signed = integrity.sign_command(command(), key, "phoenix-test-1", now=1000, ttl=120)
    digest = integrity.verify_command(
        signed, key, "phoenix-test-1", "phoenix", "641894", now=1001)
    assert digest == signed["commandDigest"]
    ack = integrity.sign_acknowledgment(
        {"ok": True, "exitCode": 0, "message": "accepted"}, signed, key,
        "phoenix-test-1", now=1002)
    integrity.verify_acknowledgment(ack, signed, key, "phoenix-test-1", "phoenix", now=1003)

    for mutated, expected in (
        (signed | {"action": "disconnect"}, "digest"),
        (signed | {"hub": "georgia"}, "hub"),
        (signed | {"signature": "0" * 64}, "signature"),
    ):
        try:
            integrity.verify_command(mutated, key, "phoenix-test-1", "phoenix", "641894", now=1001)
            raise AssertionError("forged command was accepted")
        except integrity.IntegrityError as error:
            assert expected in str(error)
    try:
        integrity.verify_command(signed, key, "phoenix-test-1", "phoenix", "641894", now=1121)
        raise AssertionError("expired command was accepted")
    except integrity.IntegrityError as error:
        assert "expired" in str(error)
    forged_ack = ack | {"message": "forged"}
    try:
        integrity.verify_acknowledgment(
            forged_ack, signed, key, "phoenix-test-1", "phoenix", now=1003)
        raise AssertionError("forged acknowledgment was accepted")
    except integrity.IntegrityError as error:
        assert "signature" in str(error)
    try:
        integrity.verify_acknowledgment(
            ack | {"commandDigest": "0" * 64}, signed, key,
            "phoenix-test-1", "phoenix", now=1003)
        raise AssertionError("acknowledgment for the wrong command digest was accepted")
    except integrity.IntegrityError as error:
        assert "digest" in str(error)
    try:
        integrity.verify_command(
            signed, bytes(reversed(key)), "phoenix-test-1", "phoenix", "641894", now=1001)
        raise AssertionError("command signed by the wrong key was accepted")
    except integrity.IntegrityError as error:
        assert "signature" in str(error)

# Queue lease, outage recovery, signed acknowledgment, and duplicate handling.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    signed = integrity.sign_command(command(), key, "phoenix-test-1", now=now, ttl=120)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{signed['id']}.json", signed, 0o600)
    first = endpoint.poll("phoenix")
    assert first["command"]["id"] == signed["id"] and first["recovered"] is False
    assert endpoint.poll("phoenix")["inflight"] is True
    inflight = queue / f"{signed['id']}.inflight"
    os.utime(inflight, (now - endpoint.LEASE_SECONDS - 1,) * 2)
    recovered = endpoint.poll("phoenix")
    assert recovered["command"]["commandDigest"] == signed["commandDigest"]
    ack = integrity.sign_acknowledgment(
        {"ok": True, "exitCode": 0, "message": "accepted"}, signed, key,
        "phoenix-test-1", now=now + 1)
    assert endpoint.acknowledge("phoenix", signed["id"], ack) == {"ok": True}
    assert endpoint.acknowledge("phoenix", signed["id"], ack)["duplicate"] is True
    conflicting = integrity.sign_acknowledgment(
        {"ok": False, "exitCode": 1, "message": "changed"}, signed, key,
        "phoenix-test-1", now=now + 2)
    try:
        endpoint.acknowledge("phoenix", signed["id"], conflicting)
        raise AssertionError("conflicting duplicate acknowledgment was accepted")
    except integrity.IntegrityError as error:
        assert "conflicting" in str(error)

# Tampered and stale queue entries fail closed and are archived, not executed.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    signed = integrity.sign_command(command(), key, "phoenix-test-1", now=now, ttl=120)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{signed['id']}.json", signed | {"node": "99999"}, 0o600)
    assert endpoint.poll("phoenix")["command"] is None
    assert list((endpoint.ARCHIVE_ROOT / "phoenix").glob("*.invalid-queued.json"))

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    expired = integrity.sign_command(command(), key, "phoenix-test-1", now=now - 200, ttl=100)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{expired['id']}.json", expired, 0o600)
    assert endpoint.poll("phoenix")["command"] is None
    result = json.loads((endpoint.RESULT_ROOT / "phoenix" / f"{expired['id']}.json").read_text())
    assert result["remoteResult"]["exitCode"] == 124

# Expired inflight commands are archived instead of being replayed.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    signed = integrity.sign_command(command(identifier="d" * 32), key,
                                    "phoenix-test-1", now=now - 200, ttl=100)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{signed['id']}.inflight", signed, 0o600)
    assert endpoint.poll("phoenix")["command"] is None
    assert not (queue / f"{signed['id']}.inflight").exists()
    result = json.loads((endpoint.RESULT_ROOT / "phoenix" / f"{signed['id']}.json").read_text())
    assert result["remoteResult"]["exitCode"] == 124

# Unexpired inflight lease prevents a second queued command from executing.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    first = integrity.sign_command(command(identifier="e" * 32), key, "phoenix-test-1", now=now)
    second = integrity.sign_command(command(identifier="f" * 32), key, "phoenix-test-1", now=now)
    endpoint.atomic_json(queue / f"{first['id']}.inflight", first, 0o600)
    endpoint.atomic_json(queue / f"{second['id']}.json", second, 0o600)
    assert endpoint.poll("phoenix")["inflight"] is True
    assert (queue / f"{second['id']}.json").exists()

# A malformed queued file is quarantined without blocking valid work.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    (queue / ("1" * 32 + ".json")).write_text("{not-json", encoding="utf-8")
    valid = integrity.sign_command(command(identifier="2" * 32), key,
                                   "phoenix-test-1", now=now)
    endpoint.atomic_json(queue / f"{valid['id']}.json", valid, 0o600)
    assert endpoint.poll("phoenix")["command"]["id"] == valid["id"]
    assert list((endpoint.ARCHIVE_ROOT / "phoenix").glob("*.invalid-queued.json"))

# An invalid inflight command is quarantined, allowing the next valid command.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    _, key = central_fixture(root)
    now = int(time.time())
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    bad = integrity.sign_command(command(identifier="3" * 32), key,
                                 "phoenix-test-1", now=now)
    endpoint.atomic_json(queue / f"{bad['id']}.inflight", bad | {"node": "99999"}, 0o600)
    valid = integrity.sign_command(command(identifier="4" * 32), key,
                                   "phoenix-test-1", now=now)
    endpoint.atomic_json(queue / f"{valid['id']}.json", valid, 0o600)
    assert endpoint.poll("phoenix")["command"]["id"] == valid["id"]
    assert list((endpoint.ARCHIVE_ROOT / "phoenix").glob("*.invalid-inflight.json"))

# Optional mode drains a bounded legacy queue; required mode rejects it.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    central_fixture(root, "optional")
    legacy = command(identifier="b" * 32)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{legacy['id']}.json", legacy, 0o600)
    assert endpoint.poll("phoenix")["command"]["id"] == legacy["id"]

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    central_fixture(root, "required")
    legacy = command(identifier="c" * 32)
    queue = endpoint.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    endpoint.atomic_json(queue / f"{legacy['id']}.json", legacy, 0o600)
    assert endpoint.poll("phoenix")["command"] is None
    assert list((endpoint.ARCHIVE_ROOT / "phoenix").glob("*.invalid-queued.json"))

# Agent ledger executes once and resends the exact signed acknowledgment.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    key_path, key = key_file(root)
    config = {"hub": "phoenix", "node": "641894",
              "commandLedger": str(root / "ledger" / "commands.json"),
              "commandIntegrity": {"mode": "required", "keyId": "phoenix-test-1",
                                   "keyFile": str(key_path)}}
    signed = integrity.sign_command(command(), key, "phoenix-test-1", ttl=120)
    calls = []
    completed = subprocess.CompletedProcess([], 0, "accepted", "")
    with patch.object(agent.subprocess, "run", side_effect=lambda *args, **kwargs: calls.append(args[0]) or completed):
        first = agent.execute_once(config, signed)
        second = agent.execute_once(config, signed)
    assert first == second and len(calls) == 1
    assert first["ackSignature"] and first["commandDigest"] == signed["commandDigest"]
    replay = signed | {"node": "54321"}
    try:
        agent.execute_once(config, replay)
        raise AssertionError("modified replay was accepted")
    except integrity.IntegrityError:
        pass
    invalid_action = integrity.sign_command(
        command(identifier="e" * 32) | {"action": "shell"}, key, "phoenix-test-1", ttl=120)
    with patch.object(agent.subprocess, "run") as run_mock:
        rejected = agent.execute_once(config, invalid_action)
    run_mock.assert_not_called()
    assert rejected["ok"] is False and rejected["exitCode"] == 2

# An interrupted execution is never repeated; the signed outcome is explicitly uncertain.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    key_path, key = key_file(root)
    ledger_path = root / "ledger" / "commands.json"
    config = {"hub": "phoenix", "node": "641894", "commandLedger": str(ledger_path),
              "commandIntegrity": {"mode": "required", "keyId": "phoenix-test-1",
                                   "keyFile": str(key_path)}}
    signed = integrity.sign_command(command(), key, "phoenix-test-1", ttl=120)
    ledger_path.parent.mkdir(parents=True)
    agent.atomic_json(ledger_path, {"version": 1, "commands": {signed["id"]: {
        "digest": signed["commandDigest"], "state": "executing", "updatedEpoch": int(time.time()),
    }}}, 0o600)
    with patch.object(agent.subprocess, "run") as run_mock:
        uncertain = agent.execute_once(config, signed)
    run_mock.assert_not_called()
    assert uncertain["ok"] is False and "uncertain" in uncertain["message"]

# Optional agent mode preserves bounded legacy compatibility and still deduplicates locally.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    key_path, _ = key_file(root)
    config = {"hub": "phoenix", "node": "641894",
              "commandLedger": str(root / "ledger" / "commands.json"),
              "commandIntegrity": {"mode": "optional", "keyId": "phoenix-test-1",
                                   "keyFile": str(key_path)}}
    legacy = command(identifier="d" * 32)
    calls = []
    completed = subprocess.CompletedProcess([], 0, "accepted", "")
    with patch.object(agent.subprocess, "run", side_effect=lambda *args, **kwargs: calls.append(args[0]) or completed):
        first = agent.execute_once(config, legacy)
        second = agent.execute_once(config, legacy)
    assert first == second and len(calls) == 1 and "ackSignature" not in first

# Kentucky connection failure never reaches execution and strict host-key options remain mandatory.
ssh_source = (SCRIPTS / "asrplus-agent.py").read_text()
assert '"StrictHostKeyChecking=yes"' in ssh_source
assert "UserKnownHostsFile=" in ssh_source and '"BatchMode=yes"' in ssh_source
assert '"ClearAllForwardings=yes"' in ssh_source and '"RequestTTY=no"' in ssh_source
failure = subprocess.CompletedProcess([], 255, "", "connection failed")
with patch.object(agent, "stamped_status", return_value={"hub": "phoenix"}), \
        patch.object(agent, "run_ssh", return_value=failure), \
        patch.object(agent, "execute_once") as execute_mock:
    try:
        agent.sync_once({"hub": "phoenix", "node": "641894"})
        raise AssertionError("Kentucky outage did not fail closed")
    except SystemExit as error:
        assert "status push failed" in str(error)
    execute_mock.assert_not_called()

# Queue saturation is rejected before a command can be signed or executed.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config_path, key = central_fixture(root)
    control.QUEUE_ROOT = root / "control-queue"
    control.AUDIT_PATH = root / "control-audit.jsonl"
    control.INTEGRITY_CONFIG = config_path
    request = {"hub": "phoenix", "action": "connect", "node": "12345", "actor": "test"}
    output = io.StringIO()
    with patch.object(control.sys, "stdin", io.StringIO(json.dumps(request))), \
            patch.object(control.sys, "stdout", output):
        assert control.main() == 0
    response = json.loads(output.getvalue())
    queued = json.loads((control.QUEUE_ROOT / "phoenix" / f"{response['id']}.json").read_text())
    integrity.verify_command(queued, key, "phoenix-test-1", "phoenix", "641894")

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config_path, _ = central_fixture(root)
    control.QUEUE_ROOT = root / "queue"
    control.AUDIT_PATH = root / "audit.jsonl"
    control.INTEGRITY_CONFIG = config_path
    queue = control.QUEUE_ROOT / "phoenix"
    queue.mkdir(parents=True)
    for index in range(control.MAX_QUEUE_DEPTH):
        (queue / f"{index:032x}.json").write_text("{}")
    request = {"hub": "phoenix", "action": "connect", "node": "12345", "actor": "test"}
    output = io.StringIO()
    with patch.object(control.sys, "stdin", io.StringIO(json.dumps(request))), \
            patch.object(control.sys, "stdout", output):
        assert control.main() == 1
    assert "queue is full" in json.loads(output.getvalue())["error"]

# Existing token/API negative authorization coverage remains part of the gate.
subprocess.run([sys.executable, str(SCRIPTS / "asrplus-token-self-test.py")], check=True)

# Talker transport relays ASR's authoritative payload without deriving identity.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    cache = root / "astapi.json"
    authoritative = {"node": "54340", "info": "W1ABC", "duration": 5}
    cache.write_text(json.dumps({"updated": time.time(), "current": {"641894": {
        "current_talker": {}, "last_talkers": [authoritative]}}}))
    talker_config = {"node": "641894", "authoritativeTalkerCache": str(cache),
                     "talkerStatus": str(root / "talkers.json"),
                     "talkerEvent": str(root / "talker-event")}
    with patch.object(agent.time, "sleep", side_effect=RuntimeError("one pass")):
        try:
            agent.watch_talkers(talker_config)
        except RuntimeError as error:
            assert str(error) == "one pass"
    telemetry = json.loads((root / "talkers.json").read_text())
    assert telemetry["current_talker"] == {}
    assert telemetry["last_talkers"] == [authoritative]
    assert telemetry["talker_authority"] == "asr"
    assert (root / "talker-event").is_file()

status = endpoint.sanitize_status("phoenix", {
    "hub": "phoenix", "node": "641894", "generated_epoch": int(time.time()),
    "connections": [], "bridges": {}, "current_talker": {}, "last_talkers": [],
})
assert status["connection_status"]["nodes"] == []

# Deployment-fixture telemetry ingestion: enforce identity and freshness,
# and never allow delayed snapshots or sequence rollback to overwrite newer state.
def status_payload(generated: int, sequence: int = 1, hub: str = "phoenix",
                   node: str = "641894") -> dict:
    return {
        "hub": hub, "node": node, "generated_epoch": generated,
        "talker_sequence": sequence, "watcher_instance": "fixture-watcher",
        "connections": [], "bridges": {}, "current_talker": {}, "last_talkers": [],
    }

now = int(time.time())
for bad_payload, expected in (
    (status_payload(now, hub="texas", node="674984"), "identity mismatch"),
    (status_payload(now, node="674984"), "identity mismatch"),
    (status_payload(now - 121), "timestamp invalid"),
    (status_payload(now + 121), "timestamp invalid"),
):
    try:
        endpoint.sanitize_status("phoenix", bad_payload)
        raise AssertionError("invalid telemetry identity/freshness was accepted")
    except ValueError as error:
        assert expected in str(error)

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    endpoint.STATUS_ROOT = root / "status"
    endpoint.STATUS_EVENT_PATH = root / "status-event"
    with patch.object(endpoint.subprocess, "run") as publish_mock:
        assert endpoint.store_status("phoenix", status_payload(now, sequence=10)) is True
        assert endpoint.STATUS_EVENT_PATH.is_file()
        assert endpoint.store_status("phoenix", status_payload(now - 1, sequence=99)) is False
        assert endpoint.store_status("phoenix", status_payload(now, sequence=9)) is False
        assert endpoint.store_status("phoenix", status_payload(now + 1, sequence=11)) is True
    stored = json.loads((endpoint.STATUS_ROOT / "phoenix.json").read_text())
    assert stored["generated_epoch"] == now + 1
    assert stored["talker_sequence"] == 11

print("ASR Plus command-integrity security lifecycle: PASS")
