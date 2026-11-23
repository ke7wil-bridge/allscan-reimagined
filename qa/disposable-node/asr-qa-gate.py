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

def guest_ipv4s(domain: str) -> tuple[set[str] | None, str]:
    result = call('virsh', '-c', 'qemu:///system', 'domifaddr', domain, '--source', 'agent')
    if result.returncode != 0:
        return None, result.stderr.strip() or 'guest IP query failed'
    addresses: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[2].lower() != 'ipv4':
            continue
        address = fields[3].split('/', 1)[0]
        if address and address != '0.0.0.0' and not address.startswith('127.'):
            addresses.add(address)
    return addresses, ', '.join(sorted(addresses)) or 'no non-loopback IPv4 address'


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

    target_ips: set[str] = set()
    target_detail = 'disposable guest is not running'
    if running:
        target_ips, target_detail = guest_ipv4s(NAME)
    ip_present = bool(target_ips)
    checks.append({'name': 'disposable-ip-present', 'ok': ip_present, 'detail': target_detail})

    unique = ip_present
    unique_detail = 'not checked; disposable IPv4 address unavailable'
    if ip_present:
        domains = call('virsh', '-c', 'qemu:///system', 'list', '--name', '--state-running')
        if domains.returncode != 0:
            unique = False
            unique_detail = domains.stderr.strip() or 'cannot enumerate running libvirt guests'
        else:
            conflicts: list[str] = []
            unknown: list[str] = []
            for domain in domains.stdout.splitlines():
                domain = domain.strip()
                if not domain or domain == NAME:
                    continue
                other_ips, detail = guest_ipv4s(domain)
                if other_ips is None:
                    unknown.append(domain)
                elif target_ips.intersection(other_ips):
                    conflicts.append(f'{domain} ({", ".join(sorted(target_ips.intersection(other_ips)))})')
            if conflicts:
                unique = False
                unique_detail = 'duplicate guest IP: ' + '; '.join(conflicts)
            elif unknown:
                unique = False
                unique_detail = 'cannot verify IP uniqueness; guest agent unavailable: ' + ', '.join(unknown)
            else:
                unique_detail = 'unique: ' + ', '.join(sorted(target_ips))
    checks.append({'name': 'disposable-ip-unique', 'ok': unique, 'detail': unique_detail})
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
