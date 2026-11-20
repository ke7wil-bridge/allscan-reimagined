#!/usr/bin/env python3
"""Standalone TGIF UDP proxy for MMDVM_Bridge. No URF dependency.

MMDVM handles TGIF authentication and ping; this proxy enforces the
configured talkgroup and prevents unexpected inbound DMR groups.
"""
import argparse
import json
import os
import re
import select
import socket
import tempfile
import time


CONTROL_FILE = os.environ.get("DMR_CONTROL_FILE", "/run/netoholics-native-dmr/dmr-admin.json")
EVIDENCE_FILE = os.environ.get("DMR_EVIDENCE_FILE", "/run/netoholics-native-dmr/dmr-admin-enforcement.json")
ROSTER_FILE = os.environ.get("DMR_ROSTER_FILE", "/run/allscan-reimagined/tgif-users/snapshots/node.json")
ROSTER_MAX_AGE = 30


def _kick_hold_seconds():
    """A Kick refuses the station's media for a bounded hold (disconnect-hold-v1)."""
    try:
        value = int(os.environ.get("DMR_KICK_HOLD_SECONDS", "300"))
    except ValueError:
        value = 300
    return min(3600, max(30, value))


KICK_HOLD_SECONDS = _kick_hold_seconds()


def kick_active(started, epoch):
    return 0 <= epoch - int(started) < KICK_HOLD_SECONDS


def applied_kicks(policy, epoch):
    """Kick tokens this relay is enforcing right now; the adapter's Kick proof."""
    tokens = policy.get("kick_tokens", {})
    return {str(key): tokens[str(key)] for key, started in policy.get("kicked_ids", {}).items()
            if kick_active(started, epoch) and isinstance(tokens.get(str(key)), str)
            and re.fullmatch(r"[a-f0-9]{32}", tokens[str(key)])}


def destination(packet):
    return int.from_bytes(packet[8:11], "big") if packet.startswith(b"DMRD") and len(packet) >= 16 else None


def inbound_allowed(packet, tg, subscriber):
    dest = destination(packet)
    return dest is None or dest == tg or (bool(packet[15] & 0x40) and dest == subscriber)


def source(packet):
    return int.from_bytes(packet[5:8], "big") if packet.startswith(b"DMRD") and len(packet) >= 16 else 0


def write_evidence(payload):
    directory = os.path.dirname(EVIDENCE_FILE)
    os.makedirs(directory, mode=0o750, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dmr-evidence.", dir=directory, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, EVIDENCE_FILE)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


# The adapter owns the control file. The relay never rewrites it: doing so
# could overwrite a simultaneous Ban, Unban, or newer Kick request.
_last_dropped_kick = {}


def supported_policy(payload):
    """Reject malformed rules rather than silently enforcing a subset."""
    if not isinstance(payload, dict):
        return False
    if not all(isinstance(payload.get(field, []), list)
               for field in ("banned_ids", "banned_callsigns", "banned_prefixes")):
        return False
    if not all(isinstance(payload.get(field, {}), dict)
               for field in ("kicked_ids", "kick_tokens")):
        return False
    if any(not str(value).isdigit() or not 0 < int(value) < 2**24
           for value in payload.get("banned_ids", [])):
        return False
    if any(not str(value).isdigit() or not 0 < int(value) < 2**24
           for value in payload.get("kicked_ids", {})):
        return False
    if any(not str(value).isdigit() or not 0 < int(value) < 2**24
           for value in payload.get("kick_tokens", {})):
        return False
    if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9/.-]{0,14}", str(value).strip().upper())
           for value in payload.get("banned_callsigns", [])):
        return False
    if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9/.-]{0,14}\*?", str(value).strip().upper())
           for value in payload.get("banned_prefixes", [])):
        return False
    return True


def read_fresh_roster(now=None):
    """Fail closed when the callsign mapping is missing or too old."""
    epoch = time.time() if now is None else now
    if not (0 <= epoch - os.stat(ROSTER_FILE).st_mtime <= ROSTER_MAX_AGE):
        raise ValueError("DMR roster is stale")
    with open(ROSTER_FILE, "r", encoding="utf-8") as handle:
        roster = json.load(handle)
    if not isinstance(roster, dict):
        raise ValueError("DMR roster is invalid")
    if "updatedEpoch" in roster and (not isinstance(roster["updatedEpoch"], (int, float))
                                     or not 0 <= epoch - roster["updatedEpoch"] <= ROSTER_MAX_AGE):
        raise ValueError("DMR roster snapshot is stale")
    rows = roster.get("clients", roster.get("urf_dmr"))
    if not isinstance(rows, list):
        raise ValueError("DMR roster clients are unavailable")
    return rows


def roster_radio_id(value):
    """Radio ID for a roster row: a 9-digit hotspot ID is the 7-digit radio ID
    plus a 2-digit ESSID, and DMR media always carries the 24-bit radio ID."""
    text = str(value or "")
    if not text.isdigit():
        return None
    number = int(text)
    if number > 0xFFFFFF and len(text) == 9:
        number //= 100
    return number if 0 < number <= 0xFFFFFF else None


def callsign_for_id(sid, now=None):
    """Resolve a unique roster callsign; unknown identities cannot bypass name bans."""
    rows = read_fresh_roster(now)
    epoch = time.time() if now is None else now
    names = {str(row.get("callsign", "")).strip().upper()
             for row in rows if isinstance(row, dict)
             and roster_radio_id(row.get("dmrid") or row.get("id")) == int(sid)
             and ("last_seen_epoch" not in row or
                  (isinstance(row["last_seen_epoch"], (int, float)) and
                   0 <= epoch - row["last_seen_epoch"] <= ROSTER_MAX_AGE))
             and re.fullmatch(r"[A-Z0-9][A-Z0-9/.-]{0,14}", str(row.get("callsign", "")).strip().upper())}
    return next(iter(names)) if len(names) == 1 else None


def admin_allowed(packet, now=None):
    sid = source(packet)
    if not sid:
        return True
    epoch = int(time.time() if now is None else now)
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not supported_policy(payload):
            return False
        banned = {int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()}
        if sid in banned:
            return False
        exact = {str(value).strip().upper() for value in payload.get("banned_callsigns", [])}
        prefixes = {str(value).strip().upper().rstrip("*") for value in payload.get("banned_prefixes", [])}
        if exact or prefixes:
            name = callsign_for_id(sid, epoch)
            if not name or name in exact or any(name.startswith(prefix) for prefix in prefixes):
                return False
        kicked = payload.get("kicked_ids", {})
        if not isinstance(kicked, dict):
            return False
        key = str(sid)
        if key not in kicked or not kick_active(kicked[key], epoch):
            _last_dropped_kick.pop(key, None)
            return True
        _last_dropped_kick[key] = epoch
        return False
    except (OSError, ValueError, TypeError, AttributeError):
        # Never claim successful policy enforcement when the control is unreadable.
        return False


def rewrite_outbound(packet, tg, subscriber, repeater):
    if not packet.startswith(b"DMRD") or len(packet) < 16:
        return packet
    out = bytearray(packet)
    out[5:8] = subscriber.to_bytes(3, "big")
    out[8:11] = tg.to_bytes(3, "big")
    out[11:15] = repeater.to_bytes(4, "big")
    return bytes(out)


def refresh_evidence(now=None):
    """Attest only to a readable, fully supported control policy."""
    with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
        policy = json.load(handle)
    if not supported_policy(policy):
        raise ValueError("relay cannot attest to unsupported policy")
    if policy.get("banned_callsigns") or policy.get("banned_prefixes"):
        read_fresh_roster(now)
    digest = policy.get("policy_digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid policy digest")
    previous = {}
    try:
        with open(EVIDENCE_FILE, "r", encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, ValueError):
        pass
    last_kick = (previous.get("lastKick") if isinstance(previous, dict)
                 and previous.get("appliedPolicyDigest") == digest else None)
    if not (isinstance(last_kick, dict)
            and policy["kick_tokens"].get(last_kick.get("dmrid")) == last_kick.get("token")):
        last_kick = None
    epoch = int(time.time() if now is None else now)
    write_evidence({"schema": 1, "contract": "dmr-local-media-v1",
                    "heartbeatEpoch": epoch,
                    "appliedPolicyDigest": digest, "lastKick": last_kick,
                    "kickHoldSeconds": KICK_HOLD_SECONDS,
                    "appliedKicks": applied_kicks(policy, epoch)})


UPSTREAM_ERRORS = (ConnectionRefusedError, ConnectionResetError, TimeoutError)


def open_upstream(address, port):
    remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        remote.connect((address, port))
    except OSError:
        remote.close()
        raise
    return remote


def recovery_delay(failures):
    return min(30, 2 ** min(max(failures - 1, 0), 5))


def relay(bind_port, peer_port, upstream, upstream_port, tg, subscriber, repeater):
    local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local.bind(("127.0.0.1", bind_port))
    remote = None
    retry_at = 0.0
    failures = 0
    print("TGIF standalone relay listening", flush=True)
    phase = ""
    authenticated = False
    try:
        while True:
            try:
                refresh_evidence()
            except (OSError, ValueError, TypeError):
                # Do not refresh heartbeat for unreadable or unsupported policy.
                pass
            if remote is None and time.monotonic() >= retry_at:
                try:
                    remote = open_upstream(upstream, upstream_port)
                except OSError:
                    failures += 1
                    retry_at = time.monotonic() + recovery_delay(failures)
            ready, _, _ = select.select([local] + ([remote] if remote else []), [], [], 1)
            for sock in ready:
                if sock is local:
                    packet, address = local.recvfrom(4096)
                    if address[0] != "127.0.0.1":
                        continue
                    if packet.startswith(b"RPTL"):
                        authenticated, phase = False, "challenge"
                    elif packet.startswith(b"RPTK"):
                        phase = "confirmation"
                    # Enforce the original local DMR source before outbound
                    # rewrite replaces it with the bridge subscriber ID.
                    # Authentication/control frames carry no DMR source.
                    if remote is not None and admin_allowed(packet):
                        try:
                            remote.send(rewrite_outbound(packet, tg, subscriber, repeater))
                        except UPSTREAM_ERRORS:
                            remote.close(); remote = None
                            authenticated, phase = False, ""
                            failures += 1
                            retry_at = time.monotonic() + recovery_delay(failures)
                else:
                    try:
                        packet = remote.recv(4096)
                    except UPSTREAM_ERRORS:
                        remote.close(); remote = None
                        authenticated, phase = False, ""
                        failures += 1
                        retry_at = time.monotonic() + recovery_delay(failures)
                        continue
                    failures = 0
                    if packet.startswith(b"RPTACK") and phase == "confirmation":
                        authenticated, phase = True, ""
                        print("TGIF master authenticated session", flush=True)
                    elif packet.startswith((b"MSTNAK", b"MSTCL")):
                        authenticated, phase = False, ""
                        print("TGIF master rejected or closed session", flush=True)
                    if inbound_allowed(packet, tg, subscriber) and admin_allowed(packet):
                        local.sendto(packet, ("127.0.0.1", peer_port))
    finally:
        local.close()
        if remote is not None:
            remote.close()


def self_test():
    tg, subscriber = 67498, 3135475
    packet = bytearray(55)
    packet[:4] = b"DMRD"
    packet[8:11] = tg.to_bytes(3, "big")
    assert inbound_allowed(packet, tg, subscriber)
    packet[8:11] = (9).to_bytes(3, "big")
    assert not inbound_allowed(packet, tg, subscriber)
    packet[8:11] = subscriber.to_bytes(3, "big")
    packet[15] = 0x40
    assert inbound_allowed(packet, tg, subscriber)
    packet[15] = 0
    assert not inbound_allowed(packet, tg, subscriber)
    assert inbound_allowed(b"MSTPONG", tg, subscriber)
    global CONTROL_FILE, EVIDENCE_FILE
    with tempfile.TemporaryDirectory() as directory:
        CONTROL_FILE = os.path.join(directory, "dmr-admin.json")
        EVIDENCE_FILE = os.path.join(directory, "dmr-admin-enforcement.json")
        packet[5:8] = (1234567).to_bytes(3, "big")
        with open(CONTROL_FILE, "w", encoding="utf-8") as handle:
            json.dump({"banned_ids": [1234567], "kicked_ids": {}}, handle)
        assert not admin_allowed(packet)
        with open(CONTROL_FILE, "w", encoding="utf-8") as handle:
            json.dump({"banned_ids": [], "kicked_ids": {"1234567": 100},
                       "kick_tokens": {"1234567": "a" * 32}, "policy_digest": "b" * 64}, handle)
        assert not admin_allowed(packet, 105)
        # The station keys up again after a quiet gap: still refused during the hold.
        assert not admin_allowed(packet, 100 + KICK_HOLD_SECONDS - 1)
        assert admin_allowed(packet, 100 + KICK_HOLD_SECONDS)
        assert json.load(open(CONTROL_FILE, encoding="utf-8"))["kicked_ids"] == {"1234567": 100}
        refresh_evidence(105)
        evidence = json.load(open(EVIDENCE_FILE, encoding="utf-8"))
        assert evidence["appliedKicks"] == {"1234567": "a" * 32}
        assert evidence["kickHoldSeconds"] == KICK_HOLD_SECONDS
        refresh_evidence(100 + KICK_HOLD_SECONDS)
        assert json.load(open(EVIDENCE_FILE, encoding="utf-8"))["appliedKicks"] == {}
    original = bytes(packet)
    rewritten = rewrite_outbound(original, tg, subscriber, 313547501)
    assert rewritten[5:8] == subscriber.to_bytes(3, "big")
    assert rewritten[8:11] == tg.to_bytes(3, "big")
    assert rewritten[11:15] == (313547501).to_bytes(4, "big")
    assert rewrite_outbound(b"MSTPING", tg, subscriber, 313547501) == b"MSTPING"
    print("Standalone TGIF relay self-test passed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--bind-port", type=int, default=62041)
    parser.add_argument("--peer-port", type=int, default=62042)
    parser.add_argument("--upstream", default="tgif.network")
    parser.add_argument("--upstream-port", type=int, default=62031)
    parser.add_argument("--talkgroup", type=int, default=67498)
    parser.add_argument("--subscriber", type=int, default=3135475)
    parser.add_argument("--repeater", type=int, default=313547501)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not 1 <= args.talkgroup <= 0xffffff or not 1 <= args.subscriber <= 0xffffff:
        parser.error("invalid talkgroup or subscriber")
    relay(args.bind_port, args.peer_port, args.upstream, args.upstream_port, args.talkgroup, args.subscriber, args.repeater)


if __name__ == "__main__":
    main()
