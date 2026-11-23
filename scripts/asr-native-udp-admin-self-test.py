#!/usr/bin/env python3
"""Run the native UDP admin proxy against a fake reflector (#134 acceptance).

Covers what Beta 8.5.1 live testing found: a P25/NXDN client re-polls within
seconds, so Kick must hold the identity off; and an M17 client such as
KE7WIL-02 must be caught by a Global Ban on its base callsign KE7WIL.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROXY = HERE / "asr-native-udp-admin.py"


def free_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def m17_callsign(value: str) -> bytes:
    alphabet = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."
    coded = 0
    for char in reversed(value):
        coded = coded * 40 + alphabet.index(char)
    return coded.to_bytes(6, "big")


class Harness:
    def __init__(self, mode: str, root: Path):
        self.mode = mode
        self.upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.upstream.bind(("127.0.0.1", 0))
        self.upstream.settimeout(0.2)
        self.public_port = free_udp_port()
        self.policy = root / "policy"
        self.policy.mkdir(parents=True, exist_ok=True)
        self.runtime = root / "runtime"
        self.set_bans([])
        env = {**os.environ, "ASR_BRIDGE_ID": mode, "ASR_BRIDGE_MODE": mode,
               "ASR_PUBLIC_HOST": "127.0.0.1", "ASR_ENABLE_IPV6": "0",
               "ASR_PUBLIC_PORT": str(self.public_port),
               "ASR_UPSTREAM_HOST": "127.0.0.1",
               "ASR_UPSTREAM_PORT": str(self.upstream.getsockname()[1]),
               "ASR_RUNTIME_ROOT": str(self.runtime),
               "ASR_GLOBAL_POLICY_DIR": str(self.policy),
               "ASR_KICK_HOLD_SECONDS": "60"}
        self.process = subprocess.Popen([sys.executable, str(PROXY)], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        deadline = time.time() + 10
        while not (self.runtime / mode / "capabilities.json").exists():
            if time.time() > deadline or self.process.poll() is not None:
                raise AssertionError(f"{mode} proxy did not start: {self.process.stderr.read().decode()[-400:]}")
            time.sleep(0.05)

    def set_bans(self, rules: list[str]) -> None:
        (self.policy / "global-bans.list").write_text("".join(rule + "\n" for rule in rules))
        (self.policy / "asr-timed-bans.json").write_text("{}")
        time.sleep(0.25)

    def send(self, packet: bytes) -> bytes | None:
        self.client.sendto(packet, ("127.0.0.1", self.public_port))
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                data, _ = self.upstream.recvfrom(2048)
            except socket.timeout:
                continue
            if data == packet:
                return data
        return None

    def kick(self, identity: str) -> dict:
        nonce = secrets.token_hex(16)
        directory = self.runtime / self.mode
        (directory / f"request-{nonce}.json").write_text(json.dumps({
            "schema": 1, "nonce": nonce, "bridgeId": self.mode, "mode": self.mode,
            "action": "kick", "identity": identity}))
        result = directory / f"result-{nonce}.json"
        deadline = time.time() + 5
        while not result.exists():
            if time.time() > deadline:
                raise AssertionError("proxy did not answer the Kick request")
            time.sleep(0.05)
        return json.loads(result.read_text())

    def close(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=5)
        self.client.close()
        self.upstream.close()


with tempfile.TemporaryDirectory(prefix="asr-native-admin-test-") as raw:
    root = Path(raw)

    p25 = Harness("p25", root / "p25")
    try:
        manifest = json.loads((p25.runtime / "p25" / "capabilities.json").read_text())
        assert manifest["kickContract"] == "disconnect-hold-v1"
        assert manifest["kickAllowsImmediateReconnect"] is False and manifest["kickHoldSeconds"] == 60
        poll = bytes([0xF0]) + b"KE7WIL    "
        assert p25.send(poll), "P25 poll was not forwarded to the reflector"
        result = p25.kick("KE7WIL")
        assert result["ok"] and result["enforcement"] == "disconnect-hold-v1" and result["holdSeconds"] == 60
        # The radio re-polls a few seconds later; the hold must refuse it.
        assert p25.send(poll) is None, "kicked P25 client was readmitted on its next poll"
        other = bytes([0xF0]) + b"N6JET     "
        p25.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        assert p25.send(other), "Kick held off an unrelated station"
    finally:
        p25.close()
    print("native admin proxy Kick hold (P25 re-poll refused): PASS")

    m17 = Harness("m17", root / "m17")
    try:
        conn = b"CONN" + m17_callsign("KE7WIL-02") + b"A"
        assert m17.send(conn), "M17 connect was not forwarded"
        m17.set_bans(["KE7WIL"])
        m17.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        assert m17.send(conn) is None, "Global Ban on KE7WIL did not block M17 client KE7WIL-02"
        m17.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        assert m17.send(b"CONN" + m17_callsign("KE7WILX") + b"A"), "base-callsign ban overmatched KE7WILX"
    finally:
        m17.close()
    print("native admin proxy ban matches callsign suffixes (M17 KE7WIL-02): PASS")

    # A proxy whose reflector port has no listener must not claim enforcement.
    nxdn = Harness("nxdn", root / "nxdn")
    try:
        nxdn.upstream.close()
        deadline = time.time() + 5
        while time.time() < deadline:
            manifest = json.loads((nxdn.runtime / "nxdn" / "capabilities.json").read_text())
            if manifest["healthy"] is False:
                break
            time.sleep(0.2)
        assert manifest["healthy"] is False and manifest["upstreamListening"] is False
        assert manifest["banClient"] is False and manifest["kickClient"] is False
    finally:
        nxdn.process.terminate()
        nxdn.process.wait(timeout=5)
        nxdn.client.close()
    print("native admin proxy reports unhealthy with no reflector listener: PASS")
