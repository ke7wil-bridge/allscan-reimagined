#!/usr/bin/env python3
"""Read-only resource checks before ASR installer package mutations."""
import argparse
import os
import re
import subprocess
from pathlib import Path
import sys

MIB = 1024 * 1024


def payload_bytes(path):
    total = 0
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            item = Path(base) / name
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
    return total


def check(path, required_bytes, required_inodes=1024):
    target = Path(path)
    while not target.exists():
        if target == target.parent:
            raise ValueError(f"No existing ancestor for {path}")
        target = target.parent
    stats = os.statvfs(target)
    available = stats.f_bavail * stats.f_frsize
    inodes = stats.f_favail
    problems = []
    if available < required_bytes:
        problems.append(f"{path}: insufficient disk space ({available // MIB} MiB available; {required_bytes // MIB} MiB required)")
    if stats.f_files and inodes < required_inodes:
        problems.append(f"{path}: insufficient free filesystem inodes ({inodes} available; {required_inodes} required)")
    return available, inodes, problems


def decode_mountinfo_path(value):
    """Decode the octal escapes used for mountinfo path fields."""
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), value)


def staging_directory(environ=None):
    """Return the configured staging directory using installer precedence."""
    environ = os.environ if environ is None else environ
    raw = environ.get('ASR_STAGE_DIR') or environ.get('TMPDIR') or '/var/tmp'
    target = Path(raw)
    if not target.is_absolute() or not target.is_dir():
        raise ValueError('ASR_STAGE_DIR/TMPDIR must be an existing absolute directory')
    return str(target.resolve())


def temporary_mount_problems(path='/tmp', mountinfo=None):
    """Report temporary filesystems that cannot safely stage executable installers."""
    if mountinfo is None:
        try:
            mountinfo = Path('/proc/self/mountinfo').read_text()
        except OSError:
            return ['Cannot inspect temporary filesystem mount options']
    target = str(Path(path).resolve())
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split(' - ', 1)
        if len(fields) != 2:
            continue
        left = fields[0].split()
        if len(left) < 6:
            continue
        try:
            mount_id = int(left[0])
        except ValueError:
            continue
        mountpoint = decode_mountinfo_path(left[4])
        if target == mountpoint or target.startswith(mountpoint.rstrip('/') + '/'):
            mounts.append((len(mountpoint), mount_id, set(left[5].split(',')), mountpoint))
    if not mounts:
        return ['Cannot identify temporary filesystem mount']
    # Mounts can be stacked at an identical mountpoint (for example, an
    # executable bind over a noexec tmpfs). The visible/topmost mount has the
    # newest mount ID; do not reject options from an obscured lower mount.
    _, _, options, mountpoint = max(mounts, key=lambda item: (item[0], item[1]))
    if 'noexec' in options:
        return [f'{path}: temporary filesystem {mountpoint} is mounted noexec; installer scripts or binaries staged there may fail (configure a sufficiently large executable staging directory)']
    return []


def package_health(run=subprocess.run):
    """Reject unfinished Debian package transactions before apt mutations."""
    if not Path('/var/lib/dpkg/status').is_file():
        return ['Debian package database missing: /var/lib/dpkg/status']
    try:
        result = run(['dpkg', '--audit'], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f'Cannot audit Debian package state ({type(exc).__name__})']
    if result.returncode != 0 or result.stdout.strip() or result.stderr.strip():
        return ['Debian package state requires repair (dpkg --audit); resolve unfinished package operations before installing ASR']
    return []


def supported_os(contents):
    fields = {}
    for line in contents.splitlines():
        key, _, value = line.partition('=')
        if key in ('ID', 'ID_LIKE'):
            fields[key] = value.strip().strip('"').lower()
    family = set((fields.get('ID', '') + ' ' + fields.get('ID_LIKE', '')).split())
    return bool(family.intersection({'debian', 'ubuntu'}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload', type=Path, required=True)
    args = parser.parse_args()
    if not args.payload.is_dir():
        parser.error('installer payload is missing')
    size = payload_bytes(args.payload)
    # Reserve room for a staged copy, rollback copy, package downloads, and
    # temporary files; these are conservative estimates, not exact forecasts.
    # Installer and bootstrap stage release assets on the persistent filesystem.
    # /tmp is frequently a 128-MiB noexec tmpfs on ASL3 nodes; do not require
    # 128 MiB free there when no installer staging uses it.
    try:
        staging = staging_directory()
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    requirements = {'/opt/allscan-reimagined': max(256 * MIB, size * 3),
                    '/root/allscan-reimagined-backups': max(128 * MIB, size * 2),
                    staging: max(128 * MIB, size)}
    # Multiple install paths commonly share the root filesystem. Sum their
    # reservations so each individually passing check cannot overcommit it.
    grouped = {}
    for path, needed in requirements.items():
        ancestor = Path(path)
        while not ancestor.exists():
            ancestor = ancestor.parent
        device = ancestor.stat().st_dev
        group = grouped.setdefault(device, {"paths": [], "required": 0, "ancestor": ancestor})
        group["paths"].append(path)
        group["required"] += needed
    problems = []
    for group in grouped.values():
        available, inodes, issues = check(group["ancestor"], group["required"], 1024 * len(group["paths"]))
        print(f"ASR preflight {', '.join(group['paths'])}: {available // MIB} MiB available, {inodes} inodes; combined estimated minimum {group['required'] // MIB} MiB")
        problems.extend(issues)
    try:
        meminfo = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('MemAvailable', 'SwapFree'):
                meminfo[key] = int(value.strip().split()[0]) // 1024
        print(f"ASR preflight memory: {meminfo.get('MemAvailable', 'unknown')} MiB available; swap free: {meminfo.get('SwapFree', 'unknown')} MiB")
    except (OSError, ValueError, IndexError):
        print('ASR preflight: memory information unavailable', file=sys.stderr)
    # This installer targets Debian-family ASL3 hosts. Fail before package
    # operations on an incompatible distribution rather than invoking apt.
    try:
        if not supported_os(Path('/etc/os-release').read_text()):
            problems.append('Unsupported operating system: Debian-family Linux required')
    except OSError:
        problems.append('Cannot determine operating system from /etc/os-release')
    problems.extend(temporary_mount_problems(staging))
    problems.extend(package_health())
    for issue in problems:
        print('ERROR: ' + issue, file=sys.stderr)
    if problems:
        print('ERROR: Resolve the preflight failures before retrying; existing ASR files have not been changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
