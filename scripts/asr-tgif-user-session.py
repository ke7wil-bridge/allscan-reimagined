#!/usr/bin/env python3
"""Node-wide TGIF authentication and connected-session snapshots.

Login secrets are accepted only on stdin and never stored. Root-only TGIF web-session
cookies persist across reboots until an administrator signs out or TGIF expires them.
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
NODE_SCOPE = 'node'


def valid_user_id(value: str) -> str:
    if value == NODE_SCOPE:
        return value
    if not re.fullmatch(r'[1-9][0-9]{0,9}', value):
        raise ValueError('invalid TGIF session scope')
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


def migrate_legacy_node_session() -> None:
    """Promote the newest legacy per-user session into the one node-wide slot."""
    target = token_path(NODE_SCOPE)
    marker = STATE / '.node-scope-migrated.json'
    if target.is_file():
        if not marker.is_file():
            atomic_json(marker, {'schema': 1, 'migratedEpoch': int(time.time())})
        return
    if marker.is_file():
        return
    candidates = [path for path in TOKENS.glob('*.json') if re.fullmatch(r'[1-9][0-9]{0,9}', path.stem)]
    if not candidates:
        return
    legacy = max(candidates, key=lambda path: path.stat().st_mtime)
    data = json.loads(legacy.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        return
    data['userId'] = NODE_SCOPE
    atomic_json(target, data)
    for directory in (SNAPSHOTS, CHALLENGES):
        source = directory / legacy.name
        destination = directory / f'{NODE_SCOPE}.json'
        if source.is_file() and not destination.exists():
            os.replace(source, destination)
    for candidate in candidates:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        for directory in (SNAPSHOTS, CHALLENGES):
            try:
                (directory / candidate.name).unlink()
            except FileNotFoundError:
                pass
    atomic_json(marker, {'schema': 1, 'migratedEpoch': int(time.time())})


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
    detail = error[:180]
    reauth_required = any(marker in detail.lower() for marker in (
        'authentication was rejected', 'authentication token', 'redirected back to sign-in',
        'web session is missing', 'not authenticated',
    ))
    return {
        'ok': False, 'configured': True, 'callsign': callsign, 'talkgroup': talkgroup,
        'updatedEpoch': current, 'clients': retained, 'stale': True,
        'reauthRequired': reauth_required, 'error': detail,
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
        'source': 'TGIF node-wide authenticated session',
    })
    sessions[session_id] = merged
    return 'connected'


def is_local_bridge_session(item: dict, node_callsign: str) -> bool:
    """Hide this node's own TGIF transport legs from Connected Clients."""
    if not isinstance(item, dict):
        return False
    callsign = str(item.get('callsign') or '').strip().upper()
    if not callsign or callsign != node_callsign.strip().upper():
        return False
    description = ' '.join(str(item.get('description') or '').strip().lower().split())
    return (
        description.startswith(f'{callsign.lower()} asl to dmr b')
        or description.startswith('asr tgif to urf bri')
    )


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
    # The sessions request is a complete current roster. Starting with the prior
    # snapshot retains clients that disappeared without a disconnect event.
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
    callsign = str(data.get('callsign', '')).strip().upper()
    sessions = {
        key: row for key, row in sessions.items()
        if not is_local_bridge_session(row, callsign)
    }
    enrich_session_transmissions(sessions, talkgroup)
    payload = {'ok': True, 'configured': True, 'reauthRequired': False, 'callsign': callsign,
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
                for secret_key in ('token', 'cookies', 'password', 'secret', 'csrf'):
                    payload.pop(secret_key, None)
                payload['configured'] = token_file.is_file()
                updated = int(payload.get('updatedEpoch') or 0)
                stale = updated <= 0 or updated > int(time.time()) + 300 or int(time.time()) - updated > 45
                payload['stale'] = bool(payload.get('stale')) or stale
                return payload
        except Exception: pass
    return {'ok': True, 'configured': token_file.is_file(), 'reauthRequired': False, 'callsign': '',
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
    migrate_legacy_node_session()
    failures = 0
    for path in [token_path(NODE_SCOPE)]:
        if not path.is_file():
            continue
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


# --- Global Ban to TGIF talkgroup ACL ------------------------------------
# ASR Global Ban rules are mirrored onto the signed-in talkgroup's TGIF access
# list ("Deny Access" by DMR ID), so a ban stops the station on the whole
# talkgroup, not only on this node's bridge. Only entries ASR created (label
# prefix + this session's author) are ever changed or removed.
ADMIN_ROOT = Path(os.environ.get('ASR_GLOBAL_BAN_ROOT', '/var/lib/allscan-reimagined/admin'))
TGIF_API = 'https://api.tgif.network'
ACL_LABEL_PREFIX = 'ASR Global Ban: '
ACL_COMMENT = 'Added by AllScan Reimagined Global Ban; removed by ASR on unban or expiry.'
ACL_FLAG_ENABLED, ACL_FLAG_WLBL, ACL_FLAG_UUID, ACL_FLAG_ACCESS = 0x01, 0x02, 0x20, 0x80
ACL_BAN_FLAGS = ACL_FLAG_ENABLED | ACL_FLAG_WLBL | ACL_FLAG_UUID | ACL_FLAG_ACCESS
# Durations TGIF's own panel offers; -1 is permanent. ASR removes the entry
# itself when its ban ends, and re-adds it if a ban outlives the 4-week cap.
ACL_DURATIONS = (900, 3600, 43200, 86400, 259200, 604800, 2419200)
ACL_RECHECK_SECONDS = 600
CALLSIGN_BAN = re.compile(r'(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{3,8}')


def ban_sync_state_path() -> Path:
    return ADMIN_ROOT / 'asr-tgif-bans.json'


def ban_sync_status_path() -> Path:
    return RUNTIME / 'ban-sync.json'


def session_opener(data: dict):
    jar = http.cookiejar.CookieJar()
    now = int(time.time())
    for item in data.get('cookies', []):
        if not isinstance(item, dict):
            continue
        expires = int(item.get('expires') or 0)
        name = str(item.get('name') or '').strip()
        value = str(item.get('value') or '')
        if not name or not value or (expires > 0 and expires <= now):
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, str(item.get('domain') or 'tgif.network'), True, False,
            str(item.get('path') or '/'), True, bool(item.get('secure')), None, True, None, None, {}, False))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [('User-Agent', USER_AGENT)]
    return opener


class TgifAcl:
    """The talkgroup ACL requests that TGIF's own control panel sends."""

    def __init__(self, data: dict):
        self.talkgroup = str(data.get('talkgroup') or '').strip()
        if not re.fullmatch(r'[1-9][0-9]{0,7}', self.talkgroup):
            raise RuntimeError('TGIF talkgroup is not configured')
        self.author = str(data.get('callsign') or '').strip().upper()
        self.opener = session_opener(data)

    def _post(self, fields: list[tuple[str, str]]) -> dict:
        body = urllib.parse.urlencode(fields + [('tgid', self.talkgroup)]).encode()
        raw = self.opener.open(TGIF_BASE + '/tgcontrol.php', data=body, timeout=12).read()
        try:
            payload = json.loads(raw.decode('utf-8', 'ignore'))
        except ValueError:
            raise RuntimeError('TGIF session was rejected; sign in to TGIF again in ASR Settings')
        if not isinstance(payload, dict) or int(payload.get('code') or 0) != 200:
            message = str(payload.get('msg') if isinstance(payload, dict) else '')[:160]
            raise RuntimeError(f'TGIF refused the request{": " + message if message else ""}')
        return payload

    def entries(self) -> list[dict]:
        rows = self._post([('action', 'getAll'), ('page', 'acl')]).get('msg')
        if isinstance(rows, dict):
            rows = list(rows.values())
        return [row for row in rows or [] if isinstance(row, dict) and 'entryid' in row]

    def add_ban(self, ids: list[str], duration: int, label: str, comment: str = ACL_COMMENT) -> None:
        fields = [('action', 'add'), ('flags', str(ACL_BAN_FLAGS)), ('duration', str(duration)),
                  ('label', label), ('comment', comment)]
        fields += [('uuid[]', value) for value in ids]
        self._post(fields)

    def remove(self, entry_id: int) -> None:
        self._post([('action', 'removeEntry'), ('entryid', str(entry_id)), ('page', 'acl')])

    def lookup_ids(self, callsign: str) -> list[str]:
        url = TGIF_API + '/dmr/userdb/' + urllib.parse.quote(callsign)
        payload = json.loads(self.opener.open(url, timeout=12).read().decode('utf-8', 'ignore'))
        if not isinstance(payload, list):
            raise RuntimeError(f'TGIF DMR ID lookup for {callsign} failed')
        ids = []
        for value in payload:
            text = str(value).strip()
            if re.fullmatch(r'[1-9][0-9]{0,8}', text) and text not in ids:
                ids.append(text)
        return ids


def acl_duration(expires_at: int, now: int) -> int:
    if expires_at <= 0:
        return -1
    remaining = max(1, expires_at - now)
    return next((value for value in ACL_DURATIONS if value >= remaining), ACL_DURATIONS[-1])


def desired_tgif_bans(author: str, now: int) -> dict[str, int]:
    """Active Global Ban callsign rules -> expiresAt (0 = permanent)."""
    rules = []
    try:
        lines = (ADMIN_ROOT / 'global-bans.list').read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        token = re.split(r'[\s,]+', line.strip(), maxsplit=1)[0].upper()
        if token and not token.startswith('#'):
            rules.append(token)
    try:
        timed = json.loads((ADMIN_ROOT / 'asr-timed-bans.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        timed = {}
    if not isinstance(timed, dict):
        raise RuntimeError('Timed-ban state is unreadable')
    desired = {}
    for rule in rules:
        # Prefix (*) and numeric node rules have no DMR ID to ban on TGIF.
        if not CALLSIGN_BAN.fullmatch(rule) or rule == author:
            continue
        record = timed.get(rule)
        expires = int(record.get('expiresAt') or 0) if isinstance(record, dict) else 0
        if expires and expires <= now:
            continue
        desired[rule] = expires
    return desired


def read_ban_sync_state() -> dict:
    try:
        payload = json.loads(ban_sync_state_path().read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {'schema': 1, 'rules': {}, 'verifiedAt': 0}
    if not isinstance(payload, dict) or not isinstance(payload.get('rules'), dict):
        return {'schema': 1, 'rules': {}, 'verifiedAt': 0}
    return payload


def ban_sync(user_id: str = NODE_SCOPE, acl: 'TgifAcl | None' = None, now: int | None = None, force: bool = False) -> dict:
    import fcntl
    now = int(time.time()) if now is None else int(now)
    if acl is None:
        try:
            acl = TgifAcl(load_token(user_id))
        except RuntimeError as exc:
            return {'ok': False, 'configured': False, 'error': str(exc)}
    lock = os.open(str(ADMIN_ROOT / '.asr-tgif-bans.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_ban_sync_state()
        records: dict[str, dict] = state['rules']
        desired = desired_tgif_bans(acl.author, now)
        # Callsigns with no DMR ID are looked up again only every recheck.
        no_id = {rule: int(at) for rule, at in (state.get('noDmrId') or {}).items()
                 if rule in desired and now - int(at) < ACL_RECHECK_SECONDS}
        unchanged = (set(desired) - set(no_id) == set(records)
                     and all(int(records[rule].get('expiresAt') or 0) == expires for rule, expires in desired.items() if rule not in no_id)
                     and not any(0 < int(rec.get('tgifExpiresAt') or 0) <= now for rec in records.values()))
        if unchanged and not force and now - int(state.get('verifiedAt') or 0) < ACL_RECHECK_SECONDS:
            return {'ok': True, 'configured': True, 'changed': False, 'cached': True, 'verified': True,
                    'talkgroup': acl.talkgroup, 'rules': sorted(records)}
        result = {'added': [], 'removed': [], 'noDmrId': [], 'errors': []}
        entries = {int(row['entryid']): row for row in acl.entries()}

        def ours(row: dict) -> bool:
            return (str(row.get('label') or '').startswith(ACL_LABEL_PREFIX)
                    and str(row.get('author') or '').strip().upper() == acl.author)

        # Drop entries for rules that were unbanned, expired or re-banned with
        # a different length, then any ASR entry this state does not track.
        keep: set[int] = set()
        handled: set[int] = set()
        for rule in list(records):
            record = records[rule]
            entry_id = int(record.get('entryId') or 0)
            row = entries.get(entry_id)
            if rule in desired and int(record.get('expiresAt') or 0) == desired[rule] and row is not None:
                tgif_expiry = int(row.get('expiration') or 0)
                if tgif_expiry < 0 or tgif_expiry > now:
                    keep.add(entry_id)
                    continue
            if row is not None and ours(row):
                try:
                    acl.remove(entry_id)
                except Exception as exc:
                    result['errors'].append(f'{rule}: {exc}')
                    keep.add(entry_id)
                    continue
                handled.add(entry_id)
            del records[rule]
            if rule not in desired:
                result['removed'].append(rule)
        for entry_id, row in entries.items():
            if entry_id not in keep and entry_id not in handled and ours(row):
                try:
                    acl.remove(entry_id)
                except Exception as exc:
                    result['errors'].append(f'entry {entry_id}: {exc}')

        own_ids: set[str] = set()
        pending = [rule for rule in sorted(desired) if rule not in records and (force or rule not in no_id)]
        if pending and acl.author:
            try:
                own_ids = set(acl.lookup_ids(acl.author))
            except Exception:
                own_ids = set()
        for rule in pending:
            try:
                ids = [value for value in acl.lookup_ids(rule) if value not in own_ids]
                if not ids:
                    result['noDmrId'].append(rule)
                    no_id[rule] = now
                    continue
                label = ACL_LABEL_PREFIX + rule
                before = {int(row['entryid']) for row in acl.entries()}
                acl.add_ban(ids, acl_duration(desired[rule], now), label)
                created = [row for row in acl.entries()
                           if int(row['entryid']) not in before and str(row.get('label') or '') == label]
                if not created:
                    raise RuntimeError('TGIF accepted the ban but it is not on the access list')
                row = max(created, key=lambda item: int(item['entryid']))
                records[rule] = {'entryId': int(row['entryid']), 'ids': ids, 'expiresAt': desired[rule],
                                 'tgifExpiresAt': int(row.get('expiration') or -1), 'syncedAt': now}
                result['added'].append(rule)
            except Exception as exc:
                result['errors'].append(f'{rule}: {exc}')

        # Verify against a fresh read of the access list.
        live = {int(row['entryid']): row for row in acl.entries()}
        wanted = ACL_FLAG_ENABLED | ACL_FLAG_ACCESS
        missing = [rule for rule, rec in records.items()
                   if int(rec['entryId']) not in live
                   or int(live[int(rec['entryId'])].get('flags') or 0) & wanted != wanted]
        result['errors'] += [f'{rule}: TGIF entry is missing or disabled' for rule in missing]
        verified = not result['errors']
        state['verifiedAt'] = now if verified else 0
        state['noDmrId'] = no_id
        atomic_json(ban_sync_state_path(), state)
        status = {'ok': verified, 'configured': True, 'changed': bool(result['added'] or result['removed']),
                  'verified': verified, 'talkgroup': acl.talkgroup, 'rules': sorted(records),
                  'updatedEpoch': now, **result}
        try:
            atomic_json(ban_sync_status_path(), status)
        except OSError:
            pass
        return status
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


# --- DMR Kick on TGIF -----------------------------------------------------
# TGIF has no plain kick: its control panel's own (disabled) "Boot" button is a
# short access-list ban. A timed ACL entry drops the station's live session at
# once, and TGIF deletes the entry itself when it ends, so Kick needs no state.
# The distinct label keeps Global Ban sync from treating it as one of its own.
KICK_LABEL_PREFIX = 'ASR Kick: '
KICK_COMMENT = 'Added by AllScan Reimagined Kick; TGIF removes it automatically when it ends.'
KICK_SECONDS = 900  # TGIF's shortest access-list duration.


def tgif_kick(user_id: str, client_id: str, callsign: str, acl: 'TgifAcl | None' = None,
              now: int | None = None) -> dict:
    """Disconnect one station from the signed-in talkgroup for KICK_SECONDS."""
    now = int(time.time()) if now is None else int(now)
    client_id = str(client_id or '').strip()
    callsign = str(callsign or '').strip().upper()
    if not re.fullmatch(r'[1-9][0-9]{0,8}', client_id):
        return {'ok': False, 'error': 'A current DMR client ID is required.'}
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_.\/-]{0,14}', callsign):
        return {'ok': False, 'error': 'Invalid DMR client callsign.'}
    if acl is None:
        try:
            acl = TgifAcl(load_token(user_id))
        except RuntimeError as exc:
            return {'ok': False, 'configured': False, 'error': str(exc)}
    try:
        if acl.author and callsign == acl.author:
            return {'ok': False, 'error': f'{callsign} owns the TGIF sign-in this node uses and cannot be kicked on TGIF.'}
        # Hold the session ID and, for a hotspot (radio ID + 2-digit ESSID), the
        # radio itself, but never an ID of the account ASR signs in with: the
        # node's own TGIF bridge session uses it.
        ids = [client_id] + ([client_id[:7]] if len(client_id) == 9 else [])
        own = set(acl.lookup_ids(acl.author)) if acl.author else set()
        own_bases = {value[:7] for value in own}
        ids = [value for value in dict.fromkeys(ids) if value not in own and value[:7] not in own_bases]
        if not ids:
            return {'ok': False, 'error': f'DMR ID {client_id} belongs to this node\'s TGIF account and cannot be kicked on TGIF.'}
        label = KICK_LABEL_PREFIX + callsign
        before = {int(row['entryid']) for row in acl.entries()}
        acl.add_ban(ids, KICK_SECONDS, label, KICK_COMMENT)
        created = [row for row in acl.entries()
                   if int(row['entryid']) not in before and str(row.get('label') or '') == label]
        if not created:
            return {'ok': False, 'error': 'TGIF accepted the Kick but it is not on the access list.'}
        row = max(created, key=lambda item: int(item['entryid']))
        expires = int(row.get('expiration') or 0)
        return {'ok': True, 'talkgroup': acl.talkgroup, 'entryId': int(row['entryid']), 'ids': ids,
                'seconds': KICK_SECONDS, 'expiresAt': expires if expires > 0 else now + KICK_SECONDS}
    except Exception as exc:
        return {'ok': False, 'error': f'TGIF Kick failed: {str(exc)[:160]}'}


def tgif_kick_self_test() -> None:
    acl = FakeAcl('KN4EWT', {'KN4EWT': ['3135475', '313547501']}, [])
    acl.now = now = 2_000_000
    # A hotspot session holds the session ID and the radio; the entry ends on its own.
    done = tgif_kick('node', '322885001', 'kf8drj', acl=acl, now=now)
    assert done['ok'] and done['ids'] == ['322885001', '3228850'] and done['expiresAt'] == now + KICK_SECONDS, done
    assert acl.calls == [f'add ASR Kick: KF8DRJ {KICK_SECONDS} 322885001,3228850'], acl.calls
    # A plain radio or app session holds just that ID.
    acl.calls.clear()
    assert tgif_kick('node', '3228850', 'KF8DRJ', acl=acl, now=now)['ids'] == ['3228850']
    # The node's own account, and any ID of it, is never put on the access list.
    acl.calls.clear()
    assert not tgif_kick('node', '3135475', 'KN4EWT', acl=acl, now=now)['ok']
    assert not tgif_kick('node', '313547502', 'W1AW', acl=acl, now=now)['ok']
    assert acl.calls == [], acl.calls
    # Bad input, a TGIF failure, or a missing entry is reported, never claimed.
    assert not tgif_kick('node', 'abc', 'KF8DRJ', acl=acl, now=now)['ok']
    assert not tgif_kick('node', '0', 'KF8DRJ', acl=acl, now=now)['ok']
    acl.add_ban = lambda *args, **kwargs: None
    assert 'not on the access list' in tgif_kick('node', '3228850', 'KF8DRJ', acl=acl, now=now)['error']
    acl.lookup_ids = lambda callsign: (_ for _ in ()).throw(RuntimeError('lookup down'))
    failed = tgif_kick('node', '3228850', 'KF8DRJ', acl=acl, now=now)
    assert not failed['ok'] and 'lookup down' in failed['error'], failed
    # Global Ban sync never removes a Kick entry.
    assert not KICK_LABEL_PREFIX.startswith(ACL_LABEL_PREFIX) and not ACL_LABEL_PREFIX.startswith(KICK_LABEL_PREFIX)
    print('TGIF DMR Kick self-test: ok')


class FakeAcl:
    """In-memory TGIF access list for the self-test."""

    def __init__(self, author: str, userdb: dict[str, list[str]], entries: list[dict] | None = None):
        self.talkgroup, self.author, self.userdb = '12345', author, userdb
        self.rows = list(entries or [])
        self.next_id = 100
        self.now = 0
        self.calls: list[str] = []

    def entries(self):
        return [dict(row) for row in self.rows]

    def add_ban(self, ids, duration, label, comment=None):
        self.calls.append(f'add {label} {duration} {",".join(ids)}')
        self.next_id += 1
        expiry = -1 if duration < 0 else self.now + duration
        self.rows.append({'entryid': self.next_id, 'flags': ACL_BAN_FLAGS, 'label': label, 'author': self.author,
                          'expiration': expiry, 'rules': [{'uuid': int(value)} for value in ids]})

    def remove(self, entry_id):
        self.calls.append(f'remove {entry_id}')
        self.rows = [row for row in self.rows if int(row['entryid']) != entry_id]

    def lookup_ids(self, callsign):
        return list(self.userdb.get(callsign, []))


def ban_sync_self_test(root: Path) -> None:
    global ADMIN_ROOT
    original = ADMIN_ROOT
    ADMIN_ROOT = root / 'admin'
    ADMIN_ROOT.mkdir(parents=True)
    try:
        def policy(rules, timed=None):
            (ADMIN_ROOT / 'global-bans.list').write_text('# Managed\n' + ''.join(r + '\n' for r in rules))
            (ADMIN_ROOT / 'asr-timed-bans.json').write_text(json.dumps(timed or {}))

        manual = {'entryid': 3, 'flags': 179, 'label': 'VK1JTPermanent Ban', 'author': 'KN4EWT', 'expiration': -1}
        acl = FakeAcl('KN4EWT', {'KN4EWT': ['3135475'], 'KF8DRJ': ['3100001', '310000101'], 'N0ID': []}, [manual])
        acl.now = now = 1_000_000
        assert acl_duration(0, now) == -1 and acl_duration(now + 900, now) == 900
        assert acl_duration(now + 3601, now) == 43200 and acl_duration(now + 30 * 86400, now) == 2419200
        # A timed ban is mirrored with every DMR ID; prefix, node and own-callsign rules are not.
        policy(['KF8DRJ', 'N0ID', 'W1*', '674982', 'KN4EWT'], {'KF8DRJ': {'createdAt': now, 'expiresAt': now + 3600}})
        first = ban_sync(acl=acl, now=now)
        assert first['ok'] and first['added'] == ['KF8DRJ'] and first['noDmrId'] == ['N0ID'], first
        assert acl.calls == ['add ASR Global Ban: KF8DRJ 3600 3100001,310000101'], acl.calls
        assert ban_sync(acl=acl, now=now + 30).get('cached')
        # Re-banning permanently replaces the timed entry.
        policy(['KF8DRJ'])
        acl.calls.clear()
        again = ban_sync(acl=acl, now=now + 60)
        assert again['ok'] and acl.calls == ['remove 101', 'add ASR Global Ban: KF8DRJ -1 3100001,310000101'], acl.calls
        # An entry deleted on TGIF by hand is put back while the ASR ban stands.
        acl.rows = [row for row in acl.rows if row['entryid'] != 102]
        acl.calls.clear()
        assert ban_sync(acl=acl, now=now + 120, force=True)['ok']
        assert acl.calls == ['add ASR Global Ban: KF8DRJ -1 3100001,310000101'], acl.calls
        # Unban removes only ASR's entry; the hand-made TGIF entry is never touched.
        policy([])
        acl.calls.clear()
        gone = ban_sync(acl=acl, now=now + 180)
        assert gone['ok'] and gone['removed'] == ['KF8DRJ'] and acl.calls == ['remove 103'], acl.calls
        assert [row['entryid'] for row in acl.rows] == [3]
        # An ASR entry left behind by lost state is cleaned up; another author's look-alike is not.
        acl.rows.append({'entryid': 200, 'flags': ACL_BAN_FLAGS, 'label': ACL_LABEL_PREFIX + 'X1ABC', 'author': 'KN4EWT', 'expiration': -1})
        acl.rows.append({'entryid': 201, 'flags': ACL_BAN_FLAGS, 'label': ACL_LABEL_PREFIX + 'X1ABC', 'author': 'KE7WIL', 'expiration': -1})
        acl.calls.clear()
        assert ban_sync(acl=acl, now=now + 240, force=True)['ok'] and acl.calls == ['remove 200'], acl.calls
        # A timed ban that has ended in ASR is removed even before the expire job runs.
        policy(['KF8DRJ'], {'KF8DRJ': {'createdAt': now, 'expiresAt': now + 1000}})
        acl.now = now + 300
        assert ban_sync(acl=acl, now=now + 300)['added'] == ['KF8DRJ']
        acl.calls.clear()
        ended = ban_sync(acl=acl, now=now + 1001)
        assert ended['removed'] == ['KF8DRJ'] and acl.calls == ['remove 104'], acl.calls
        # A TGIF failure is reported, never claimed as synced.
        policy(['KF8DRJ'])
        acl.lookup_ids = lambda callsign: (_ for _ in ()).throw(RuntimeError('lookup down'))
        failed = ban_sync(acl=acl, now=now + 1100)
        assert not failed['ok'] and not failed['verified'] and 'KF8DRJ: lookup down' in failed['errors'], failed
        assert not ban_sync(acl=acl, now=now + 1110).get('cached'), 'a failed sync must retry'
    finally:
        ADMIN_ROOT = original
    print('TGIF Global Ban sync self-test: ok')


def self_test() -> None:
    tgif_kick_self_test()
    with tempfile.TemporaryDirectory(prefix='asr-tgif-ban-sync-test-') as temp:
        saved_runtime = RUNTIME
        globals()['RUNTIME'] = Path(temp) / 'runtime'
        try:
            ban_sync_self_test(Path(temp))
        finally:
            globals()['RUNTIME'] = saved_runtime
    assert valid_user_id('12') == '12'
    assert valid_user_id(NODE_SCOPE) == NODE_SCOPE
    assert valid_callsign('ke7wil') == 'KE7WIL'
    sample = {'cookies': [{'name': 'PHPSESSID', 'value': 'abc123', 'expires': 0}]}
    assert cookie_header(sample) == 'PHPSESSID=abc123'
    assert failure_snapshot('12', 'KE7WIL', '12345', 'TGIF session authentication was rejected', 100)['reauthRequired']
    assert not failure_snapshot('12', 'KE7WIL', '12345', 'temporary network timeout', 100)['reauthRequired']
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
    assert is_local_bridge_session({'callsign': 'KE7WIL', 'description': 'KE7WIL ASL to DMR B'}, 'KE7WIL')
    assert is_local_bridge_session({'callsign': 'KE7WIL', 'description': 'ASR TGIF to URF bri'}, 'KE7WIL')
    assert not is_local_bridge_session({'callsign': 'N1ABC', 'description': 'N1ABC ASL to DMR B'}, 'KE7WIL')
    assert not is_local_bridge_session({'callsign': 'KE7WIL', 'description': 'Personal hotspot'}, 'KE7WIL')
    original_paths = (STATE, RUNTIME, TOKENS, SNAPSHOTS, CHALLENGES)
    original_login = login_to_tgif
    original_collect = collect_user
    with tempfile.TemporaryDirectory(prefix='asr-tgif-session-test-') as temp:
        root = Path(temp)
        globals()['STATE'] = root / 'state'
        globals()['RUNTIME'] = root / 'runtime'
        globals()['TOKENS'] = STATE / 'tokens'
        globals()['SNAPSHOTS'] = RUNTIME / 'snapshots'
        globals()['CHALLENGES'] = RUNTIME / 'challenges'
        ensure_storage()
        issued = iter(['first-cookie', 'replacement-cookie'])
        globals()['login_to_tgif'] = lambda callsign, secret, talkgroup, captcha='', challenge=None: {
            'cookies': [{'name': 'PHPSESSID', 'value': next(issued), 'domain': 'tgif.network', 'path': '/', 'secure': True, 'expires': 0}],
            'controlUrl': TGIF_BASE + '/tgcontrol.php',
        }
        atomic_json(token_path('12'), {'userId': '12', 'callsign': 'KE7WIL', 'talkgroup': test_tg,
                    'cookies': [{'name': 'PHPSESSID', 'value': 'legacy-cookie', 'expires': 0}]})
        migrate_legacy_node_session()
        assert token_path(NODE_SCOPE).is_file() and load_token(NODE_SCOPE)['userId'] == NODE_SCOPE
        assert not token_path('12').exists() and (STATE / '.node-scope-migrated.json').is_file()
        token_path(NODE_SCOPE).unlink()
        globals()['collect_user'] = lambda user_id: public_status(user_id)
        first = login_user(NODE_SCOPE, 'KE7WIL', test_tg, 'first-password')
        token_file = token_path(NODE_SCOPE)
        token_text = token_file.read_text(encoding='utf-8')
        assert first['configured'] and 'first-password' not in token_text
        assert token_file.stat().st_mode & 0o777 == 0o600
        second = login_user(NODE_SCOPE, 'KE7WIL', test_tg, 'replacement-password')
        replacement_text = token_file.read_text(encoding='utf-8')
        assert second['configured'] and 'replacement-password' not in replacement_text
        assert 'first-cookie' not in replacement_text and 'replacement-cookie' in replacement_text
        globals()['RUNTIME'] = root / 'runtime-after-reboot'
        globals()['SNAPSHOTS'] = RUNTIME / 'snapshots'
        globals()['CHALLENGES'] = RUNTIME / 'challenges'
        ensure_storage()
        atomic_json(snapshot_path(NODE_SCOPE), {'ok': True, 'configured': True, 'updatedEpoch': int(time.time()), 'clients': [], 'cookies': ['must-not-leak'], 'token': 'must-not-leak', 'password': 'must-not-leak'})
        public = public_status(NODE_SCOPE)
        assert public['configured'] and not any(key in public for key in ('cookies', 'token', 'password', 'secret'))
        assert not logout_user(NODE_SCOPE)['configured']
    globals()['STATE'], globals()['RUNTIME'], globals()['TOKENS'], globals()['SNAPSHOTS'], globals()['CHALLENGES'] = original_paths
    globals()['login_to_tgif'] = original_login
    globals()['collect_user'] = original_collect
    print('node-wide TGIF session helper self-test: ok')


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    login = sub.add_parser('login'); login.add_argument('user_id'); login.add_argument('callsign'); login.add_argument('--talkgroup', required=True); login.add_argument('--captcha', default='')
    status = sub.add_parser('status'); status.add_argument('user_id')
    logout = sub.add_parser('logout'); logout.add_argument('user_id')
    collect = sub.add_parser('collect'); collect.add_argument('user_id')
    sub.add_parser('collect-all'); sub.add_parser('self-test')
    sync = sub.add_parser('ban-sync'); sync.add_argument('user_id'); sync.add_argument('--force', action='store_true')
    kick = sub.add_parser('kick'); kick.add_argument('user_id'); kick.add_argument('client_id'); kick.add_argument('callsign')
    args = parser.parse_args()
    if args.command == 'self-test': self_test(); return 0
    if os.geteuid() != 0: parser.error('run as root')
    ensure_storage()
    migrate_legacy_node_session()
    if args.command == 'login':
        secret = sys.stdin.readline().rstrip('\r\n')
        print(json.dumps(login_user(args.user_id, args.callsign, args.talkgroup, secret, args.captcha), separators=(',', ':'))); return 0
    if args.command == 'status': print(json.dumps(public_status(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'logout': print(json.dumps(logout_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect': print(json.dumps(collect_user(args.user_id), separators=(',', ':'))); return 0
    if args.command == 'collect-all': return collect_all()
    if args.command == 'ban-sync':
        result = ban_sync(valid_user_id(args.user_id), force=args.force)
        print(json.dumps(result, separators=(',', ':'))); return 0 if result.get('ok') else 1
    if args.command == 'kick':
        result = tgif_kick(valid_user_id(args.user_id), args.client_id, args.callsign)
        print(json.dumps(result, separators=(',', ':'))); return 0 if result.get('ok') else 1
    return 2


if __name__ == '__main__': raise SystemExit(main())
