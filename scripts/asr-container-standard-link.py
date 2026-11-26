#!/usr/bin/env python3
"""Host-only Standard Bridge link control through the constrained ASR broker."""
from __future__ import annotations
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if not spec or not spec.loader:
        raise RuntimeError('Host bridge module is unavailable.')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

recovery = load('asr_container_standard_recovery', 'asr-fixed-bridge-recovery.py')
asterisk = load('asr_container_standard_asterisk', 'asr-container-asterisk.py')

def owned_doorway(profile, bridge_id, loader=asterisk.safe_root_json, installations=asterisk.INSTALLATIONS):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,31}', bridge_id):
        raise RuntimeError('Invalid Standard Bridge identity.')
    config = loader(profile.asr_config / 'config.json', profile.client_uid, profile.client_gid)
    rows = config.get('bridges', [])
    if not isinstance(rows, list):
        raise RuntimeError('Invalid bridge configuration.')
    matches = [row for row in rows if isinstance(row, dict) and row.get('id') == bridge_id]
    if len(matches) != 1 or matches[0].get('cardType', 'standard') != 'standard':
        raise RuntimeError('Configured Standard Bridge was not found.')
    row = matches[0]
    main, node = str(config.get('node', '')), str(row.get('node', ''))
    if not recovery.NODE_RE.fullmatch(main) or not recovery.NODE_RE.fullmatch(node) or node in {main, '1999'}:
        raise RuntimeError('Invalid or reserved bridge doorway.')
    if sum(isinstance(item, dict) and str(item.get('node', '')) == node for item in rows) != 1:
        raise RuntimeError('Bridge doorway is shared or ambiguous.')
    record = loader(installations / (bridge_id + '.json'))
    if (record.get('bridgeId') != bridge_id or str(record.get('bridgeNode', '')) != node
            or record.get('bridgeType') != row.get('mode')
            or str(record.get('role') or 'standard') != 'standard'):
        raise RuntimeError('Standard Bridge installation ownership could not be verified.')
    return main, node


def container_runner(profile, bridge_id, main, node, execute=subprocess.run):
    def run(arguments):
        command = tuple(arguments)
        allowed = {('rpt', 'localnodes'), ('rpt', 'lstats', main)}
        for source, target in ((main, node), (node, main)):
            for verb in ('3', '11'):
                allowed.add(('rpt', 'cmd', source, 'ilink', verb, target))
        if command not in allowed:
            raise RuntimeError('Standard Bridge command is outside the authorized doorway.')
        if owned_doorway(profile, bridge_id) != (main, node):
            raise RuntimeError('Standard Bridge ownership changed during control.')
        if command != ('rpt', 'localnodes'):
            asterisk.authorize_command(' '.join(command), profile)
        result = execute(['/usr/bin/docker', 'exec', profile.asterisk_container,
                          '/usr/sbin/asterisk', '-rx', ' '.join(command)],
                         capture_output=True, text=True, timeout=5, check=False)
        if result.returncode != 0:
            raise RuntimeError('Asterisk container command failed.')
        return result.stdout
    return run


def main():
    try:
        if os.geteuid() != 0 or Path('/.dockerenv').exists():
            raise RuntimeError('Host Standard Bridge control is unavailable.')
        backend = asterisk.backend()
        profile = backend.load_profile()
        if profile is None:
            raise RuntimeError('Container provisioning profile is unavailable.')
        backend.validate_active_profile(profile)
        config = profile.asr_config / 'config.json'
        rpt = profile.asterisk_config / 'rpt.conf'
        with recovery.recovery_lock():
            if sys.argv[1:] == ['--plan-json']:
                asterisk.safe_root_json(config, profile.client_uid, profile.client_gid)
                result = recovery.inspect_native_holds(recovery.read_json_object(config), rpt)
            elif len(sys.argv) == 4 and sys.argv[1] == '--manual-link' and sys.argv[2] in {'connect', 'disconnect'}:
                verb, identity = sys.argv[2:]
                main_node, node = owned_doorway(profile, identity)
                result = recovery.manual_link(config, rpt, identity, verb,
                                              runner=container_runner(profile, identity, main_node, node))
            else:
                raise RuntimeError('Invalid host Standard Bridge request.')
        print(json.dumps(result)); return 0
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:240]})); return 1

if __name__ == '__main__':
    raise SystemExit(main())
