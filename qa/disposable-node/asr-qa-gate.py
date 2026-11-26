#!/usr/bin/env python3
"""Fail-closed host-side gate for disposable ASR qualification; never marks a release passed."""
from __future__ import annotations
import argparse
import datetime
import ipaddress
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
    """Query the guest agent, not potentially stale DHCP leases."""
    try:
        result = call('virsh', '-c', 'qemu:///system', 'domifaddr', domain, '--source', 'agent')
    except (OSError, subprocess.TimeoutExpired):
        return None, 'guest agent query unavailable'
    if result.returncode != 0:
        return None, 'guest agent query failed'
    addresses: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        # Match the protocol/address suffix, including continuation rows.
        if len(fields) < 2 or fields[-2].lower() != 'ipv4':
            if any(field.lower() == 'ipv4' for field in fields):
                return None, 'malformed IPv4 row'
            continue
        try:
            address = ipaddress.IPv4Interface(fields[-1]).ip
        except ValueError:
            return None, 'invalid IPv4 address from guest agent'
        if not (address.is_loopback or address.is_unspecified):
            addresses.add(str(address))
    if not addresses:
        return None, 'no verified non-loopback IPv4 address'
    return addresses, ', '.join(sorted(addresses))


def running_domains() -> set[str] | None:
    try:
        result = call('virsh', '-c', 'qemu:///system', 'list', '--name', '--state-running')
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def check_ip_uniqueness(running: bool) -> list[dict]:
    target, detail = guest_ipv4s(NAME) if running else (None, 'disposable guest not running')
    present = {'name': 'disposable-ip-present', 'ok': bool(target), 'detail': detail}
    unique = {'name': 'disposable-ip-unique', 'ok': False, 'detail': 'target IPv4 unavailable'}
    if not target:
        return [present, unique]
    domains = running_domains()
    if domains is None or NAME not in domains:
        unique['detail'] = 'running domain inventory unavailable or target missing'
        return [present, unique]
    conflicts, unknown = [], []
    for domain in sorted(domains - {NAME}):
        peer, _ = guest_ipv4s(domain)
        if peer is None:
            unknown.append(domain)
        elif target & peer:
            conflicts.append(f'{domain} ({", ".join(sorted(target & peer))})')
    if conflicts or unknown:
        reasons = []
        if conflicts:
            reasons.append('duplicate guest IP: ' + '; '.join(conflicts))
        if unknown:
            reasons.append('cannot verify IPv4 uniqueness: ' + ', '.join(unknown))
        unique['detail'] = '; '.join(reasons)
    elif running_domains() != domains or guest_ipv4s(NAME)[0] != target:
        unique['detail'] = 'running guest inventory or target address changed during check; retry'
    else:
        unique.update(ok=True, detail='unique among observed running guests: ' + ', '.join(sorted(target)))
    return [present, unique]


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
    checks.extend(check_ip_uniqueness(running))
    return checks

def report(checks: list[dict], output: Path) -> dict:
    return {'schema': 1, 'generatedUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'scope': 'host-readiness-only', 'vm': NAME,
            'ready': bool(checks) and all(c['ok'] for c in checks), 'releaseQualified': False,
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
