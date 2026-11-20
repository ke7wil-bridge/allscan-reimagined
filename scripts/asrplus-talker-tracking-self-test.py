#!/usr/bin/env python3
"""Isolated ASR Plus talker/connection simulations; never issues radio commands."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


status = load("asrplus_status", ROOT / "scripts/asrplus-status.py")
agent = load("asrplus_agent", ROOT / "scripts/asrplus-agent.py")
endpoint = load("asrplus_endpoint", ROOT / "scripts/asrplus-remote-endpoint.py")


def talker(node: str, started: int, ended: int = 0, current: bool = True, local: bool = False) -> dict:
    return {"node": node, "callsign": f"CALL{node[-2:]}", "description": "Test station",
        "location": "Test Location", "source": "AllStar", "duration": "00:05",
        "eventEpoch": ended or started, "startedEpoch": started, "current": current,
        "originatedLocally": local}


def hub(hub_id: str, node: str, rows: list[dict], stale: bool = False) -> dict:
    return {"id": hub_id, "name": hub_id.title(), "location": hub_id.title(), "node": node,
        "stale": stale, "talkers": rows}


def aggregate(hubs: list[dict]) -> list[dict]:
    return status.aggregate_talkers(hubs, 1_700_000_100)


HUBS = [("kentucky", "674982"), ("phoenix", "641894"), ("georgia", "674980")]

# 1-3: a station originating through each hub is selected and attributed to that hub.
for index, (hub_id, hub_node) in enumerate(HUBS):
    rows = [hub(other_id, other_node, [talker("54340", 100 + index)] if other_id == hub_id else [])
        for other_id, other_node in HUBS]
    result = aggregate(rows)
    assert result[0]["node"] == "54340" and result[0]["hubId"] == hub_id

# 4-5: relay hubs are never named when the edge hub reports the actual station.
for edge_id, edge_node in HUBS[1:]:
    result = aggregate([
        hub("kentucky", "674982", [talker(edge_node, 200)]),
        hub(edge_id, edge_node, [talker("54340", 200)]),
        hub("georgia" if edge_id == "phoenix" else "phoenix", "674980" if edge_id == "phoenix" else "641894", []),
    ])
    assert result[0]["node"] == "54340" and result[0]["callsign"] != edge_node

# The confirmed Phoenix topology has one origin and two relays, all tied to the
# same actual station identity.
phoenix_case = [
    hub("kentucky", "674982", [talker("641894", 250)]),
    hub("phoenix", "641894", [talker("53430", 250)]),
    hub("georgia", "674980", [talker("674982", 250)]),
]
result = aggregate(phoenix_case)
assert result[0]["node"] == "53430" and result[0]["hubId"] == "phoenix"
assert [item["txState"] for item in phoenix_case] == ["relay", "origin", "relay"]
assert phoenix_case[0]["txLabel"] == "RELAY · Phoenix (641894)"
assert phoenix_case[1]["txLabel"] == "TX · CALL30 / 53430"

# 6: a hub may be shown only with explicit local-origin evidence.
result = aggregate([hub("phoenix", "641894", [talker("641894", 300, local=True)])])
assert result[0]["node"] == "641894"

# 7: unkeying moves an event to history and clears current promptly.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    astdb = root / "astdb.txt"
    astdb.write_text("641894|KE7WIL|Phoenix Hub|Phoenix, Arizona\n54340|W1ABC|Test Station|Mesa, Arizona\n")
    config = {"node": "641894", "astdb": str(astdb), "talkerStatus": str(root / "talkers.json"),
        "talkerEvent": str(root / "talker-event")}
    clock = iter((400, 405))
    original_time = agent.time.time
    agent.time.time = lambda: next(clock)
    try:
        state = agent.TalkerState(config)
        state.update(False, {"54340": {"keyed": True}})
        state.update(False, {"54340": {"keyed": False}})
    finally:
        agent.time.time = original_time
    payload = json.loads((root / "talkers.json").read_text())
    assert payload["current_talker"] == {} and payload["last_talkers"][0]["duration_seconds"] == 5
    assert payload["talker_sequence"] == 2 and payload["watcher_detected_epoch_ms"] > 0
    assert (root / "talker-event").is_file()
    state = agent.TalkerState(config)
    local = state.entry("local-rf", 410)
    assert local["node"] == "641894" and local["callsign"] == "KE7WIL"
assert agent.parse_alinks("0") == {}

# Required short-duration coverage. A sub-second event is retained with a zero
# integer-second duration; longer events retain their exact durations.
for offset, duration in enumerate((0, 1, 2, 3, 10)):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = {"node": "641894", "astdb": str(root / "missing"),
            "talkerStatus": str(root / "talkers.json"), "talkerEvent": str(root / "event")}
        start = 2000.1 + offset * 20
        end = start + (0.5 if duration == 0 else duration)
        values = iter((start, end))
        original_time = agent.time.time
        agent.time.time = lambda: next(values)
        try:
            state = agent.TalkerState(config)
            state.update(False, {"53430": {"keyed": True}})
            state.update(False, {"53430": {"keyed": False}})
        finally:
            agent.time.time = original_time
        recent = json.loads((root / "talkers.json").read_text())["last_talkers"][0]
        assert recent["node"] == "53430" and recent["duration_seconds"] == duration

# A watcher restart preserves history/sequence and republishes authoritative
# idle state; a watcher failure publishes an immediate uncertain/cleared state.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config = {"node": "641894", "astdb": str(root / "missing"),
        "talkerStatus": str(root / "talkers.json"), "talkerEvent": str(root / "event")}
    original_time = agent.time.time
    agent.time.time = lambda: 3000
    try:
        first = agent.TalkerState(config)
        first.update(False, {"53430": {"keyed": True}})
        prior_sequence = first.sequence
        restarted = agent.TalkerState(config)
        assert restarted.sequence == prior_sequence
        restarted.update(False, {})
        restarted.failed(RuntimeError("simulated disconnect"))
    finally:
        agent.time.time = original_time
    payload = json.loads((root / "talkers.json").read_text())
    assert payload["healthy"] is False and payload["current_talker"] == {}
    assert payload["talker_sequence"] > prior_sequence

# Duplicate events are idempotent; rapid and sub-second transitions are still
# retained as distinct history records even when their integer duration is zero.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config = {"node": "641894", "astdb": str(root / "missing"),
        "talkerStatus": str(root / "talkers.json"), "talkerEvent": str(root / "event")}
    values = iter((1000.0, 1000.1, 1000.5, 1000.6, 1001.0))
    original_time = agent.time.time
    agent.time.time = lambda: next(values)
    try:
        state = agent.TalkerState(config)
        state.update(False, {"53430": {"keyed": True}})
        sequence = state.sequence
        state.update(False, {"53430": {"keyed": True}})  # duplicate key
        assert state.sequence == sequence
        state.update(False, {"53430": {"keyed": False}})
        state.update(False, {"53430": {"keyed": False}})  # duplicate unkey
        state.update(False, {"53430": {"keyed": True}})
    finally:
        agent.time.time = original_time
    payload = json.loads((root / "talkers.json").read_text())
    assert payload["current_talker"]["node"] == "53430"
    assert payload["last_talkers"][0]["node"] == "53430"

# 8: matching observations deduplicate, but distinct transmissions remain distinct.
result = aggregate([
    hub("kentucky", "674982", [talker("54340", 500, 510, False), talker("54340", 520, 530, False)]),
    hub("phoenix", "641894", [talker("54340", 501, 511, False)]),
])
assert len(result) == 2 and [row["eventEpoch"] for row in result] == [530, 511]
rapid = aggregate([hub("kentucky", "674982", [
    talker("54340", 540, 541, False), talker("54340", 542, 543, False)])])
assert len(rapid) == 2

# 9: stale hub telemetry cannot create current or recent cards.
stale_hub = hub("phoenix", "641894", [talker("54340", 600)], stale=True)
assert aggregate([stale_hub]) == [] and stale_hub["txState"] == "idle"
with tempfile.TemporaryDirectory() as directory:
    status_path = Path(directory) / "status.json"
    status_path.write_text(json.dumps({"generated_epoch": 100, "connection_status": {
        "nodes": [], "current_talker": {"node": "54340", "keyed_started_epoch": 95}}}))
    built = status.build_hub({"id": "phoenix", "name": "Phoenix", "node": "641894",
        "stateKey": "phoenix", "statusFile": str(status_path), "staleAfterSeconds": 180},
        {"phoenix_ready": True, "phoenix_age_seconds": 0}, {}, {}, 120)
    assert built["stale"] is False and not any(row["current"] for row in built["talkers"])

# Kentucky reads its existing event-driven watcher file directly, overriding a
# slower public snapshot for talker/key state while retaining connection data.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    public_path, live_path = root / "public.json", root / "live.json"
    public_path.write_text(json.dumps({"generated_epoch": 118, "connection_status": {
        "nodes": [{"node": "641894", "elapsed": "00:29:19:331", "keyed": "no"}],
        "current_talker": {"node": "stale", "keyed_started_epoch": 100}, "last_talkers": []}}))
    live_path.write_text(json.dumps({"healthy": True, "updated_epoch": 120, "fresh_for_seconds": 15,
        "current_talker": {"node": "641894", "keyed_started_epoch": 119},
        "node_states": {"641894": {"keyed": "yes"}}, "history": []}))
    definition = {"id": "kentucky", "name": "Kentucky", "node": "674982", "stateKey": "kentucky",
        "statusFile": str(public_path), "talkerStatusFile": str(live_path)}
    built = status.build_hub(definition, {"kentucky_ready": True}, {}, {}, 120)
    assert built["talkers"][0]["node"] == "641894" and built["connections"][0]["keyed"] is True
    assert built["connections"][0]["connected"] == "00:29:19"
    live_path.write_text(json.dumps({"healthy": True, "updated_epoch": 121, "fresh_for_seconds": 15,
        "current_talker": {}, "node_states": {"641894": {"keyed": "no"}}, "history": []}))
    cleared = status.build_hub(definition, {"kentucky_ready": True}, {}, {}, 121)
    assert not any(row["current"] for row in cleared["talkers"])

# Freshness boundary: remote telemetry may retain historical events but must
# not keep displaying an expired active TX after its talker TTL.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    remote = root / "remote.json"
    remote.write_text(json.dumps({"generated_epoch": 100, "connection_status": {
        "nodes": [], "current_talker": {"node": "53430", "keyed_started_epoch": 99},
        "last_talkers": [{"node": "12345", "keyed_started_epoch": 90,
                          "event_epoch": 95, "duration_seconds": 5}]}}))
    definition = {"id": "phoenix", "name": "Phoenix", "node": "641894",
                  "stateKey": "phoenix", "statusFile": str(remote),
                  "talkerStaleAfterSeconds": 15, "staleAfterSeconds": 180}
    fresh = status.build_hub(definition, {"phoenix_ready": True}, {}, {}, 115)
    expired = status.build_hub(definition, {"phoenix_ready": True}, {}, {}, 116)
    assert any(row["current"] for row in fresh["talkers"])
    assert not any(row["current"] for row in expired["talkers"])
    assert any(not row["current"] for row in expired["talkers"])

# 10: a fresh talker file is used after reconnect; an expired one is not.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    talker_path = root / "talkers.json"
    public_path = root / "missing.json"
    config = {"hub": "georgia", "node": "674980", "astdb": str(root / "astdb.txt"),
        "publicStatus": str(public_path), "talkerStatus": str(talker_path)}
    now = int(agent.time.time())
    talker_path.write_text(json.dumps({"healthy": True, "updated_epoch": now,
        "current_talker": {"node": "54340"}, "last_talkers": []}))
    output = "NODE PEER RECONNECTS DIRECTION CONNECT TIME CONNECT STATE\n----\n674982 44.30.157.21 1 IN 00:01:02 ESTABLISHED\n54340 1 IN 00:00:10 ESTABLISHED\n"
    original_run = agent.subprocess.run
    agent.subprocess.run = lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, output, "")
    try:
        fresh = agent.collect(config)
        talker_path.write_text(json.dumps({"healthy": True, "updated_epoch": now - 21,
            "current_talker": {"node": "54340"}, "last_talkers": []}))
        stale = agent.collect(config)
    finally:
        agent.subprocess.run = original_run
    assert fresh["current_talker"]["node"] == "54340" and stale["current_talker"] == {}
    assert fresh["connections"][0]["node"] == "674982"  # first data row is no longer skipped
    assert fresh["connections"][1]["keyed"] is True

# 11: relay-only activity is explicitly RELAY, never attributed to the relay node.
relay_only = [hub("kentucky", "674982", [talker("641894", 700)]), hub("phoenix", "641894", [])]
result = aggregate(relay_only)
assert result == [] and relay_only[0]["txState"] == "relay"
assert relay_only[0]["txLabel"] == "RELAY · Origin pending"

# Relay-first ordering gets a bounded identity-resolution window instead of a
# visible false unknown. Origin-first and the resolved follow-up stay immediate.
event_start = 1_700_000_100
relay_first = [hub("kentucky", "674982", [talker("641894", event_start)]),
    hub("phoenix", "641894", []), hub("georgia", "674980", [])]
result = aggregate(relay_first)
assert result[0]["callsign"] == "" and result[0]["identityPendingUntilEpochMs"] > event_start * 1000
assert relay_first[0]["txState"] == "pending"
assert relay_first[0]["txLabel"] == "ACTIVE · Origin not yet verified"
expired_relay_first = [hub("kentucky", "674982", [talker("641894", event_start)]),
    hub("phoenix", "641894", []), hub("georgia", "674980", [])]
expired_result = status.aggregate_talkers(expired_relay_first, event_start + 3)
assert expired_result == [] and expired_relay_first[0]["txState"] == "relay"
assert expired_relay_first[0]["txLabel"] == "RELAY · Origin pending"
resolved = [hub("kentucky", "674982", [talker("641894", event_start)]),
    hub("phoenix", "641894", [talker("53430", event_start)]), hub("georgia", "674980", [])]
assert aggregate(resolved)[0]["node"] == "53430" and resolved[1]["txState"] == "origin"
origin_first = [hub("kentucky", "674982", []),
    hub("phoenix", "641894", [talker("53430", event_start)]), hub("georgia", "674980", [])]
assert aggregate(origin_first)[0]["node"] == "53430" and origin_first[1]["txState"] == "origin"

# A verified unkey wins over lagging downstream relay snapshots. The completed
# station moves to Recent and cannot be resurrected as Unidentified Station.
lagged = [
    hub("kentucky", "674982", [talker("641894", 720)]),
    hub("phoenix", "641894", [talker("53430", 720, 730, False)]),
    hub("georgia", "674980", [talker("674982", 720)]),
]
result = aggregate(lagged)
assert result[0]["node"] == "53430" and result[0]["current"] is False
assert [item["txState"] for item in lagged] == ["idle", "idle", "idle"]
assert not any(row.get("current") for row in result)

# A relay must not be cleared when multiple completed origins from the same
# upstream hub fit its timing window; attribution is ambiguous, not idle.
ambiguous_completed = [
    hub("phoenix", "641894", [talker("11111", 725, 730, False), talker("22222", 727, 731, False)]),
    hub("kentucky", "674982", [talker("641894", 728)]),
]
aggregate(ambiguous_completed)
assert ambiguous_completed[1]["txState"] == "unknown"
assert ambiguous_completed[1]["txLabel"] == "RELAY · Origin ambiguous"

# Two direct stations keying close together must never inherit one another's
# identities, even if only one origin is currently verified.
direct_conflict = [
    hub("kentucky", "674982", [talker("11111", 715)]),
    hub("phoenix", "641894", [talker("22222", 716)]),
]
aggregate(direct_conflict)
assert direct_conflict[0]["txLabel"] == "TX · CALL11 / 11111"
assert direct_conflict[1]["txLabel"] == "TX · CALL22 / 22222"

# Relay-chain telemetry can arrive in any order. Each observed hub must
# classify the same Phoenix station without inventing a second origin.
for arrival in (("phoenix", "kentucky", "georgia"),
                ("georgia", "kentucky", "phoenix"),
                ("kentucky", "phoenix", "georgia")):
    rows_by_id = {
        "phoenix": hub("phoenix", "641894", [talker("53430", 900)]),
        "kentucky": hub("kentucky", "674982", [talker("641894", 901)]),
        "georgia": hub("georgia", "674980", [talker("674982", 902)]),
    }
    ordered = [rows_by_id[key] for key in arrival]
    aggregate(ordered)
    assert rows_by_id["phoenix"]["txState"] == "origin"
    assert rows_by_id["kentucky"]["txState"] == "relay"
    assert rows_by_id["georgia"]["txState"] == "relay"
    assert rows_by_id["georgia"]["txLabel"] == "RELAY · Phoenix (641894)"

# When two origins overlap, a relay timestamp compatible with both is
# ambiguous; never guess a station's identity.
ambiguous = [
    hub("phoenix", "641894", [talker("11111", 920)]),
    hub("kentucky", "674982", [talker("22222", 922)]),
    hub("georgia", "674980", [talker("641894", 921)]),
]
aggregate(ambiguous)
assert ambiguous[0]["txState"] == "origin"
assert ambiguous[1]["txState"] == "origin"
assert ambiguous[2]["txState"] == "unknown"
assert "11111" not in ambiguous[2]["txLabel"] and "22222" not in ambiguous[2]["txLabel"]

# A completed Phoenix origin and its lagging Kentucky relay must not suppress
# an unrelated active Georgia origin with a nearby timestamp.
independent_after_unkey = [
    hub("kentucky", "674982", [talker("641894", 720)]),
    hub("phoenix", "641894", [talker("53430", 720, 730, False)]),
    hub("georgia", "674980", [talker("77777", 721)]),
]
aggregate(independent_after_unkey)
assert [item["txState"] for item in independent_after_unkey] == ["idle", "idle", "origin"]

# Independent simultaneous origins remain independently classified; the
# singular Current card selects the newest without fabricating identity.
simultaneous = [
    hub("kentucky", "674982", [talker("11111", 740)]),
    hub("phoenix", "641894", [talker("22222", 742)]),
    hub("georgia", "674980", []),
]
result = aggregate(simultaneous)
assert result[0]["node"] == "22222"
assert simultaneous[0]["txState"] == "origin" and simultaneous[1]["txState"] == "origin"

# 12: recent events are globally sorted by actual event time.
result = aggregate([
    hub("kentucky", "674982", [talker("11111", 790, 800, False)]),
    hub("phoenix", "641894", [talker("22222", 795, 810, False)]),
])
assert [row["node"] for row in result] == ["22222", "11111"]

# Endpoint preserves only bounded, timestamp-valid talker telemetry.
original_time = endpoint.time.time
endpoint.time.time = lambda: 900
try:
    cleaned = endpoint.sanitize_status("phoenix", {"hub": "phoenix", "node": "641894",
        "generated_epoch": 900, "connections": [], "bridges": {},
        "current_talker": {"node": "54340", "keyed_started_epoch": 895},
        "last_talkers": [{"node": "54340", "event_epoch": 890, "keyed_started_epoch": 880}]})
finally:
    endpoint.time.time = original_time
assert cleaned["connection_status"]["current_talker"]["node"] == "54340"
assert cleaned["connection_status"]["last_talkers"][0]["event_epoch"] == 890

# Endpoint lifecycle sequencing triggers aggregation only on real talker
# transitions and rejects an older active snapshot after a newer unkey.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    original_root, original_event, original_time = endpoint.STATUS_ROOT, endpoint.STATUS_EVENT_PATH, endpoint.time.time
    endpoint.STATUS_ROOT, endpoint.STATUS_EVENT_PATH = root / "status", root / "status-event"
    endpoint.time.time = lambda: 1002
    def wire(generated: int, sequence: int, current: bool) -> dict:
        return {"hub": "phoenix", "node": "641894", "generated_epoch": generated,
            "talker_sequence": sequence, "watcher_instance": "watcher-a", "connections": [], "bridges": {},
            "current_talker": {"node": "53430", "keyed_started_epoch": 995} if current else {},
            "last_talkers": [] if current else [{"node": "53430", "keyed_started_epoch": 995,
                "event_epoch": 1000, "duration_seconds": 5}]}
    try:
        assert endpoint.store_status("phoenix", wire(1000, 1, True)) is True
        marker = endpoint.STATUS_EVENT_PATH.stat().st_mtime_ns
        assert endpoint.store_status("phoenix", wire(1001, 1, True)) is False
        assert endpoint.STATUS_EVENT_PATH.stat().st_mtime_ns == marker
        time.sleep(0.002)
        assert endpoint.store_status("phoenix", wire(1002, 2, False)) is True
        assert endpoint.STATUS_EVENT_PATH.stat().st_mtime_ns > marker
        assert endpoint.store_status("phoenix", wire(1001, 1, True)) is False
        saved = json.loads((endpoint.STATUS_ROOT / "phoenix.json").read_text())
        assert saved["connection_status"]["current_talker"] == {}
    finally:
        endpoint.STATUS_ROOT, endpoint.STATUS_EVENT_PATH, endpoint.time.time = original_root, original_event, original_time

# The serialized watcher publisher drains a second transition that arrives
# while its first SSH publication is still active.
original_publish = agent.publish_transition
started, release = threading.Event(), threading.Event()
calls = []
def fake_publish(config: dict) -> None:
    calls.append(len(calls) + 1)
    if len(calls) == 1:
        started.set()
        assert release.wait(1)
agent.publish_transition = fake_publish
try:
    publisher = agent.EventPublisher({})
    publisher.trigger()
    assert started.wait(1)
    publisher.trigger()
    release.set()
    deadline = time.monotonic() + 1
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert calls == [1, 2]
finally:
    agent.publish_transition = original_publish

# Installed app_rpt H:MM:SS:subseconds output is normalized without treating
# the final field as seconds; hours above 24 become day-prefixed display.
durations = {
    "00:00:09:7": "00:00:09", "00:29:19:331": "00:29:19",
    "12:22:39:931": "12:22:39", "25:02:03:004": "1d 01:02:03",
    "1:02:03": "01:02:03", "2:03:04:05:006": "2d 03:04:05",
    "": "", "Never": "Never", "01:70:02:003": "01:70:02:003",
}
for source, expected in durations.items():
    assert status.normalize_connection_duration(source) == expected

# 13 and UI reliability: controls remain intact, refresh is near-real-time, active is red.
javascript = (ROOT / "ops/asrplus/asrplus.js").read_text()
css = (ROOT / "ops/asrplus/asrplus.css").read_text()
index = (ROOT / "ops/asrplus/index.php").read_text()
assert "method: 'POST'" in javascript and "void control(hub, 'disconnect'" in javascript
assert "window.setInterval(refresh, 1000)" in javascript and "renderTalkers(payload.talkers)" in javascript
assert "identityPending" in javascript and "tx-status is-${txState}" in javascript
final_current_rule = css.rindex("html .talker-card.is-current")
assert final_current_rule > css.rindex('html[data-theme="matrix"]')
assert "#ff3b3b !important" in css[final_current_rule:]
assert "asrplus.css?v=20261112-3" in index and "asrplus.js?v=20261112-3" in index
assert "generation < lastRenderedGeneration" in javascript and "refreshInFlight" in javascript
assert "generationChanged" in javascript and "if (generationChanged || identityPending)" in javascript
assert "browserRenderedEpochMs" in javascript and "X-ASRPlus-Response-Epoch-Ms" in (ROOT / "ops/asrplus/api.php").read_text()

# Event path units remove avoidable timer latency while the five-second timers
# remain as recovery fallbacks.
agent_path = (ROOT / "ops/asrplus/asrplus-agent.path").read_text()
status_path = (ROOT / "ops/asrplus/asrplus-status.path").read_text()
assert "PathChanged=/run/asrplus/talker-event" in agent_path
assert "PathChanged=/var/lib/asrplus/status-event" in status_path
assert ".netoholics-public-status-talker-live.json" in status_path
assert "remote-status/phoenix.json" not in status_path and "remote-status/georgia.json" not in status_path
assert "StartLimitIntervalSec=0" in (ROOT / "ops/asrplus/asrplus-status.service").read_text()
assert "OnUnitActiveSec=5" in (ROOT / "ops/asrplus/asrplus-agent.timer").read_text()
assert "OnUnitActiveSec=5" in (ROOT / "ops/asrplus/asrplus-status.timer").read_text()
assert 'run_ssh(config, "asrplus-sync"' in (ROOT / "scripts/asrplus-agent.py").read_text()
assert 'original == "asrplus-sync"' in (ROOT / "scripts/asrplus-remote-endpoint.py").read_text()
assert "EventPublisher(config)" in (ROOT / "scripts/asrplus-agent.py").read_text()
assert "ControlMaster=auto" in (ROOT / "scripts/asrplus-agent.py").read_text()
assert "talkerStatusFile" in (ROOT / "ops/asrplus/asrplus.json").read_text()

print("PASS: ASR Plus three-hub talker tracking simulations")

# A relay-first observation is active, not IDLE or a fabricated origin.
relay_first = [hub("kentucky", "674982", [talker("641894", 1_700_000_099)]),
               hub("phoenix", "641894", [])]
aggregate(relay_first)
assert relay_first[0]["txState"] == "pending"
assert relay_first[0]["txLabel"].startswith("ACTIVE")
assert relay_first[1]["txState"] == "idle"

# Browser must not disguise a pending active transmission as IDLE.
js = (ROOT / "ops/asrplus/asrplus.js").read_text()
assert "identityPending ? 'idle'" not in js
assert "ACTIVE · Identifying station" in js
css = (ROOT / "ops/asrplus/asrplus.css").read_text()
assert "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)" in css
