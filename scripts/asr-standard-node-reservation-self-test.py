#!/usr/bin/env python3
"""Offline Standard/Net Bridge node reservation regression tests (#130)."""
import importlib.util
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location('reserve_' + name, HERE / f'asr-bridge-setup-{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

core, digital, dmr, m17, zello = (load(n) for n in ('core', 'digital', 'dmr', 'm17', 'zello'))
assert core.allocate_node(range(1001, 1998)) == 1998
for end in (1998, 1999):
    try:
        core.allocate_node(range(1001, 1999), end=end)
    except core.ProvisioningError:
        pass
    else:
        raise AssertionError('automatic assignment used reserved node 1999')

cases = [(digital, digital.DigitalSettings(mode, mode + '_audit', 'N0CALL', 1234567, 15846, '127.0.0.1', 41400)) for mode in digital.MODES]
cases += [(dmr, dmr.DmrSettings('dmr_audit', 'N0CALL', 1234567, 12345)),
          (m17, m17.M17Settings('m17_audit', 'N0CALL', 'M17-TST', '127.0.0.1', 17000, 'A')),
          (zello, zello.ZelloSettings('zello_audit', 'test', 'test', 'test'))]

def reject(action, expected):
    try:
        action()
    except (digital.PlanError, dmr.PlanError, m17.InstallError, zello.PlanError) as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError('accepted conflicting node assignment')

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    config = root / 'etc/allscan-reimagined/config.json'
    for path, data in [('/etc/allscan-reimagined/config.json', json.dumps({'node': '600000', 'bridges': []})),
                       ('/etc/asterisk/rpt.conf', '[nodes]\n600000 = radio@127.0.0.1/600000,NONE\n[600000]\n'),
                       ('/etc/asterisk/modules.conf', 'load = app_rpt.so\n'), ('/etc/os-release', 'ID=debian\n'),
                       ('/usr/local/sbin/allscan-reimagined-m17-bridge-control', '#!/bin/sh\n'),
                       ('/usr/local/sbin/allscan-reimagined-m17-usrp-connector', '#!/bin/sh\n'),
                       ('/etc/systemd/system/allscan-reimagined-m17-bridge@.service', '[Service]\n'),
                       ('/usr/lib/asterisk/modules/chan_usrp.so', 'test')]:
        p = root / path.lstrip('/'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data); p.chmod(0o755)
    for module, settings in cases:
        assert module.plan(root, replace(settings, bridge_node=1998))['bridgeNode'] == 1998
        reject(lambda: module.plan(root, replace(settings, bridge_node=1999)), 'reserved for the Unified Net Bridge')
        if module is zello:
            continue
        net = replace(settings, card_type='m17_net') if module is m17 else replace(settings, bridge_role='net')
        assert module.plan(root, net)['bridgeNode'] == 1999
        # Intentional Net sharing cannot mask a legacy Standard occupying 1999.
        config.write_text(json.dumps({'node': '600000', 'bridges': [
            {'id': 'legacy_standard', 'node': '1999', 'mode': 'zello', 'cardType': 'standard'},
            {'id': 'existing_net', 'node': '1999', 'mode': 'ysf', 'cardType': 'ysf_net'}]}))
        reject(lambda: module.plan(root, net), 'occupied by a Standard Bridge')
        config.write_text(json.dumps({'node': '600000', 'bridges': []}))
print('Standard/Net node reservation: PASS (all six protocols, allocator and Net collision guard)')
