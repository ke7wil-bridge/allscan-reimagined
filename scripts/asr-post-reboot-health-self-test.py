#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import subprocess
spec = importlib.util.spec_from_file_location('health', Path(__file__).with_name('asr-post-reboot-health.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def healthy(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, 'LoadState=loaded\nActiveState=active\nSubState=running\n', '')

def failed(command, **kwargs):
    if command[2] == 'apache2.service':
        return subprocess.CompletedProcess(command, 0, 'LoadState=loaded\nActiveState=failed\n', '')
    return healthy(command, **kwargs)

config = {'bridges': [{'mode': 'm17', 'node': '1006', 'backendMode': 'managed'},
                      {'mode': 'p25', 'backendMode': 'display_only'}]}
result = module.inspect(config, healthy)
assert result['ok'] and len(result['checks']) == 5
assert result['checks'][-1]['state'] == 'configured-not-connectivity-verified'
assert not module.inspect(config, failed)['ok']
assert not module.inspect({'bridges': {}}, healthy)['ok']
print('ASR post-reboot read-only health self-test: PASS')
