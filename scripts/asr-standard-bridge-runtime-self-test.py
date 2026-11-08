#!/usr/bin/env python3
"""Verify runtime-based status and reject unsupported connectivity guesses."""
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).with_name('asr_bridge_status.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
bridge = {'id': 'm17_johnny2', 'mode': 'm17'}
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'm17_johnny2.state.json'
    data = {'bridgeId': 'm17_johnny2', 'updatedEpoch': 1000, 'digitalLinked': True,
            'linkState': 'linked', 'reflectorCallsign': 'M17-073',
            'confirmedTarget': {'module': 'A'}}
    path.write_text(json.dumps(data))
    assert m.confirmed_m17_state(bridge, 1001, Path(tmp))['linked'] is True
    assert m.confirmed_m17_state(bridge, 1100, Path(tmp)) == {}
    assert m.confirmed_m17_state(bridge, 1100, Path(tmp)).get('linked') is None
    data['digitalLinked'] = False
    data['linkState'] = 'disconnected'
    path.write_text(json.dumps(data))
    assert m.confirmed_m17_state(bridge, 1001, Path(tmp))['online'] is False
    data['bridgeId'] = 'different'
    path.write_text(json.dumps(data))
    assert m.confirmed_m17_state(bridge, 1001, Path(tmp)) == {}

class Result:
    returncode = 0
    stdout = 'ActiveState=active\nNRestarts=0\nActiveEnterTimestampMonotonic=123456\n'
config = {'bridges': [{'id': 'ysf_johnny2', 'backendMode': 'managed',
                       'gatewayService': 'ysfgateway-ysf_johnny2.service',
                       'mmdvmService': 'mmdvm-bridge-ysf_johnny2.service'}]}
with patch.object(m.subprocess, 'run', side_effect=[Result(), Result()]):
    units, issues = m.bridge_service_health(config, 'ysf_johnny2')
assert len(units) == 2 and issues == []
print('Runtime confirmation and healthy services: PASS')

# An NXDN inactivity unlink immediately followed by a network-activity
# relink does not establish successful end-to-end audio or external health.
nxdn = {'id': 'nxdn_johnny2', 'mode': 'nxdn', 'node': '1005', 'backendMode': 'managed'}
state = m.initial_activity_state()
entry = m.public_entry(nxdn, state)
assert entry['online'] is None and entry['linked'] is None
assert entry['health_severity'] is None
print('NXDN inactivity/relink remains unverified: PASS')
