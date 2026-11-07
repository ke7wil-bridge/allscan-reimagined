#!/usr/bin/env python3
"""Fail-closed host-side gate for disposable ASR qualification; never marks a release passed."""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

NAME = 'asr-qa-disposable'
TEMPLATE = 'asr-qa-asl3-template'

def call(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=15, check=False)

def check_vm() -> list[dict]:
    checks = []
    result = call('virsh', '-c', 'qemu:///system', 'domstate', NAME)
    running = result.returncode == 0 and result.stdout.strip() == 'running'
    checks.append({'name': 'disposable-running', 'ok': running, 'detail': result.stdout.strip() if result.returncode == 0 else 'not-defined'})
    result = call('virsh', '-c', 'qemu:///system', 'domstate', TEMPLATE)
    stopped = result.returncode == 0 and result.stdout.strip() == 'shut off'
    checks.append({'name': 'template-powered-off', 'ok': stopped, 'detail': result.stdout.strip() if result.returncode == 0 else 'not-defined'})
    result = call('virsh', '-c', 'qemu:///system', 'domblklist', NAME, '--details') if running else None
    base = Path(os.environ.get('ASR_QA_DIR', str(Path.home() / 'vms/asr-qa')))
    disk = str(base / f'{NAME}.qcow2')
    disk_attached = bool(result and result.returncode == 0 and disk in result.stdout)
    checks.append({'name': 'disposable-disk-attached', 'ok': disk_attached, 'detail': 'expected overlay attached' if disk_attached else 'overlay not confirmed'})
    return checks

def report(checks: list[dict], output: Path) -> dict:
    return {'schema': 1, 'generatedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'scope': 'host-readiness-only', 'vm': NAME,
            'ready': all(c['ok'] for c in checks), 'releaseQualified': False,
            'checks': checks, 'releaseTests': 'not-run'}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        checks = check_vm()
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks = [{'name': 'libvirt-query', 'ok': False, 'detail': type(exc).__name__}]
    payload = report(checks, args.output or Path(''))
    encoded = json.dumps(payload, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.is_symlink():
            raise SystemExit('refusing symlink report destination')
        args.output.write_text(encoded)
    sys.stdout.write(encoded)
    return 0 if payload['ready'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
