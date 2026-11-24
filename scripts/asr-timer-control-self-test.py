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
        'allscan-reimagined-admin-health.timer', 'calendar', 'Mon..Fri 08:00',
        'allscan-reimagined-health.service', 'ASR health check', True,
    )
    preview = mod.plan(argparse.Namespace(
        unit='allscan-reimagined-admin-health.timer', schedule_type='calendar', schedule='Mon..Fri 08:00',
        service='allscan-reimagined-health.service', description='ASR health check', persistent=True,
    ))
    monotonic = mod.render_timer(
        'allscan-reimagined-admin-health.timer', 'unit-active', '15 min',
        'allscan-reimagined-health.service', 'ASR health check', False,
    )
assert 'OnCalendar=Mon..Fri 08:00' in rendered
assert 'Persistent=true' in rendered
assert 'Unit=allscan-reimagined-health.service' in rendered
assert 'OnUnitActiveSec=15 min' in monotonic and 'OnCalendar=' not in monotonic
assert preview['confirmation'] == 'APPLY allscan-reimagined-admin-health.timer'
assert mod.confirmation('restart', 'allscan-reimagined-health.timer') == 'RESTART allscan-reimagined-health.timer'

safe_details = {'protected': False, 'protectionReason': '', 'properties': {'Triggers': 'logrotate.service'}}
with patch.object(mod, 'inspect_timer', return_value=safe_details), patch.object(mod, 'systemctl', return_value=Mock(returncode=0, stdout='', stderr='')) as ctl:
    args = argparse.Namespace(action='trigger', unit='logrotate.timer', scope='system', confirm='TRIGGER logrotate.timer')
    result = mod.action(args)
    assert result['service'] == 'logrotate.service'
    assert any(call.args[:2] == ('start', 'logrotate.service') for call in ctl.call_args_list)

with patch.object(mod, 'inspect_timer', return_value=safe_details), patch.object(mod, 'systemctl', return_value=Mock(returncode=0, stdout='', stderr='')) as ctl:
    result = mod.action(argparse.Namespace(action='restart', unit='logrotate.timer', scope='system', confirm='RESTART logrotate.timer'))
    assert result['action'] == 'restart'
    assert any(call.args[:2] == ('restart', 'logrotate.timer') for call in ctl.call_args_list)

for action, unit, confirm in (
    ('stop', 'apt-daily.timer', 'STOP apt-daily.timer'),
    ('start', 'logrotate.timer', ''),
):
    details = {'protected': unit in mod.CRITICAL, 'protectionReason': 'critical timer', 'properties': {'Triggers': 'logrotate.service'}}
    with patch.object(mod, 'inspect_timer', return_value=details):
        try:
            mod.action(argparse.Namespace(action=action, unit=unit, scope='system', confirm=confirm))
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
    def fixture_systemctl(*args, **kwargs):
        if args[0] == 'show':
            unit = args[1]
            exists = (units / unit).is_file()
            return Mock(returncode=0, stdout=(
                f"Id={unit}\nLoadState=loaded\nFragmentPath={units / unit}\nTransient=no\nUnitFileState=disabled\nTriggers=allscan-reimagined-test.service\n"
                if exists else f"Id={unit}\nLoadState=not-found\nFragmentPath=\n"), stderr='')
        if args[0] == 'is-enabled':
            return Mock(returncode=1, stdout='disabled\n', stderr='')
        if args[0] == 'is-active':
            return Mock(returncode=3, stdout='inactive\n', stderr='')
        return Mock(returncode=0, stdout='', stderr='')
    with patch.object(mod, 'UNIT_DIR', units), patch.object(mod, 'BACKUP_DIR', backups), \
         patch.object(mod, 'AUDIT_LOG', root / 'audit.jsonl'), patch.object(mod, 'LOCK_FILE', root / 'control.lock'), \
         patch.object(mod, 'run', return_value=Mock(returncode=0, stdout='', stderr='')), \
         patch.object(mod.os, 'chown'), \
         patch.object(mod, 'systemctl', side_effect=fixture_systemctl):
        apply_args = argparse.Namespace(
            unit='allscan-reimagined-admin-test.timer', scope='system', schedule_type='calendar', schedule='daily',
            service='allscan-reimagined-test.service', description='ASR test timer',
            persistent=True, confirm='APPLY allscan-reimagined-admin-test.timer',
        )
        applied = mod.apply_timer(apply_args)
        assert (units / apply_args.unit).is_file()
        assert applied['backup'].startswith('system.' + apply_args.unit + '.')
        original_backup = json.loads((backups / applied['backup']).read_text())
        assert original_backup['content'] is None
        delete_args = argparse.Namespace(unit=apply_args.unit, scope='system', confirm='DELETE allscan-reimagined-admin-test.timer')
        deleted = mod.delete_timer(delete_args)
        assert not (units / apply_args.unit).exists()
        listed = mod.list_backups(apply_args.unit, user=False)
        assert any(item['id'] == deleted['backup'] and item['existed'] for item in listed)
        rollback_args = argparse.Namespace(
            unit=apply_args.unit, backup=deleted['backup'],
            scope='system',
            confirm='ROLLBACK allscan-reimagined-admin-test.timer',
        )
        mod.rollback(rollback_args)
        assert (units / apply_args.unit).is_file()

        vendor = 'backup-report.timer'
        override_args = argparse.Namespace(
            unit=vendor, scope='system', schedule_type='unit-active', schedule='30 min',
            persistent=False, confirm='OVERRIDE backup-report.timer',
        )
        safe_vendor = {
            'protected': False, 'protectionReason': '', 'editable': True,
            'properties': {'Triggers': 'backup-report.service'},
        }
        with patch.object(mod, 'inspect_timer', return_value=safe_vendor):
            overridden = mod.override_timer(override_args)
        dropin = units / (vendor + '.d') / mod.DROPIN_NAME
        assert dropin.is_file() and 'OnUnitActiveSec=30 min' in dropin.read_text()
        assert not (units / vendor).exists(), 'vendor timer file was replaced'
        vendor_backups = mod.list_backups(vendor, user=False)
        assert vendor_backups[0]['mode'] == 'override'
        with patch.object(mod, "inspect_timer", return_value=safe_vendor):
            mod.rollback(argparse.Namespace(
            unit=vendor, scope='system', backup=overridden['backup'],
            confirm='ROLLBACK backup-report.timer',
        ))
        assert not dropin.exists()
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

# ASR safety timers are protected like critical OS timers.
for unit in ('allscan-reimagined-reapply.timer', 'allscan-reimagined-global-ban-expire.timer'):
    assert unit in mod.CRITICAL
    try:
        with patch.object(mod, 'inspect_timer', return_value={'protected': True, 'protectionReason': 'critical timer', 'properties': {}}):
            mod.action(argparse.Namespace(unit=unit, action='stop', scope='system', confirm=f'STOP {unit}'))
        raise AssertionError(f'stopped protected ASR timer: {unit}')
    except mod.ControlError as exc:
        assert 'protected' in str(exc).lower()

# Monotonic timers (OnBootSec/OnUnitActiveSec) have no NextElapseUSecRealtime;
# the next run comes from list-timers instead of showing blank.
assert mod.format_usec(0) == '' and mod.format_usec('x') == ''
assert mod.format_usec(1795194488779953)[:3] in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
listing = json.dumps([{'unit': 'allscan-reimagined-reapply.timer', 'next': 1795194488779953, 'last': 1795194148214682}])
with patch.object(mod, 'systemctl', return_value=Mock(returncode=0, stdout=listing, stderr='')):
    schedule = mod.runtime_schedule()
assert schedule['allscan-reimagined-reapply.timer']['next'] == mod.format_usec(1795194488779953)
assert schedule['allscan-reimagined-reapply.timer']['last'] == mod.format_usec(1795194148214682)

dependency_props = {'LoadState': 'loaded', 'Triggers': 'cleanup.service', 'PartOf': 'shutdown.target'}
assert 'dependency' in mod.protection_reason('cleanup.timer', properties=dependency_props)
assert 'dependency' in mod.protection_reason('cleanup.timer', user=True, properties=dependency_props)
assert mod.protection_reason('unknown.timer', properties={})
try:
    mod.storage_path('test.timer', user=True, mode='override', create=True)
    raise AssertionError('accepted privileged user-home write')
except mod.ControlError:
    pass

# Canonical identity and runtime-only definitions must be protected.
for props in (
    {"LoadState": "loaded", "Id": "apt-daily.timer"},
    {"LoadState": "loaded", "Names": "safe.timer apt-daily.timer"},
    {"LoadState": "loaded", "Id": "other.timer"},
    {"LoadState": "loaded", "Transient": "yes"},
    {"LoadState": "loaded", "FragmentPath": "/run/systemd/generator/safe.timer"},
    {"LoadState": "loaded", "UnitFileState": "generated"},
):
    assert mod.protection_reason("safe.timer", properties=props)
    assert mod.protection_reason("safe.timer", user=True, properties=props)

# Exercise the actual user filesystem worker in an isolated temporary home.
import os
from types import SimpleNamespace

def rejects(fn):
    try:
        fn()
    except mod.ControlError:
        return
    raise AssertionError("Unsafe operation was accepted")

with tempfile.TemporaryDirectory(prefix="asr-user-worker-test-") as directory:
    home = Path(directory)
    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(),
                              pw_dir=str(home), pw_name="test")
    assert account.pw_uid != 0 and account.pw_gid != 0, "Run this fixture unprivileged"
    unit = "allscan-reimagined-admin-fixture.timer"
    content = "[Timer]\nOnCalendar=daily\n"
    with patch.object(mod, "invoking_account", return_value=account):
        assert mod.user_file(unit, "direct", "read") is None
        mod.user_file(unit, "direct", "write", content)
        assert mod.user_file(unit, "direct", "read") == content
        path = home / ".config/systemd/user" / unit
        assert path.stat().st_uid == account.pw_uid
        assert path.stat().st_mode & 0o777 == 0o600
        mod.user_file(unit, "override", "write", content)
        assert mod.user_file(unit, "override", "read") == content
        mod.user_file(unit, "override", "remove")
        assert mod.user_file(unit, "override", "read") is None
        mod.user_file(unit, "direct", "remove")
        outside = home / "sentinel"
        outside.write_text("unchanged")
        path.symlink_to(outside)
        for operation in ("read", "write", "remove"):
            rejects(lambda: mod.user_file(unit, "direct", operation, content))
        assert outside.read_text() == "unchanged"
        path.unlink()
        os.link(outside, path)
        rejects(lambda: mod.user_file(unit, "direct", "read"))
        rejects(lambda: mod.user_file(unit, "direct", "write", content))
        assert outside.read_text() == "unchanged"
        path.unlink()
        os.mkfifo(path)
        rejects(lambda: mod.user_file(unit, "direct", "read"))
        path.unlink()
        path.write_text("x" * (mod.MAX_DEFINITION + 1))
        rejects(lambda: mod.user_file(unit, "direct", "read"))
        path.unlink()
        parent = path.parent
        parent.rename(parent.with_name("saved-user"))
        parent.symlink_to(home, target_is_directory=True)
        rejects(lambda: mod.user_file(unit, "direct", "write", content))
        assert not (home / unit).exists()

# A failed or ambiguous systemd query must never authorize a new definition.
for result in (
    Mock(returncode=1, stdout="", stderr="connection failed"),
    Mock(returncode=0, stdout="LoadState=not-found", stderr=""),
    Mock(returncode=0, stdout="Id=other.timer\nLoadState=not-found", stderr=""),
):
    with patch.object(mod, "systemctl", return_value=result):
        rejects(lambda: mod.require_absent_timer("example.timer", user=False))

# Cross-account backup rejection occurs before writing any definition.
with patch.object(mod, "invoking_account", return_value=SimpleNamespace(pw_uid=1001)), \
     patch.object(mod, "write_definition") as write:
    rejects(lambda: mod.restore_backup({"scope":"user", "mode":"direct",
        "unit":"allscan-reimagined-admin-fixture.timer", "accountUid":1002,
        "content":content}))
    write.assert_not_called()

source = Path(__file__).with_name('asr-timer-control.py').read_text()
assert 'shell=True' not in source
assert 'os.system' not in source
assert 'MANAGED_PREFIX = "allscan-reimagined-admin-"' in source
assert 'DROPIN_NAME = "90-allscan-reimagined-schedule.conf"' in source
print('ASR timer control self-test: ok')
