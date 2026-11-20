#!/usr/bin/env python3
"""Narrow privileged timer action dispatcher; default is dry-run.

Intended for an explicitly reviewed sudoers rule, never a generic shell API.
"""
import argparse
import json
import re
import subprocess
import sys

UNIT_RE = re.compile(r'^[A-Za-z0-9_.@:-]{1,240}\.timer$')
ACTIONS = {'enable', 'disable', 'start', 'stop', 'restart', 'trigger'}
CRITICAL = {'apt-daily.timer', 'apt-daily-upgrade.timer', 'systemd-tmpfiles-clean.timer',
            'fstrim.timer', 'logrotate.timer', 'man-db.timer', 'fwupd-refresh.timer'}


def plan(action, unit, *, confirm='', allow_vendor=False):
    if action not in ACTIONS:
        raise ValueError('Action not allowed')
    if not UNIT_RE.fullmatch(unit) or '..' in unit:
        raise ValueError('Invalid timer unit')
    if unit in CRITICAL:
        raise ValueError('Critical timer is protected')
    if not unit.startswith('allscan-reimagined-') and not allow_vendor:
        raise ValueError('Vendor timer requires explicit separate authorization')
    if confirm != unit:
        raise ValueError('Type the exact timer name to confirm')
    if action == 'trigger':
        return ['systemctl', '--system', 'start', unit[:-6] + '.service']
    return ['systemctl', '--system', action, unit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=sorted(ACTIONS))
    parser.add_argument('unit')
    parser.add_argument('--confirm', default='')
    parser.add_argument('--allow-vendor', action='store_true')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        command = plan(args.action, args.unit, confirm=args.confirm, allow_vendor=args.allow_vendor)
        if args.execute:
            # Execution is intentionally unavailable until a dedicated, reviewed
            # authorization policy and root-owned installation exist. A CLI flag
            # must never become an implicit privilege boundary.
            raise ValueError('Execution disabled pending privileged integration review')
        print(json.dumps({'ok': True, 'dry_run': True, 'command': command}))
        return 0
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
