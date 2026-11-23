#!/usr/bin/env python3
"""Validate staged per-hub config/module bundles without production secrets."""
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]/'ops'/'asrplus'/'staging'
for hub, entry in [('masterhub','asrplus-control.py'),('masterhub','asrplus-remote-endpoint.py'),('phoenix','asrplus-agent'),('ky2','asrplus-ky-agent')]:
    assert (root/hub/entry).is_file()
    assert (root/hub/'asrplus_command_integrity.py').is_file()
master=json.loads((root/'masterhub/command-integrity.json').read_text())['hubs']
phoenix=json.loads((root/'phoenix/asrplus-agent.json').read_text())
ky=json.loads((root/'ky2/asrplus-ky-control.json').read_text())
watch=json.loads((root/'ky2/asrplus-ky.json').read_text())
assert set(master)=={'phoenix','georgia'}
for hub,cfg in [('phoenix',phoenix),('georgia',ky)]:
    central=master[hub]
    agent=cfg['commandIntegrity']
    assert central['keyId']==agent['keyId']
    assert central['keyFile']==agent['keyFile']
    assert central['mode']==agent['mode']=='optional'
    assert not Path(central['keyFile']).exists(), 'real key must not be staged in repository'
assert phoenix['hub']=='phoenix' and phoenix['node']=='641894'
assert phoenix['eventPublishMode']=='outbound' and phoenix['talkerHeartbeatSeconds']==5
assert ky['hub']=='georgia' and ky['node']=='674980'
assert ky['eventPublishMode']=='masterhub-pull' and ky['talkerHeartbeatSeconds']==1
assert ky['controlPath'] and ky['key'] and ky['knownHosts']
assert set(watch)=={'node','talkerEvent','talkerStatus'}
assert 'commandIntegrity' not in watch and 'commandLedger' not in watch
print('ASR Plus staged hub config/module bundle: PASS')
