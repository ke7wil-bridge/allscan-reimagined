#!/usr/bin/env python3
"""Read-only ASL3 Broadcastify stream health snapshot; never restarts services."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import stat
import subprocess

STREAM_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

def snapshot(config_dir: Path, fifo_dir: Path, systemctl='systemctl') -> dict:
    streams = []
    if config_dir.is_dir():
        for config in sorted(config_dir.glob('*.conf')):
            name = config.stem
            if not STREAM_ID.fullmatch(name) or config.is_symlink() or not config.is_file():
                continue
            unit = f'asl-broadcastify@{name}.service'
            try:
                result = subprocess.run([systemctl, 'is-active', unit], capture_output=True, text=True, timeout=5)
                service = result.stdout.strip() if result.stdout.strip() else 'unknown'
                active = result.returncode == 0 and service == 'active'
            except (OSError, subprocess.TimeoutExpired):
                service, active = 'unknown', False
            fifo = fifo_dir / f'{name}.fifo'
            try:
                fifo_exists = stat.S_ISFIFO(fifo.lstat().st_mode) and not fifo.is_symlink()
            except OSError:
                fifo_exists = False
            streams.append({'id': name, 'service': unit, 'serviceState': service,
                            'fifoPresent': fifo_exists, 'healthy': active and fifo_exists,
                            'reason': 'ok' if active and fifo_exists else 'service-inactive-or-fifo-missing'})
    return {'schema': 1, 'configured': bool(streams), 'streams': streams,
            'scope': 'read-only-local-process-and-fifo',
            'note': 'Does not verify remote Broadcastify ingest or audio delivery'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-dir', type=Path, default=Path('/etc/asterisk/broadcastify'))
    parser.add_argument('--fifo-dir', type=Path, default=Path('/var/lib/asterisk'))
    args = parser.parse_args()
    print(json.dumps(snapshot(args.config_dir, args.fifo_dir), indent=2))
