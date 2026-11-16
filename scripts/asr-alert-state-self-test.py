#!/usr/bin/env python3
"""Alert stability and deduplication tests (#126)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("asr_alert_state", Path(__file__).with_name("asr-alert-state.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
obs = [{"id": "resource.low_ram", "severity": "warning", "component": "node-resources", "message": "Low RAM"}]
s1 = module.update({}, obs, 100)
assert not s1["active"] and not s1["events"]
s2 = module.update(s1, obs, 160)
assert len(s2["active"]) == 1 and s2["events"][0]["event"] == "activated"
assert s2["active"][0]["firstSeenEpoch"] == 100
s3 = module.update(s2, obs, 220)
assert len(s3["active"]) == 1 and not s3["events"]
s4 = module.update(s3, [{**obs[0], "severity": "critical"}], 280)
assert s4["events"][0]["event"] == "severity-changed"
s5 = module.update(s4, [], 340)
assert not s5["active"] and s5["events"][0]["event"] == "resolved"
s6 = module.update(s5, [], 400)
assert not s6["events"]
s7 = module.update(s6, obs, 460)
assert not s7["active"] and not s7["events"]
assert module.resource_observations(["Available RAM is low."], {"memAvailableKb": 100000})[0]["severity"] == "critical"
for bad in ([obs[0], obs[0]], [{"id": "../unsafe", "severity": "warning"}]):
    try:
        module.update({}, bad, 100)
        raise AssertionError("invalid alert accepted")
    except ValueError:
        pass
print("ASR alert state self-test: PASS")
