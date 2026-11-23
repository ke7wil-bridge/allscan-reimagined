#!/usr/bin/env python3
"""Verify deployed-style Python entrypoints can import the shared signing module."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

scripts = Path(__file__).resolve().parent
entries = ('asrplus-agent.py', 'asrplus-control.py', 'asrplus-remote-endpoint.py')
with tempfile.TemporaryDirectory() as directory:
    deployed = Path(directory) / 'sbin'
    deployed.mkdir()
    for name in (*entries, 'asrplus_command_integrity.py'):
        shutil.copy2(scripts / name, deployed / name)
    for name in entries:
        code = ('import importlib.util; '
                f's=importlib.util.spec_from_file_location("entry", {str(deployed / name)!r}); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m)')
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, '-I', '-c',
                                 'import sys; sys.path.insert(0, '+repr(str(deployed))+'); '+code],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 0, (name, result.stderr)
print('ASR Plus deployed-style shared module imports: PASS')
