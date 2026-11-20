#!/usr/bin/env python3
"""Contract tests for the read-only timer inventory."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).with_name('asr-systemd-timers.py')
spec = importlib.util.spec_from_file_location('asr_systemd_timers', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

for invalid in ['../foo.timer', '/etc/foo.timer', 'foo.service', 'foo.timer;id', 'foo timer', '', 'foo..timer']:
    try:
        module.validate_unit(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f'unsafe unit accepted: {invalid!r}')
assert module.validate_unit('apt-daily.timer') == 'apt-daily.timer'
with patch.object(module, 'run_systemctl', return_value='[{"unit_file":"apt-daily.timer","state":"disabled","preset":"enabled"},{"unit_file":"../../bad.timer"}]') as run:
    items = module.inventory()
    assert items == [{'name': 'apt-daily.timer', 'unitFileState': 'disabled', 'preset': 'enabled', 'scope': 'system'}]
    assert '--all' in run.call_args.args[0]
with patch.object(module, 'run_systemctl', return_value='Id=apt-daily.timer\nActiveState=inactive\nFragmentPath=/usr/lib/systemd/system/apt-daily.timer\n') as run:
    details = module.inspect('apt-daily.timer', user=True)
    assert details['scope'] == 'user'
    assert details['properties']['ActiveState'] == 'inactive'
    assert run.call_args.kwargs['user'] is True
print('ASR systemd timers read-only self-test: ok')
