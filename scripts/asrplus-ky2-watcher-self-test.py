#!/usr/bin/env python3
"""Regression guard for KY2 split-config read-only watcher."""
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent', Path(__file__).with_name('asrplus-agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    staged = json.loads((Path(__file__).resolve().parents[1] / 'ops' / 'asrplus' /
                         'staging' / 'ky2' / 'asrplus-ky.json').read_text())
    cfg = staged | {'talkerStatus': str(root / 'talkers.json'),
                    'talkerEvent': str(root / 'talker-event')}
    with patch.object(agent, 'node_metadata', return_value={}), \
         patch.object(agent, 'labels', return_value={}), \
         patch.object(agent.threading, 'Thread') as thread:
        state = agent.TalkerState(cfg)
        publisher = agent.EventPublisher(cfg)
        assert not publisher.enabled and not thread.called
        assert state.update(False, {'12345': {'keyed': True}})
        publisher.trigger()
        assert not publisher.pending
        payload = json.loads((root / 'talkers.json').read_text())
        assert payload['healthy'] and payload['talker_sequence'] >= 1
        assert (root / 'talker-event').exists()
        assert state.update(False, {'12345': {'keyed': False}})
        payload = json.loads((root / 'talkers.json').read_text())
        assert payload['last_talkers'][0]['node'] == '12345'
        assert not thread.called
print('ASR Plus KY2 split-config watcher (no SSH publisher): PASS')
