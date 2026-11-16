#!/usr/bin/env python3
"""Ensure low-RAM diagnostics never restart Asterisk or healthy/unattributed services."""
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('diag', Path(__file__).with_name('asr-node-diagnostics.py'))
diag = importlib.util.module_from_spec(spec); sys.modules[spec.name] = diag; spec.loader.exec_module(diag)
class Result:
    returncode = 0
calls = []
def fake(cmd, **kwargs):
    calls.append(cmd)
    return Result()
def check(rows, available, state):
    calls.clear()
    return diag.recover({'epoch': 20000, 'memAvailableKb': available}, rows, state, fake, lambda _: None)
critical = diag.CRITICAL_RAM_KB - 1
# A heavy Cockpit login session is not an ASR-managed recovery target.
assert check([{'name': 'cockpit-bridge', 'pssKb': 250*1024}, {'name':'apache2','pssKb':100*1024}], critical, {}) is None
assert not calls
# Never restart Asterisk, even if it is the dominant memory consumer.
assert check([{'name':'asterisk','pssKb':350*1024}], critical, {}) is None
assert not calls
# Healthy available RAM must suppress recovery despite high Apache PSS.
assert check([{'name':'apache2','pssKb':200*1024}], diag.CRITICAL_RAM_KB+1, {'counts':{'apache2.service':2}}) is None
assert not calls
# A first or second low-memory observation is insufficient.
state = {}
for _ in range(2):
    assert check([{'name':'apache2','pssKb':120*1024}], critical, state) is None
assert not calls
# Third repeated observation may restart only the measured Apache service.
result = check([{'name':'apache2','pssKb':120*1024}], critical, state)
assert result and calls[0] == ['systemctl','try-restart','apache2.service']
# Cooldown prevents repeated recovery loops.
state = {'counts':{'apache2.service':2}, 'lastRecovery':{'apache2.service':19999}}
assert check([{'name':'apache2','pssKb':120*1024}], critical, state) is None
assert not calls
print('ASR low-memory recovery isolation: PASS')
