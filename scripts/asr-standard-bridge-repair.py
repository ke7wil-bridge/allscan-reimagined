#!/usr/bin/env python3
"""Restart only the configured, allowlisted service chain for one Standard Bridge."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

CONFIG = Path('/etc/allscan-reimagined/config.json')
ID_RE = re.compile(r'^[a-z][a-z0-9_-]{1,31}$')
UNIT_RE = re.compile(r'^[A-Za-z0-9_.@:-]+\.service$')


def configured_units(config: dict, bridge_id: str) -> list[str]:
    bridges = config.get('bridges') if isinstance(config.get('bridges'), list) else []
    bridge = next((row for row in bridges if isinstance(row, dict) and row.get('id') == bridge_id), None)
    if not bridge or str(bridge.get('cardType', 'standard')) != 'standard':
        raise ValueError('Configured Standard Bridge was not found.')
    mapping = config.get('bridgeHealthServices') if isinstance(config.get('bridgeHealthServices'), dict) else {}
    units = mapping.get(bridge_id)
    if not isinstance(units, list) or not units:
        raise ValueError('This bridge has no approved repair service chain.')
    clean = []
    for value in units:
        unit = str(value).strip()
        if not UNIT_RE.fullmatch(unit) or unit in clean:
            raise ValueError('Bridge repair service configuration is invalid.')
        clean.append(unit)
    if len(clean) > 8:
        raise ValueError('Bridge repair service chain is too large.')
    return clean


def repair(bridge_id: str) -> dict:
    if os.geteuid() != 0:
        raise PermissionError('Bridge repair must run as root.')
    if not ID_RE.fullmatch(bridge_id):
        raise ValueError('Invalid bridge id.')
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    units = configured_units(config, bridge_id)
    subprocess.run(['systemctl', 'stop', *reversed(units)], check=True, timeout=30)
    for unit in units:
        subprocess.run(['systemctl', 'start', unit], check=True, timeout=30)
    time.sleep(1)
    states = {}
    for unit in units:
        result = subprocess.run(['systemctl', 'is-active', unit], text=True, capture_output=True, timeout=5)
        states[unit] = result.stdout.strip() or 'unknown'
    ok = all(state == 'active' for state in states.values())
    return {'ok': ok, 'bridgeId': bridge_id, 'services': states,
            'message': 'Bridge service chain restarted.' if ok else 'One or more bridge services did not become active.'}


def self_test() -> None:
    fixture = {'bridges': [{'id': 'ysf', 'cardType': 'standard'}],
               'bridgeHealthServices': {'ysf': ['md380-emu-ysf.service', 'analog_bridge_ysf.service']}}
    assert configured_units(fixture, 'ysf') == ['md380-emu-ysf.service', 'analog_bridge_ysf.service']
    for bad in (['bad unit'], ['ssh.service', 'ssh.service'], ['x'] * 9):
        candidate = {**fixture, 'bridgeHealthServices': {'ysf': bad}}
        try:
            configured_units(candidate, 'ysf')
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe repair service list was accepted')
    print('standard bridge repair self-test: ok')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('bridge_id', nargs='?')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if not args.bridge_id:
        parser.error('bridge id is required')
    try:
        payload = repair(args.bridge_id)
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:240]}, separators=(',', ':')))
        return 1
    print(json.dumps(payload, separators=(',', ':')))
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
