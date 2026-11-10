#!/usr/bin/env python3
"""Read-only checks inside Thomas's isolated guest; never print AMI credentials."""
import json
from pathlib import Path
import socket
import subprocess
import sys


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20).strip()


assert socket.gethostname() == 'thomas-asr-qa', 'Wrong guest'
assert 'VERSION_ID="13"' in Path('/etc/os-release').read_text(), 'Expected Debian 13'
assert command('systemctl', 'is-active', 'asterisk') == 'active'
assert command('systemctl', 'is-active', 'apache2') == 'active'
subprocess.run(['sudo', '-u', 'asterisk', 'test', '-r', '/etc/asterisk/manager.conf'], check=True)
assert not command('systemctl', '--failed', '--no-legend'), 'Failed systemd services'
stats = command('asterisk', '-rx', 'rpt stats 1999')
assert 'NODE 1999 STATISTICS' in stats, 'Private node missing'
assert '<NONE>' in stats, 'Unexpected node connections'
assert '0 IAX2 registrations.' in command('asterisk', '-rx', 'iax2 show registry')
credentials = json.loads(Path('/root/asr-qa/ami.json').read_text())
with socket.create_connection(('127.0.0.1', 5038), timeout=5) as sock:
    sock.recv(4096)
    sock.sendall(('Action: Login\r\nUsername: ' + credentials['username'] +
                  '\r\nSecret: ' + credentials['secret'] + '\r\nEvents: off\r\n\r\n').encode())
    response = b''
    while b'\r\n\r\n' not in response:
        part = sock.recv(4096)
        if not part:
            raise RuntimeError('AMI closed before login response')
        response += part
    assert b'Response: Success' in response, 'AMI login failed'
    sock.sendall(b'Action: Logoff\r\n\r\n')
listeners = command('ss', '-lntup')
for line in listeners.splitlines():
    if ':5038 ' in line or ':4569 ' in line:
        assert '127.0.0.1:' in line, 'AMI/IAX listener not private'
offline = '--offline' in sys.argv
if offline:
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=2):
            raise RuntimeError('Unexpected external network access')
    except OSError:
        pass
print(json.dumps({'os': 'Debian 13', 'asterisk': command('asterisk', '-rx', 'core show version'),
                  'node': '1999', 'ami': True, 'externalConnections': 0,
                  'iaxRegistrations': 0, 'failedServices': 0,
                  'egressBlocked': offline,
                  'releaseQualified': False}, indent=2))
