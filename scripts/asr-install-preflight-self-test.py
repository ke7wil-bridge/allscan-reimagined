#!/usr/bin/env python3
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import importlib.util

spec = importlib.util.spec_from_file_location('preflight', Path(__file__).with_name('asr-install-preflight.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as root:
    p = Path(root)
    (p / 'file').write_bytes(b'abc')
    (p / 'link').symlink_to(p / 'file')
    assert module.payload_bytes(p) == 3
    (p / 'nested').mkdir()
    (p / 'nested' / 'loop').symlink_to(p, target_is_directory=True)
    assert module.payload_bytes(p) == 3
    stats = SimpleNamespace(f_bavail=100, f_frsize=1024, f_favail=2, f_files=100)
    with patch.object(module.os, 'statvfs', return_value=stats):
        available, inodes, problems = module.check(p / 'future', 200000, 5)
        assert available == 102400 and inodes == 2
        assert len(problems) == 2
        assert module.check(p, 1000, 1)[2] == []
# Doug's ASL3 node had a 128-MiB /tmp tmpfs mounted noexec. Free bytes
# alone cannot establish that executable staging is safe.
mounts = ('36 22 0:31 / / rw,relatime - ext4 /dev/vda1 rw\n'
          '41 36 0:50 / /tmp rw,nosuid,nodev,noexec,size=131072k - tmpfs tmpfs rw\n')
assert 'noexec' in module.temporary_mount_problems('/tmp', mounts)[0]
assert module.temporary_mount_problems('/var/tmp', mounts) == []
assert module.temporary_mount_problems('/tmp', mounts.replace('noexec,', '')) == []
good = SimpleNamespace(returncode=0, stdout='', stderr='')
bad = SimpleNamespace(returncode=0, stdout='unpacked package', stderr='')
with patch.object(module.Path, 'is_file', return_value=True):
    assert module.package_health(lambda *a, **kw: good) == []
    assert len(module.package_health(lambda *a, **kw: bad)) == 1
    assert len(module.package_health(lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr=''))) == 1
    def timeout(*a, **kw):
        raise module.subprocess.TimeoutExpired('dpkg', 15)
    assert len(module.package_health(timeout)) == 1
with patch.object(module.Path, 'is_file', return_value=False):
    assert len(module.package_health(lambda *a, **kw: good)) == 1
# Multiple destination directories on one filesystem must reserve their
# combined budget, not independently claim the same free bytes.
with tempfile.TemporaryDirectory() as root:
    p = Path(root)
    stats = SimpleNamespace(f_bavail=500, f_frsize=1024 * 1024, f_favail=10000, f_files=100000)
    with patch.object(module.os, 'statvfs', return_value=stats):
        assert module.check(p, 256 * module.MIB, 1024)[2] == []
        assert module.check(p, (256 + 128 + 128) * module.MIB, 3072)[2]
# Confirm the preflight refuses non-Debian systems before installer mutations.
assert module.supported_os('ID=debian\n')
assert module.supported_os('ID=raspbian\nID_LIKE="debian"\n')
assert module.supported_os('ID=ubuntu\n')
assert not module.supported_os('ID=fedora\nID_LIKE="rhel"\n')
assert not module.supported_os('ID=unknown\n')
# Execute the real read-only preflight entry point against a temporary payload,
# injecting a disk-full error and then a healthy host. No host paths are written.
with tempfile.TemporaryDirectory() as root:
    from contextlib import redirect_stdout, redirect_stderr
    from io import StringIO
    p = Path(root)
    (p / 'web').mkdir()
    (p / 'web' / 'index.html').write_text('test')
    argv = ['asr-install-preflight.py', '--payload', str(p)]
    fake_stat = SimpleNamespace(st_dev=1, st_size=4, st_mode=0o040755)
    with patch.object(module.sys, 'argv', argv), \
         patch.object(module.Path, 'stat', return_value=fake_stat), \
         patch.object(module, 'package_health', return_value=[]), \
         patch.object(module, 'supported_os', return_value=True), \
         patch.object(module, 'check', return_value=(1, 0, ['simulated disk full'])):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as err:
            assert module.main() == 1
            assert 'simulated disk full' in err.getvalue()
    with patch.object(module.sys, 'argv', argv), \
         patch.object(module.Path, 'stat', return_value=fake_stat), \
         patch.object(module, 'package_health', return_value=[]), \
         patch.object(module, 'supported_os', return_value=True), \
         patch.object(module, 'check', return_value=(1024 * module.MIB, 10000, [])):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            assert module.main() == 0
# Multiple destination directories on one filesystem must reserve their
# combined budget, not independently claim the same free bytes.
with tempfile.TemporaryDirectory() as root:
    p = Path(root)
    stats = SimpleNamespace(f_bavail=500, f_frsize=1024 * 1024, f_favail=10000, f_files=100000)
    with patch.object(module.os, 'statvfs', return_value=stats):
        assert module.check(p, 256 * module.MIB, 1024)[2] == []
        assert module.check(p, (256 + 128 + 128) * module.MIB, 3072)[2]
print('ASR installer resource preflight self-test: PASS')
