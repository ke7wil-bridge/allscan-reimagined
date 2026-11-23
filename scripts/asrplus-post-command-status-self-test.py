#!/usr/bin/env python3
"""Ensure confirmed connect/disconnect status is published without live I/O."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent', Path(__file__).with_name('asrplus-agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
for action, rows, expected_pushes in (
    ('connect', [{'node': '12345'}], 1),
    ('disconnect', [], 1),
    ('connect', [], 0),
    ('ban-list', [], 0),
):
    calls = []
    command = {'id': 'a' * 32, 'node': '12345', 'action': action}
    def ssh(_cfg, operation, stdin=''):
        calls.append(operation)
        if operation == 'asrplus-sync':
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'command': command}))
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')
    with patch.object(agent, 'run_ssh', side_effect=ssh), \
         patch.object(agent, 'stamped_status', return_value={'connections': rows}), \
         patch.object(agent, 'execute_once', return_value={'ok': True}), \
         patch.object(agent.time, 'sleep'):
        assert agent.sync_once({'hub': 'phoenix', 'node': '641894'}) == 0
    assert calls.count('asrplus-status') == expected_pushes, (action, calls)
    assert calls.count('asrplus-ack ' + command['id']) == 1
print('ASR Plus post-command status transitions: PASS')
