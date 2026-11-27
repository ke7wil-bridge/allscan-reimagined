#!/usr/bin/env python3
"""Offline regression gate for the continuous authoritative talker feed."""

import importlib.util
import json
from pathlib import Path
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


agent = load("asr_authority_agent", "asrplus-agent.py")
endpoint = load("asr_authority_endpoint", "asrplus-remote-endpoint.py")
status = load("asr_authority_status", "asrplus-status.py")
now = int(time.time())

for version in ("1.02", "1.03", "1.04", "1.05", "1.06"):
    server = (ROOT / f"compat/allscan-v{version}/astapi/server.php").read_text()
    for required in (
        "--asr-collector", "flock($sharedHandle, LOCK_EX | LOCK_NB)",
        "writeSharedStatus($sharedCache", "filesize($messagePath) > 1048576",
        "$currentInode !== $messageInode", "$messageOffset = 0",
        "$cacheStale", "asrStatusForAmiAvailability($cleanCurrent, $unavailable)",
        "talker_source_instance", "talker_event_sequence", "origin_events",
    ):
        assert required in server, (version, required)

agent_source = (SCRIPTS / "asrplus-agent.py").read_text()
for retired in ("class AmiClient", "class TalkerState", "manager_credentials(",
                "parse_talker_status(", "parse_alinks("):
    assert retired not in agent_source, retired
for unit_name in ("asrplus-talker-watcher.service",
                  "texas/asrplus-texas-talker-watcher.service"):
    unit = (ROOT / "ops/asrplus" / unit_name).read_text()
    assert "InaccessiblePaths=/etc/asterisk/manager.conf" in unit
    assert "-/run/allscan-reimagined" in unit

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    cache = root / "astapi.json"
    current = {"node": "54340", "info": "W1ABC", "source": "AllStar",
               "started_epoch": now - 2, "duration": 2}
    short = {"node": "54341", "info": "W2ABC", "source": "EchoLink",
             "started_epoch": now - 3, "event_epoch": now - 2, "duration": 1}
    origin = {"sequence": 7, "node": "54341", "source": "AllStar",
              "started_epoch": now - 3, "event_epoch": now - 2}
    cache.write_text(json.dumps({"updated": time.time(),
        "talker_source_instance": "a" * 64, "talker_event_sequence": 7,
        "origin_events": [origin], "current": {"641894": {
        "current_talker": current, "last_talkers": [short]}}}))
    config = {"node": "641894", "authoritativeTalkerCache": str(cache)}
    snapshot = agent.read_asr_talkers(config)
    assert snapshot["current_talker"] == current
    assert snapshot["last_talkers"] == [short]
    assert snapshot["watcher_instance"] == "a" * 64
    assert snapshot["talker_sequence"] == 7
    assert snapshot["origin_events"] == [origin]

    payload = {"hub": "phoenix", "node": "641894", "generated_epoch": now,
               "connections": [], "bridges": {}, "talker_authority": "asr",
               **snapshot}
    remote = endpoint.sanitize_status("phoenix", payload)
    assert remote["talker_authority"] == "asr"
    assert remote["talker_source_epoch"] == int(snapshot["talker_source_epoch"])
    assert remote["connection_status"]["current_talker"]["info"] == "W1ABC"
    assert remote["connection_status"]["last_talkers"][0]["duration_seconds"] == 1

    remote_path = root / "remote.json"
    remote_path.write_text(json.dumps(remote))
    hub = status.build_hub({"id": "phoenix", "name": "Phoenix", "node": "641894",
        "statusFile": str(remote_path)}, {}, {}, {}, now)
    assert hub["talkers"][0]["callsign"] == "W1ABC"
    assert hub["talkers"][0]["duration"] == "00:02"
    assert hub["talkers"][1]["source"] == "EchoLink"
    assert hub["talkers"][1]["duration"] == "00:01"

    cache.write_text(json.dumps({"updated": time.time() - 11, "current": {"641894": {
        "current_talker": current, "last_talkers": [short]}}}))
    assert agent.read_asr_talkers(config) == {}
    remote["talker_source_epoch"] = now - 16
    remote_path.write_text(json.dumps(remote))
    stale = status.build_hub({"id": "phoenix", "name": "Phoenix", "node": "641894",
        "statusFile": str(remote_path)}, {}, {}, {}, now)
    assert stale["talkers"] == [] and stale["txState"] == "unknown"
    assert stale["txLabel"] == "Unavailable"

print("ASR authoritative continuous talker feed: PASS")
