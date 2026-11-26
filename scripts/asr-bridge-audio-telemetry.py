#!/usr/bin/env python3
"""Publish real post-gain USRP PCM levels for managed bridge instances."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import socket
import struct
import tempfile
import time

CONFIG = Path("/etc/allscan-reimagined/config.json")
RUNTIME = Path("/run/allscan-reimagined-bridge-audio")
OUTPUT = RUNTIME / "levels.json"
BRIDGE_ID = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")


def pcm_levels(frame: bytes) -> tuple[int, float]:
    if not frame or len(frame) % 2 or len(frame) > 640:
        raise ValueError("invalid PCM16 frame")
    samples = [value for (value,) in struct.iter_unpack("<h", frame)]
    return max(abs(value) for value in samples), math.sqrt(sum(value * value for value in samples) / len(samples))


def usrp_pcm(packet: bytes) -> bytes | None:
    if len(packet) != 352 or packet[:4] != b"USRP" or struct.unpack_from("!I", packet, 20)[0] != 0:
        return None
    return packet[32:]


def udp_payload(frame: bytes) -> tuple[int, bytes] | None:
    """Return destination port and UDP payload from an AF_PACKET IPv4 frame."""
    for start in (14, 0, 16):
        if len(frame) < start + 28 or frame[start] >> 4 != 4:
            continue
        ihl = (frame[start] & 0x0F) * 4
        if ihl < 20 or len(frame) < start + ihl + 8 or frame[start + 9] != socket.IPPROTO_UDP:
            continue
        udp = start + ihl
        _source, destination, length, _checksum = struct.unpack_from("!HHHH", frame, udp)
        if length < 8 or udp + length > len(frame):
            return None
        return destination, frame[udp + 8:udp + length]
    return None


def port_map(config: dict) -> dict[int, list[tuple[str, str]]]:
    mapped: dict[int, list[tuple[str, str]]] = {}
    for bridge in config.get("bridges", []) if isinstance(config, dict) else []:
        if not isinstance(bridge, dict):
            continue
        bridge_id = bridge.get("id")
        if not isinstance(bridge_id, str) or not BRIDGE_ID.fullmatch(bridge_id):
            continue
        ports = bridge.get("setupPorts") if isinstance(bridge.get("setupPorts"), dict) else {}
        rx = bridge.get("m17UsrpRxPort", ports.get("usrp_rx"))
        tx = bridge.get("m17UsrpTxPort", ports.get("usrp_tx"))
        for port, direction in ((rx, "outbound"), (tx, "inbound")):
            if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                mapped.setdefault(port, []).append((bridge_id, direction))
    return mapped


def update(levels: dict, destinations: list[tuple[str, str]], pcm: bytes, epoch: float,
           window: dict | None = None) -> None:
    peak, rms = pcm_levels(pcm)
    for bridge_id, direction in destinations:
        entry = levels.setdefault(bridge_id, {})
        entry[direction + "AudioPeak"] = peak
        entry[direction + "AudioRms"] = rms
        entry[direction + "AudioEpoch"] = epoch
        if window is not None:
            accumulator = window.setdefault(bridge_id, {}).setdefault(direction, [0, 0.0, 0])
            count = len(pcm) // 2
            accumulator[0] = max(accumulator[0], peak)
            accumulator[1] += rms * rms * count
            accumulator[2] += count


def publish(levels: dict, now: float, output: Path = OUTPUT,
            window: dict | None = None) -> None:
    # Aggregate every observed PCM sample in the publication interval:
    # maximum sample peak and sample-weighted RMS, independently per direction.
    if window is not None:
        for bridge_id, directions in window.items():
            for direction, (peak, squared_sum, count) in directions.items():
                if not count or bridge_id not in levels:
                    continue
                entry = levels[bridge_id]
                entry[direction + "AudioPeak"] = peak
                entry[direction + "AudioRms"] = math.sqrt(squared_sum / count)
        window.clear()
    clean = {}
    for bridge_id, values in levels.items():
        current = {}
        for direction in ("inbound", "outbound"):
            epoch = values.get(direction + "AudioEpoch", 0)
            if isinstance(epoch, (int, float)) and 0 < epoch <= now + 1 and now - epoch <= 3:
                for suffix in ("Peak", "Rms", "Epoch"):
                    current[direction + "Audio" + suffix] = values[direction + "Audio" + suffix]
        if current:
            clean[bridge_id] = current
    levels.clear()
    levels.update(clean)
    output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".levels.", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "updatedEpoch": now, "bridges": clean}, handle, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, output)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run(config_path: Path = CONFIG, output_path: Path = OUTPUT) -> None:
    capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
    capture.bind(("lo", 0))
    capture.settimeout(0.2)
    levels: dict = {}
    window: dict = {}
    mapped: dict[int, list[tuple[str, str]]] = {}
    next_config = next_publish = 0.0
    while True:
        now = time.time()
        if now >= next_config:
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
                mapped = port_map(loaded)
            except (OSError, ValueError, TypeError):
                mapped = {}
            next_config = now + 2
        try:
            decoded = udp_payload(capture.recv(2048))
            if decoded and decoded[0] in mapped:
                pcm = usrp_pcm(decoded[1])
                if pcm is not None:
                    update(levels, mapped[decoded[0]], pcm, now, window)
        except socket.timeout:
            pass
        if now >= next_publish:
            publish(levels, now, output_path, window)
            next_publish = now + 0.2


def self_test() -> None:
    pcm = struct.pack("<4h", -32768, -1000, 0, 2000)
    assert pcm_levels(pcm)[0] == 32768
    usrp = bytearray(352); usrp[:4] = b"USRP"; usrp[32:40] = pcm
    assert usrp_pcm(bytes(usrp))[:8] == pcm
    udp = struct.pack("!HHHH", 40000, 32001, 360, 0) + bytes(usrp)
    ip = bytearray(20); ip[0] = 0x45; ip[9] = socket.IPPROTO_UDP
    frame = bytes(14) + bytes(ip) + udp
    assert udp_payload(frame) == (32001, bytes(usrp))
    mapped = port_map({"bridges": [{"id": "p25_test", "setupPorts": {"usrp_rx": 32000, "usrp_tx": 32001}}]})
    assert mapped == {32000: [("p25_test", "outbound")], 32001: [("p25_test", "inbound")]}
    levels = {}; update(levels, mapped[32001], bytes(usrp)[32:], 100.0)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "levels.json"; publish(levels, 100.0, path)
        saved = json.loads(path.read_text())
        assert saved["bridges"]["p25_test"]["inboundAudioPeak"] == 32768
    # A quiet packet cannot erase a louder packet's peak in the same window.
    window = {}
    values = {}
    update(values, [("p25_test", "inbound")], struct.pack("<4h", 10000, -10000, 10000, -10000), 101.0, window)
    update(values, [("p25_test", "inbound")], struct.pack("<4h", 0, 0, 0, 0), 101.1, window)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "window.json"
        publish(values, 101.1, path, window)
        sample = json.loads(path.read_text())["bridges"]["p25_test"]
        assert sample["inboundAudioPeak"] == 10000
        assert abs(sample["inboundAudioRms"] - math.sqrt(50000000)) < .001
        assert window == {}
    print("bridge real-PCM telemetry self-test: PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    self_test() if arguments.self_test else run(arguments.config, arguments.output)
