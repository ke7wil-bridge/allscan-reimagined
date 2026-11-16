#!/usr/bin/env python3
import json
import fcntl
import os
import re
import select
import socket
import struct
import sys
import tempfile
import time

DMR_ID = int(os.environ.get("DMR_ID", "3224939"))
DMR_NETWORK_ID = int(os.environ.get("DMR_NETWORK_ID", str(DMR_ID)))
TGIF_HOST = os.environ.get("TGIF_HOST", "tgif.network")
TGIF_PORT = int(os.environ.get("TGIF_PORT", "62031"))
LOCAL_PORT = int(os.environ.get("LOCAL_PORT", "62040"))
MMDVM_PORT = int(os.environ.get("MMDVM_PORT", "62033"))
URF_HOST = os.environ.get("URF_HOST", "127.0.0.1")
URF_PORT = int(os.environ.get("URF_PORT", "62030"))
URF_LOCAL_PORT = int(os.environ.get("URF_LOCAL_PORT", "62032"))
URF_ENABLED = os.environ.get("URF_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
URF_TG = int(os.environ.get("URF_TG", "4001"))
TGIF_TG = int(os.environ.get("TGIF_TG", "86753"))
TARGET_FILE = os.environ.get("TGIF_TARGET_FILE", "")
BLOCKED = {int(x) for x in os.environ.get("BLOCKED_DMR_IDS", "").split(",") if x.strip().isdigit()}
HEALTH_DIR = os.environ.get("HEALTH_DIR", "")
ROSTER_FILE = os.environ.get("DMR_ROSTER_FILE", os.path.join(HEALTH_DIR, "dmr-clients.json") if HEALTH_DIR else "")
CONTROL_FILE = os.environ.get("DMR_CONTROL_FILE", os.path.join(os.path.dirname(ROSTER_FILE), "dmr-admin.json") if ROSTER_FILE else "")
EVIDENCE_FILE = os.environ.get(
    "DMR_EVIDENCE_FILE",
    os.path.join(os.path.dirname(CONTROL_FILE), "dmr-admin-enforcement.json") if CONTROL_FILE else "",
)
ID_LOOKUP_FILE = os.environ.get("DMR_ID_LOOKUP_FILE", "")
CALLSIGN = os.environ.get("CALLSIGN", "").strip().upper()
URF_IDENTITY_FILE = os.path.join(HEALTH_DIR, "urf-identity.json") if HEALTH_DIR else ""
urf_login_attempts = 0
id_lookup_cache = {"mtime": -1.0, "values": {}}
last_dropped_kick = {}
last_evidence_heartbeat = 0
last_evidence_digest = ""


def callsign_for_id(source):
    if source == DMR_ID and CALLSIGN:
        return CALLSIGN
    if not ID_LOOKUP_FILE:
        return ""
    try:
        mtime = os.stat(ID_LOOKUP_FILE).st_mtime
        if mtime != id_lookup_cache["mtime"]:
            values = {}
            with open(ID_LOOKUP_FILE, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    fields = line.strip().split()
                    if len(fields) >= 2 and fields[0].isdigit():
                        values[int(fields[0])] = fields[1].upper()
            id_lookup_cache.update({"mtime": mtime, "values": values})
        return id_lookup_cache["values"].get(int(source), "")
    except (OSError, ValueError, TypeError):
        return ""


def mark_health(name):
    if not HEALTH_DIR:
        return
    path = os.path.join(HEALTH_DIR, name + "-health")
    try:
        with open(path, "a", encoding="utf-8"):
            os.utime(path, None)
    except OSError:
        pass


def clear_health(name):
    if not HEALTH_DIR:
        return
    try:
        os.unlink(os.path.join(HEALTH_DIR, name + "-health"))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def publish_urf_identity(state):
    """Publish the latest local URFD authentication result atomically."""
    if not URF_IDENTITY_FILE:
        return
    payload = {
        "state": state,
        "dmrid": str(DMR_ID),
        "callsign": CALLSIGN,
        "attempts": urf_login_attempts,
        "updated_epoch": int(time.time()),
    }
    try:
        os.makedirs(os.path.dirname(URF_IDENTITY_FILE), exist_ok=True)
        temporary = URF_IDENTITY_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(temporary, URF_IDENTITY_FILE)
    except OSError:
        pass


def publish_dmr_client(source, now=None):
    if not ROSTER_FILE or not source:
        return
    epoch = int(time.time() if now is None else now)
    row = {"dmrid": str(source), "id": str(source), "last_seen_epoch": epoch, "connected": True}
    callsign = callsign_for_id(source)
    if callsign:
        row["callsign"] = callsign
    prior = {}
    try:
        with open(ROSTER_FILE, "r", encoding="utf-8") as handle:
            prior = json.load(handle)
    except (OSError, ValueError):
        pass
    events = prior.get("events", []) if isinstance(prior, dict) else []
    prior_rows = prior.get("urf_dmr", []) if isinstance(prior, dict) else []
    prior_ids = {str(item.get("dmrid") or item.get("id") or "") for item in prior_rows if isinstance(item, dict)}
    prior_seen = max((int(item.get("last_seen_epoch", 0)) for item in prior_rows if isinstance(item, dict) and str(item.get("dmrid") or item.get("id") or "") == str(source)), default=0)
    # A source returning after the same 45-second expiry used by Connected Clients is a new session.
    if str(source) not in prior_ids or prior_seen <= 0 or epoch - prior_seen > 45:
        if prior_seen > 0 and epoch - prior_seen > 45:
            disconnect_epoch = prior_seen + 45
            if not any(isinstance(item, dict) and item.get("event") == "disconnect" and str(item.get("dmrid", "")) == str(source) and int(item.get("epoch", 0)) == disconnect_epoch for item in events):
                events.append({"dmrid": str(source), "callsign": row.get("callsign", ""), "event": "disconnect", "epoch": disconnect_epoch})
        events.append({"dmrid": str(source), "callsign": row.get("callsign", ""), "event": "connect", "epoch": epoch})
    events = [item for item in events if isinstance(item, dict) and epoch - int(item.get("epoch", 0)) <= 86400][-100:]
    # TX updates one session without replacing the rest of the current-membership
    # roster.  Membership expires independently in the main loop.
    rows = [item for item in prior_rows
            if isinstance(item, dict)
            and str(item.get("dmrid") or item.get("id") or "") != str(source)]
    rows.append(row)
    payload = {"urf_dmr": rows, "events": events, "_asr_meta": {"urf_dmr": {"kind": "current", "mode": "dmr"}}}
    os.makedirs(os.path.dirname(ROSTER_FILE), exist_ok=True)
    tmp = ROSTER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp, ROSTER_FILE)


def remove_dmr_client(source, reason="disconnect"):
    if not ROSTER_FILE or not source:
        return
    try:
        with open(ROSTER_FILE, "r", encoding="utf-8") as handle:
            prior = json.load(handle)
    except (OSError, ValueError):
        return
    if not isinstance(prior, dict):
        return
    rows = [row for row in prior.get("urf_dmr", []) if isinstance(row, dict)]
    removed = [row for row in rows if str(row.get("dmrid") or row.get("id") or "") == str(source)]
    if not removed:
        return
    epoch = int(time.time())
    events = [item for item in prior.get("events", []) if isinstance(item, dict)]
    for row in removed:
        events.append({"dmrid": str(source), "callsign": row.get("callsign", ""), "event": "disconnect", "epoch": epoch, "reason": reason})
    prior["urf_dmr"] = [row for row in rows if row not in removed]
    prior["events"] = events[-100:]
    tmp = ROSTER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(prior, handle, separators=(",", ":"))
    os.replace(tmp, ROSTER_FILE)


def supported_admin_policy(payload):
    """Accept only policy shapes that this packet path can enforce completely."""
    if not isinstance(payload, dict):
        return False
    if not all(isinstance(payload.get(field, []), list)
               for field in ("banned_ids", "banned_callsigns", "banned_prefixes")):
        return False
    if not all(isinstance(payload.get(field, {}), dict)
               for field in ("kicked_ids", "kick_tokens", "banned_clients")):
        return False
    if not re.fullmatch(r"[a-f0-9]{64}", str(payload.get("policy_digest", ""))):
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
    identity = r"[A-Z0-9][A-Z0-9/._-]{0,14}"
    if any(not re.fullmatch(identity, str(value).strip().upper())
           for value in payload.get("banned_callsigns", [])):
        return False
    if any(not re.fullmatch(identity, str(value).strip().upper())
           for value in payload.get("banned_prefixes", [])):
        return False
    return True


def admin_policy():
    if not CONTROL_FILE:
        raise ValueError("DMR administration control is unavailable")
    with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not supported_admin_policy(payload):
        raise ValueError("DMR administration policy is unsupported")
    return payload


def write_enforcement_evidence(policy, now=None, last_kick=None):
    global last_evidence_heartbeat, last_evidence_digest
    if not EVIDENCE_FILE:
        return
    epoch = int(time.time() if now is None else now)
    previous = {}
    try:
        with open(EVIDENCE_FILE, "r", encoding="utf-8") as handle:
            previous = json.load(handle)
    except (OSError, ValueError):
        pass
    if last_kick is None and isinstance(previous, dict) \
            and previous.get("appliedPolicyDigest") == policy["policy_digest"]:
        candidate = previous.get("lastKick")
        if isinstance(candidate, dict) and policy["kick_tokens"].get(candidate.get("dmrid")) == candidate.get("token"):
            last_kick = candidate
    payload = {
        "schema": 1,
        "contract": "dmr-local-media-v1",
        "heartbeatEpoch": epoch,
        "appliedPolicyDigest": policy["policy_digest"],
        "lastKick": last_kick,
    }
    os.makedirs(os.path.dirname(EVIDENCE_FILE), mode=0o750, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".dmr-enforcement.", dir=os.path.dirname(EVIDENCE_FILE))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, EVIDENCE_FILE)
        last_evidence_heartbeat = epoch
        last_evidence_digest = policy["policy_digest"]
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def refresh_enforcement_evidence(now=None):
    """Heartbeat only while the active packet path can enforce the full policy."""
    policy = admin_policy()
    epoch = int(time.time() if now is None else now)
    if epoch != last_evidence_heartbeat or policy["policy_digest"] != last_evidence_digest:
        write_enforcement_evidence(policy, epoch)


def admin_media_allowed(packet, direction, now=None):
    """Enforce current DMR media policy before either forwarding direction."""
    sid = source_id(packet)
    if sid is None:
        return True  # Authentication, keepalive, and other control traffic.
    epoch = int(time.time() if now is None else now)
    try:
        policy = admin_policy()
    except (OSError, ValueError, TypeError, AttributeError):
        # Invalid policy must not be reported or silently bypassed by DMR media.
        return False
    if sid in {int(value) for value in policy["banned_ids"]}:
        remove_dmr_client(sid, "ban")
        return False
    exact = {str(value).strip().upper() for value in policy["banned_callsigns"]}
    prefixes = {str(value).strip().upper() for value in policy["banned_prefixes"]}
    if exact or prefixes:
        name = callsign_for_id(sid)
        if not name or name in exact or any(name.startswith(prefix) for prefix in prefixes):
            remove_dmr_client(sid, "ban")
            return False
    key = str(sid)
    kicked = policy["kicked_ids"]
    if key not in kicked:
        last_dropped_kick.pop(key, None)
        return True
    started = int(kicked[key])
    last_drop = int(last_dropped_kick.get(key, started))
    if epoch - started > 10 and epoch - last_drop > 10:
        last_dropped_kick.pop(key, None)
        return True
    last_dropped_kick[key] = epoch
    remove_dmr_client(sid, "kick")
    token = policy["kick_tokens"].get(key)
    if isinstance(token, str) and re.fullmatch(r"[a-f0-9]{32}", token):
        write_enforcement_evidence(
            policy,
            epoch,
            {"dmrid": key, "token": token, "observedEpoch": epoch, "direction": direction},
        )
    return False


def read_admin_control():
    if not CONTROL_FILE:
        return set(), {}, set(), set()
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return set(), {}, set(), set()
    banned = {int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()}
    kicked = {}
    for key, value in payload.get("kicked_ids", {}).items():
        if str(key).isdigit():
            try:
                kicked[int(key)] = int(value)
            except (TypeError, ValueError):
                pass
    exact = {str(value).strip().upper() for value in payload.get("banned_callsigns", []) if str(value).strip()}
    prefixes = {str(value).strip().upper().rstrip("*") for value in payload.get("banned_prefixes", []) if str(value).strip().rstrip("*")}
    return banned, kicked, exact, prefixes


def write_admin_control(banned, kicked, exact=None, prefixes=None, *, changed_id=None, expected_epoch=None):
    if not CONTROL_FILE:
        return
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    # Share the adapter's stable lock; replacing the control inode must not
    # discard a simultaneous Global Ban policy digest or Kick token.
    lock_path = CONTROL_FILE + ".lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        # Keep adapter-owned policy fields and newer Kick requests intact.
        # A stale packet-processing snapshot must not overwrite another Kick.
        if changed_id is not None:
            key = str(changed_id)
            current = payload.get("kicked_ids", {})
            if not isinstance(current, dict) or current.get(key) != expected_epoch:
                return
            if changed_id in kicked:
                current[key] = int(kicked[changed_id])
            else:
                current.pop(key, None)
                tokens = payload.get("kick_tokens", {})
                if isinstance(tokens, dict): tokens.pop(key, None)
            payload["kicked_ids"] = current
        else:
            payload["kicked_ids"] = {str(key): int(value) for key, value in sorted(kicked.items())}
        fd, temporary = tempfile.mkstemp(prefix=".dmr-control.", dir=os.path.dirname(CONTROL_FILE))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            os.replace(temporary, CONTROL_FILE)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def subscriber_id_bytes():
    return struct.pack(">I", DMR_ID)


def network_id_bytes():
    return struct.pack(">I", DMR_NETWORK_ID)


def rewrite_dst(packet, dst):
    if not packet.startswith(b"DMRD") or len(packet) < 11:
        return packet
    out = bytearray(packet)
    out[8:11] = int(dst).to_bytes(3, "big")
    return bytes(out)


def source_id(packet):
    if packet.startswith(b"DMRD") and len(packet) >= 8:
        return int.from_bytes(packet[5:8], "big")
    return None


def destination_id(packet):
    if packet.startswith(b"DMRD") and len(packet) >= 11:
        return int.from_bytes(packet[8:11], "big")
    return None


def rewrite_for_urf(packet):
    return rewrite_dst(packet, 4000 if destination_id(packet) == 4000 else URF_TG)


def active_tgif_tg():
    if TARGET_FILE:
        try:
            raw = open(TARGET_FILE, "r", encoding="ascii").read().strip()
            value = int(raw)
            if 1 <= value <= 0xFFFFFF and value != 4000:
                return value
        except (OSError, ValueError):
            pass
    return TGIF_TG


def inbound_matches_active_tg(packet):
    """Relay the selected group or a private call addressed only to this subscriber."""
    if not packet.startswith(b"DMRD"):
        return True
    destination = destination_id(packet)
    return destination == active_tgif_tg() or (
        len(packet) > 15 and bool(packet[15] & 0x40) and destination == DMR_ID
    )


def rewrite_for_tgif(packet):
    if not packet.startswith(b"DMRD") or len(packet) < 15:
        return packet
    out = bytearray(packet)
    target = active_tgif_tg()
    out[5:8] = DMR_ID.to_bytes(3, "big")
    out[8:11] = target.to_bytes(3, "big")
    out[11:15] = network_id_bytes()
    return bytes(out)


def urf_login(sock):
    sock.send(b"RPTL" + subscriber_id_bytes())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            packet = sock.recv(256)
        except socket.timeout:
            continue
        if packet.startswith(b"RPTACK") and len(packet) >= 10:
            # URFD's local managed reflector only validates the standard RPTK
            # packet shape/DMR identity; the TGIF credential stays on the TGIF side.
            sock.send(b"RPTK" + subscriber_id_bytes() + (b"\x00" * 32))
            continue
        if packet == b"RPTACK":
            return True
        if packet.startswith(b"MSTNAK"):
            return False
    return False


def connect_urf():
    global urf_login_attempts
    urf_login_attempts += 1
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", URF_LOCAL_PORT))
    sock.connect((URF_HOST, URF_PORT))
    sock.settimeout(0.5)
    if not urf_login(sock):
        sock.close()
        publish_urf_identity("rejected")
        print(
            f"URFWIL DMR login rejected for DMR ID {DMR_ID}; verify the configured DMR identity",
            flush=True,
        )
        return None
    sock.setblocking(False)
    mark_health("urf")
    publish_urf_identity("authenticated")
    print(f"URFWIL DMR session connected: {URF_HOST}:{URF_PORT}", flush=True)
    return sock


def close_urf(sock, reason):
    clear_health("urf")
    print(f"URFWIL DMR session lost ({reason}); reconnecting", flush=True)
    try:
        sock.close()
    except OSError:
        pass


def connect_tgif():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.connect((TGIF_HOST, TGIF_PORT))
    sock.setblocking(False)
    return sock


def close_tgif(sock, reason):
    clear_health("tgif")
    print(f"TGIF DMR socket lost ({reason}); reconnecting", flush=True)
    try:
        sock.close()
    except OSError:
        pass


def validate_config():
    if not 1 <= DMR_ID <= 0xFFFFFF:
        raise ValueError("DMR_ID must fit the DMR 24-bit source-ID field")
    if not 1 <= DMR_NETWORK_ID <= 0xFFFFFFFF:
        raise ValueError("DMR_NETWORK_ID must fit the MMDVM 32-bit repeater-ID field")
    if not 1 <= TGIF_TG <= 0xFFFFFF or TGIF_TG == 4000:
        raise ValueError("TGIF_TG must be 1-16777215 and cannot be disconnect TG 4000")
    if URF_ENABLED and not 1 <= URF_TG <= 0xFFFFFF:
        raise ValueError("URF_TG must be 1-16777215")


def main():
    validate_config()
    local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local.bind(("127.0.0.1", LOCAL_PORT))

    tgif = None

    print(
        f"TGIF/URF DMR bridge active: TGIF={TGIF_HOST}:{TGIF_PORT} "
        f"URF={'disabled' if not URF_ENABLED else f'{URF_HOST}:{URF_PORT}'} "
        f"TGIF_TG={TGIF_TG} URF_TG={URF_TG}",
        flush=True,
    )
    urf = None
    last_urf_attempt = 0.0
    last_urf_response = 0.0
    last_ping = 0.0
    last_tgif_ping = 0.0
    last_tgif_attempt = 0.0
    tgif_authenticated = False
    tgif_login_phase = ""
    clear_health("tgif")
    clear_health("urf")
    try:
        os.unlink(URF_IDENTITY_FILE)
    except (FileNotFoundError, OSError):
        pass

    while True:
        now = time.monotonic()
        try:
            refresh_enforcement_evidence()
        except (OSError, ValueError, TypeError):
            # A missing/malformed policy intentionally lets the evidence
            # heartbeat expire, disabling administrative capabilities.
            pass
        if tgif is None and now - last_tgif_attempt >= 2:
            last_tgif_attempt = now
            try:
                tgif = connect_tgif()
            except OSError as error:
                print(f"TGIF DMR reconnect failed: {error}", flush=True)

        if URF_ENABLED and urf is None and now - last_urf_attempt >= 2:
            last_urf_attempt = now
            try:
                urf = connect_urf()
            except OSError as error:
                print(f"URFWIL DMR reconnect failed: {error}", flush=True)
            if urf is not None:
                last_urf_response = now
                last_ping = now

        if urf is not None and now - last_urf_response >= 15:
            close_urf(urf, "keepalive response timeout")
            urf = None

        if urf is not None and now - last_ping >= 5:
            try:
                urf.send(b"RPTPING" + subscriber_id_bytes())
                last_ping = now
            except OSError as error:
                close_urf(urf, error)
                urf = None

        # The pinned MMDVM_Bridge sends RPTPING every ten seconds, while TGIF's
        # master can expire an idle peer sooner after only a small amount of
        # jitter or packet loss.  Supplement its keepalive, but always proxy the
        # master's real MSTPONG back to MMDVM_Bridge below.
        if tgif is not None and tgif_authenticated and now - last_tgif_ping >= 5:
            try:
                tgif.send(b"RPTPING" + network_id_bytes())
                last_tgif_ping = now
            except OSError as error:
                close_tgif(tgif, error)
                tgif = None
                tgif_authenticated = False
                tgif_login_phase = ""

        sockets = [local]
        if tgif is not None:
            sockets.append(tgif)
        if urf is not None:
            sockets.append(urf)
        readable, _, _ = select.select(sockets, [], [], 1)
        # Traffic-derived roster fallback: expire silent clients after 10 seconds.
        # This keeps stale clients from appearing connected after the app/session is gone.
        try:
            with open(ROSTER_FILE, "r", encoding="utf-8") as handle:
                roster = json.load(handle)
            now_epoch = int(time.time())
            for row in list(roster.get("urf_dmr", [])):
                if isinstance(row, dict) and now_epoch - int(row.get("last_seen_epoch", 0)) > 10:
                    remove_dmr_client(int(row.get("dmrid") or row.get("id") or 0), "disconnect")
        except (OSError, ValueError, TypeError):
            pass
        for current in readable:
            if current is local:
                packet, address = local.recvfrom(4096)
                if address[0] == "127.0.0.1":
                    if packet.startswith(b"RPTL"):
                        tgif_authenticated = False
                        tgif_login_phase = "challenge"
                        clear_health("tgif")
                    elif packet.startswith(b"RPTK"):
                        tgif_login_phase = "confirmation"
                    # The adapter is a transparent control-plane proxy.  In
                    # particular, do not synthesize MSTPONG here: doing so masks
                    # an upstream session takeover from MMDVM_Bridge and prevents
                    # its normal timeout/re-authentication path from running.
                    if tgif is not None and admin_media_allowed(packet, "outbound"):
                        try:
                            tgif.send(packet)
                        except OSError as error:
                            close_tgif(tgif, error)
                            tgif = None
                            tgif_authenticated = False
                            tgif_login_phase = ""
                continue

            if current is tgif:
                try:
                    packet = tgif.recv(4096)
                except OSError as error:
                    close_tgif(tgif, error)
                    tgif = None
                    tgif_authenticated = False
                    tgif_login_phase = ""
                    continue
                if packet.startswith(b"RPTACK") and tgif_login_phase == "confirmation":
                    tgif_login_phase = ""
                    if not tgif_authenticated:
                        print("TGIF DMR session authenticated", flush=True)
                    tgif_authenticated = True
                    mark_health("tgif")
                elif packet.startswith((b"MSTNAK", b"MSTCL")):
                    if packet.startswith(b"MSTNAK") and tgif_login_phase == "confirmation":
                        print("TGIF authentication rejected by master", flush=True)
                    if tgif_authenticated:
                        print("TGIF DMR session closed", flush=True)
                    tgif_authenticated = False
                    tgif_login_phase = ""
                    clear_health("tgif")
                elif tgif_authenticated and packet.startswith((b"MSTPONG", b"DMRD")):
                    mark_health("tgif")
                if not inbound_matches_active_tg(packet):
                    continue
                sid = source_id(packet)
                if sid in BLOCKED:
                    # A blocked subscriber is not an active ASR client. Remove stale
                    # roster presence immediately while retaining event history.
                    remove_dmr_client(sid, "ban")
                    print(f"Blocked inbound DMR source ID {sid}", flush=True)
                    continue
                if not admin_media_allowed(packet, "inbound"):
                    print(f"Suppressed inbound DMR source ID {sid}", flush=True)
                    continue
                if sid:
                    publish_dmr_client(sid)
                local.sendto(packet, ("127.0.0.1", MMDVM_PORT))
                if urf is not None and packet.startswith(b"DMRD"):
                    try:
                        urf.send(rewrite_for_urf(packet))
                    except OSError as error:
                        close_urf(urf, error)
                        urf = None
                continue

            try:
                packet = urf.recv(4096)
                last_urf_response = time.monotonic()
                mark_health("urf")
            except OSError as error:
                close_urf(urf, error)
                urf = None
                continue
            if packet.startswith(b"MSTPONG"):
                continue
            if packet.startswith(b"MSTCL"):
                close_urf(urf, "server closed the session")
                urf = None
                continue
            if packet.startswith(b"DMRD"):
                # URFD emits TG9 and may not have a DMR ID for USRP-originated audio.
                # Present a complete, authenticated DMR identity and the selected TGIF TG.
                if tgif_authenticated and admin_media_allowed(packet, "outbound"):
                    try:
                        tgif.send(rewrite_for_tgif(packet))
                    except OSError as error:
                        close_tgif(tgif, error)
                        tgif = None
                        tgif_authenticated = False
                        tgif_login_phase = ""


def self_test():
    validate_config()
    packet = bytearray(55)
    packet[0:4] = b"DMRD"
    packet[5:8] = (1234567).to_bytes(3, "big")
    packet[8:11] = TGIF_TG.to_bytes(3, "big")
    packet[11:15] = (7654321).to_bytes(4, "big")
    toward_urf = rewrite_for_urf(bytes(packet))
    old_roster = globals().get("ROSTER_FILE", "")
    old_target = globals().get("TARGET_FILE", "")
    old_identity_file = globals().get("URF_IDENTITY_FILE", "")
    old_control = globals().get("CONTROL_FILE", "")
    old_evidence = globals().get("EVIDENCE_FILE", "")
    old_attempts = globals().get("urf_login_attempts", 0)
    try:
        with tempfile.TemporaryDirectory() as scratch:
            globals()["CONTROL_FILE"] = os.path.join(scratch, "dmr-admin.json")
            globals()["EVIDENCE_FILE"] = os.path.join(scratch, "dmr-admin-enforcement.json")
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                json.dump({"policy_digest": "a" * 64, "kick_tokens": {"1234567": "b" * 32},
                           "banned_ids": [1234567], "banned_callsigns": ["N7BAN"],
                           "kicked_ids": {"1234567": 100}}, handle)
            write_admin_control({1234567}, {1234567: 101}, {"N7BAN"}, set())
            with open(globals()["CONTROL_FILE"], encoding="utf-8") as handle:
                preserved = json.load(handle)
            assert preserved["policy_digest"] == "a" * 64
            assert preserved["kick_tokens"] == {"1234567": "b" * 32}
            assert preserved["banned_callsigns"] == ["N7BAN"]
            assert preserved["kicked_ids"] == {"1234567": 101}
            # A concurrent newer Kick must survive stale expiry processing.
            write_admin_control({1234567}, {}, changed_id=1234567, expected_epoch=100)
            with open(globals()["CONTROL_FILE"], encoding="utf-8") as handle:
                preserved = json.load(handle)
            assert preserved["kicked_ids"] == {"1234567": 101}
            write_admin_control({1234567}, {}, changed_id=1234567, expected_epoch=101)
            with open(globals()["CONTROL_FILE"], encoding="utf-8") as handle:
                preserved = json.load(handle)
            assert preserved["kicked_ids"] == {} and preserved["kick_tokens"] == {}
            policy = {"policy_digest": "c" * 64, "banned_ids": [],
                      "banned_callsigns": [], "banned_prefixes": [], "banned_clients": {},
                      "kicked_ids": {}, "kick_tokens": {}}
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                json.dump(policy, handle)
            assert admin_media_allowed(bytes(packet), "inbound", 200)
            assert admin_media_allowed(bytes(packet), "outbound", 200)
            assert admin_media_allowed(b"RPTPING-control", "outbound", 200)
            policy["banned_ids"] = [1234567]
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                json.dump(policy, handle)
            assert not admin_media_allowed(bytes(packet), "inbound", 201)
            assert not admin_media_allowed(bytes(packet), "outbound", 201)
            policy["banned_ids"] = []
            policy["kicked_ids"] = {"1234567": 202}
            policy["kick_tokens"] = {"1234567": "d" * 32}
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                json.dump(policy, handle)
            assert not admin_media_allowed(bytes(packet), "inbound", 203)
            with open(globals()["EVIDENCE_FILE"], encoding="utf-8") as handle:
                evidence = json.load(handle)
            assert evidence["contract"] == "dmr-local-media-v1"
            assert evidence["lastKick"] == {"dmrid": "1234567", "token": "d" * 32,
                                             "observedEpoch": 203, "direction": "inbound"}
            assert not admin_media_allowed(bytes(packet), "outbound", 208)
            assert admin_media_allowed(bytes(packet), "outbound", 220)
            other = bytearray(packet); other[5:8] = (7654321).to_bytes(3, "big")
            assert admin_media_allowed(bytes(other), "inbound", 208)
            policy["kicked_ids"] = {}; policy["kick_tokens"] = {}
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                json.dump(policy, handle)
            refresh_enforcement_evidence(221)
            with open(globals()["EVIDENCE_FILE"], encoding="utf-8") as handle:
                evidence = json.load(handle)
            assert evidence["heartbeatEpoch"] == 221 and evidence["lastKick"] is None
            with open(globals()["CONTROL_FILE"], "w", encoding="utf-8") as handle:
                handle.write("{bad")
            assert not admin_media_allowed(bytes(packet), "inbound", 222)
            globals()["ROSTER_FILE"] = os.path.join(scratch, "dmr-clients.json")
            globals()["URF_IDENTITY_FILE"] = os.path.join(scratch, "urf-identity.json")
            globals()["urf_login_attempts"] = 2
            publish_urf_identity("rejected")
            with open(globals()["URF_IDENTITY_FILE"], "r", encoding="utf-8") as handle:
                identity = json.load(handle)
            assert identity["state"] == "rejected" and identity["attempts"] == 2
            publish_urf_identity("authenticated")
            with open(globals()["URF_IDENTITY_FILE"], "r", encoding="utf-8") as handle:
                identity = json.load(handle)
            assert identity["state"] == "authenticated"
            publish_dmr_client(1234567, 100)
            publish_dmr_client(7654321, 101)
            with open(globals()["ROSTER_FILE"], "r", encoding="utf-8") as handle:
                roster = json.load(handle)
            assert {row["dmrid"] for row in roster["urf_dmr"]} == {"1234567", "7654321"}
            globals()["TARGET_FILE"] = os.path.join(scratch, "net-target")
            with open(globals()["TARGET_FILE"], "w", encoding="ascii") as handle:
                handle.write("67498\n")
            assert active_tgif_tg() == 67498
            assert inbound_matches_active_tg(bytes(packet)) is False
            packet[8:11] = (67498).to_bytes(3, "big")
            assert inbound_matches_active_tg(bytes(packet)) is True
            packet[8:11] = DMR_ID.to_bytes(3, "big")
            packet[15] = 0x40
            assert inbound_matches_active_tg(bytes(packet)) is True
            packet[15] = 0x00
            assert inbound_matches_active_tg(bytes(packet)) is False
            assert inbound_matches_active_tg(b"MSTPONG") is True
            with open(globals()["TARGET_FILE"], "w", encoding="ascii") as handle:
                handle.write("4000\n")
            assert active_tgif_tg() == TGIF_TG
    finally:
        globals()["ROSTER_FILE"] = old_roster
        globals()["TARGET_FILE"] = old_target
        globals()["URF_IDENTITY_FILE"] = old_identity_file
        globals()["CONTROL_FILE"] = old_control
        globals()["EVIDENCE_FILE"] = old_evidence
        globals()["urf_login_attempts"] = old_attempts
    assert destination_id(toward_urf) == URF_TG
    packet[8:11] = (4000).to_bytes(3, "big")
    assert destination_id(rewrite_for_urf(bytes(packet))) == 4000
    toward_tgif = rewrite_for_tgif(bytes(packet))
    assert source_id(toward_tgif) == DMR_ID
    assert destination_id(toward_tgif) == active_tgif_tg()
    assert toward_tgif[11:15] == network_id_bytes()
    print("TGIF/URFWIL DMR adapter self-test: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        main()
