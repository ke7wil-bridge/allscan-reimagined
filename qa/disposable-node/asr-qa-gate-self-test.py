#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import subprocess
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('qa_gate', Path(__file__).with_name('asr-qa-gate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not module.report([], Path('.'))['ready']
assert not module.report([], Path('.'))['releaseQualified']
assert not module.report([{'ok': False}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['releaseTests'] == 'not-run'


def fixture(target='vnet0 aa:bb:cc:dd:ee:01 ipv4 192.168.122.111/24',
            peer='vnet1 aa:bb:cc:dd:ee:02 ipv4 192.168.122.112/24',
            inventory=None, second_inventory=None, second_target=None, running=True):
    inventory = [module.NAME, 'peer'] if inventory is None else inventory
    counts = {'list': 0, 'target': 0}
    def fake(*args):
        command = args[3:]
        out, rc = '', 0
        if command[:2] == ('domstate', module.NAME):
            out = 'running' if running else 'shut off'
        elif command[:2] == ('domstate', module.TEMPLATE):
            out = 'shut off'
        elif command[:2] == ('domblklist', module.NAME):
            out = '/qa/asr-qa-disposable.qcow2'
        elif command[:2] == ('domifaddr', module.NAME):
            counts['target'] += 1
            out = second_target if counts['target'] > 1 and second_target is not None else target
        elif command[:2] == ('domifaddr', 'peer'):
            if isinstance(peer, BaseException):
                raise peer
            out = peer
        elif command[:3] == ('list', '--name', '--state-running'):
            counts['list'] += 1
            domains = second_inventory if counts['list'] > 1 and second_inventory is not None else inventory
            out = '\n'.join(domains)
        else:
            raise AssertionError(f'unexpected command: {args}')
        if out is None:
            rc, out = 1, ''
        return subprocess.CompletedProcess(args, rc, out + '\n', '')
    with patch.object(module, 'call', fake), patch.dict(module.os.environ, {'ASR_QA_DIR': '/qa'}):
        checks = module.check_vm()
    return {c['name']: c for c in checks}, module.report(checks, Path('.'))


def verify(expected, **kwargs):
    checks, report = fixture(**kwargs)
    assert checks['disposable-ip-unique']['ok'] is expected, checks
    assert report['ready'] is expected, report
    assert report['releaseQualified'] is False
    assert report['releaseTests'] == 'not-run'
    return checks

verify(True)
verify(True, inventory=[module.NAME])
verify(False, peer='vnet1 aa:bb:cc:dd:ee:02 ipv4 192.168.122.111/24')
verify(False, peer='vnet1 aa:bb:cc:dd:ee:02 ipv4 192.168.122.112/24\n     - ipv4 192.168.122.111/24')
verify(False, peer=None)
verify(False, peer=subprocess.TimeoutExpired('virsh', 15))
verify(False, peer='')
verify(False, peer='vnet1 mac ipv4 999.1.1.1/24')
verify(False, peer='vnet1 mac ipv4 not-an-address')
verify(False, target='lo mac ipv4 127.0.0.1/8')
verify(False, target=None)
verify(False, target='vnet0 mac ipv6 fe80::1/64')
verify(False, inventory=['peer'])
verify(False, second_inventory=[module.NAME])
verify(False, second_target='vnet0 mac ipv4 192.168.122.113/24')
verify(False, running=False)
print('Disposable VM fail-closed gate self-test: PASS (16 readiness fixtures plus report guards)')
