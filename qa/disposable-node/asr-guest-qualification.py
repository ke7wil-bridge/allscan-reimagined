#!/usr/bin/env python3
"""Read-only initial ASL3 guest qualification; fail closed until install cycles exist."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys

VM = 'asr-qa-disposable'

def run(args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

def check(name, args, expected, timeout=20):
    try:
        p = run(args, timeout)
        ok = p.returncode == 0 and expected in p.stdout
        return {'name': name, 'status': 'PASS' if ok else 'FAIL', 'detail': (p.stdout + p.stderr).strip()[:500]}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {'name': name, 'status': 'FAIL', 'detail': type(e).__name__}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.output.is_symlink():
        ap.error('refusing symlink report')
    checks = [check('disposable-vm-running', ['virsh','-c','qemu:///system','domstate',VM], 'running'),
              check('template-off', ['virsh','-c','qemu:///system','domstate','asr-qa-asl3-template'], 'shut off')]
    try:
        p = run(['virsh','-c','qemu:///system','domifaddr',VM,'--source','lease'])
        import re
        ips = re.findall(r'\b(192\.168\.122\.\d+)/\d+', p.stdout)
        ip = ips[0] if p.returncode == 0 and len(ips) == 1 else None
    except (OSError, subprocess.TimeoutExpired):
        ip = None
    checks.append({'name':'isolated-guest-ip','status':'PASS' if ip else 'FAIL','detail':ip or 'No unique libvirt NAT lease'})
    if ip:
        ssh = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=yes',f'asrqa@{ip}']
        checks.extend([
            check('debian-12',ssh+['. /etc/os-release; echo "$ID $VERSION_ID"'],'debian 12'),
            check('asl3-installed',ssh+['dpkg-query -W -f="${Status}" asl3'],'install ok installed'),
            check('asterisk-inactive',ssh+['systemctl is-active asterisk || true'],'inactive'),
            check('temporary-filesystem',ssh+['df -h /tmp && df -i /tmp && findmnt -T /tmp -no OPTIONS'],'Filesystem'),
        ])
    # No false green: these must be executed by a separate disposable guest
    # installation/upgrade runner, not inferred from self-tests or VM readiness.
    for name in ('fresh-install','upgrade-preserves-config','failed-upgrade-rollback',
                 'reboot-service-health','frontend-version-integrity','bridge-lifecycle-matrix'):
        checks.append({'name':name,'status':'UNTESTED','detail':'requires disposable guest integration run'})
    qualified = all(c['status']=='PASS' for c in checks)
    report={'schema':1,'generatedUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'vm':VM,'releaseQualified':qualified,'checks':checks}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if qualified else 1

if __name__=='__main__':
    sys.exit(main())
