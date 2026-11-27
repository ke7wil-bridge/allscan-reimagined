#!/usr/bin/env python3
"""Regression guard for KY2 split-config read-only watcher."""
import importlib.util
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent', Path(__file__).with_name('asrplus-agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    staged = json.loads((Path(__file__).resolve().parents[1] / 'ops' / 'asrplus' /
                         'staging' / 'ky2' / 'asrplus-ky.json').read_text())
    cache = root / 'astapi.json'
    talker = {'node': '12345', 'info': 'N0CALL', 'duration': 3}
    cache.write_text(json.dumps({'updated': time.time(), 'current': {
        str(staged['node']): {'current_talker': talker, 'last_talkers': [talker]}}}))
    cfg = staged | {'talkerStatus': str(root / 'talkers.json'),
                    'talkerEvent': str(root / 'talker-event'),
                    'authoritativeTalkerCache': str(cache)}
    with patch.object(agent.threading, 'Thread') as thread, \
         patch.object(agent.time, 'sleep', side_effect=RuntimeError('one pass')):
        try:
            agent.watch_talkers(cfg)
        except RuntimeError as error:
            assert str(error) == 'one pass'
        payload = json.loads((root / 'talkers.json').read_text())
        assert payload['healthy'] and payload['talker_authority'] == 'asr'
        assert payload['current_talker'] == talker and payload['last_talkers'] == [talker]
        assert (root / 'talker-event').exists()
        assert not thread.called
    cache.write_text(json.dumps({'updated': time.time() - 30, 'current': {
        str(staged['node']): {'current_talker': talker, 'last_talkers': [talker]}}}))
    with patch.object(agent.time, 'sleep', side_effect=RuntimeError('one pass')):
        try:
            agent.watch_talkers(cfg)
        except RuntimeError:
            pass
    payload = json.loads((root / 'talkers.json').read_text())
    assert not payload['healthy'] and payload['talker_authority'] == 'unavailable'
    assert not payload.get('current_talker') and not payload.get('last_talkers')
print('ASR Plus KY2 authoritative relay watcher (no SSH publisher): PASS')
