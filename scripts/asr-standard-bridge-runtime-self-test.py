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
            'linkState': 'linked', 'confirmedTarget': {'reflector': 'M17-073', 'module': 'A'}}
    path.write_text(json.dumps(data))
    assert m.confirmed_m17_state(bridge, 1001, Path(tmp))['linked'] is True
    assert m.confirmed_m17_state(bridge, 1001, Path(tmp))['reflector'] == 'M17-073'
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

# An idle NXDN card must not infer successful end-to-end audio or external
# health.  Inactivity/relink behavior is exercised by the production NXDN
# control entry point's self-test, which shares the installed P25/NXDN core.
nxdn = {'id': 'nxdn_johnny2', 'mode': 'nxdn', 'node': '1005', 'backendMode': 'managed'}
state = m.initial_activity_state()
entry = m.public_entry(nxdn, state)
assert entry['online'] is None and entry['linked'] is None
assert entry['health_severity'] is None
print('NXDN idle status does not fabricate runtime connectivity: PASS')

# Issue 309: retained sampler API and strictly scoped local legacy M17 proof.
import os
from types import SimpleNamespace
header = 'NODE PEER RECONNECTS DIRECTION CONNECT TIME CONNECT STATE\n---- ---- ---------- --------- ------------ -------------\n'
text = header + '1001 127.0.0.1 0 IN 1:02:03:004 ESTABLISHED\nBCSTFY 0 IN 1:02:03:004 ESTABLISHED\n'
with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=text)):
    assert m.confirmed_transport_links('674982', {'1001','1002'}) == {'1001':True,'1002':False}
with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='Unable to open socket for seeding global EID. You will have to set it manually.\n'+text)):
    assert m.confirmed_transport_links('674982', {'1001'}) == {'1001':True}
for rc, output in [(1,text),(0,'error'),(0,text+'unexpected row')]:
    with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=rc, stdout=output)):
        assert m.confirmed_transport_links('674982', {'1001'}) == {'1001':None}
legacy={'id':'m17','mode':'m17','backendMode':'display_only',
        'activityLogDirectory':'/var/log/m17','activityLogStem':'USRP2M17'}
with tempfile.TemporaryDirectory() as tmp:
    ini=Path(tmp)/'bridge.ini'; report=Path(tmp)/'report.json'
    ini.write_text('[M17 Network]\nCallsign=AB1CD N\nAddress=127.0.0.1\nName=M17-ABC A\n')
    ini.chmod(0o600)
    report_data={'Callsign':'M17-ABC','Clients':[{'Callsign':'AB1CD N','Module':'A','Protocol':'M17','IP':'127.0.0.1','ListenOnly':False}]}
    def check(data,stamp=1000,bridge=legacy):
        report.write_text(json.dumps(data))
        report.chmod(0o600)
        os.utime(report,(stamp,stamp))
        return m.confirmed_legacy_m17_state(bridge,1001,ini,report,os.getuid())
    assert check(report_data)=={'online':True,'linked':True,'reflector':'M17-ABC','module':'A'}
    assert check(report_data,stamp=900)=={}
    assert check(report_data,bridge=dict(legacy,backendMode='managed'))=={}
    assert check(dict(report_data,Clients=[]))=={}
    assert check(dict(report_data,Clients=report_data['Clients']*2))=={}
    assert check(dict(report_data,Clients=[dict(report_data['Clients'][0],Module='B')]))=={}
    assert check(dict(report_data,Clients=[dict(report_data['Clients'][0],IP='192.0.2.1')]))=={}
    assert check(dict(report_data,Callsign='M17-XYZ'))=={}
print('Issue 309 transport compatibility and legacy M17 evidence: PASS')
