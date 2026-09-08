#!/usr/bin/env python3
"""Per-AllScan-user TGIF authentication and connected-session snapshots.

Login secrets are accepted only on stdin and never stored. Opaque TGIF session
tokens live only under /run so they disappear on reboot.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

RUNTIME = Path('/run/allscan-reimagined/tgif-users')
TOKENS = RUNTIME / 'tokens'
SNAPSHOTS = RUNTIME / 'snapshots'
TGIF_BASE = 'https://tgif.network'
DEFAULT_TG = '86753'
USER_AGENT = 'AllScan-Reimagined TGIF client tracking'


def valid_user_id(value: str) -> str:
    if not re.fullmatch(r'[1-9][0-9]{0,9}', value):
        raise ValueError('invalid AllScan user id')
    return value

def valid_callsign(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{3,10}', value):
        raise ValueError('invalid callsign')
    return value


def token_path(user_id: str) -> Path:
    return TOKENS / f'{valid_user_id(user_id)}.json'


def snapshot_path(user_id: str) -> Path:
    return SNAPSHOTS / f'{valid_user_id(user_id)}.json'


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, separators=(',', ':'))
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def login_to_tgif(callsign: str, secret: str, talkgroup: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [('User-Agent', USER_AGENT)]
    signin = opener.open(TGIF_BASE + '/signin.php', timeout=12).read().decode('utf-8', 'ignore')
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)', signin, re.I)
    if not match:
        raise RuntimeError('TGIF sign-in page did not provide a CSRF token')
    body = urllib.parse.urlencode({
        'csrf_token': match.group(1),
        'lcallsign': callsign,
        'lpassword': secret,
    }).encode()
    login_raw = opener.open(TGIF_BASE + '/signin.php', data=body, timeout=12).read().decode('utf-8', 'ignore')
    try:
        login_result = json.loads(login_raw)
    except ValueError as exc:
        raise RuntimeError('TGIF sign-in returned an unexpected response') from exc
    if not isinstance(login_result, dict) or int(login_result.get('code') or 0) != 200:
        detail = str(login_result.get('msg') or 'TGIF rejected the login').strip()
        raise RuntimeError('TGIF sign-in failed: ' + detail[:160])
    control_body = urllib.parse.urlencode({'tab': 'tab1', 'data': '', 'tgid': talkgroup}).encode()
    control = opener.open(TGIF_BASE + '/tgcontrol.php', data=control_body, timeout=12).read().decode('utf-8', 'ignore')
    token_patterns = [
        r'\bapi_token\s*=\s*["\']([^"\']{16,2048})["\']',
        r'\bapi[_-]?token\s*[:=]\s*["\']([^"\']{16,2048})["\']',
        r'["\']api[_-]?token["\']\s*:\s*["\']([^"\']{16,2048})["\']',
    ]
    token_match = next((m for pattern in token_patterns if (m := re.search(pattern, control, re.I))), None)
    if not token_match:
        title = re.search(r'<title[^>]*>(.*?)</title>', control, re.I | re.S)
        page = re.sub(r'\s+', ' ', title.group(1)).strip()[:80] if title else 'unknown page'
        signed_out = bool(re.search(r'name=["\']l(?:callsign|password)["\']', control, re.I))
        detail = 'TGIF redirected back to sign-in' if signed_out else 'TGIF control panel no longer exposes a compatible session token'
        raise RuntimeError(f'{detail} ({page})')
    return token_match.group(1)


def load_token(user_id: str) -> dict:
    path = token_path(user_id)
    if not path.is_file():
        raise RuntimeError('TGIF is not authenticated for this AllScan user')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not str(data.get('token', '')).strip():
        raise RuntimeError('TGIF session token is missing')
    return data

def collect_user(user_id: str) -> dict:
    import socketio
    data = load_token(user_id)
    token = str(data['token'])
    talkgroup = str(data.get('talkgroup') or DEFAULT_TG)
    sessions: dict[str, dict] = {}
    authenticated = False
    sio = socketio.Client(logger=False, engineio_logger=False, reconnection=False)

    @sio.event
    def connect():
        sio.emit('handshake', token)

    @sio.on('status')
    def status(value):
        nonlocal authenticated
        if str(value) == '200':
            authenticated = True
            sio.emit('cli-state', {'scope': int(talkgroup), 'page': 'tgadmin', 'action': 'sessions'})

    @sio.on('dmr_session')
    def dmr_session(item):
        if not isinstance(item, dict): return
        session_id = str(item.get('uuid', '')).strip()
        if not session_id: return
        if str(item.get('state', '')) == '0':
            sessions.pop(session_id, None); return
        if str(item.get('ts1_talkgroup', '')) != talkgroup and str(item.get('ts2_talkgroup', '')) != talkgroup:
            sessions.pop(session_id, None); return
        sessions[session_id] = {
            'callsign': str(item.get('callsign') or '').strip()[:20],
            'name': str(item.get('name') or item.get('shortname') or '').strip()[:80],
            'dmrid': session_id,
            'id': session_id,
            'talkgroup': talkgroup,
            'last_seen_epoch': int(time.time()),
            'source': 'TGIF per-user authenticated session',
        }

    try:
        sio.connect(TGIF_BASE, transports=['websocket', 'polling'], wait_timeout=10)
        deadline = time.monotonic() + 3.0
        while sio.connected and time.monotonic() < deadline:
            sio.sleep(0.1)
    finally:
        try: sio.disconnect()
        except Exception: pass
        shutdown = getattr(sio, 'shutdown', None)
        if callable(shutdown):
            try: shutdown()
            except Exception: pass
    if not authenticated:
        raise RuntimeError('TGIF session authentication was rejected')
    payload = {'ok': True, 'configured': True, 'callsign': str(data.get('callsign', '')),
               'talkgroup': talkgroup, 'updatedEpoch': int(time.time()),
               'clients': list(sessions.values()), 'error': ''}
    atomic_json(snapshot_path(user_id), payload)
    return payload

def public_status(user_id: str) -> dict:
    token_file = token_path(user_id)
    snapshot_file = snapshot_path(user_id)
    if snapshot_file.is_file():
        try:
            payload = json.loads(snapshot_file.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.pop('token', None)
                payload['configured'] = token_file.is_file()
                updated = int(payload.get('updatedEpoch') or 0)
                if updated <= 0 or updated > int(time.time()) + 300 or int(time.time()) - updated > 45:
                    payload['clients'] = []
                return payload
        except Exception: pass
    return {'ok': True, 'configured': token_file.is_file(), 'callsign': '',
            'talkgroup': DEFAULT_TG, 'updatedEpoch': 0, 'clients': [], 'error': ''}


def login_user(user_id: str, callsign: str, talkgroup: str, secret: str) -> dict:
    user_id = valid_user_id(user_id); callsign = valid_callsign(callsign)
    if not re.fullmatch(r'[1-9][0-9]{0,7}', talkgroup): raise ValueError('invalid TGIF talkgroup')
    if not secret: raise ValueError('password is required')
    token = login_to_tgif(callsign, secret, talkgroup)
    atomic_json(token_path(user_id), {'userId': user_id, 'callsign': callsign,
                'talkgroup': talkgroup, 'token': token, 'authenticatedEpoch': int(time.time())})
    try: return collect_user(user_id)
    except Exception as exc:
        payload = {'ok': False, 'configured': True, 'callsign': callsign, 'talkgroup': talkgroup,
                   'updatedEpoch': int(time.time()), 'clients': [], 'error': str(exc)[:180]}
        atomic_json(snapshot_path(user_id), payload); return payload


def logout_user(user_id: str) -> dict:
    for path in (token_path(user_id), snapshot_path(user_id)):
        try: path.unlink()
        except FileNotFoundError: pass
    return public_status(user_id)

def collect_all() -> int:
    TOKENS.mkdir(parents=True, exist_ok=True)
    failures = 0
    for path in sorted(TOKENS.glob('*.json')):
        try:
            collect_user(path.stem)
        except Exception as exc:
            failures += 1
            try:
                meta = load_token(path.stem)
                atomic_json(snapshot_path(path.stem), {
                    'ok': False, 'configured': True, 'callsign': str(meta.get('callsign', '')),
                    'talkgroup': str(meta.get('talkgroup', DEFAULT_TG)), 'updatedEpoch': int(time.time()),
                    'clients': [], 'error': str(exc)[:180]})
            except Exception: pass
    return 0


def self_test() -> None:
    assert valid_user_id('12') == '12'
    assert valid_callsign('ke7wil') == 'KE7WIL'
    sample = '<script>var api_token = "' + ('a' * 256) + '";</script>'
    assert re.search(r'\bapi_token\s*=\s*["\']([^"\']{32,1024})["\']', sample).group(1) == 'a' * 256
    print('per-user TGIF session helper self-test: ok')


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    login = sub.add_parser('login'); login.add_argument('user_id'); login.add_argument('callsign'); login.add_argument('--talkgroup', default=DEFAULT_TG)
    status = sub.add_parser('status'); status.add_argument('user_id')
    logout = sub.add_parser('logout'); logout.add_argument('user_id')
    collect = sub.add_parser('collect'); collect.add_argument('user_id')
    sub.add_parser('collect-all'); sub.add_parser('self-test')
    args = parser.parse_args()
    if args.command == 'self-test': self_test(); return 0
    if os.geteuid() != 0: parser.error('run as root')
    if args.command == 'login':
        secret = sys.stdin.readline().rstrip('\r\n')
        print(json.dumps(login_user(args.user_id, args.callsign, args.talkgroup, secret), separators=(',', ':'))); return 0
    if args.command == 'status': print(json.dumps(public_status(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'logout': print(json.dumps(logout_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect': print(json.dumps(collect_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect-all': return collect_all()
    return 2


if __name__ == '__main__': raise SystemExit(main())
