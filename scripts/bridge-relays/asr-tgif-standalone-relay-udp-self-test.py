#!/usr/bin/env python3
"""Loopback-only end-to-end TGIF UDP forwarding and policy enforcement test."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

SCRIPT = Path(__file__).with_name('asr-tgif-standalone-relay.py')

def bind():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    s.settimeout(2)
    return s

with tempfile.TemporaryDirectory() as directory:
    upstream, peer, reserve = bind(), bind(), bind()
    upstream_port = upstream.getsockname()[1]
    relay_port = reserve.getsockname()[1]
    reserve.close()
    control = Path(directory) / 'control.json'
    evidence = Path(directory) / 'evidence.json'
    control.write_text(json.dumps({'policy_digest': 'a'*64, 'banned_ids': [], 'kicked_ids': {}, 'kick_tokens': {}}))
    env = dict(os.environ, DMR_CONTROL_FILE=str(control), DMR_EVIDENCE_FILE=str(evidence))
    proc = subprocess.Popen([sys.executable, str(SCRIPT), '--bind-port', str(relay_port),
                             '--peer-port', str(peer.getsockname()[1]), '--upstream', '127.0.0.1',
                             '--upstream-port', str(upstream.getsockname()[1])], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    client = bind()
    try:
        time.sleep(.3)
        assert proc.poll() is None, 'relay exited early'
        client.sendto(b'RPTLtest', ('127.0.0.1', relay_port))
        data, relay_addr = upstream.recvfrom(4096)
        assert data == b'RPTLtest'
        upstream.sendto(b'RPTACK', relay_addr)
        assert peer.recvfrom(4096)[0] == b'RPTACK'
        packet = bytearray(55)
        packet[:4] = b'DMRD'
        packet[5:8] = (1234567).to_bytes(3, 'big')
        packet[8:11] = (67498).to_bytes(3, 'big')
        upstream.sendto(packet, relay_addr)
        assert peer.recvfrom(4096)[0] == packet
        control.write_text(json.dumps({'policy_digest': 'a'*64, 'banned_ids': [1234567], 'kicked_ids': {}, 'kick_tokens': {}}))
        upstream.sendto(packet, relay_addr)
        peer.settimeout(.4)
        try:
            peer.recvfrom(4096)
            raise AssertionError('banned media reached MMDVM')
        except socket.timeout:
            pass
        assert evidence.exists(), 'heartbeat was not generated'
        assert json.loads(evidence.read_text())['appliedPolicyDigest'] == 'a'*64
        # Simulate upstream ICMP port-unreachable without touching real TGIF.
        # Connected UDP on loopback reports ECONNREFUSED after a send to a closed port.
        upstream.close()
        client.sendto(b'MSTPING-outage', ('127.0.0.1', relay_port))
        time.sleep(.25)
        client.sendto(b'MSTPING-refusal', ('127.0.0.1', relay_port))
        time.sleep(.4)
        assert proc.poll() is None, 'upstream refusal terminated local relay'
        # Bind the original upstream port and wait for bounded reconnect.
        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        upstream.bind(('127.0.0.1', upstream_port))
        upstream.settimeout(.5)
        recovered = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            client.sendto(b'RPTL-recovery', ('127.0.0.1', relay_port))
            try:
                data, new_addr = upstream.recvfrom(4096)
                if data == b'RPTL-recovery':
                    upstream.sendto(b'RPTACK-recovered', new_addr)
                    peer.settimeout(1)
                    assert peer.recvfrom(4096)[0] == b'RPTACK-recovered'
                    recovered = True
                    break
            except socket.timeout:
                pass
        assert recovered, 'relay did not recover after upstream UDP refusal'
        print('TGIF standalone loopback UDP forwarding/Ban/outage recovery: PASS')
    finally:
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        client.close(); upstream.close(); peer.close()
