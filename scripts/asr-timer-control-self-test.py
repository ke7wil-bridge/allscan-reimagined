#!/usr/bin/env python3
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('control', Path(__file__).with_name('asr-timer-control.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.validate_unit('allscan-reimagined-admin-health.timer', managed=True)
assert mod.validate_service('allscan-reimagined-health.service')
for unit in ('../bad.timer', '/etc/bad.timer', 'bad.service', 'bad timer', 'foo..timer', 'cron.timer'):
    try:
        mod.validate_unit(unit, managed=True)
        raise AssertionError(f'accepted unsafe managed unit: {unit}')
    except mod.ControlError:
        pass
for service in ('ssh.service', '../bad.service', 'allscan-reimagined-bad.timer'):
    try:
        mod.validate_service(service)
        raise AssertionError(f'accepted unsafe service: {service}')
    except mod.ControlError:
        pass

with patch.object(mod, 'run', return_value=Mock(returncode=0, stdout='', stderr='')):
    rendered = mod.render_timer(
        'allscan-reimagined-admin-health.timer', 'Mon..Fri 08:00',
        'allscan-reimagined-health.service', 'ASR health check', True,
    )
    preview = mod.plan(argparse.Namespace(
        unit='allscan-reimagined-admin-health.timer', calendar='Mon..Fri 08:00',
        service='allscan-reimagined-health.service', description='ASR health check', persistent=True,
    ))
assert 'OnCalendar=Mon..Fri 08:00' in rendered
assert 'Persistent=true' in rendered
assert 'Unit=allscan-reimagined-health.service' in rendered
assert preview['confirmation'] == 'APPLY allscan-reimagined-admin-health.timer'
assert mod.confirmation('restart', 'allscan-reimagined-health.timer') == 'RESTART allscan-reimagined-health.timer'

show = Mock(returncode=0, stdout='Id=allscan-reimagined-health.timer\nTriggers=allscan-reimagined-health.service\n', stderr='')
with patch.object(mod, 'systemctl', return_value=show) as ctl, patch.object(mod, 'run', return_value=Mock(returncode=0, stdout='', stderr='')):
    args = argparse.Namespace(action='trigger', unit='allscan-reimagined-health.timer', confirm='TRIGGER allscan-reimagined-health.timer')
    result = mod.action(args)
    assert result['service'] == 'allscan-reimagined-health.service'
    assert any(call.args[:2] == ('start', 'allscan-reimagined-health.service') for call in ctl.call_args_list)

for action, unit, confirm in (
    ('stop', 'apt-daily.timer', 'STOP apt-daily.timer'),
    ('disable', 'cron.timer', 'DISABLE cron.timer'),
    ('start', 'allscan-reimagined-health.timer', ''),
):
    try:
        mod.action(argparse.Namespace(action=action, unit=unit, confirm=confirm))
        raise AssertionError(f'accepted unsafe action: {action} {unit}')
    except mod.ControlError:
        pass

rows_json = json.dumps([{'unit_file': 'apt-daily.timer', 'state': 'disabled', 'preset': 'enabled'}])
def fake_systemctl(*args, **kwargs):
    if args[0] == 'list-unit-files':
        return Mock(returncode=0, stdout=rows_json, stderr='')
    return Mock(returncode=0, stdout='ActiveState=inactive\nTriggers=apt-daily.service\n', stderr='')
with patch.object(mod, 'systemctl', side_effect=fake_systemctl):
    rows = mod.timer_rows()
assert rows[0]['name'] == 'apt-daily.timer'
assert rows[0]['protected'] is True
assert rows[0]['activeState'] == 'inactive'

with tempfile.TemporaryDirectory(prefix='asr-timer-control-test.') as directory:
    root = Path(directory)
    units = root / 'units'; units.mkdir()
    backups = root / 'backups'
    with patch.object(mod, 'UNIT_DIR', units), patch.object(mod, 'BACKUP_DIR', backups), \
         patch.object(mod, 'AUDIT_LOG', root / 'audit.jsonl'), patch.object(mod, 'LOCK_FILE', root / 'control.lock'), \
         patch.object(mod, 'run', return_value=Mock(returncode=0, stdout='', stderr='')), \
         patch.object(mod, 'systemctl', return_value=Mock(returncode=1, stdout='disabled\n', stderr='')):
        apply_args = argparse.Namespace(
            unit='allscan-reimagined-admin-test.timer', calendar='daily',
            service='allscan-reimagined-test.service', description='ASR test timer',
            persistent=True, confirm='APPLY allscan-reimagined-admin-test.timer',
        )
        applied = mod.apply_timer(apply_args)
        assert (units / apply_args.unit).is_file()
        assert applied['backup'].startswith(apply_args.unit + '.')
        original_backup = json.loads((backups / applied['backup']).read_text())
        assert original_backup['content'] is None
        delete_args = argparse.Namespace(unit=apply_args.unit, confirm='DELETE allscan-reimagined-admin-test.timer')
        deleted = mod.delete_timer(delete_args)
        assert not (units / apply_args.unit).exists()
        listed = mod.list_backups(apply_args.unit)
        assert any(item['id'] == deleted['backup'] and item['existed'] for item in listed)
        rollback_args = argparse.Namespace(
            unit=apply_args.unit, backup=deleted['backup'],
            confirm='ROLLBACK allscan-reimagined-admin-test.timer',
        )
        mod.rollback(rollback_args)
        assert (units / apply_args.unit).is_file()
    unsafe_target = root / 'unsafe-target'; unsafe_target.mkdir()
    unsafe_link = root / 'unsafe-link'; unsafe_link.symlink_to(unsafe_target, target_is_directory=True)
    try:
        mod.secure_directory(unsafe_link, 0o700)
        raise AssertionError('accepted symlinked privileged directory')
    except mod.ControlError:
        pass
    with patch.object(mod, 'AUDIT_LOG', root / 'audit-dir' / 'timer-audit.jsonl'):
        mod.audit('Test Admin', 'restart', 'allscan-reimagined-health.timer', True)
        audit_row = json.loads((root / 'audit-dir' / 'timer-audit.jsonl').read_text())
        assert audit_row['actor'] == 'Test Admin' and audit_row['ok'] is True

source = Path(__file__).with_name('asr-timer-control.py').read_text()
assert 'shell=True' not in source
assert 'os.system' not in source
assert 'MANAGED_PREFIX = "allscan-reimagined-admin-"' in source
print('ASR timer control self-test: ok')
