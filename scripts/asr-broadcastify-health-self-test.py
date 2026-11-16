#!/usr/bin/env python3
"""Offline Broadcastify health checks, no real services touched."""
import importlib.util
import os
from pathlib import Path
import tempfile
import subprocess
import sys

spec = importlib.util.spec_from_file_location('broadcastify_health', Path(__file__).with_name('asr-broadcastify-health.py'))
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp); conf = root / 'config'; fifo = root / 'fifo'; conf.mkdir(); fifo.mkdir()
    assert module.snapshot(conf, fifo)['configured'] is False
    (conf / 'BCSTFY.conf').write_text('test')
    os.mkfifo(fifo / 'BCSTFY.fifo')
    mock = root / 'systemctl'
    mock.write_text('#!/bin/sh\necho active\n'); mock.chmod(0o755)
    good = module.snapshot(conf, fifo, str(mock))
    assert good['configured'] and good['streams'][0]['healthy']
    mock.write_text('#!/bin/sh\necho inactive\nexit 3\n')
    assert not module.snapshot(conf, fifo, str(mock))['streams'][0]['healthy']
    mock.write_text('#!/bin/sh\necho active\n')
    (fifo / 'BCSTFY.fifo').unlink()
    assert not module.snapshot(conf, fifo, str(mock))['streams'][0]['healthy']
print('Broadcastify read-only local health: PASS')
