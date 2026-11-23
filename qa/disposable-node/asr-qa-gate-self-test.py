#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import os
import subprocess
spec = importlib.util.spec_from_file_location('qa_gate', Path(__file__).with_name('asr-qa-gate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not module.report([], Path('.'))['releaseQualified']
assert not module.report([{'ok': False}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['releaseTests'] == 'not-run'

original_call = module.call
os.environ['ASR_QA_DIR'] = '/qa'
def run_fixture(other_ip='192.168.122.112', target_ip='192.168.122.111', fail_other=False):
    def fake_call(*args):
        command = args[3:]
        if command[:2] == ('domstate', module.NAME):
            return subprocess.CompletedProcess(args, 0, 'running\n', '')
        if command[:2] == ('domstate', module.TEMPLATE):
            return subprocess.CompletedProcess(args, 0, 'shut off\n', '')
        if command[:2] == ('domblklist', module.NAME):
            return subprocess.CompletedProcess(args, 0, '/qa/asr-qa-disposable.qcow2\n', '')
        if command[:2] == ('domifaddr', module.NAME):
            out = f'vnet0 aa:bb:cc:dd:ee:01 ipv4 {target_ip}/24\n'
            return subprocess.CompletedProcess(args, 0, out, '')
        if command[:3] == ('list', '--name', '--state-running'):
            return subprocess.CompletedProcess(args, 0, 'asr-qa-disposable\nasr-qa-other\n', '')
        if command[:2] == ('domifaddr', 'asr-qa-other'):
            if fail_other:
                return subprocess.CompletedProcess(args, 1, '', 'guest agent unavailable')
            out = f'vnet1 aa:bb:cc:dd:ee:02 ipv4 {other_ip}/24\n'
            return subprocess.CompletedProcess(args, 0, out, '')
        raise AssertionError(f'unexpected virsh command: {args}')
    module.call = fake_call
    try:
        return module.check_vm()
    finally:
        module.call = original_call

unique = run_fixture()
assert next(c for c in unique if c['name'] == 'disposable-ip-unique')['ok']
duplicate = run_fixture(other_ip='192.168.122.111')
assert not next(c for c in duplicate if c['name'] == 'disposable-ip-unique')['ok']
unknown = run_fixture(fail_other=True)
assert not next(c for c in unknown if c['name'] == 'disposable-ip-unique')['ok']
print('Disposable VM fail-closed gate self-test: PASS (including duplicate IP detection)')
