#!/usr/bin/env python3
import hashlib
import json
import os
import select
import socket
import struct
import sys
import time

DMR_ID = int(os.environ.get("DMR_ID", "3224939"))
TGIF_HOST = os.environ.get("TGIF_HOST", "tgif.network")
TGIF_PORT = int(os.environ.get("TGIF_PORT", "62031"))
LOCAL_PORT = int(os.environ.get("LOCAL_PORT", "62040"))
MMDVM_PORT = int(os.environ.get("MMDVM_PORT", "62033"))
URF_HOST = os.environ.get("URF_HOST", "127.0.0.1")
URF_PORT = int(os.environ.get("URF_PORT", "62030"))
URF_LOCAL_PORT = int(os.environ.get("URF_LOCAL_PORT", "62032"))
URF_TG = int(os.environ.get("URF_TG", "4001"))
TGIF_TG = int(os.environ.get("TGIF_TG", "86753"))
BLOCKED = {int(x) for x in os.environ.get("BLOCKED_DMR_IDS", "").split(",") if x.strip().isdigit()}
HEALTH_DIR = os.environ.get("HEALTH_DIR", "")
ROSTER_FILE = os.environ.get("DMR_ROSTER_FILE", os.path.join(HEALTH_DIR, "dmr-clients.json") if HEALTH_DIR else "")
CONTROL_FILE = os.environ.get("DMR_CONTROL_FILE", os.path.join(os.path.dirname(ROSTER_FILE), "dmr-admin.json") if ROSTER_FILE else "")
CALLSIGN = os.environ.get("CALLSIGN", "").strip().upper()


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


def publish_dmr_client(source, now=None):
    if not ROSTER_FILE or not source:
        return
    epoch = int(time.time() if now is None else now)
    row = {"dmrid": str(source), "id": str(source), "last_seen_epoch": epoch, "connected": True}
    if source == DMR_ID and CALLSIGN:
        row["callsign"] = CALLSIGN
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
    payload = {"urf_dmr": [row], "events": events, "_asr_meta": {"urf_dmr": {"kind": "current", "mode": "dmr"}}}
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


def read_admin_control():
    if not CONTROL_FILE:
        return set(), {}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return set(), {}
    banned = {int(value) for value in payload.get("banned_ids", []) if str(value).isdigit()}
    kicked = {}
    for key, value in payload.get("kicked_ids", {}).items():
        if str(key).isdigit():
            try:
                kicked[int(key)] = int(value)
            except (TypeError, ValueError):
                pass
    return banned, kicked


def write_admin_control(banned, kicked):
    if not CONTROL_FILE:
        return
    payload = {"banned_ids": sorted(banned), "kicked_ids": {str(key): int(value) for key, value in sorted(kicked.items())}}
    os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
    tmp = CONTROL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))
    os.replace(tmp, CONTROL_FILE)


def dmr_id_bytes():
    return struct.pack(">I", DMR_ID)


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


def rewrite_for_tgif(packet):
    if not packet.startswith(b"DMRD") or len(packet) < 15:
        return packet
    out = bytearray(packet)
    out[5:8] = DMR_ID.to_bytes(3, "big")
    out[8:11] = TGIF_TG.to_bytes(3, "big")
    out[11:15] = dmr_id_bytes()
    return bytes(out)


def urf_login(sock):
    sock.send(b"RPTL" + dmr_id_bytes())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            packet = sock.recv(256)
        except socket.timeout:
            continue
        if packet.startswith(b"RPTACK") and len(packet) >= 10:
            # URFD currently accepts the standard 40-byte RPTK shape.
            # Supply a deterministic digest; no TGIF credential is exposed here.
            seed = packet[6:10]
            digest = hashlib.sha256(seed + b"URFWIL").digest()
            sock.send(b"RPTK" + dmr_id_bytes() + digest)
            continue
        if packet.startswith(b"RPTACK"):
            return True
        if packet.startswith(b"MSTNAK"):
            return False
    return False


def connect_urf():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", URF_LOCAL_PORT))
    sock.connect((URF_HOST, URF_PORT))
    sock.settimeout(0.5)
    if not urf_login(sock):
        sock.close()
        return None
    sock.setblocking(False)
    print(f"URFWIL DMR session connected: {URF_HOST}:{URF_PORT}", flush=True)
    mark_health("urf")
    return sock


def close_urf(sock, reason):
    clear_health("urf")
    print(f"URFWIL DMR session lost ({reason}); reconnecting", flush=True)
    try:
        sock.close()
    except OSError:
        pass


def validate_config():
    if not 1 <= DMR_ID <= 0xFFFFFF:
        raise ValueError("DMR_ID must fit the DMR 24-bit source-ID field")
    if not 1 <= TGIF_TG <= 0xFFFFFF or TGIF_TG == 4000:
        raise ValueError("TGIF_TG must be 1-16777215 and cannot be disconnect TG 4000")
    if not 1 <= URF_TG <= 0xFFFFFF:
        raise ValueError("URF_TG must be 1-16777215")


def main():
    validate_config()
    local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    local.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    local.bind(("127.0.0.1", LOCAL_PORT))

    tgif = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tgif.connect((TGIF_HOST, TGIF_PORT))

    print(
        f"TGIF/URF DMR bridge active: TGIF={TGIF_HOST}:{TGIF_PORT} "
        f"URF={URF_HOST}:{URF_PORT} TGIF_TG={TGIF_TG} URF_TG={URF_TG}",
        flush=True,
    )
    urf = None
    last_urf_attempt = 0.0
    last_urf_response = 0.0
    last_ping = 0.0

    while True:
        now = time.monotonic()
        if urf is None and now - last_urf_attempt >= 2:
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
                urf.send(b"RPTPING" + dmr_id_bytes())
                last_ping = now
            except OSError as error:
                close_urf(urf, error)
                urf = None

        sockets = [local, tgif]
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
                    tgif.send(packet)
                continue

            if current is tgif:
                packet = tgif.recv(4096)
                mark_health("tgif")
                sid = source_id(packet)
                banned_ids, kicked_ids = read_admin_control()
                if sid in BLOCKED or sid in banned_ids:
                    # A blocked subscriber is not an active ASR client. Remove stale
                    # roster presence immediately while retaining event history.
                    remove_dmr_client(sid, "ban")
                    print(f"Blocked inbound DMR source ID {sid}", flush=True)
                    continue
                if sid in kicked_ids:
                    now_epoch = int(time.time())
                    if now_epoch - kicked_ids[sid] <= 10:
                        # Keep this session kicked while it is still sending. A real
                        # disconnect creates a quiet gap; after 10 quiet seconds the
                        # next packet is treated as a fresh session and may reconnect.
                        kicked_ids[sid] = now_epoch
                        write_admin_control(banned_ids, kicked_ids)
                        remove_dmr_client(sid, "kick")
                        print(f"Kicked inbound DMR source ID {sid}", flush=True)
                        continue
                    del kicked_ids[sid]
                    write_admin_control(banned_ids, kicked_ids)
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
                tgif.send(rewrite_for_tgif(packet))


def self_test():
    validate_config()
    packet = bytearray(55)
    packet[0:4] = b"DMRD"
    packet[5:8] = (1234567).to_bytes(3, "big")
    packet[8:11] = TGIF_TG.to_bytes(3, "big")
    packet[11:15] = (7654321).to_bytes(4, "big")
    toward_urf = rewrite_for_urf(bytes(packet))
    old_roster = globals().get("ROSTER_FILE", "")
    try:
        globals()["ROSTER_FILE"] = ""
        publish_dmr_client(1234567, 100)
    finally:
        globals()["ROSTER_FILE"] = old_roster
    assert destination_id(toward_urf) == URF_TG
    packet[8:11] = (4000).to_bytes(3, "big")
    assert destination_id(rewrite_for_urf(bytes(packet))) == 4000
    toward_tgif = rewrite_for_tgif(bytes(packet))
    assert source_id(toward_tgif) == DMR_ID
    assert destination_id(toward_tgif) == TGIF_TG
    assert toward_tgif[11:15] == dmr_id_bytes()
    print("TGIF/URFWIL DMR adapter self-test: ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        main()
