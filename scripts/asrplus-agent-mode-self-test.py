#!/usr/bin/env python3
"""Offline regression tests for shared Phoenix/KY2 agent operating modes."""
import importlib.util
import json
from pathlib import Path
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
    with patch.object(agent, "node_metadata", return_value={}), patch.object(agent, "labels", return_value={}):
        state = agent.TalkerState(cfg)
        assert state.heartbeat_seconds == heartbeat

for invalid in (0, 61, -1):
    with patch.object(agent, "node_metadata", return_value={}), patch.object(agent, "labels", return_value={}):
        try:
            agent.TalkerState({"node": "1", "talkerHeartbeatSeconds": invalid})
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
