#!/usr/bin/env python3
"""Per-AllScan-user TGIF authentication and connected-session snapshots.

Login secrets are accepted only on stdin and never stored. Root-only TGIF web-session
cookies persist across reboots until the user signs out or TGIF expires the session.
"""
from __future__ import annotations

import argparse
import base64
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

STATE = Path('/var/lib/allscan-reimagined/tgif-users')
RUNTIME = Path('/run/allscan-reimagined/tgif-users')
TOKENS = STATE / 'tokens'
SNAPSHOTS = RUNTIME / 'snapshots'
CHALLENGES = RUNTIME / 'challenges'
TGIF_BASE = 'https://tgif.network'
DEFAULT_TG = ''
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

def challenge_path(user_id: str) -> Path:
    return CHALLENGES / f'{valid_user_id(user_id)}.json'


def ensure_storage() -> None:
    for path in (STATE, TOKENS, RUNTIME, SNAPSHOTS, CHALLENGES):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)


def prior_session_roster(user_id: str, now: int | None = None) -> dict[str, dict]:
    path = snapshot_path(user_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(payload, dict) or not isinstance(payload.get('clients'), list):
        return {}
    rows: dict[str, dict] = {}
    for row in payload['clients']:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get('dmrid') or row.get('id') or row.get('uuid') or '').strip()
        if session_id:
            rows[session_id] = dict(row)
    return rows


def failure_snapshot(user_id: str, callsign: str, talkgroup: str, error: str, now: int | None = None) -> dict:
    current = int(time.time() if now is None else now)
    retained = [row for row in prior_session_roster(user_id, current).values() if str(row.get('talkgroup', '')) == talkgroup]
    return {
        'ok': False, 'configured': True, 'callsign': callsign, 'talkgroup': talkgroup,
        'updatedEpoch': current, 'clients': retained, 'stale': True, 'error': error[:180],
    }


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

def login_to_tgif(callsign: str, secret: str, talkgroup: str, captcha: str = '', challenge: dict | None = None) -> dict:
    jar = http.cookiejar.CookieJar()
    if challenge:
        for item in challenge.get('cookies', []):
            jar.set_cookie(http.cookiejar.Cookie(0, item['name'], item['value'], None, False, item.get('domain') or 'tgif.network', True, False, item.get('path') or '/', True, bool(item.get('secure')), None, True, None, None, {}, False))
        csrf = str(challenge.get('csrf') or '')
    else:
        csrf = ''
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [('User-Agent', USER_AGENT)]
    if not csrf:
        signin = opener.open(TGIF_BASE + '/signin.php', timeout=12).read().decode('utf-8', 'ignore')
        match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)', signin, re.I)
        if not match:
            raise RuntimeError('TGIF sign-in page did not provide a CSRF token')
        csrf = match.group(1)
    fields = {'csrf_token': csrf, 'lcallsign': callsign, 'lpassword': secret}
    if captcha:
        fields['captcha_code1'] = captcha.strip().lower()
    body = urllib.parse.urlencode(fields).encode()
    login_raw = opener.open(TGIF_BASE + '/signin.php', data=body, timeout=12).read().decode('utf-8', 'ignore')
    try:
        login_result = json.loads(login_raw)
    except ValueError as exc:
        raise RuntimeError('TGIF sign-in returned an unexpected response') from exc
    if not isinstance(login_result, dict) or int(login_result.get('code') or 0) != 200:
        detail = str(login_result.get('msg') or 'TGIF rejected the login').strip()
        if 'captcha' in detail.lower():
            captcha_html = opener.open(TGIF_BASE + '/signin.php', data=urllib.parse.urlencode({'captcha': '1'}).encode(), timeout=12).read().decode('utf-8', 'ignore')
            image = re.search(r'<img[^>]+id=["\']captcha1["\'][^>]+src=["\']([^"\']+)', captcha_html, re.I)
            if not image:
                raise RuntimeError('TGIF requested CAPTCHA but did not return an image')
            image_url = urllib.parse.urljoin(TGIF_BASE, image.group(1).replace('&amp;', '&'))
            image_response = opener.open(image_url, timeout=12)
            image_bytes = image_response.read()
            if not image_bytes or len(image_bytes) > 512000:
                raise RuntimeError('TGIF CAPTCHA image was empty or too large')
            content_type = str(image_response.headers.get_content_type() or 'image/png')
            if content_type not in {'image/png', 'image/jpeg', 'image/gif'}:
                content_type = 'image/png'
            captcha_data = 'data:' + content_type + ';base64,' + base64.b64encode(image_bytes).decode('ascii')
            cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path or '/', 'secure': bool(c.secure)} for c in jar]
            return {'captchaRequired': True, 'captchaUrl': captcha_data, 'csrf': csrf, 'cookies': cookies}
        raise RuntimeError('TGIF sign-in failed: ' + detail[:160])
    control_body = urllib.parse.urlencode({'tab': 'tab1', 'data': '', 'tgid': talkgroup}).encode()
    response = opener.open(TGIF_BASE + '/tgcontrol.php', data=control_body, timeout=12)
    control = response.read().decode('utf-8', 'ignore')
    signed_out = bool(re.search(r'name=["\']l(?:callsign|password)["\']', control, re.I))
    if signed_out or '/signin.php' in response.geturl():
        raise RuntimeError('TGIF redirected back to sign-in')
    cookies = []
    now = int(time.time())
    for cookie in jar:
        if cookie.is_expired(now):
            continue
        cookies.append({
            'name': cookie.name,
            'value': cookie.value,
            'domain': cookie.domain,
            'path': cookie.path or '/',
            'secure': bool(cookie.secure),
            'expires': int(cookie.expires or 0),
        })
    if not cookies:
        raise RuntimeError('TGIF sign-in succeeded but no web session cookie was returned')
    return {'cookies': cookies, 'controlUrl': response.geturl()}



def load_token(user_id: str) -> dict:
    path = token_path(user_id)
    if not path.is_file():
        raise RuntimeError('TGIF is not authenticated for this AllScan user')
    data = json.loads(path.read_text(encoding='utf-8'))
    token = str(data.get('token', '')).strip()
    cookies = data.get('cookies')
    if not token and not (isinstance(cookies, list) and cookies):
        raise RuntimeError('TGIF web session is missing')
    return data


def cookie_header(data: dict) -> str:
    cookies = data.get('cookies')
    if not isinstance(cookies, list):
        return ''
    pairs = []
    now = int(time.time())
    for item in cookies:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        value = str(item.get('value') or '')
        expires = int(item.get('expires') or 0)
        if name and value and (expires <= 0 or expires > now):
            pairs.append(f'{name}={value}')
    return '; '.join(pairs)

def current_api_token(data: dict, talkgroup: str) -> str:
    jar = http.cookiejar.CookieJar()
    now = int(time.time())
    for item in data.get('cookies', []):
        if not isinstance(item, dict):
            continue
        expires = int(item.get('expires') or 0)
        if expires > 0 and expires <= now:
            continue
        name = str(item.get('name') or '').strip()
        value = str(item.get('value') or '')
        if not name or not value:
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False,
            str(item.get('domain') or 'tgif.network'), True, False,
            str(item.get('path') or '/'), True, bool(item.get('secure')),
            None, True, None, None, {}, False
        ))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [('User-Agent', USER_AGENT)]
    body = urllib.parse.urlencode({'tab': 'tab1', 'data': '', 'tgid': talkgroup}).encode()
    fragment = opener.open(TGIF_BASE + '/tgcontrol.php', data=body, timeout=12).read().decode('utf-8', 'ignore')
    match = re.search(r'\bapi_token\s*=\s*["\']([^"\']{16,4096})["\']', fragment, re.I)
    if not match:
        raise RuntimeError('TGIF talkgroup session feed did not provide an authentication token')
    return match.group(1)




def update_session_roster(sessions: dict[str, dict], item: dict, talkgroup: str, now: int | None = None) -> str:
    """Apply connection evidence without treating TX state as connection state."""
    if not isinstance(item, dict):
        return 'ignored'
    session_id = str(item.get('uuid', '')).strip()
    if not session_id:
        return 'ignored'
    event = str(item.get('event') or item.get('action') or '').strip().lower()
    connected = item.get('connected')
    explicit_disconnect = event in {'disconnect', 'disconnected', 'remove', 'removed'} or connected is False
    if explicit_disconnect:
        sessions.pop(session_id, None)
        return 'disconnected'
    tg1 = str(item.get('ts1_talkgroup', '')).strip()
    tg2 = str(item.get('ts2_talkgroup', '')).strip()
    has_tg_evidence = bool(tg1 or tg2)
    explicit_connect = event in {'connect', 'connected', 'add', 'added'} or connected is True
    tx_evidence = any(key in item for key in ('tx', 'transmitting', 'talking', 'ptt'))
    if has_tg_evidence and talkgroup not in {tg1, tg2} and explicit_connect:
        sessions.pop(session_id, None)
        return 'disconnected'
    prior = sessions.get(session_id, {})
    if not prior and (not has_tg_evidence or talkgroup not in {tg1, tg2} or ((tx_evidence or str(item.get('state', '')) == '0') and not explicit_connect)):
        return 'talker-only'
    merged = dict(prior)
    merged.update({key: value for key, value in item.items() if value not in (None, '')})
    merged.update({
        'callsign': str(merged.get('callsign') or '').strip()[:20],
        'name': str(merged.get('name') or merged.get('shortname') or '').strip()[:80],
        'dmrid': session_id, 'id': session_id, 'talkgroup': talkgroup,
        'last_seen_epoch': int(time.time() if now is None else now),
        'source': 'TGIF per-user authenticated session',
    })
    sessions[session_id] = merged
    return 'connected'


def enrich_session_transmissions(sessions: dict[str, dict], talkgroup: str, log_dir: Path = Path('/var/log/mmdvm'), now: int | None = None) -> None:
    """Enrich existing connected rows; voice activity never establishes membership."""
    from datetime import datetime, timezone
    current = int(time.time() if now is None else now)
    pattern = re.compile(r'^M: (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.\d+)? DMR Slot [12], received network (?:voice header|late entry) from (\S+) to TG ' + re.escape(talkgroup) + r'\s*$')
    tx = {}
    try:
        logs = sorted(log_dir.glob('MMDVM_Bridge-*.log'), key=lambda p: p.stat().st_mtime)[-2:]
        for path in logs:
            with path.open('rb') as handle:
                size = handle.seek(0, 2)
                handle.seek(max(0, size - 524288))
                if size > 524288:
                    handle.readline()
                lines = handle.read().decode('utf-8', 'replace').splitlines()
            for line in lines:
                match = pattern.fullmatch(line)
                if not match:
                    continue
                epoch = int(datetime.strptime(match[1] + ' ' + match[2], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
                if epoch <= 0 or epoch > current:
                    continue
                key = match[3].upper()
                tx[key] = max(epoch, tx.get(key, 0))
    except (OSError, ValueError):
        pass
    for row in sessions.values():
        call = str(row.get('callsign') or '').strip().upper()
        # A log callsign cannot distinguish multiple sessions of the same callsign.
        epoch = tx.get(call, 0)
        if epoch:
            row['last_tx_epoch'] = max(int(row.get('last_tx_epoch') or 0), epoch)

def collect_user(user_id: str) -> dict:
    import socketio
    data = load_token(user_id)
    cookies = cookie_header(data)
    talkgroup = str(data.get('talkgroup') or '').strip()
    if not re.fullmatch(r'[1-9][0-9]{0,7}', talkgroup):
        raise ValueError('TGIF talkgroup is required; sign in with the configured DMR talkgroup')
    token = current_api_token(data, talkgroup)
    sessions: dict[str, dict] = {key: row for key, row in prior_session_roster(user_id).items() if str(row.get('talkgroup', '')) == talkgroup}
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
        update_session_roster(sessions, item, talkgroup)

    try:
        headers = {'Cookie': cookies} if cookies else None
        sio.connect(TGIF_BASE, headers=headers, transports=['websocket', 'polling'], wait_timeout=10)
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
    enrich_session_transmissions(sessions, talkgroup)
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
                stale = updated <= 0 or updated > int(time.time()) + 300 or int(time.time()) - updated > 45
                payload['stale'] = bool(payload.get('stale')) or stale
                return payload
        except Exception: pass
    return {'ok': True, 'configured': token_file.is_file(), 'callsign': '',
            'talkgroup': DEFAULT_TG, 'updatedEpoch': 0, 'clients': [], 'error': ''}


def login_user(user_id: str, callsign: str, talkgroup: str, secret: str, captcha: str = '') -> dict:
    user_id = valid_user_id(user_id); callsign = valid_callsign(callsign)
    if not re.fullmatch(r'[1-9][0-9]{0,7}', talkgroup): raise ValueError('invalid TGIF talkgroup')
    if not secret: raise ValueError('password is required')
    challenge = None
    if captcha and challenge_path(user_id).is_file():
        challenge = json.loads(challenge_path(user_id).read_text(encoding='utf-8'))
    session = login_to_tgif(callsign, secret, talkgroup, captcha, challenge)
    if session.get('captchaRequired'):
        atomic_json(challenge_path(user_id), {'csrf': session['csrf'], 'cookies': session['cookies'], 'callsign': callsign, 'talkgroup': talkgroup, 'createdEpoch': int(time.time())})
        return {'ok': True, 'configured': False, 'captchaRequired': True, 'captchaUrl': session['captchaUrl'], 'callsign': callsign, 'talkgroup': talkgroup, 'clients': [], 'error': 'TGIF requires CAPTCHA verification.'}
    try: challenge_path(user_id).unlink()
    except FileNotFoundError: pass
    atomic_json(token_path(user_id), {'userId': user_id, 'callsign': callsign,
                'talkgroup': talkgroup, 'cookies': session['cookies'],
                'authenticatedEpoch': int(time.time())})
    try: return collect_user(user_id)
    except Exception as exc:
        payload = failure_snapshot(user_id, callsign, talkgroup, str(exc))
        atomic_json(snapshot_path(user_id), payload); return payload


def logout_user(user_id: str) -> dict:
    for path in (token_path(user_id), snapshot_path(user_id), challenge_path(user_id)):
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
                atomic_json(snapshot_path(path.stem), failure_snapshot(
                    path.stem, str(meta.get('callsign', '')), str(meta.get('talkgroup', DEFAULT_TG)), str(exc)
                ))
            except Exception: pass
    return 0


def self_test() -> None:
    assert valid_user_id('12') == '12'
    assert valid_callsign('ke7wil') == 'KE7WIL'
    sample = {'cookies': [{'name': 'PHPSESSID', 'value': 'abc123', 'expires': 0}]}
    assert cookie_header(sample) == 'PHPSESSID=abc123'
    test_tg = '12345'
    sessions: dict[str, dict] = {}
    for index, call in enumerate(['CLIENT-A', 'CLIENT-B', 'CLIENT-C', 'CLIENT-D', 'CLIENT-E'], 1):
        update_session_roster(sessions, {'uuid': str(index), 'callsign': call, 'state': '1', 'ts2_talkgroup': test_tg}, test_tg, 100)
    assert len(sessions) == 5
    update_session_roster(sessions, {'uuid': '3', 'callsign': 'CLIENT-C', 'state': '1', 'ts2_talkgroup': test_tg, 'tx': True}, test_tg, 101)
    assert len(sessions) == 5 and {row['callsign'] for row in sessions.values()} == {'CLIENT-A','CLIENT-B','CLIENT-C','CLIENT-D','CLIENT-E'}
    update_session_roster(sessions, {'uuid': '3', 'callsign': 'CLIENT-C', 'state': '0', 'ts2_talkgroup': test_tg, 'tx': False}, test_tg, 102)
    assert len(sessions) == 5, 'TX end incorrectly removed a connected client'
    update_session_roster(sessions, {'uuid': '3', 'event': 'disconnect'}, test_tg, 103)
    assert len(sessions) == 4 and all(row['callsign'] != 'CLIENT-C' for row in sessions.values())
    update_session_roster(sessions, {'uuid': '2', 'callsign': 'CLIENT-B', 'state': '1', 'ts2_talkgroup': test_tg, 'tx': True}, test_tg, 104)
    assert len(sessions) == 4 and sessions['2']['callsign'] == 'CLIENT-B'
    # A current-talker-only partial update cannot create or replace the connection snapshot.
    before = dict(sessions)
    assert update_session_roster(sessions, {'uuid': '999', 'callsign': 'TALKER-ONLY', 'state': '1'}, test_tg, 105) == 'talker-only'
    assert sessions == before
    print('per-user TGIF session helper self-test: ok')


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    login = sub.add_parser('login'); login.add_argument('user_id'); login.add_argument('callsign'); login.add_argument('--talkgroup', required=True); login.add_argument('--captcha', default='')
    status = sub.add_parser('status'); status.add_argument('user_id')
    logout = sub.add_parser('logout'); logout.add_argument('user_id')
    collect = sub.add_parser('collect'); collect.add_argument('user_id')
    sub.add_parser('collect-all'); sub.add_parser('self-test')
    args = parser.parse_args()
    if args.command == 'self-test': self_test(); return 0
    if os.geteuid() != 0: parser.error('run as root')
    ensure_storage()
    if args.command == 'login':
        secret = sys.stdin.readline().rstrip('\r\n')
        print(json.dumps(login_user(args.user_id, args.callsign, args.talkgroup, secret, args.captcha), separators=(',', ':'))); return 0
    if args.command == 'status': print(json.dumps(public_status(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'logout': print(json.dumps(logout_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect': print(json.dumps(collect_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect-all': return collect_all()
    return 2


if __name__ == '__main__': raise SystemExit(main())
