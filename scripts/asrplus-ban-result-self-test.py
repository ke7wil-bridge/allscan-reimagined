#!/usr/bin/env python3
"""Offline checks for ban-result request validation and response handling."""
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('asrplus_control', root / 'asrplus-control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def invoke(request, exists=False, record=None):
    output = io.StringIO()
    with patch.object(control.sys, 'stdin', io.StringIO(json.dumps(request))), \
         patch.object(control.sys, 'stdout', output), \
         patch.object(Path, 'is_file', return_value=exists), \
         patch.object(Path, 'read_text', return_value=json.dumps(record or {})):
        code = control.main()
    return code, json.loads(output.getvalue())

identifier = 'a' * 32
for hub in ('phoenix', 'arizona-hub', 'georgia', 'kentucky-hub', 'texas', 'texas-hub'):
    code, response = invoke({'hub': hub, 'action': 'ban-result', 'id': identifier})
    assert code == 0 and response == {'ok': True, 'pending': True}, (hub, response)
    code, response = invoke({'hub': hub, 'action': 'ban-result', 'id': identifier}, True,
                            {'id': identifier, 'hub': 'phoenix' if hub in ('phoenix', 'arizona-hub') else 'georgia' if hub in ('georgia', 'kentucky-hub') else 'texas', 'localNode': control.HUBS[hub], 'action': 'ban-list', 'remoteResult': {'ok': True}})
    assert code == 0 and response['result'] == {'ok': True}, (hub, response)
for identifier_bad in ('', '../x', 'A' * 32, 'z' * 32, 'a' * 31, 'a' * 33):
    code, response = invoke({'hub': 'phoenix', 'action': 'ban-result', 'id': identifier_bad})
    assert code != 0 and response.get('ok') is False, (identifier_bad, response)
for hub in ('masterhub', 'kentucky'):
    code, response = invoke({'hub': hub, 'action': 'ban-result', 'id': identifier})
    assert code != 0 and response.get('ok') is False, (hub, response)
code, response = invoke({'hub': 'phoenix', 'action': 'ban-result', 'id': identifier}, True,
                        {'id': identifier, 'hub': 'phoenix', 'localNode': '641894', 'action': 'connect', 'remoteResult': {'ok': True}})
assert code != 0 and response.get('ok') is False, response
for changed in ({'id': 'b' * 32}, {'hub': 'georgia'}, {'localNode': '674980'}, {'remoteResult': 'invalid'}):
    record = {'id': identifier, 'hub': 'phoenix', 'localNode': '641894',
              'action': 'ban-list', 'remoteResult': {'ok': True}} | changed
    code, response = invoke({'hub': 'phoenix', 'action': 'ban-result', 'id': identifier}, True, record)
    assert code != 0 and response.get('ok') is False, (changed, response)
print('ASR Plus moderation result lookup validation: PASS')
