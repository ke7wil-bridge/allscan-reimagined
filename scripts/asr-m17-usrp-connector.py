#!/usr/bin/env python3
"""Headless, fail-closed M17 simple-client and USRP audio connector foundation.

The connector will not open network sockets unless configuration permission,
the Codec2 dependency, and the explicit per-instance audio qualification flag
all pass.  Link state is confirmed only by a reflector ACKN and keepalives.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_CONTROL_SCRIPT = SCRIPT_DIR / "asr-m17-bridge-control.py"
INSTALLED_CONTROL_SCRIPT = SCRIPT_DIR / "allscan-reimagined-m17-bridge-control"
CONTROL_SCRIPT = SOURCE_CONTROL_SCRIPT if SOURCE_CONTROL_SCRIPT.is_file() else INSTALLED_CONTROL_SCRIPT
USRP_MAGIC = b"USRP"
USRP_HEADER_SIZE = 32
USRP_AUDIO_SIZE = 320
USRP_VOICE_SIZE = USRP_HEADER_SIZE + USRP_AUDIO_SIZE
USRP_TYPE_VOICE = 0
M17_MAGIC = b"M17 "
M17_STREAM_SIZE = 54
M17_PAYLOAD_SIZE = 16
M17_VOICE_TYPE = 0x0005  # legacy Stream + Codec2 3200 + no encryption
M17_CRC_POLY = 0x5935
M17_CRC_INITIAL = 0xFFFF
STATE_WRITE_INTERVAL = 1.0
COMMAND_MAX_AGE = 300
STANDARD_RECONNECT_DELAY = 5.0
INBOUND_STREAM_TIMEOUT = 2.0
ASTERISK_BIN = Path("/usr/sbin/asterisk")
ASTERISK_TIMEOUT = 8.0
ALLSTAR_CONFIRM_ATTEMPTS = 40
ALLSTAR_CONFIRM_INTERVAL = 0.25


class ConnectorError(RuntimeError):
    pass


class EncryptedM17Error(ConnectorError):
    pass


Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def default_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectorError("Asterisk bridge-link command could not be run.") from exc


def parse_lstats_links(output: str) -> set[tuple[str, str]]:
    if not re.search(
        r"^NODE\s+PEER\s+RECONNECTS\s+DIRECTION\s+CONNECT TIME\s+CONNECT STATE\s*$",
        output,
        re.MULTILINE,
    ):
        raise ConnectorError("Asterisk direct-link status was not recognized.")
    links: set[tuple[str, str]] = set()
    for line in output.splitlines():
        fields = line.split()
        if (
            len(fields) >= 6
            and fields[0].isdigit()
            and fields[3] in {"IN", "OUT"}
            and fields[-1] == "ESTABLISHED"
        ):
            links.add((fields[0], fields[3]))
    return links


def direct_linked(
    local_node: str, bridge_node: str, runner: Runner = default_runner
) -> bool:
    completed = runner(
        [str(ASTERISK_BIN), "-rx", f"rpt lstats {local_node}"], ASTERISK_TIMEOUT
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ConnectorError("Asterisk direct-link status returned an error.")
    return (bridge_node, "OUT") in parse_lstats_links(completed.stdout)


def set_direct_link(
    local_node: str,
    bridge_node: str,
    linked: bool,
    runner: Runner = default_runner,
) -> None:
    if direct_linked(local_node, bridge_node, runner) == linked:
        return
    completed = runner(
        [
            str(ASTERISK_BIN), "-rx",
            f"rpt cmd {local_node} ilink {'3' if linked else '11'} {bridge_node}",
        ],
        ASTERISK_TIMEOUT,
    )
    if completed.returncode != 0:
        raise ConnectorError("Asterisk bridge-link command returned an error.")
    for _ in range(ALLSTAR_CONFIRM_ATTEMPTS):
        if direct_linked(local_node, bridge_node, runner) == linked:
            return
        time.sleep(ALLSTAR_CONFIRM_INTERVAL)
    raise ConnectorError(
        f"Asterisk did not confirm the bridge-node {'link' if linked else 'unlink'}."
    )


def load_control_module(path: Path = CONTROL_SCRIPT) -> ModuleType:
    try:
        parent = path.parent.lstat()
        info = path.lstat()
    except OSError as exc:
        raise ConnectorError("M17 bridge control module is unavailable.") from exc
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise ConnectorError("M17 bridge control directory is unsafe.")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ConnectorError("M17 bridge control module must be a regular file.")
    if os.geteuid() == 0 and (
        parent.st_uid != 0
        or stat.S_IMODE(parent.st_mode) & 0o022
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise ConnectorError("M17 bridge control module has unsafe ownership or permissions.")
    loader = SourceFileLoader("asr_m17_bridge_control", str(path))
    spec = importlib.util.spec_from_loader("asr_m17_bridge_control", loader)
    if spec is None or spec.loader is None:
        raise ConnectorError("M17 bridge control module could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def m17_crc(data: bytes) -> int:
    crc = M17_CRC_INITIAL
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ M17_CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def m17_encryption_type(frame_type: int) -> int:
    # Current public reflectors still carry legacy TYPE values.  Version-3
    # values are distinguishable by the high nibble and use a wider field.
    if frame_type & 0xF000:
        return (frame_type >> 9) & 0x7
    return (frame_type >> 3) & 0x3


def m17_is_codec2_3200(frame_type: int) -> bool:
    if frame_type & 0xF000:
        return ((frame_type >> 12) & 0xF) == 2
    return bool(frame_type & 1) and ((frame_type >> 1) & 0x3) == 2


def reflector_destination(reflector: str, module: str) -> str:
    destination = f"{reflector} {module}"
    if len(destination) > 9:
        raise ConnectorError("M17 reflector/module destination exceeds nine characters.")
    return destination


def build_m17_stream(
    control: ModuleType,
    stream_id: int,
    destination: str,
    source: str,
    frame_number: int,
    payload: bytes,
    *,
    eot: bool = False,
    frame_type: int = M17_VOICE_TYPE,
    metadata: bytes = bytes(14),
) -> bytes:
    if len(payload) != M17_PAYLOAD_SIZE or len(metadata) != 14:
        raise ConnectorError("M17 stream payload or metadata length is invalid.")
    if m17_encryption_type(frame_type) != 0:
        raise EncryptedM17Error("Encrypted M17 stream construction is forbidden.")
    number = int(frame_number) & 0x7FFF
    if eot:
        number |= 0x8000
    body = (
        M17_MAGIC
        + struct.pack(">H", int(stream_id) & 0xFFFF)
        + control.encode_callsign(destination)
        + control.encode_callsign(source)
        + struct.pack(">H", frame_type & 0xFFFF)
        + metadata
        + struct.pack(">H", number)
        + payload
    )
    return body + struct.pack(">H", m17_crc(body))


def parse_m17_stream(control: ModuleType, packet: bytes) -> dict[str, Any]:
    if len(packet) != M17_STREAM_SIZE or packet[:4] != M17_MAGIC:
        raise ConnectorError("Malformed M17 stream packet.")
    expected_crc = struct.unpack(">H", packet[-2:])[0]
    if m17_crc(packet[:-2]) != expected_crc:
        raise ConnectorError("M17 stream packet CRC failed.")
    frame_type = struct.unpack(">H", packet[18:20])[0]
    if m17_encryption_type(frame_type) != 0:
        raise EncryptedM17Error("Encrypted M17 stream was rejected.")
    if not m17_is_codec2_3200(frame_type):
        raise ConnectorError("M17 stream is not unencrypted Codec2 3200 voice.")
    frame_number = struct.unpack(">H", packet[34:36])[0]
    return {
        "streamId": struct.unpack(">H", packet[4:6])[0],
        "destination": control.decode_callsign(packet[6:12]),
        "source": control.decode_callsign(packet[12:18]),
        "frameType": frame_type,
        "metadata": packet[20:34],
        "frameNumber": frame_number & 0x7FFF,
        "eot": bool(frame_number & 0x8000),
        "payload": packet[36:52],
    }


def build_usrp_packet(sequence: int, ptt: bool, pcm: bytes = b"") -> bytes:
    if ptt and len(pcm) != USRP_AUDIO_SIZE:
        raise ConnectorError("Keyed USRP voice packet must contain 320 audio bytes.")
    if not ptt and pcm:
        raise ConnectorError("Unkeyed USRP packet must not contain audio.")
    header = USRP_MAGIC + struct.pack(
        ">7I", int(sequence) & 0xFFFFFFFF, 0, 1 if ptt else 0, 0, USRP_TYPE_VOICE, 0, 0
    )
    return header + pcm


def parse_usrp_packet(packet: bytes) -> dict[str, Any]:
    if len(packet) not in {USRP_HEADER_SIZE, USRP_VOICE_SIZE} or packet[:4] != USRP_MAGIC:
        raise ConnectorError("Malformed USRP packet.")
    sequence, memory, ptt, talkgroup, packet_type, multiplex, reserved = struct.unpack(
        ">7I", packet[4:USRP_HEADER_SIZE]
    )
    if ptt not in {0, 1} or packet_type != USRP_TYPE_VOICE:
        raise ConnectorError("Unsupported USRP packet state or type.")
    if ptt == 1 and len(packet) != USRP_VOICE_SIZE:
        raise ConnectorError("Keyed USRP packet is missing its audio payload.")
    if ptt == 0 and len(packet) != USRP_HEADER_SIZE:
        raise ConnectorError("Unkeyed USRP packet contains unexpected data.")
    return {
        "sequence": sequence,
        "memory": memory,
        "ptt": bool(ptt),
        "talkgroup": talkgroup,
        "type": packet_type,
        "multiplex": multiplex,
        "reserved": reserved,
        "pcm": packet[USRP_HEADER_SIZE:],
    }


class Codec2Adapter:
    """Minimal Codec2 3200 C-API adapter: two 20-ms frames per M17 frame."""

    def __init__(self, library: str | None = None) -> None:
        if sys.byteorder != "little":
            raise ConnectorError("Codec2/USRP audio requires a little-endian host.")
        library_name = library or ctypes.util.find_library("codec2")
        if not library_name:
            raise ConnectorError("libcodec2 was not found.")
        try:
            self.library = ctypes.CDLL(library_name)
        except OSError as exc:
            raise ConnectorError("libcodec2 could not be loaded.") from exc
        self.library.codec2_create.argtypes = [ctypes.c_int]
        self.library.codec2_create.restype = ctypes.c_void_p
        self.library.codec2_destroy.argtypes = [ctypes.c_void_p]
        self.library.codec2_destroy.restype = None
        self.library.codec2_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.POINTER(ctypes.c_short),
        ]
        self.library.codec2_encode.restype = None
        self.library.codec2_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_short),
            ctypes.POINTER(ctypes.c_ubyte),
        ]
        self.library.codec2_decode.restype = None
        self.library.codec2_samples_per_frame.argtypes = [ctypes.c_void_p]
        self.library.codec2_samples_per_frame.restype = ctypes.c_int
        self.library.codec2_bits_per_frame.argtypes = [ctypes.c_void_p]
        self.library.codec2_bits_per_frame.restype = ctypes.c_int
        self.state = self.library.codec2_create(0)  # CODEC2_MODE_3200
        if not self.state:
            raise ConnectorError("Codec2 3200 state could not be created.")
        self.samples_per_frame = int(self.library.codec2_samples_per_frame(self.state))
        self.bits_per_frame = int(self.library.codec2_bits_per_frame(self.state))
        self.bytes_per_frame = (self.bits_per_frame + 7) // 8
        if (self.samples_per_frame, self.bits_per_frame, self.bytes_per_frame) != (160, 64, 8):
            self.close()
            raise ConnectorError("libcodec2 does not expose the required M17 Codec2 3200 geometry.")

    def close(self) -> None:
        if getattr(self, "state", None):
            self.library.codec2_destroy(self.state)
            self.state = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def encode_40ms(self, pcm: bytes) -> bytes:
        if len(pcm) != USRP_AUDIO_SIZE * 2:
            raise ConnectorError("Codec2 encoder requires exactly 40 ms of 8-kHz PCM.")
        output = bytearray()
        for offset in (0, USRP_AUDIO_SIZE):
            speech = (ctypes.c_short * self.samples_per_frame).from_buffer_copy(
                pcm[offset:offset + USRP_AUDIO_SIZE]
            )
            bits = (ctypes.c_ubyte * self.bytes_per_frame)()
            self.library.codec2_encode(self.state, bits, speech)
            output.extend(bytes(bits))
        return bytes(output)

    def decode_40ms(self, payload: bytes) -> bytes:
        if len(payload) != M17_PAYLOAD_SIZE:
            raise ConnectorError("Codec2 decoder requires one 16-byte M17 voice payload.")
        output = bytearray()
        for offset in (0, self.bytes_per_frame):
            bits = (ctypes.c_ubyte * self.bytes_per_frame).from_buffer_copy(
                payload[offset:offset + self.bytes_per_frame]
            )
            speech = (ctypes.c_short * self.samples_per_frame)()
            self.library.codec2_decode(self.state, speech, bits)
            output.extend(bytes(speech))
        return bytes(output)


class AudioBridgeCore:
    """Pure framing/audio state used by the socket runtime and temp self-tests."""

    def __init__(
        self,
        control: ModuleType,
        bridge: dict[str, Any],
        link: Any,
        codec: Any,
        stream_id_factory: Callable[[], int] | None = None,
    ) -> None:
        self.control = control
        self.bridge = bridge
        self.link = link
        self.codec = codec
        self.stream_id_factory = stream_id_factory or (lambda: secrets.randbits(16) or 1)
        self.usrp_sequence = 0
        self.outbound_stream_id = 0
        self.outbound_frame_number = 0
        self.outbound_pcm = bytearray()
        self.inbound_stream_id = 0
        self.inbound_source = ""
        self.inbound_last_epoch = 0.0

    def _next_usrp(self, ptt: bool, pcm: bytes = b"") -> bytes:
        self.usrp_sequence = (self.usrp_sequence + 1) & 0xFFFFFFFF
        return build_usrp_packet(self.usrp_sequence, ptt, pcm)

    def _target(self) -> dict[str, Any]:
        target = self.link.state.get("confirmedTarget")
        if self.link.state.get("linkState") != "linked" or not isinstance(target, dict):
            raise ConnectorError("M17 reflector link is not confirmed.")
        return target

    def _outbound_packet(self, payload: bytes, *, eot: bool = False) -> bytes:
        target = self._target()
        if not self.outbound_stream_id:
            self.outbound_stream_id = int(self.stream_id_factory()) & 0xFFFF or 1
            self.outbound_frame_number = 0
        packet = build_m17_stream(
            self.control,
            self.outbound_stream_id,
            reflector_destination(target["reflector"], target["module"]),
            self.bridge["callsign"],
            self.outbound_frame_number,
            payload,
            eot=eot,
        )
        self.outbound_frame_number = (self.outbound_frame_number + 1) & 0x7FFF
        if eot:
            self.outbound_stream_id = 0
            self.outbound_frame_number = 0
        return packet

    def handle_usrp(self, packet: bytes) -> list[bytes]:
        frame = parse_usrp_packet(packet)
        if frame["ptt"]:
            if self.inbound_stream_id:
                return []
            self._target()
            self.outbound_pcm.extend(frame["pcm"])
            if len(self.outbound_pcm) < USRP_AUDIO_SIZE * 2:
                return []
            pcm = bytes(self.outbound_pcm[:USRP_AUDIO_SIZE * 2])
            del self.outbound_pcm[:USRP_AUDIO_SIZE * 2]
            return [self._outbound_packet(self.codec.encode_40ms(pcm))]
        if not self.outbound_stream_id and not self.outbound_pcm:
            return []
        if self.outbound_pcm:
            self.outbound_pcm.extend(bytes(USRP_AUDIO_SIZE * 2 - len(self.outbound_pcm)))
            payload = self.codec.encode_40ms(bytes(self.outbound_pcm))
        else:
            payload = self.codec.encode_40ms(bytes(USRP_AUDIO_SIZE * 2))
        self.outbound_pcm.clear()
        return [self._outbound_packet(payload, eot=True)]

    def handle_m17(self, packet: bytes, now: float | None = None) -> list[bytes]:
        self._target()
        frame = parse_m17_stream(self.control, packet)
        if self.outbound_stream_id or self.outbound_pcm:
            return []
        if self.inbound_stream_id and self.inbound_stream_id != int(frame["streamId"]):
            return []
        epoch = float(time.time() if now is None else now)
        self.inbound_stream_id = int(frame["streamId"])
        self.inbound_source = str(frame["source"])
        self.inbound_last_epoch = epoch
        self.link.note_stream(frame["source"], frame["streamId"], False, now=epoch)
        pcm = self.codec.decode_40ms(frame["payload"])
        output = [
            self._next_usrp(True, pcm[:USRP_AUDIO_SIZE]),
            self._next_usrp(True, pcm[USRP_AUDIO_SIZE:]),
        ]
        if frame["eot"]:
            self.link.note_stream(frame["source"], frame["streamId"], True, now=epoch)
            self.inbound_stream_id = 0
            self.inbound_source = ""
            self.inbound_last_epoch = 0.0
            output.append(self._next_usrp(False))
        return output

    def tick_audio(self, now: float | None = None) -> list[bytes]:
        epoch = float(time.time() if now is None else now)
        if self.inbound_stream_id and epoch - self.inbound_last_epoch > INBOUND_STREAM_TIMEOUT:
            self.link.note_stream(
                self.inbound_source, self.inbound_stream_id, True, now=epoch
            )
            self.inbound_stream_id = 0
            self.inbound_source = ""
            self.inbound_last_epoch = 0.0
            return [self._next_usrp(False)]
        return []


def codec_readiness(bridge: dict[str, Any], library: str | None = None) -> tuple[Codec2Adapter | None, str]:
    if bridge.get("audioQualified") is not True:
        return None, "M17 audio path has not been explicitly qualified for this bridge instance."
    try:
        return Codec2Adapter(library), ""
    except ConnectorError as exc:
        return None, str(exc)


class ConnectorRuntime:
    def __init__(
        self,
        control: ModuleType,
        bridge: dict[str, Any],
        codec: Codec2Adapter,
        runner: Runner = default_runner,
    ) -> None:
        self.control = control
        self.bridge = bridge
        self.codec = codec
        self.runner = runner
        self.link = control.M17LinkState()
        self.core = AudioBridgeCore(control, bridge, self.link, codec)
        self.selector = selectors.DefaultSelector()
        self.m17_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.usrp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.remote: tuple[str, int] | None = None
        self.last_command_id = ""
        self.last_state_write = 0.0
        self.started_ns = time.time_ns()
        self.next_reconnect_epoch = 0.0
        self.disconnect_context = ""
        self.failed_connect_error = ""

    def open(self) -> None:
        self.m17_socket.bind((self.bridge["m17BindAddress"], self.bridge["m17BindPort"]))
        self.usrp_socket.bind((self.bridge["usrpBindAddress"], self.bridge["usrpRxPort"]))
        self.m17_socket.setblocking(False)
        self.usrp_socket.setblocking(False)
        self.selector.register(self.m17_socket, selectors.EVENT_READ, "m17")
        self.selector.register(self.usrp_socket, selectors.EVENT_READ, "usrp")
        if self.bridge["cardType"] == "standard":
            self.connect(self.bridge["fixedTarget"])

    def close(self) -> None:
        try:
            if self.link.state.get("linkState") in {
                "connecting", "digital_linked", "linked", "disconnecting", "failed"
            } and self.remote:
                self.m17_socket.sendto(self.control.disc_packet(self.bridge["callsign"]), self.remote)
        except OSError:
            pass
        self.selector.close()
        self.m17_socket.close()
        self.usrp_socket.close()
        self.codec.close()

    def _resolve(self, target: dict[str, Any]) -> tuple[str, int]:
        try:
            results = socket.getaddrinfo(
                target["host"], target["port"], socket.AF_INET, socket.SOCK_DGRAM
            )
        except socket.gaierror as exc:
            raise ConnectorError("M17 reflector hostname could not be resolved.") from exc
        if not results:
            raise ConnectorError("M17 reflector hostname returned no IPv4 address.")
        return str(results[0][4][0]), int(target["port"])

    def connect(self, target: dict[str, Any]) -> None:
        target = self.control.validate_target(target)
        self.control.validate_permission(self.bridge.get("permission"))
        if self.remote and self.link.state.get("linkState") in {"connecting", "linked"}:
            self.m17_socket.sendto(self.control.disc_packet(self.bridge["callsign"]), self.remote)
        self.remote = self._resolve(target)
        self.link.request(target)
        self.disconnect_context = ""
        self.failed_connect_error = ""
        self.next_reconnect_epoch = 0.0
        self.m17_socket.sendto(
            self.control.conn_packet(self.bridge["callsign"], target["module"]), self.remote
        )

    def _observed_allstar_link(self) -> bool | None:
        try:
            return direct_linked(
                self.bridge["localNode"], self.bridge["node"], self.runner
            )
        except ConnectorError:
            return None

    def _finish_digital_disconnect(self) -> None:
        context = self.disconnect_context or "remote"
        try:
            set_direct_link(
                self.bridge["localNode"], self.bridge["node"], False, self.runner
            )
        except ConnectorError as exc:
            self.link.fail_disconnect(str(exc), self._observed_allstar_link())
            self.remote = None
            return
        self.remote = None
        if context == "failed_connect":
            self.link.complete_failed_connect(
                self.failed_connect_error or "The AllStar bridge link failed."
            )
        else:
            self.link.disconnect(
                "Reflector disconnected this client." if context == "remote" else ""
            )
        self.disconnect_context = ""
        self.failed_connect_error = ""
        if self.bridge["cardType"] == "standard":
            self.next_reconnect_epoch = time.time() + STANDARD_RECONNECT_DELAY

    def _unlink_after_digital_loss(self) -> None:
        try:
            set_direct_link(
                self.bridge["localNode"], self.bridge["node"], False, self.runner
            )
            self.link.mark_allstar_state(False)
        except ConnectorError as exc:
            self.link.fail_disconnect(str(exc), self._observed_allstar_link())

    def disconnect(self, reason: str = "User requested disconnect.") -> None:
        self.disconnect_context = "user"
        state = str(self.link.state.get("linkState", ""))
        if self.remote and (
            self.link.state.get("digitalLinked") is True
            or state in {"connecting", "digital_linked", "linked", "failed", "disconnecting", "disconnect_failed"}
        ):
            self.link.begin_disconnect()
            self.m17_socket.sendto(
                self.control.disc_packet(self.bridge["callsign"]), self.remote
            )
            return
        self.link.confirm_digital_disconnect(reason)
        self._finish_digital_disconnect()

    def _read_command(self) -> None:
        path = self.bridge["commandPath"]
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            self.link.state["lastError"] = "M17 control command is unreadable or invalid."
            raise ConnectorError("M17 control command is unreadable or invalid.") from exc
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise ConnectorError("M17 control command schema is invalid.")
        expected_keys = {
            "schema", "commandId", "createdEpoch", "createdNs", "bridgeId",
            "action", "target", "user",
        }
        if set(payload) != expected_keys:
            raise ConnectorError("M17 control command fields are invalid.")
        if payload.get("user") != self.control.clean_user(payload.get("user")):
            raise ConnectorError("M17 control command user is invalid.")
        command_id = str(payload.get("commandId", ""))
        if not command_id or command_id == self.last_command_id:
            return
        if payload.get("bridgeId") != self.bridge["id"]:
            raise ConnectorError("M17 control command targets another bridge.")
        try:
            created = int(payload.get("createdEpoch", 0))
            created_ns = int(payload.get("createdNs", 0))
        except (TypeError, ValueError) as exc:
            raise ConnectorError("M17 control command timestamp is invalid.") from exc
        if abs(int(time.time()) - created) > COMMAND_MAX_AGE:
            raise ConnectorError("M17 control command is stale.")
        if created_ns <= self.started_ns:
            raise ConnectorError("M17 control command predates this connector process.")
        action = payload.get("action")
        if action == "connect":
            raw_target = payload.get("target")
            if not isinstance(raw_target, dict):
                raise ConnectorError("M17 connect command target is invalid.")
            target = self.control.approved_destination(
                self.bridge, raw_target.get("reflector"), raw_target.get("module")
            )
            if target != raw_target:
                raise ConnectorError("M17 connect command target does not match Settings.")
            self.connect(target)
        elif action == "disconnect":
            if payload.get("target") is not None:
                raise ConnectorError("M17 disconnect command must not include a target.")
            self.disconnect()
        else:
            raise ConnectorError("Unsupported M17 control command.")
        self.last_command_id = command_id
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _handle_m17_datagram(self, packet: bytes, address: tuple[str, int]) -> None:
        if self.remote is None or address[0] != self.remote[0] or address[1] != self.remote[1]:
            return
        if packet[:4] == M17_MAGIC:
            try:
                usrp_packets = self.core.handle_m17(packet)
            except EncryptedM17Error:
                self.disconnect("Encrypted M17 stream was rejected.")
                return
            for usrp_packet in usrp_packets:
                self.usrp_socket.sendto(
                    usrp_packet,
                    (self.bridge["usrpRemoteAddress"], self.bridge["usrpTxPort"]),
                )
            return
        prior_state = str(self.link.state.get("linkState", ""))
        response = self.link.handle_control(packet)
        if response == "PONG" and self.remote:
            self.m17_socket.sendto(
                self.control.keepalive_packet(b"PONG", self.bridge["callsign"]), self.remote
            )
        magic = packet[:4]
        if magic == b"ACKN":
            if prior_state == "disconnecting":
                if self.remote:
                    self.m17_socket.sendto(
                        self.control.disc_packet(self.bridge["callsign"]), self.remote
                    )
                return
            try:
                set_direct_link(
                    self.bridge["localNode"], self.bridge["node"], True, self.runner
                )
                self.link.mark_combined_linked()
            except (ConnectorError, self.control.ControlError) as exc:
                self.failed_connect_error = str(exc)[:160]
                self.disconnect_context = "failed_connect"
                self.link.fail_connect_allstar(
                    self.failed_connect_error, self._observed_allstar_link()
                )
                if self.remote:
                    self.m17_socket.sendto(
                        self.control.disc_packet(self.bridge["callsign"]), self.remote
                    )
            return
        if magic == b"NACK":
            self.remote = None
            self._unlink_after_digital_loss()
            if self.bridge["cardType"] == "standard":
                self.next_reconnect_epoch = time.time() + STANDARD_RECONNECT_DELAY
            return
        if magic == b"DISC" and self.link.state.get("linkState") == "digital_disconnected":
            if not self.disconnect_context:
                self.disconnect_context = "remote"
            self._finish_digital_disconnect()
            return
        if (
            self.bridge["cardType"] == "standard"
            and self.link.state.get("linkState") in {"disconnected", "rejected"}
        ):
            self.next_reconnect_epoch = time.time() + STANDARD_RECONNECT_DELAY

    def _publish_state(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_state_write < STATE_WRITE_INTERVAL:
            return
        payload = dict(self.link.state)
        payload.update({
            "schema": 1,
            "bridgeId": self.bridge["id"],
            "updatedEpoch": now,
            "audioReady": True,
            "dependencyError": "",
        })
        self.control.atomic_json(self.bridge["statePath"], payload)
        self.last_state_write = now

    def run_once(self, timeout: float = 0.25) -> None:
        try:
            self._read_command()
        except (ConnectorError, self.control.ControlError) as exc:
            self.link.state["lastError"] = str(exc)[:160]
        for key, _events in self.selector.select(timeout):
            try:
                packet, address = key.fileobj.recvfrom(2048)
                if key.data == "m17":
                    self._handle_m17_datagram(packet, address)
                else:
                    if address != (self.bridge["usrpRemoteAddress"], self.bridge["usrpTxPort"]):
                        continue
                    for m17_packet in self.core.handle_usrp(packet):
                        if self.remote:
                            self.m17_socket.sendto(m17_packet, self.remote)
            except (ConnectorError, self.control.ControlError, OSError) as exc:
                self.link.state["lastError"] = str(exc)[:160]
        for usrp_packet in self.core.tick_audio():
            self.usrp_socket.sendto(
                usrp_packet,
                (self.bridge["usrpRemoteAddress"], self.bridge["usrpTxPort"]),
            )
        if self.link.tick():
            self.remote = None
            self._unlink_after_digital_loss()
            if self.bridge["cardType"] == "standard":
                self.next_reconnect_epoch = time.time() + STANDARD_RECONNECT_DELAY
        if (
            self.bridge["cardType"] == "standard"
            and self.next_reconnect_epoch
            and time.time() >= self.next_reconnect_epoch
        ):
            try:
                self.connect(self.bridge["fixedTarget"])
            except (ConnectorError, self.control.ControlError, OSError) as exc:
                self.link.state["lastError"] = str(exc)[:160]
                self.next_reconnect_epoch = time.time() + STANDARD_RECONNECT_DELAY
        self._publish_state()

    def run(self) -> None:
        try:
            self.open()
            self._publish_state(force=True)
            while True:
                self.run_once()
        finally:
            self.close()


class _FakeCodec:
    def encode_40ms(self, pcm: bytes) -> bytes:
        if len(pcm) != 640:
            raise ConnectorError("fake test codec input length")
        return bytes(range(16))

    def decode_40ms(self, payload: bytes) -> bytes:
        if len(payload) != 16:
            raise ConnectorError("fake test codec input length")
        return bytes((index % 251 for index in range(640)))

    def close(self) -> None:
        pass


class _FakeAsteriskRunner:
    def __init__(
        self, linked: bool = False, fail_link: bool = False, fail_unlink: bool = False
    ) -> None:
        self.linked = linked
        self.fail_link = fail_link
        self.fail_unlink = fail_unlink

    def __call__(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        command = argv[-1]
        if command.startswith("rpt lstats "):
            output = (
                "NODE      PEER                RECONNECTS  DIRECTION  CONNECT TIME        CONNECT STATE\n"
                "----      ----                ----------  ---------  ------------        -------------\n"
            )
            if self.linked:
                output += "1996      127.0.0.1           0           OUT        00:00:01:000        ESTABLISHED\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        if " ilink 3 " in command:
            if self.fail_link:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            self.linked = True
            return subprocess.CompletedProcess(argv, 0, "command accepted\n", "")
        if " ilink 11 " in command:
            if self.fail_unlink:
                return subprocess.CompletedProcess(argv, 1, "", "failed")
            self.linked = False
            return subprocess.CompletedProcess(argv, 0, "command accepted\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")


def self_test() -> None:
    control = load_control_module()
    with tempfile.TemporaryDirectory(prefix="asr-m17-extensionless-") as directory:
        installed_path = Path(directory) / "allscan-reimagined-m17-bridge-control"
        installed_path.write_bytes(CONTROL_SCRIPT.read_bytes())
        installed_path.chmod(0o755)
        installed_control = load_control_module(installed_path)
        assert installed_control.validate_module("c") == "C"
    assert m17_crc(b"123456789") == 0x772B
    target = {
        "reflector": "M17-M17",
        "host": "127.0.0.1",
        "port": 17000,
        "module": "C",
        "encrypted": False,
    }
    bridge = {
        "id": "m17_net",
        "mode": "m17",
        "cardType": "m17_net",
        "localNode": "123456",
        "node": "1996",
        "permission": "approved",
        "callsign": "N0CALL",
        "audioQualified": True,
        "approvedDestinations": [target],
    }
    link = control.M17LinkState()
    link.request(target, now=10.0)
    link.handle_control(b"ACKN", now=11.0)
    link.mark_combined_linked()
    core = AudioBridgeCore(control, bridge, link, _FakeCodec(), stream_id_factory=lambda: 0x1234)

    usrp_voice = build_usrp_packet(1, True, bytes(320))
    assert parse_usrp_packet(usrp_voice)["ptt"] is True
    assert len(core.handle_usrp(usrp_voice)) == 0
    outbound = core.handle_usrp(build_usrp_packet(2, True, bytes([1]) * 320))
    assert len(outbound) == 1
    parsed_outbound = parse_m17_stream(control, outbound[0])
    assert parsed_outbound["streamId"] == 0x1234
    assert parsed_outbound["source"] == "N0CALL"
    assert parsed_outbound["destination"] == "M17-M17 C"
    eot = core.handle_usrp(build_usrp_packet(3, False))
    assert len(eot) == 1 and parse_m17_stream(control, eot[0])["eot"] is True

    inbound = build_m17_stream(
        control, 0x4321, "M17-M17 C", "N0CALL", 7, bytes(range(16)), eot=False
    )
    usrp_output = core.handle_m17(inbound, now=12.0)
    assert len(usrp_output) == 2
    assert all(parse_usrp_packet(packet)["ptt"] for packet in usrp_output)
    assert link.state["talker"] == "N0CALL"
    assert link.state["talkerAuthenticated"] is False
    assert core.handle_usrp(build_usrp_packet(4, True, bytes(320))) == []
    assert not core.outbound_pcm
    competing = build_m17_stream(
        control, 0x9999, "M17-M17 C", "AB1CD", 1, bytes(range(16))
    )
    assert core.handle_m17(competing, now=12.1) == []
    assert link.state["talker"] == "N0CALL"
    timed_out_audio = core.tick_audio(now=14.1)
    assert len(timed_out_audio) == 1
    assert parse_usrp_packet(timed_out_audio[0])["ptt"] is False
    assert link.state["talker"] == ""
    usrp_output = core.handle_m17(inbound, now=15.0)
    assert len(usrp_output) == 2
    inbound_eot = build_m17_stream(
        control, 0x4321, "M17-M17 C", "N0CALL", 8, bytes(range(16)), eot=True
    )
    usrp_output = core.handle_m17(inbound_eot, now=15.1)
    assert len(usrp_output) == 3
    assert parse_usrp_packet(usrp_output[-1])["ptt"] is False
    assert link.state["talker"] == ""

    corrupted = bytearray(inbound)
    corrupted[40] ^= 1
    try:
        parse_m17_stream(control, bytes(corrupted))
    except ConnectorError:
        pass
    else:
        raise AssertionError("bad M17 CRC was accepted")
    encrypted = build_m17_stream(
        control, 1, "M17-M17 C", "N0CALL", 1, bytes(16), frame_type=M17_VOICE_TYPE
    )
    encrypted = bytearray(encrypted)
    encrypted[19] |= 0x08
    encrypted[-2:] = struct.pack(">H", m17_crc(encrypted[:-2]))
    try:
        parse_m17_stream(control, bytes(encrypted))
    except EncryptedM17Error:
        pass
    else:
        raise AssertionError("encrypted M17 stream was accepted")

    unlinked = control.M17LinkState()
    unlinked_core = AudioBridgeCore(control, bridge, unlinked, _FakeCodec())
    try:
        unlinked_core.handle_m17(inbound, now=20.0)
    except ConnectorError:
        pass
    else:
        raise AssertionError("inbound audio was accepted before link confirmation")

    unqualified = dict(bridge, audioQualified=False)
    codec, error = codec_readiness(unqualified)
    assert codec is None and "not been explicitly qualified" in error
    codec, error = codec_readiness(bridge, "/definitely/not/libcodec2.so")
    assert codec is None and error

    with tempfile.TemporaryDirectory(prefix="asr-m17-connector-") as directory:
        state_path = Path(directory) / "state.json"
        payload = dict(link.state, schema=1, bridgeId=bridge["id"], updatedEpoch=time.time(), audioReady=False)
        control.atomic_json(state_path, payload)
        assert json.loads(state_path.read_text(encoding="utf-8"))["audioReady"] is False

    links_output = (
        "NODE      PEER                RECONNECTS  DIRECTION  CONNECT TIME        CONNECT STATE\n"
        "----      ----                ----------  ---------  ------------        -------------\n"
        "1996      127.0.0.1           0           OUT        00:00:01:000        ESTABLISHED\n"
    )
    assert ("1996", "OUT") in parse_lstats_links(links_output)
    fake_runner = _FakeAsteriskRunner()
    set_direct_link("123456", "1996", True, fake_runner)
    assert fake_runner.linked is True
    set_direct_link("123456", "1996", False, fake_runner)
    assert fake_runner.linked is False

    runtime_runner = _FakeAsteriskRunner()
    runtime = ConnectorRuntime(control, bridge, _FakeCodec(), runtime_runner)
    runtime.remote = ("127.0.0.1", 17000)
    runtime.link.request(target, now=time.time())
    runtime._handle_m17_datagram(b"ACKN", runtime.remote)
    assert runtime.link.state["linkState"] == "linked"
    assert runtime.link.state["digitalLinked"] is True
    assert runtime.link.state["allstarLinked"] is True
    runtime.disconnect()
    assert runtime.link.state["linkState"] == "disconnecting"
    runtime._handle_m17_datagram(b"DISC", runtime.remote)
    assert runtime.link.state["linkState"] == "disconnected"
    assert runtime_runner.linked is False
    runtime.close()

    failing_runner = _FakeAsteriskRunner(fail_link=True)
    failing_runtime = ConnectorRuntime(control, bridge, _FakeCodec(), failing_runner)
    failing_runtime.remote = ("127.0.0.1", 17000)
    failing_runtime.link.request(target, now=time.time())
    failing_runtime._handle_m17_datagram(b"ACKN", failing_runtime.remote)
    assert failing_runtime.link.state["linkState"] == "failed"
    assert failing_runtime.link.state["digitalLinked"] is True
    failing_runtime._handle_m17_datagram(b"DISC", failing_runtime.remote)
    assert failing_runtime.link.state["linkState"] == "failed"
    assert failing_runtime.link.state["digitalLinked"] is False
    assert failing_runtime.link.state["allstarLinked"] is False
    failing_runtime.close()

    partial_runner = _FakeAsteriskRunner(linked=True, fail_unlink=True)
    partial_runtime = ConnectorRuntime(control, bridge, _FakeCodec(), partial_runner)
    partial_runtime.remote = ("127.0.0.1", 17000)
    partial_runtime.link.request(target, now=time.time())
    partial_runtime._handle_m17_datagram(b"ACKN", partial_runtime.remote)
    assert partial_runtime.link.state["linkState"] == "linked"
    partial_runtime.disconnect()
    partial_runtime._handle_m17_datagram(b"DISC", partial_runtime.remote)
    assert partial_runtime.link.state["linkState"] == "partial_failure"
    assert partial_runtime.link.state["digitalLinked"] is False
    assert partial_runtime.link.state["allstarLinked"] is True
    partial_runtime.close()


def publish_not_ready(control: ModuleType, bridge: dict[str, Any], error: str) -> None:
    payload = control.initial_link_state()
    payload.update({
        "schema": 1,
        "bridgeId": bridge["id"],
        "updatedEpoch": time.time(),
        "audioReady": False,
        "dependencyError": str(error)[:160],
        "lastError": str(error)[:160],
    })
    control.atomic_json(bridge["statePath"], payload)


def main() -> int:
    control = load_control_module()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default="")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("M17 USRP connector self-test passed")
        return 0
    if not args.bridge or args.run == args.check:
        parser.error("--bridge and exactly one of --check or --run are required")
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "M17 connector must run as root."}, separators=(",", ":")))
        return 1
    try:
        bridge = control.bridge_config(args.bridge, control.CONFIG_PATH)
        codec, error = codec_readiness(bridge)
        if codec is None:
            publish_not_ready(control, bridge, error)
            print(json.dumps({
                "ok": False,
                "bridgeId": bridge["id"],
                "audioReady": False,
                "error": error,
            }, separators=(",", ":")))
            return 1
        if args.check:
            codec.close()
            print(json.dumps({
                "ok": True,
                "bridgeId": bridge["id"],
                "audioReady": True,
                "codec": "Codec2 3200",
                "usrp": "8 kHz signed 16-bit PCM",
            }, separators=(",", ":")))
            return 0
        ConnectorRuntime(control, bridge, codec).run()
    except (control.ControlError, ConnectorError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
