#!/usr/bin/env python3
"""Validate staged per-hub config/module bundles without production secrets."""
import json
import hashlib
from pathlib import Path

root=Path(__file__).resolve().parents[1]/'ops'/'asrplus'/'staging'
for hub, entry in [('masterhub','asrplus-control.py'),('masterhub','asrplus-remote-endpoint.py'),('phoenix','asrplus-agent'),('ky2','asrplus-ky-agent')]:
    assert (root/hub/entry).is_file()
    assert (root/hub/'asrplus_command_integrity.py').is_file()
master=json.loads((root/'masterhub/command-integrity.json').read_text())['hubs']
phoenix=json.loads((root/'phoenix/asrplus-agent.json').read_text())
ky=json.loads((root/'ky2/asrplus-ky-control.json').read_text())
watch=json.loads((root/'ky2/asrplus-ky.json').read_text())
assert set(master)=={'phoenix','ky2'}
for hub,cfg in [('phoenix',phoenix),('ky2',ky)]:
    central=master[hub]
    agent=cfg['commandIntegrity']
    assert central['keyId']==agent['keyId']
    assert central['keyFile']==agent['keyFile']
    assert central['mode']==agent['mode']=='optional'
    assert not Path(central['keyFile']).exists(), 'real key must not be staged in repository'
assert phoenix['hub']=='phoenix' and phoenix['node']=='641894'
assert phoenix['eventPublishMode']=='outbound' and phoenix['talkerHeartbeatSeconds']==5
assert ky['hub']=='ky2' and ky['node']=='674980'
assert ky['eventPublishMode']=='masterhub-pull' and ky['talkerHeartbeatSeconds']==1
assert ky['controlPath'] and ky['key'] and ky['knownHosts']
assert ky['commandIntegrity']['keyId']=='ky2-2026-01'
assert ky['commandIntegrity']['keyFile']=='/etc/asrplus/command-keys/ky2.key'
assert set(watch)=={'node','talkerEvent','talkerStatus'}
assert 'commandIntegrity' not in watch and 'commandLedger' not in watch
dropin=(root/'phoenix/asrplus-agent.service.d/command-integrity.conf').read_text()
assert dropin.startswith('[Service]\n')
assert 'ReadWritePaths=/var/lib/asrplus-agent' in dropin
manifest={}
for line in (root/'SHA256SUMS').read_text().splitlines():
    digest, relative=line.split('  ', 1)
    path=root/relative.removeprefix('./')
    assert path.is_file() and path.is_relative_to(root)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    manifest[path.relative_to(root).as_posix()]=digest
expected={path.relative_to(root).as_posix() for path in root.rglob('*')
          if path.is_file() and path.name!='SHA256SUMS' and '__pycache__' not in path.parts}
assert set(manifest)==expected
print('ASR Plus staged hub config/module bundle: PASS')
