#!/usr/bin/env python3
"""Regression guard for issue #154: atomic Settings writes after backup restore."""
from pathlib import Path
import re
import tempfile
import os
import subprocess

root = Path(__file__).resolve().parents[1]
installer = (root / 'install.sh').read_text()
reapply = (root / 'scripts/asr-reapply.sh').read_text()
configure = (root / 'scripts/asr-configure.sh').read_text()
controller = (root / 'compat/allscan-v1.05/asr-settings/settings-controller.php').read_text()

assert installer.index('WEB_GROUP="www-data"') < installer.index('CHANGES_STARTED=1'), 'web group must be initialized before rollback is armed'
assert installer.index('WEB_GROUP="www-data"') < installer.index('CHANGES_STARTED=1'), 'rollback must not be armed before web group is resolved'
restore = installer.split('restore_shared_backup() {', 1)[1].split('\n}\n', 1)[0]
assert re.search(r'install -d -o root -g "\$WEB_GROUP" -m 775 /etc/allscan-reimagined', restore), 'restore must preserve web-group directory writes'
for name, script in [('reapply', reapply), ('configure', configure)]:
    assert 'chown "root:$WEB_GROUP" "$CONFIG_DIR"' in script, name
    assert 'chmod 775 "$CONFIG_DIR"' in script, name
assert "ASR_SETTINGS_FILE . '.tmp.' . getmypid()" in controller
assert 'rename($tmp, ASR_SETTINGS_FILE)' in controller
with tempfile.TemporaryDirectory() as tmp:
    directory = Path(tmp)
    config = directory / 'config.json'
    config.write_text('{"headerText":"old"}\n')
    staged = directory / 'config.json.tmp.test'
    staged.write_text('{"headerText":"new"}\n')
    staged.replace(config)
    assert '"new"' in config.read_text()
# Exercise the actual directory-write behavior with a different uid/gid when root
# is available; static checks above run in unprivileged CI as well.
if os.geteuid() == 0:
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o755)
        directory = Path(tmp) / 'etc'
        directory.mkdir(mode=0o775)
        os.chown(directory, 0, 33)
        os.chmod(directory, 0o775)
        def attempt():
            return subprocess.run(['setpriv', '--reuid=33', '--regid=33', '--clear-groups',
                'sh', '-c', 'printf test > "$1/config.json.tmp.test"', '_', str(directory)],
                capture_output=True).returncode
        assert attempt() == 0, 'web group must be able to write temp settings file'
        (directory / 'config.json.tmp.test').unlink()
        os.chmod(directory, 0o755)
        assert attempt() != 0, 'test must detect missing web-group write permission'
print('Issue #154 settings permission and atomic-write regression guards: PASS')
