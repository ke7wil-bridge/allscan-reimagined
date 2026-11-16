#!/usr/bin/env python3
"""Read-only post-boot ASR dependency check; never restarts services (#131)."""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path

CORE = ('asterisk.service', 'apache2.service')
TIMERS = ('allscan-reimagined-node-diagnostics.timer', 'allscan-reimagined-release-check.timer')


def inspect(config: dict, runner=subprocess.run) -> dict:
    checks = []
    units = [(unit, 'service') for unit in CORE] + [(unit, 'timer') for unit in TIMERS]
    for unit, kind in units:
        try:
            result = runner(['systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState', '--no-pager'],
                            capture_output=True, text=True, timeout=5, check=False)
            fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            active = fields.get('ActiveState') == 'active'
            loaded = fields.get('LoadState') == 'loaded'
            checks.append({'unit': unit, 'kind': kind, 'ok': result.returncode == 0 and loaded and active,
                           'state': fields.get('ActiveState', 'unknown'), 'loaded': loaded})
        except (OSError, subprocess.TimeoutExpired):
            checks.append({'unit': unit, 'kind': kind, 'ok': False, 'state': 'unavailable', 'loaded': False})
    bridges = config.get('bridges', [])
    if not isinstance(bridges, list):
        return {'ok': False, 'checks': checks, 'error': 'invalid bridge configuration'}
    # Only check services explicitly configured as managed. Never treat
    # operator-disabled or display-only bridges as failed connectivity.
    for bridge in bridges:
        if not isinstance(bridge, dict) or bridge.get('backendMode') != 'managed':
            continue
        mode = str(bridge.get('mode', '')).lower()
        if mode not in {'m17', 'p25', 'nxdn', 'ysf', 'dmr'}:
            continue
        # Bridge runtime unit naming differs by backend; report configured
        # inventory without inventing a systemd unit or network reachability.
        checks.append({'kind': 'bridge-config', 'mode': mode, 'node': str(bridge.get('node', '')),
                       'ok': True, 'state': 'configured-not-connectivity-verified'})
    return {'ok': all(item['ok'] for item in checks), 'checks': checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('/etc/allscan-reimagined/config.json'))
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        if not isinstance(config, dict):
            raise ValueError('invalid configuration object')
    except (OSError, ValueError):
        print(json.dumps({'ok': False, 'error': 'configuration unavailable or invalid'}))
        return 1
    result = inspect(config)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
