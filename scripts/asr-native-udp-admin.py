#!/usr/bin/env python3
"""Verified ASR administration proxy for native UDP digital reflectors."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import tempfile
import time

BRIDGE_ID = os.environ.get("ASR_BRIDGE_ID", "")
MODE = os.environ.get("ASR_BRIDGE_MODE", "").lower()
PUBLIC_HOST = os.environ.get("ASR_PUBLIC_HOST", "0.0.0.0")
PUBLIC_IPV6_HOST = os.environ.get("ASR_PUBLIC_IPV6_HOST", "::")
ENABLE_IPV6 = os.environ.get("ASR_ENABLE_IPV6", "1").lower() not in {"0", "false", "no"}
PUBLIC_PORT = int(os.environ.get("ASR_PUBLIC_PORT", "0"))
UPSTREAM_HOST = os.environ.get("ASR_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("ASR_UPSTREAM_PORT", "0"))
RUNTIME_ROOT = Path(os.environ.get("ASR_RUNTIME_ROOT", "/run/asr-standalone-admin"))
POLICY_DIR = Path(os.environ.get("ASR_GLOBAL_POLICY_DIR", "/var/lib/allscan-reimagined/admin"))
RUNTIME = RUNTIME_ROOT / BRIDGE_ID
BLACKLIST = POLICY_DIR / "global-bans.list"
TIMED_BANS = POLICY_DIR / "asr-timed-bans.json"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
IDENTITY_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.\/-]{0,14}$")
NONCE_RE = re.compile(r"^[a-f0-9]{32}$")
SUPPORTED = {"ysf", "p25", "nxdn", "m17"}
def atomic_json(path, payload, mode=0o640):
    descriptor, temporary = tempfile.mkstemp(prefix=".asr-native-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize(raw):
    value = raw.decode("ascii", "ignore") if isinstance(raw, bytes) else str(raw)
    value = re.sub(r"\s+[A-Z]$", "", value.strip().upper())
    return value if IDENTITY_RE.fullmatch(value) else ""


def decode_m17(raw):
    if len(raw) != 6:
        return ""
    alphabet = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."
    coded = int.from_bytes(raw, "big")
    if coded > 0xF46108FFFFFF:
        return ""
    chars = []
    while coded:
        chars.append(alphabet[coded % 40])
        coded //= 40
    return normalize("".join(chars))
def packet_info(packet):
    if MODE == "ysf" and len(packet) >= 14:
        if packet[:4] == b"YSFP":
            return "connect", normalize(packet[4:14]), b"YSFU" + packet[4:14]
        if packet[:4] == b"YSFU":
            return "unlink", normalize(packet[4:14]), packet[:14]
    elif MODE == "p25" and len(packet) >= 11:
        if packet[0] == 0xF0:
            return "connect", normalize(packet[1:11]), bytes([0xF1]) + packet[1:11]
        if packet[0] == 0xF1:
            return "unlink", normalize(packet[1:11]), packet[:11]
    elif MODE == "nxdn" and len(packet) >= 17:
        if packet[:5] == b"NXDNP":
            return "connect", normalize(packet[5:15]), b"NXDNU" + packet[5:17]
        if packet[:5] == b"NXDNU":
            return "unlink", normalize(packet[5:15]), packet[:17]
    elif MODE == "m17" and len(packet) >= 10:
        command = packet[:4]
        identity = decode_m17(packet[4:10])
        if len(packet) == 11 and command in (b"CONN", b"LSTN"):
            return "connect", identity, b"DISC" + packet[4:10]
        if len(packet) == 10 and command == b"DISC":
            return "unlink", identity, packet[:10]
    return "data", "", b""


def current_policy():
    blacklist = BLACKLIST.read_bytes()
    timed = TIMED_BANS.read_bytes()
    parsed = json.loads(timed.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Global timed-ban policy is invalid.")
    rules = []
    for raw in blacklist.decode("utf-8").splitlines():
        token = raw.strip().split(maxsplit=1)[0] if raw.strip() else ""
        if token and not token.startswith("#"):
            token = token.upper()
            if token.endswith("*"):
                base = token[:-1]
                if IDENTITY_RE.fullmatch(base):
                    rules.append(token)
            elif IDENTITY_RE.fullmatch(token):
                rules.append(token)
    digest = hashlib.sha256(
        b"asr-global-ban-v1\0" + blacklist + b"\0" + timed
    ).hexdigest()
    return sorted(set(rules)), digest


def is_banned(identity, rules):
    return any(
        identity.startswith(rule[:-1]) if rule.endswith("*") else identity == rule
        for rule in rules
    )


class Session:
    def __init__(self, frontend, address, identity, unlink):
        self.frontend = frontend
        self.address = address
        self.identity = identity
        self.unlink = unlink
        self.last_seen = time.monotonic()
        self.upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.upstream.connect((UPSTREAM_HOST, UPSTREAM_PORT))
    def close(self, notify=True):
        if notify and self.unlink:
            try:
                self.upstream.send(self.unlink)
            except OSError:
                pass
        self.upstream.close()


def validate_environment():
    if not ID_RE.fullmatch(BRIDGE_ID):
        raise ValueError("Invalid ASR bridge identifier.")
    if MODE not in SUPPORTED:
        raise ValueError("Unsupported native bridge protocol.")
    if not 1 <= PUBLIC_PORT <= 65535 or not 1 <= UPSTREAM_PORT <= 65535:
        raise ValueError("Invalid native bridge UDP port.")
    if PUBLIC_PORT == UPSTREAM_PORT:
        raise ValueError("Public and reflector UDP ports must differ.")


def main():
    validate_environment()
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(RUNTIME, 0o755)
    public = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    public.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    public.bind((PUBLIC_HOST, PUBLIC_PORT))
    publics = [public]
    if ENABLE_IPV6:
        public6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        public6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        public6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        public6.bind((PUBLIC_IPV6_HOST, PUBLIC_PORT))
        publics.append(public6)
    sessions = {}
    rules, applied, healthy = [], "", False
    policy_stamp = None
    last_manifest = 0.0
    print(
        f"ASR {MODE.upper()} admin proxy active on {len(publics)} frontend(s): "
        f"{PUBLIC_PORT} to {UPSTREAM_HOST}:{UPSTREAM_PORT}",
        flush=True,
    )
    def drop(address, notify=True):
        session = sessions.pop(address, None)
        if session is None:
            return 0
        session.close(notify)
        return 1

    def drop_identity(identity):
        return sum(
            drop(address)
            for address, session in list(sessions.items())
            if session.identity == identity
        )

    def reconcile(force=False):
        nonlocal rules, applied, healthy, policy_stamp
        stamp = (BLACKLIST.stat().st_mtime_ns, TIMED_BANS.stat().st_mtime_ns)
        if not force and stamp == policy_stamp:
            return
        updated, digest = current_policy()
        rules, applied, healthy, policy_stamp = updated, digest, True, stamp
        for address, session in list(sessions.items()):
            if is_banned(session.identity, rules):
                drop(address)

    while True:
        now = time.monotonic()
        try:
            reconcile()
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            healthy = False
            if not applied:
                rules = ["*"]
            print(f"Global ban policy unavailable: {exc}", flush=True)

        for path in RUNTIME.glob("request-*.json"):
            response, nonce = None, ""
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
                nonce = str(request["nonce"])
                if not NONCE_RE.fullmatch(nonce) or path.name != f"request-{nonce}.json":
                    raise ValueError("Invalid request nonce.")
                action = request.get("action")
                if (
                    request.get("schema") != 1
                    or request.get("bridgeId") != BRIDGE_ID
                    or request.get("mode") != MODE
                ):
                    raise ValueError("Request targets another bridge.")
                response = {
                    "schema": 1, "nonce": nonce, "bridgeId": BRIDGE_ID,
                    "mode": MODE, "action": action, "ok": False, "verified": False,
                }
                if action == "reconcile":
                    supplied = request.get("policy")
                    reconcile(True)
                    if not isinstance(supplied, dict) or supplied.get("digest") != applied:
                        raise ValueError("Global ban policy changed during reconciliation.")
                    response.update(
                        ok=True, verified=True, appliedPolicyDigest=applied,
                        removed=sum(
                            drop(address)
                            for address, session in list(sessions.items())
                            if is_banned(session.identity, rules)
                        ),
                    )
                elif action == "kick":
                    identity = normalize(request.get("identity", ""))
                    if not identity:
                        raise ValueError("Invalid bridge client identity.")
                    removed = drop_identity(identity)
                    if removed < 1:
                        raise ValueError("Client was no longer connected or could not be kicked.")
                    response.update(
                        ok=True, verified=True, identity=identity, removed=removed,
                        enforcement="disconnect-until-reconnect",
                    )
                else:
                    raise ValueError("Unsupported native bridge administrative action.")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(response, dict) and NONCE_RE.fullmatch(nonce):
                    response["error"] = str(exc)
                else:
                    path.unlink(missing_ok=True)
                    continue
            atomic_json(RUNTIME / f"result-{nonce}.json", response)
            path.unlink(missing_ok=True)

        if now - last_manifest >= 1:
            atomic_json(RUNTIME / "capabilities.json", {
                "schema": 1, "bridgeId": BRIDGE_ID, "mode": MODE,
                "adapter": "asr-native-udp-proxy", "heartbeatEpoch": int(time.time()),
                "enforcementEvidence": "relay-observed-v1",
                "healthy": healthy, "listClients": True,
                "kickClient": healthy, "kickContract": "disconnect-until-reconnect",
                "kickRequiresCurrentSession": True, "kickAllowsImmediateReconnect": True,
                "banClient": healthy, "unbanClient": healthy, "listBans": healthy,
                "banContract": "global-timed-v1", "appliedPolicyDigest": applied,
            }, mode=0o644)
            last_manifest = now

        readable = publics + [session.upstream for session in sessions.values()]
        ready, _, _ = select.select(readable, [], [], 0.1)
        for current in ready:
            if current in publics:
                packet, address = current.recvfrom(2048)
                # RefCheck/DVRef YSF validation uses a stateless YSFS query,
                # not a YSFP client login. Forward the query without creating
                # a client session and return the reply from the public socket.
                if MODE == "ysf" and packet[:4] == b"YSFS":
                    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        probe.settimeout(2.0)
                        probe.connect((UPSTREAM_HOST, UPSTREAM_PORT))
                        probe.send(packet)
                        reply = probe.recv(2048)
                        if reply:
                            current.sendto(reply, address)
                    except OSError as exc:
                        print(f"YSF status probe forwarding failed for {address}: {exc}", flush=True)
                    finally:
                        probe.close()
                    continue
                kind, identity, unlink = packet_info(packet)
                session = sessions.get(address)
                if kind == "connect" and identity:
                    if session is not None and session.identity != identity:
                        drop(address)
                        session = None
                    if not healthy or is_banned(identity, rules):
                        if session is not None:
                            drop(address)
                        continue
                    if session is None:
                        session = Session(current, address, identity, unlink)
                        sessions[address] = session
                if session is None:
                    continue
                if is_banned(session.identity, rules):
                    drop(address)
                    continue
                session.last_seen = now
                try:
                    session.upstream.send(packet)
                except OSError:
                    drop(address, False)
                    continue
                if kind == "unlink":
                    drop(address, False)
                continue

            session = next(
                (item for item in sessions.values() if item.upstream is current), None
            )
            if session is None:
                continue
            try:
                packet = current.recv(2048)
                if packet:
                    session.frontend.sendto(packet, session.address)
            except OSError:
                drop(session.address, False)

        for address, session in list(sessions.items()):
            if now - session.last_seen > 180:
                drop(address, False)


if __name__ == "__main__":
    main()
