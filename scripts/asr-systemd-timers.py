#!/usr/bin/env python3
"""Read-only inventory for ASR Admin systemd timer management.

No privilege escalation or unit mutation is performed by this module.
"""
import argparse
import json
import re
import subprocess
from typing import Any

UNIT = re.compile(r"^[A-Za-z0-9_.@:-]{1,256}\.timer$")


def validate_unit(name: str) -> str:
    if not isinstance(name, str) or not UNIT.fullmatch(name) or '..' in name or '/' in name:
        raise ValueError('Invalid timer unit name')
    return name


def run_systemctl(args: list[str], *, user: bool = False) -> str:
    cmd = ['systemctl', '--user' if user else '--system', '--no-pager', '--no-legend', *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:400] or 'systemctl failed')
    return result.stdout


def inventory(*, user: bool = False) -> list[dict[str, Any]]:
    # list-unit-files includes inactive/disabled/static units omitted by list-timers.
    files = run_systemctl(['list-unit-files', '--type=timer', '--all', '--output=json'], user=user)
    units = json.loads(files)
    result = []
    for row in units:
        name = row.get('unit_file', '')
        try:
            validate_unit(name)
        except ValueError:
            continue
        result.append({'name': name, 'unitFileState': row.get('state', 'unknown'),
                       'preset': row.get('preset', 'unknown'), 'scope': 'user' if user else 'system'})
    return sorted(result, key=lambda item: item['name'])


def inspect(name: str, *, user: bool = False) -> dict[str, Any]:
    validate_unit(name)
    props = ['Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'FragmentPath',
             'SourcePath', 'DropInPaths', 'Triggers', 'NextElapseUSecRealtime',
             'NextElapseUSecMonotonic', 'LastTriggerUSec', 'Result', 'Description']
    output = run_systemctl(['show', name, '--no-pager', *[f'--property={p}' for p in props]], user=user)
    parsed = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    return {'scope': 'user' if user else 'system', 'name': name,
            'properties': {key: parsed.get(key, '') for key in props}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['list', 'inspect'])
    parser.add_argument('--unit')
    parser.add_argument('--user', action='store_true')
    args = parser.parse_args()
    if args.operation == 'inspect' and not args.unit:
        parser.error('--unit is required for inspect')
    try:
        payload = inventory(user=args.user) if args.operation == 'list' else inspect(args.unit, user=args.user)
        print(json.dumps({'ok': True, 'data': payload}, sort_keys=True))
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
