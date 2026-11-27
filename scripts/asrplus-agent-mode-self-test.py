#!/usr/bin/env python3
"""Offline regression tests for shared Phoenix/KY2 agent operating modes."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("asrplus_agent", root / "asrplus-agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

for name, mode, heartbeat in (("phoenix", "outbound", 5), ("ky2", "masterhub-pull", 1)):
    cfg = json.loads((root.parent / "ops/asrplus" / f"{name}-agent.json").read_text())
    assert cfg["eventPublishMode"] == mode
    assert cfg["talkerHeartbeatSeconds"] == heartbeat
    with patch.object(agent.threading, "Thread") as thread:
        publisher = agent.EventPublisher(cfg)
        assert publisher.enabled == (mode == "outbound")
        assert thread.called == (mode == "outbound")
        publisher.trigger()
        assert publisher.pending == (mode == "outbound")
    with tempfile.TemporaryDirectory() as directory:
        cache = Path(directory) / "astapi.json"
        talker = {"node": "12345", "info": "N0CALL", "duration": 2}
        cache.write_text(json.dumps({"updated": time.time(), "current": {
            str(cfg["node"]): {"current_talker": talker, "last_talkers": [talker]}}}))
        snapshot = agent.read_asr_talkers(cfg | {"authoritativeTalkerCache": str(cache)})
        assert snapshot["current_talker"] == talker and snapshot["last_talkers"] == [talker]

for invalid in (0, 61, -1):
    with patch.object(agent, "EventPublisher"), patch.object(agent.time, "sleep", side_effect=RuntimeError):
        try:
            agent.watch_talkers({"node": "1", "talkerHeartbeatSeconds": invalid})
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid heartbeat accepted: {invalid}")
try:
    agent.EventPublisher({"eventPublishMode": "invalid"})
except ValueError:
    pass
else:
    raise AssertionError("invalid event publish mode accepted")
print("ASR Plus unified agent operating modes: PASS")
