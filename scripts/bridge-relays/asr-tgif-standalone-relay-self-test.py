#!/usr/bin/env python3
"""Offline suppression-evidence regression tests for the standalone TGIF relay."""
import importlib.util
import json
import os
import time
import tempfile
from pathlib import Path

source = Path(__file__).with_name('asr-tgif-standalone-relay.py')
spec = importlib.util.spec_from_file_location('tgif_relay_test', source)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    relay.CONTROL_FILE = str(root / 'control.json')
    relay.EVIDENCE_FILE = str(root / 'evidence.json')
    relay.ROSTER_FILE = str(root / 'roster.json')
    sid, digest, token = '1234567', 'a' * 64, 'b' * 32
    packet = bytearray(55)
    packet[:4] = b'DMRD'
    packet[5:8] = int(sid).to_bytes(3, 'big')
    def policy(**changes):
        value = {'policy_digest': digest, 'banned_ids': [], 'kicked_ids': {}, 'kick_tokens': {}}
        value.update(changes)
        Path(relay.CONTROL_FILE).write_text(json.dumps(value))
    def receipt():
        return json.loads(Path(relay.EVIDENCE_FILE).read_text())
    policy()
    assert relay.admin_allowed(packet, 100)
    assert not Path(relay.EVIDENCE_FILE).exists()
    policy(banned_ids=[int(sid)])
    assert not relay.admin_allowed(packet, 101)
    assert not Path(relay.EVIDENCE_FILE).exists(), 'Ban must not forge Kick evidence'
    policy(kicked_ids={sid: 102}, kick_tokens={sid: token})
    assert not Path(relay.EVIDENCE_FILE).exists(), 'Control update alone must not acknowledge Kick'
    assert not relay.admin_allowed(packet, 103)
    assert receipt()['lastKick'] == {'dmrid': sid, 'token': token}
    assert receipt()['appliedPolicyDigest'] == digest
    first = receipt()['lastKick']
    # A second Kick must not be acknowledged using the first Kick's token.
    next_token = 'c' * 32
    policy(kicked_ids={sid: 104}, kick_tokens={sid: next_token})
    assert receipt()['lastKick'] == first
    assert not relay.admin_allowed(packet, 105)
    assert receipt()['lastKick'] == {'dmrid': sid, 'token': next_token}
    relay.refresh_evidence(106)
    assert receipt()['lastKick'] == {'dmrid': sid, 'token': next_token}
    assert receipt()['heartbeatEpoch'] == 106
    policy(policy_digest='d' * 64)
    relay.refresh_evidence(107)
    assert receipt()['lastKick'] is None, 'policy change must invalidate old Kick receipt'
    assert receipt()['appliedPolicyDigest'] == 'd' * 64
    policy(kicked_ids={sid: 104}, kick_tokens={sid: next_token})
    relay.refresh_evidence(108)
    assert receipt()['lastKick'] is None, 'old receipt must not revive on policy rollback'

    # No receipt is allowed for a different subscriber.
    other = bytearray(packet)
    other[5:8] = (7654321).to_bytes(3, 'big')
    assert relay.admin_allowed(other, 106)
    assert receipt()['lastKick'] is None, 'unrelated traffic cannot restore invalidated receipt'
    assert json.loads(Path(relay.CONTROL_FILE).read_text())['kicked_ids'][sid] == 104
    assert not relay.admin_allowed(packet, 110)
    assert not relay.admin_allowed(packet, 114), 'continuous stream must remain blocked'
    assert relay.admin_allowed(packet, 125), 'quiet gap permits a new stream'
    policy()
    assert relay.admin_allowed(packet, 126), 'Unban / cleared Kick permits traffic'
    Path(relay.CONTROL_FILE).write_text('{bad')
    assert not relay.admin_allowed(packet, 127), 'invalid policy must fail closed'
    policy(policy_digest='invalid')
    before = receipt()
    try:
        relay.refresh_evidence(130)
        raise AssertionError('invalid digest was attested')
    except ValueError:
        pass
    assert receipt() == before
    policy(banned_callsigns=['N1ABC'])
    assert not relay.admin_allowed(packet, 128), 'unsupported callsign bans must fail closed'
    before = receipt()
    try:
        relay.refresh_evidence(131)
        raise AssertionError('unsupported callsign ban was attested')
    except (ValueError, OSError):
        pass
    assert receipt() == before
    Path(relay.ROSTER_FILE).write_text(json.dumps({'clients': [
        {'dmrid': sid, 'callsign': 'N1ABC'}]}))
    os.utime(relay.ROSTER_FILE, (128, 128))
    assert not relay.admin_allowed(packet, 128)
    relay.refresh_evidence(132)
    assert receipt()['appliedPolicyDigest'] == digest
    Path(relay.ROSTER_FILE).write_text(json.dumps({'urf_dmr': [
        {'dmrid': sid, 'callsign': 'N1ABC', 'last_seen_epoch': 1}]}))
    os.utime(relay.ROSTER_FILE, (128, 128))
    policy(banned_callsigns=['OTHER'])
    assert not relay.admin_allowed(packet, 132), 'stale client row cannot bypass callsign policy'
    Path(relay.ROSTER_FILE).write_text(json.dumps({'urf_dmr': [
        {'dmrid': sid, 'callsign': 'N1ABC', 'last_seen_epoch': 132}]}))
    os.utime(relay.ROSTER_FILE, (132, 132))
    policy(banned_callsigns=['OTHER'])
    assert relay.admin_allowed(packet, 132), 'unmatched callsign ban must allow known client'
    policy(banned_prefixes=['N1'])
    assert not relay.admin_allowed(packet, 128), 'matching prefix ban must block media'
    os.utime(relay.ROSTER_FILE, (1, 1))
    assert not relay.admin_allowed(packet, 128), 'stale roster must block restricted traffic'
    before = receipt()
    try:
        relay.refresh_evidence(128)
        raise AssertionError('stale roster was attested')
    except ValueError:
        pass
    assert receipt() == before
    os.utime(relay.ROSTER_FILE, (128, 128))
    policy(banned_prefixes=['N1*'])
    assert not relay.admin_allowed(packet, 128), 'wildcard prefix must block matching callsign'
    for invalid in ({'banned_prefixes': ['*']}, {'banned_prefixes': ['']},
                    {'banned_callsigns': ['N1ABC!']}, {'banned_ids': ['not-a-number']},
                    {'kicked_ids': {'not-a-number': 1}}):
        policy(**invalid)
        assert not relay.admin_allowed(packet, 128), 'invalid policy must fail closed'
        before = receipt()
        try:
            relay.refresh_evidence(128)
            raise AssertionError('invalid rule was attested')
        except ValueError:
            pass
        assert receipt() == before
    policy(kicked_ids={sid: 128}, kick_tokens={sid: 'invalid'})
    before = receipt()
    assert not relay.admin_allowed(packet, 129)
    assert receipt() == before, 'invalid nonce must not create evidence'
    assert [relay.recovery_delay(n) for n in (1, 2, 3, 4, 6, 20)] == [1, 2, 4, 8, 30, 30]
    upstream = relay.open_upstream('127.0.0.1', 62031)
    assert upstream.type == relay.socket.SOCK_DGRAM
    upstream.close()
    print('Standalone TGIF enforcement evidence regression: PASS')
