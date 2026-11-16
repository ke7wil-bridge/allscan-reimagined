#!/usr/bin/env python3
"""Fixture-only bridge regressions; no live bridge setup or connections."""
import os
from pathlib import Path
import socket
import subprocess

assert socket.gethostname() == 'thomas-asr-qa', 'Wrong guest'
os.chdir('/home/asrqa/asr-source')
tests = [
    ('asr-bridge-stale-status-self-test.py',),
    ('asr-ysf-bridge-control.py', '--self-test'),
    ('asr-p25-bridge-control.py', 'self-test'),
    ('asr-nxdn-bridge-control.py', 'self-test'),
    ('asr-m17-bridge-control.py', '--self-test'),
    ('asr-m17-usrp-connector.py', '--self-test'),
    ('asr-bridge-setup-dmr-self-test.py',),
    ('asr-bridge-setup-urf-native-self-test.py',),
    ('asr-bridge-lifecycle.py', 'self-test'),
]
for name, *arguments in tests:
    assert Path('scripts', name).is_file(), 'Upload a reviewed source archive first'
    subprocess.run(['python3', 'scripts/' + name, *arguments], check=True, timeout=120)
print('Fixture bridge regressions passed; live protocol/audio testing not performed')
