#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import subprocess
spec = importlib.util.spec_from_file_location('qa_gate', Path(__file__).with_name('asr-qa-gate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not module.report([], Path('.'))['releaseQualified']
assert not module.report([{'ok': False}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['ready']
assert module.report([{'ok': True}], Path('.'))['releaseTests'] == 'not-run'
print('Disposable VM fail-closed gate self-test: PASS')
