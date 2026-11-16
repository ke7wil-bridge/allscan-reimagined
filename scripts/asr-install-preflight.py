#!/usr/bin/env python3
"""Read-only resource checks before ASR installer package mutations."""
import argparse
import os
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
    requirements = {'/opt/allscan-reimagined': max(256 * MIB, size * 3),
                    '/root/allscan-reimagined-backups': max(128 * MIB, size * 2),
                    '/tmp': max(128 * MIB, size)}
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
    problems.extend(package_health())
    for issue in problems:
        print('ERROR: ' + issue, file=sys.stderr)
    if problems:
        print('ERROR: Resolve the preflight failures before retrying; existing ASR files have not been changed.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
