#!/usr/bin/env python3
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('control', Path(__file__).with_name('asr-timer-control.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert mod.plan('restart', 'allscan-reimagined-health.timer', confirm='allscan-reimagined-health.timer') == ['systemctl', '--system', 'restart', 'allscan-reimagined-health.timer']
assert mod.plan('trigger', 'allscan-reimagined-health.timer', confirm='allscan-reimagined-health.timer')[-1] == 'allscan-reimagined-health.service'
for action, unit, confirm in [('delete', 'allscan-reimagined-health.timer', 'allscan-reimagined-health.timer'), ('stop', '../bad.timer', '../bad.timer'), ('disable', 'apt-daily.timer', 'apt-daily.timer'), ('start', 'cron.timer', 'cron.timer'), ('stop', 'allscan-reimagined-health.timer', '')]:
    try:
        mod.plan(action, unit, confirm=confirm)
        raise AssertionError(f'Accepted unsafe request: {action} {unit}')
    except ValueError:
        pass
print('ASR timer control self-test: ok')
