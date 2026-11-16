#!/usr/bin/env python3
"""Verify concurrent control writers serialize across separate processes."""
import importlib.util
import json
import multiprocessing
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).with_name('asr-dmr-standalone-admin-adapter.py')
def load():
    spec = importlib.util.spec_from_file_location('dmr_admin_lock_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def update(path, key, queue):
    adapter = load()
    with adapter.control_lock(Path(path)):
        value = adapter.control_payload(Path(path))
        queue.put(('entered', key))
        time.sleep(0.15)
        value[key] = True
        adapter.atomic(Path(path), value)

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'control.json'
        path.write_text('{}')
        queue = multiprocessing.Queue()
        workers = [multiprocessing.Process(target=update, args=(str(path), key, queue))
                   for key in ('ban', 'kick')]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(5)
            assert worker.exitcode == 0, 'control writer failed or hung'
        assert json.loads(path.read_text()) == {'ban': True, 'kick': True}, 'concurrent update lost'
        adapter = load()
        evidence = Path(directory) / 'evidence.json'
        digest = 'a' * 64
        adapter.atomic(evidence, {'schema': 1, 'contract': 'dmr-local-media-v1',
                                  'heartbeatEpoch': int(time.time()),
                                  'appliedPolicyDigest': 'b' * 64})
        assert not adapter.capability('test', digest, evidence)['kickClient'], 'wrong digest must disable controls'
        adapter.atomic(evidence, {'schema': 1, 'contract': 'dmr-local-media-v1',
                                  'heartbeatEpoch': int(time.time()),
                                  'appliedPolicyDigest': digest})
        assert adapter.capability('test', digest, evidence)['kickClient'], 'matching evidence must enable controls'
        assert not adapter.capability('test', 'c' * 64, evidence)['kickClient'], 'restart must not trust unrelated policy' 
        print('DMR admin concurrent control writers: PASS')
