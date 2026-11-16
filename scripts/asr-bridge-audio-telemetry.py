#!/usr/bin/env python3
"""Shared PCM16 bridge meter math for native or container audio producers.

The trusted host-side collector publishes schema=1 to
/run/allscan-reimagined-bridge-audio/levels.json; this module does not
write to that privileged location or infer audio from PTT.
"""
import math
import struct
import time


def pcm16_rms(frame: bytes) -> float:
    """Return linear PCM16 RMS (0..32768), or raise for malformed frames."""
    if not isinstance(frame, bytes) or len(frame) == 0 or len(frame) % 2:
        raise ValueError("expected nonempty, aligned PCM16 frame")
    samples = struct.iter_unpack("<h", frame)
    total = 0
    count = 0
    for (value,) in samples:
        total += value * value
        count += 1
    return math.sqrt(total / count)


def sample(direction: str, frame: bytes, epoch: float | None = None) -> dict:
    if direction not in ("inbound", "outbound"):
        raise ValueError("invalid audio direction")
    rms = pcm16_rms(frame)
    timestamp = time.time() if epoch is None else float(epoch)
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("invalid sample timestamp")
    return {direction + "AudioRms": rms, direction + "AudioEpoch": timestamp}


def merge_sample(snapshot: dict, bridge_id: str, measurement: dict, now: float | None = None) -> dict:
    """Merge a real PCM measurement; discard stale samples and unknown fields.

    The caller is responsible for authenticating the bridge producer and for
    atomically publishing the returned snapshot from a trusted host process.
    """
    import re
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise ValueError("invalid bridge ID")
    if not isinstance(measurement, dict):
        raise ValueError("invalid measurement")
    current = time.time() if now is None else float(now)
    if not math.isfinite(current) or current <= 0:
        raise ValueError("invalid clock")
    bridges = {}
    existing = snapshot.get("bridges", {}) if isinstance(snapshot, dict) else {}
    if isinstance(existing, dict):
        for key, values in existing.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", key):
                continue
            if not isinstance(values, dict):
                continue
            clean = {}
            for direction in ("inbound", "outbound"):
                rms = values.get(direction + "AudioRms")
                epoch = values.get(direction + "AudioEpoch")
                if isinstance(rms, (int, float)) and not isinstance(rms, bool) and isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
                    if math.isfinite(rms) and math.isfinite(epoch) and 0 <= rms <= 32768 and 0 < epoch <= current + 1 and current - epoch <= 3:
                        clean[direction + "AudioRms"] = float(rms)
                        clean[direction + "AudioEpoch"] = float(epoch)
            if clean:
                bridges[key] = clean
    valid = {}
    for direction in ("inbound", "outbound"):
        rms = measurement.get(direction + "AudioRms")
        epoch = measurement.get(direction + "AudioEpoch")
        if isinstance(rms, (int, float)) and not isinstance(rms, bool) and isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
            if math.isfinite(rms) and math.isfinite(epoch) and 0 <= rms <= 32768 and 0 < epoch <= current + 1 and current - epoch <= 3:
                valid[direction + "AudioRms"] = float(rms)
                valid[direction + "AudioEpoch"] = float(epoch)
    if not valid:
        raise ValueError("measurement has no fresh audio")
    bridges[bridge_id] = {**bridges.get(bridge_id, {}), **valid}
    return {"schema": 1, "bridges": bridges}


def encode_snapshot(snapshot: dict) -> bytes:
    """Serialize a bounded snapshot for a privileged, atomic host-side writer."""
    import json
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1:
        raise ValueError("unsupported audio schema")
    bridges = snapshot.get("bridges")
    if not isinstance(bridges, dict) or len(bridges) > 64:
        raise ValueError("invalid bridge collection")
    for bridge_id, levels in bridges.items():
        import re
        if not isinstance(bridge_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
            raise ValueError("invalid bridge ID")
        if not isinstance(levels, dict) or not levels:
            raise ValueError("invalid bridge levels")
        for key, value in levels.items():
            if key not in ("inboundAudioRms", "inboundAudioEpoch", "outboundAudioRms", "outboundAudioEpoch"):
                raise ValueError("unknown audio field")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError("nonfinite audio field")
        for direction in ("inbound", "outbound"):
            rms = levels.get(direction + "AudioRms")
            epoch = levels.get(direction + "AudioEpoch")
            if (rms is None) != (epoch is None):
                raise ValueError("incomplete audio pair")
            if rms is not None and (rms < 0 or rms > 32768 or epoch <= 0):
                raise ValueError("invalid audio sample")
    return (json.dumps(snapshot, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def usrp_voice_pcm(packet: bytes) -> bytes | None:
    """Extract 160 signed PCM16 samples from an ASL chan_usrp voice datagram.

    Reject control, key/unkey and malformed frames. A producer must capture
    packets at its existing authenticated audio endpoint; never bind a second
    listener to the production USRP port.
    """
    if not isinstance(packet, bytes) or len(packet) != 352:
        return None
    if packet[:4] != b"USRP":
        return None
    import struct
    # chan_usrp packet type is a network-order uint32 at offset 20.
    packet_type = struct.unpack_from("!I", packet, 20)[0]
    if packet_type != 0:
        return None
    return packet[32:]


def usrp_sample(direction: str, packet: bytes, epoch: float | None = None) -> dict | None:
    pcm = usrp_voice_pcm(packet)
    return sample(direction, pcm, epoch) if pcm is not None else None
