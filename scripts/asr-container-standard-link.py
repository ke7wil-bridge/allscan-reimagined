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


def recover_owned_links(profile, config, quiesced, *, doorway=owned_doorway,
                        runner_factory=container_runner, sleeper=recovery.time.sleep):
    """Recover opted-in, uniquely owned local Standard links only.

    Called by the host timer under the writer, recovery and config locks.
    This deliberately cannot originate a required remote AllStar link,
    a Net Bridge link, or an arbitrary Asterisk command.
    """
    main_node, targets, _ = recovery.configured_targets(config, quiesced)
    report = {"checked": 0, "reconnected": [], "alreadyLinked": [],
              "pending": [], "unavailable": [], "failed": [], "manualHeldNodes": sorted(recovery.manually_held_nodes(config))}
    for target in targets:
        identity, node = target["id"], target["node"]
        if node == "1999":
            report["unavailable"].append(node)
            continue
        try:
            if doorway(profile, identity) != (main_node, node):
                raise RuntimeError("Standard Bridge ownership changed")
            runner = runner_factory(profile, identity, main_node, node)
            local = recovery.listed_local_nodes(runner(["rpt", "localnodes"]))
            if not {main_node, node} <= local:
                report["unavailable"].append(node)
                continue
            def link_state():
                return recovery.verified_link_state(runner(["rpt", "lstats", main_node]), node)
            report["checked"] += 1
            state = link_state()
            if state == "established":
                report["alreadyLinked"].append(node)
                continue
            if state == "pending":
                report["pending"].append(node)
                continue
            for source, destination in ((main_node, node), (node, main_node)):
                runner(["rpt", "cmd", source, "ilink", "3", destination])
                for _ in range(6):
                    if link_state() == "established":
                        break
                    sleeper(0.25)
                else:
                    continue
                report["reconnected"].append(node)
                break
            else:
                report["failed"].append(node)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            report["failed"].append(node)
    return report


def recover_periodic(profile):
    # The web request and periodic host service must serialize with each
    # other and with configuration writers. Do not update manualDisconnect.
    with recovery.config_writer_guard(), recovery.recovery_lock(), recovery.config_directory_lock(profile.asr_config):
        config = asterisk.safe_root_json(profile.asr_config / "config.json", profile.client_uid, profile.client_gid)
        return recover_owned_links(profile, config, recovery.load_quiesced_nodes())


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
        if sys.argv[1:] == ['--plan-json']:
            with recovery.recovery_lock():
                asterisk.safe_root_json(config, profile.client_uid, profile.client_gid)
                result = recovery.inspect_native_holds(recovery.read_json_object(config), rpt)
        elif len(sys.argv) == 4 and sys.argv[1] == '--manual-link' and sys.argv[2] in {'connect', 'disconnect'}:
            verb, identity = sys.argv[2:]
            with recovery.config_writer_guard(), recovery.recovery_lock():
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
