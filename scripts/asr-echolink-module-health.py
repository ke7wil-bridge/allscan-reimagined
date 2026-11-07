#!/usr/bin/env python3
"""Read-only EchoLink module readiness check. No reloads or restarts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess

MODULE = 'chan_echolink.so'

def config_state(contents: str) -> str:
    matches = []
    for line in contents.splitlines():
        line = re.split(r'[;#]', line, maxsplit=1)[0].strip()
        match = re.fullmatch(r'(load|noload)\s*=\s*chan_echolink\.so', line, re.I)
        if match: matches.append(match.group(1).lower())
    if 'noload' in matches: return 'disabled'
    if 'load' in matches: return 'enabled'
    return 'automatic-or-unspecified'

def check(modules_conf: Path, module_path: Path, asterisk='asterisk') -> dict:
    try:
        contents = modules_conf.read_text()
        configured = config_state(contents)
    except OSError:
        configured = 'unknown'
    installed = module_path.is_file()
    try:
        proc = subprocess.run([asterisk, '-rx', 'module show like chan_echolink.so'], capture_output=True, text=True, timeout=8)
        loaded = proc.returncode == 0 and bool(re.search(r'(?im)^chan_echolink\.so\s+.*\bRunning\b', proc.stdout))
    except (OSError, subprocess.TimeoutExpired):
        loaded = False
    if not installed: reason = 'EchoLink module binary is missing.'
    elif configured == 'disabled': reason = 'modules.conf disables EchoLink (noload = chan_echolink.so).'
    elif not loaded: reason = 'EchoLink module is not running in Asterisk.'
    else: reason = 'Module running; registration not verified.'
    return {'installed': installed, 'configState': configured, 'loaded': loaded,
            'operational': installed and configured != 'disabled' and loaded,
            'registration': 'not-verified', 'reason': reason,
            'readOnly': True}

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--modules-conf', type=Path, default=Path('/etc/asterisk/modules.conf'))
    p.add_argument('--module-path', type=Path, default=Path('/usr/lib/asterisk/modules/chan_echolink.so'))
    args = p.parse_args()
    print(json.dumps(check(args.modules_conf, args.module_path), indent=2))
