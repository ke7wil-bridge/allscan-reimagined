#!/usr/bin/env python3
"""Fail-closed configuration, control, and protocol state for ASR M17 bridges.

This module deliberately does not claim a network link when a connect command is
queued.  Only the connector may publish ``linked`` after receiving MREFD ACKN.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
RUN_DIR = Path("/run/allscan-reimagined-m17")
AUDIT_LOG = Path("/var/log/allscan-reimagined/m17-bridge-control.log")
BRIDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
NODE_RE = re.compile(r"^[0-9]{3,10}$")
REFLECTOR_RE = re.compile(r"^M17-[A-Z0-9]{3}$")
CALLSIGN_RE = re.compile(r"^[A-Z0-9][A-Z0-9./-]{2,8}$")
HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
MODULE_RE = re.compile(r"^[A-Z]$")
USERNAME_RE = re.compile(r"[^A-Za-z0-9_.@+-]")
KEEPALIVE_TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0


class ControlError(RuntimeError):
    pass


def require_secure_config_file(path: Path, expected_uid: int = 0) -> None:
    """Accept the protected Settings file, including its intentional web-group write bit."""
    try:
        parent = path.parent.lstat()
        info = path.lstat()
    except OSError as exc:
        raise ControlError("ASR bridge configuration does not exist.") from exc
    if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
        raise ControlError("ASR configuration directory is unsafe.")
    if parent.st_uid != expected_uid or stat.S_IMODE(parent.st_mode) & 0o002:
        raise ControlError("ASR configuration directory must be owner-controlled.")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ControlError("ASR bridge configuration must be a regular file, not a link.")
    if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) & 0o002:
        raise ControlError("ASR bridge configuration must be owner-controlled.")


def load_config(
    path: Path = CONFIG_PATH, *, expected_uid: int | None = None
) -> dict[str, Any]:
    if expected_uid is not None:
        require_secure_config_file(path, expected_uid)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("ASR bridge configuration could not be read.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("bridges", []), list):
        raise ControlError("ASR bridge configuration is invalid.")
    return payload


def validate_host(value: object, label: str) -> str:
    host = str(value or "").strip()
    if not host or any(character in host for character in "\r\n\0/\\[]@;"):
        raise ControlError(f"{label} is invalid.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOSTNAME_RE.fullmatch(host):
            raise ControlError(f"{label} is invalid.")
    return host


def validate_ip(value: object, label: str) -> str:
    text = str(value or "").strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ControlError(f"{label} must be an IP address.") from exc
    if address.version != 4:
        raise ControlError(f"{label} must be an IPv4 address.")
    return str(address)


def validate_port(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ControlError(f"{label} is invalid.")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ControlError(f"{label} is invalid.") from exc
    if not 1 <= port <= 65535:
        raise ControlError(f"{label} must be between 1 and 65535.")
    return port


def validate_callsign(value: object) -> str:
    callsign = str(value or "").strip().upper()
    if (
        not CALLSIGN_RE.fullmatch(callsign)
        or not any(character.isalpha() for character in callsign)
        or not any(character.isdigit() for character in callsign)
    ):
        raise ControlError("M17 bridge callsign is invalid.")
    return callsign


def validate_reflector(value: object) -> str:
    reflector = str(value or "").strip().upper()
    if not REFLECTOR_RE.fullmatch(reflector):
        raise ControlError("M17 reflector must use the M17-XXX designator format.")
    return reflector


def validate_module(value: object) -> str:
    module = str(value or "").strip().upper()
    if not MODULE_RE.fullmatch(module):
        raise ControlError("M17 reflector module must be one letter from A through Z.")
    return module


def validate_permission(value: object) -> str:
    permission = str(value or "").strip()
    if permission not in {"self_owned", "approved"}:
        raise ControlError("M17 bridge permission is missing or not approved.")
    return permission


def validate_target(entry: object, label: str = "M17 target") -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ControlError(f"{label} is invalid.")
    encrypted = entry.get("encrypted")
    if encrypted is not False:
        raise ControlError(f"{label} is encrypted or its encryption state is unknown.")
    return {
        "reflector": validate_reflector(entry.get("reflector")),
        "host": validate_host(entry.get("host"), f"{label} host"),
        "port": validate_port(entry.get("port"), f"{label} port"),
        "module": validate_module(entry.get("module")),
        "encrypted": False,
    }


def target_key(target: dict[str, Any]) -> tuple[str, str]:
    return str(target["reflector"]), str(target["module"])


def _runtime_path(bridge_id: str, suffix: str, value: object) -> Path:
    expected = RUN_DIR / f"{bridge_id}.{suffix}.json"
    path = Path(str(value or expected))
    if path != expected:
        raise ControlError(f"M17 {suffix} path must be {expected}.")
    return path


def _fixed_target(bridge: dict[str, Any]) -> dict[str, Any]:
    return validate_target(
        {
            "reflector": bridge.get("m17Reflector"),
            "host": bridge.get("m17Host"),
            "port": bridge.get("m17Port"),
            "module": bridge.get("m17Module"),
            "encrypted": bridge.get("m17Encrypted"),
        },
        "Fixed M17 target",
    )


def validate_bridge(bridge: object, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bridge, dict):
        raise ControlError("M17 bridge configuration is invalid.")
    bridge_id = str(bridge.get("id", ""))
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("M17 bridge ID is invalid.")
    if str(bridge.get("mode", "")).strip().lower() != "m17":
        raise ControlError("Selected bridge mode is not M17.")
    card_type = str(bridge.get("cardType", "standard"))
    if card_type not in {"standard", "m17_net"}:
        raise ControlError("Selected bridge is not an M17 Standard or Net Bridge.")
    local_node = str(config.get("node", "")).strip()
    bridge_node = str(bridge.get("node", "")).strip()
    if not NODE_RE.fullmatch(local_node) or not NODE_RE.fullmatch(bridge_node):
        raise ControlError("M17 bridge AllStar node configuration is invalid.")
    if local_node == bridge_node:
        raise ControlError("M17 bridge node must be separate from the main AllStar node.")
    permission = validate_permission(bridge.get("bridgePermission"))
    callsign = validate_callsign(bridge.get("m17Callsign"))
    m17_bind_address = validate_ip(bridge.get("m17BindAddress"), "M17 bind address")
    m17_bind_port = validate_port(bridge.get("m17BindPort"), "M17 bind port")
    usrp_bind_address = validate_ip(
        bridge.get("m17UsrpBindAddress"), "M17 USRP bind address"
    )
    usrp_rx_port = validate_port(bridge.get("m17UsrpRxPort"), "M17 USRP receive port")
    usrp_remote_address = validate_ip(
        bridge.get("m17UsrpRemoteAddress"), "M17 USRP remote address"
    )
    usrp_tx_port = validate_port(bridge.get("m17UsrpTxPort"), "M17 USRP transmit port")
    if m17_bind_port == usrp_rx_port:
        raise ControlError("M17 and USRP receive sockets must not share a port.")

    approved: list[dict[str, Any]] = []
    raw_approved = bridge.get("approvedDestinations", [])
    if not isinstance(raw_approved, list) or len(raw_approved) > 256:
        raise ControlError("M17 approved destination list is invalid.")
    seen_targets: set[tuple[str, str]] = set()
    for raw_target in raw_approved:
        target = validate_target(raw_target, "Approved M17 destination")
        key = target_key(target)
        if key in seen_targets:
            raise ControlError("M17 approved destinations contain a duplicate reflector/module.")
        seen_targets.add(key)
        approved.append(target)

    fixed_target = None if card_type == "m17_net" else _fixed_target(bridge)
    if card_type == "m17_net" and not approved:
        raise ControlError("M17 Net Bridge needs at least one approved destination.")

    for other in config.get("bridges", []):
        if not isinstance(other, dict) or other is bridge:
            continue
        other_id = str(other.get("id", ""))
        if other_id == bridge_id:
            raise ControlError("M17 bridge ID overlaps another bridge.")
        if str(other.get("node", "")).strip() == bridge_node:
            raise ControlError("M17 bridge node overlaps another configured bridge.")
        local_receive_ports = {m17_bind_port, usrp_rx_port}
        for field in ("m17BindPort", "m17UsrpRxPort"):
            try:
                other_value = int(other.get(field))
            except (TypeError, ValueError):
                continue
            if other_value in local_receive_ports:
                raise ControlError("M17/USRP receive port overlaps another bridge instance.")
        if str(other.get("m17Callsign", "")).strip().upper() == callsign:
            raise ControlError("M17 callsign overlaps another configured bridge instance.")

    return {
        "id": bridge_id,
        "mode": "m17",
        "cardType": card_type,
        "localNode": local_node,
        "node": bridge_node,
        "permission": permission,
        "callsign": callsign,
        "m17BindAddress": m17_bind_address,
        "m17BindPort": m17_bind_port,
        "usrpBindAddress": usrp_bind_address,
        "usrpRxPort": usrp_rx_port,
        "usrpRemoteAddress": usrp_remote_address,
        "usrpTxPort": usrp_tx_port,
        "audioQualified": bridge.get("m17AudioQualified") is True,
        "statePath": _runtime_path(bridge_id, "state", bridge.get("m17StatePath")),
        "commandPath": _runtime_path(bridge_id, "command", bridge.get("m17CommandPath")),
        "fixedTarget": fixed_target,
        "approvedDestinations": approved,
    }


def bridge_config(
    bridge_id: str,
    path: Path = CONFIG_PATH,
    *,
    expected_uid: int = 0,
) -> dict[str, Any]:
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    config = load_config(path, expected_uid=expected_uid)
    matches = [
        bridge for bridge in config.get("bridges", [])
        if isinstance(bridge, dict) and bridge.get("id") == bridge_id
    ]
    if len(matches) != 1:
        raise ControlError("Configured M17 bridge was not found or is duplicated.")
    return validate_bridge(matches[0], config)


def approved_destination(bridge: dict[str, Any], reflector: object, module: object) -> dict[str, Any]:
    requested_key = (validate_reflector(reflector), validate_module(module))
    if bridge.get("cardType") != "m17_net":
        fixed = bridge.get("fixedTarget")
        if isinstance(fixed, dict) and target_key(fixed) == requested_key:
            return fixed
        raise ControlError("Standard M17 Bridge destination is fixed in Settings.")
    for target in bridge.get("approvedDestinations", []):
        if target_key(target) == requested_key:
            return dict(target)
    raise ControlError("M17 destination is not in this bridge's approved destination list.")


def encode_callsign(value: str) -> bytes:
    text = str(value).strip().upper()
    if not text or len(text) > 9:
        raise ControlError("M17 address is invalid.")
    alphabet = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."
    number = 0
    for character in reversed(text):
        try:
            digit = alphabet.index(character)
        except ValueError as exc:
            raise ControlError("M17 address contains an unsupported character.") from exc
        number = number * 40 + digit
    if not 0 < number < (1 << 48):
        raise ControlError("M17 address is outside the encodable range.")
    return number.to_bytes(6, "big")


def decode_callsign(value: bytes) -> str:
    if len(value) != 6:
        raise ControlError("Encoded M17 address must be six bytes.")
    alphabet = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."
    number = int.from_bytes(value, "big")
    if number == 0:
        return ""
    characters: list[str] = []
    while number and len(characters) < 9:
        number, digit = divmod(number, 40)
        characters.append(alphabet[digit])
    if number:
        raise ControlError("Encoded M17 address is outside the standard range.")
    return "".join(characters).rstrip()


def conn_packet(callsign: str, module: str) -> bytes:
    return b"CONN" + encode_callsign(callsign) + validate_module(module).encode("ascii")


def disc_packet(callsign: str) -> bytes:
    return b"DISC" + encode_callsign(callsign)


def keepalive_packet(magic: bytes, callsign: str) -> bytes:
    if magic not in {b"PING", b"PONG"}:
        raise ControlError("Unsupported M17 keepalive packet type.")
    return magic + encode_callsign(callsign)


def parse_control_packet(packet: bytes) -> dict[str, Any]:
    if len(packet) < 4:
        raise ControlError("M17 control packet is too short.")
    magic = packet[:4]
    expected_lengths = {
        b"ACKN": {4},
        b"NACK": {4},
        b"DISC": {4, 10},
        b"PING": {10},
        b"PONG": {10},
    }
    if magic not in expected_lengths or len(packet) not in expected_lengths[magic]:
        raise ControlError("Unsupported or malformed M17 control packet.")
    result: dict[str, Any] = {"magic": magic.decode("ascii")}
    if len(packet) == 10:
        result["callsign"] = decode_callsign(packet[4:10])
    return result


def initial_link_state() -> dict[str, Any]:
    return {
        "linkState": "disconnected",
        "digitalLinked": False,
        "allstarLinked": False,
        "allstarLinkState": "unlinked",
        "requestedTarget": None,
        "confirmedTarget": None,
        "requestEpoch": 0.0,
        "disconnectRequestEpoch": 0.0,
        "lastKeepaliveEpoch": 0.0,
        "talker": "",
        "talkerAuthenticated": False,
        "talkerEpoch": 0.0,
        "streamId": 0,
        "lastError": "",
    }


class M17LinkState:
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state = initial_link_state()
        if isinstance(state, dict):
            for key in self.state:
                if key in state:
                    self.state[key] = state[key]

    def request(self, target: dict[str, Any], now: float | None = None) -> None:
        validated = validate_target(target)
        self.state.update({
            "linkState": "connecting",
            "digitalLinked": False,
            "requestedTarget": validated,
            "confirmedTarget": None,
            "requestEpoch": float(time.time() if now is None else now),
            "disconnectRequestEpoch": 0.0,
            "lastKeepaliveEpoch": 0.0,
            "talker": "",
            "talkerAuthenticated": False,
            "streamId": 0,
            "lastError": "",
        })

    def disconnect(self, reason: str = "") -> None:
        self.state.update({
            "linkState": "disconnected",
            "digitalLinked": False,
            "allstarLinked": False,
            "allstarLinkState": "unlinked",
            "requestedTarget": None,
            "confirmedTarget": None,
            "requestEpoch": 0.0,
            "disconnectRequestEpoch": 0.0,
            "lastKeepaliveEpoch": 0.0,
            "talker": "",
            "talkerAuthenticated": False,
            "talkerEpoch": 0.0,
            "streamId": 0,
            "lastError": str(reason)[:160],
        })

    def begin_disconnect(self, now: float | None = None) -> None:
        self.state.update({
            "linkState": "disconnecting",
            "disconnectRequestEpoch": float(time.time() if now is None else now),
            "talker": "",
            "talkerAuthenticated": False,
            "talkerEpoch": float(time.time() if now is None else now),
            "streamId": 0,
            "lastError": "",
        })

    def confirm_digital_disconnect(self, reason: str = "") -> None:
        self.state.update({
            "linkState": "digital_disconnected",
            "digitalLinked": False,
            "requestedTarget": None,
            "confirmedTarget": None,
            "requestEpoch": 0.0,
            "disconnectRequestEpoch": 0.0,
            "lastKeepaliveEpoch": 0.0,
            "talker": "",
            "talkerAuthenticated": False,
            "streamId": 0,
            "lastError": str(reason)[:160],
        })

    def mark_combined_linked(self) -> None:
        if self.state.get("digitalLinked") is not True:
            raise ControlError("M17 digital link is not confirmed.")
        self.state.update({
            "linkState": "linked",
            "allstarLinked": True,
            "allstarLinkState": "linked",
            "lastError": "",
        })

    def mark_allstar_state(self, linked: bool | None) -> None:
        self.state["allstarLinked"] = linked
        self.state["allstarLinkState"] = (
            "linked" if linked is True else "unlinked" if linked is False else "unknown"
        )

    def fail_connect_allstar(self, error: str, linked: bool | None = None) -> None:
        self.mark_allstar_state(linked)
        self.state.update({
            "linkState": "failed",
            "lastError": str(error)[:160],
            "talker": "",
            "talkerAuthenticated": False,
            "streamId": 0,
        })

    def fail_disconnect(self, error: str, linked: bool | None) -> None:
        self.mark_allstar_state(linked)
        self.state.update({
            "linkState": (
                "disconnect_failed" if self.state.get("digitalLinked") else "partial_failure"
            ),
            "lastError": str(error)[:160],
        })

    def complete_failed_connect(self, error: str) -> None:
        self.state.update({
            "linkState": "failed",
            "digitalLinked": False,
            "allstarLinked": False,
            "allstarLinkState": "unlinked",
            "requestedTarget": None,
            "confirmedTarget": None,
            "requestEpoch": 0.0,
            "disconnectRequestEpoch": 0.0,
            "lastKeepaliveEpoch": 0.0,
            "lastError": str(error)[:160],
        })

    def handle_control(self, packet: bytes, now: float | None = None) -> str | None:
        event = parse_control_packet(packet)
        magic = event["magic"]
        epoch = float(time.time() if now is None else now)
        if magic == "ACKN":
            if self.state["linkState"] not in {"connecting", "disconnecting"} or not self.state["requestedTarget"]:
                raise ControlError("Unexpected M17 acknowledgement.")
            disconnecting = self.state["linkState"] == "disconnecting"
            self.state["linkState"] = "disconnecting" if disconnecting else "digital_linked"
            self.state["digitalLinked"] = True
            self.state["confirmedTarget"] = dict(self.state["requestedTarget"])
            self.state["lastKeepaliveEpoch"] = epoch
        elif magic == "NACK":
            if self.state["linkState"] != "connecting":
                raise ControlError("Unexpected M17 negative acknowledgement.")
            self.state["linkState"] = "rejected"
            self.state["digitalLinked"] = False
            self.state["confirmedTarget"] = None
            self.state["lastError"] = "Reflector rejected the connection request."
        elif magic in {"PING", "PONG"}:
            if self.state["linkState"] == "linked":
                self.state["lastKeepaliveEpoch"] = epoch
            return "PONG" if magic == "PING" else None
        elif magic == "DISC":
            self.confirm_digital_disconnect("Reflector confirmed the digital disconnect.")
        return None

    def note_stream(self, source: str, stream_id: int, eot: bool, now: float | None = None) -> None:
        if self.state["linkState"] != "linked":
            return
        epoch = float(time.time() if now is None else now)
        if eot:
            if int(self.state.get("streamId", 0)) == int(stream_id):
                self.state.update({"talker": "", "talkerEpoch": epoch, "streamId": 0})
            return
        self.state.update({
            "talker": str(source)[:9],
            "talkerAuthenticated": False,
            "talkerEpoch": epoch,
            "streamId": int(stream_id) & 0xFFFF,
        })

    def tick(self, now: float | None = None) -> bool:
        epoch = float(time.time() if now is None else now)
        if (
            self.state["linkState"] == "connecting"
            and epoch - float(self.state.get("requestEpoch", 0)) > CONNECT_TIMEOUT
        ):
            self.state.update({
                "linkState": "timed_out",
                "confirmedTarget": None,
                "talker": "",
                "talkerAuthenticated": False,
                "streamId": 0,
                "lastError": "Reflector did not acknowledge the connection request.",
            })
            return True
        if (
            self.state["linkState"] == "linked"
            and epoch - float(self.state.get("lastKeepaliveEpoch", 0)) > KEEPALIVE_TIMEOUT
        ):
            self.state.update({
                "linkState": "timed_out",
                "digitalLinked": False,
                "confirmedTarget": None,
                "talker": "",
                "talkerAuthenticated": False,
                "streamId": 0,
                "lastError": "M17 keepalive timed out.",
            })
            return True
        if (
            self.state["linkState"] == "disconnecting"
            and epoch - float(self.state.get("disconnectRequestEpoch", 0)) > CONNECT_TIMEOUT
        ):
            self.state.update({
                "linkState": "disconnect_failed",
                "lastError": "M17 reflector did not confirm the disconnect request.",
            })
        return False


def secure_runtime_dir(path: Path = RUN_DIR) -> None:
    path.mkdir(mode=0o755, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022:
        raise ControlError(f"{path} must not be group/world-writable.")


def atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o644) -> None:
    secure_runtime_dir(path.parent)
    if path.is_symlink():
        raise ControlError("M17 runtime path must not be a symbolic link.")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def clean_user(value: object) -> str:
    return USERNAME_RE.sub("_", str(value or "unknown"))[:80] or "unknown"


def audit(
    bridge_id: str,
    user: object,
    action: str,
    target: dict[str, Any] | None,
    result: str,
    path: Path = AUDIT_LOG,
    *,
    expected_uid: int = 0,
) -> None:
    record = {
        "epoch": int(time.time()),
        "user": clean_user(user),
        "bridge": bridge_id,
        "action": action,
        "reflector": str((target or {}).get("reflector", "")),
        "module": str((target or {}).get("module", "")),
        "result": re.sub(r"[\r\n\0]+", " ", str(result))[:160],
    }
    try:
        path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        parent = path.parent.lstat()
    except OSError as exc:
        raise ControlError("M17 audit directory is unavailable.") from exc
    if (
        stat.S_ISLNK(parent.st_mode)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != expected_uid
        or stat.S_IMODE(parent.st_mode) & 0o022
    ):
        raise ControlError("M17 audit directory is unsafe.")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o640)
    except OSError as exc:
        raise ControlError("M17 audit log could not be opened securely.") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_uid
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ControlError("M17 audit log has unsafe ownership or permissions.")
        os.fchmod(descriptor, 0o640)
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def queue_command(
    bridge: dict[str, Any], action: str, target: dict[str, Any] | None, user: object
) -> dict[str, Any]:
    if action not in {"connect", "disconnect"}:
        raise ControlError("Unsupported M17 control action.")
    validate_permission(bridge.get("permission"))
    if action == "connect":
        target = validate_target(target)
    command = {
        "schema": 1,
        "commandId": f"{time.time_ns():x}",
        "createdEpoch": int(time.time()),
        "createdNs": time.time_ns(),
        "bridgeId": bridge["id"],
        "action": action,
        "target": target if action == "connect" else None,
        "user": clean_user(user),
    }
    atomic_json(bridge["commandPath"], command, 0o640)
    return command


def read_public_status(bridge: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(bridge["statePath"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "ok": False,
            "bridgeId": bridge["id"],
            "linkState": "unavailable",
            "digitalLinked": False,
            "allstarLinked": None,
            "allstarLinkState": "unknown",
            "requestedTarget": None,
            "confirmedTarget": None,
            "talker": "",
            "talkerAuthenticated": False,
            "error": "M17 connector state is unavailable.",
        }
    epoch = float(time.time() if now is None else now)
    updated = float(payload.get("updatedEpoch", 0)) if isinstance(payload, dict) else 0.0
    if not isinstance(payload, dict) or epoch - updated > KEEPALIVE_TIMEOUT + 5:
        return {
            "ok": False,
            "bridgeId": bridge["id"],
            "linkState": "unavailable",
            "digitalLinked": False,
            "allstarLinked": None,
            "allstarLinkState": "unknown",
            "requestedTarget": None,
            "confirmedTarget": None,
            "talker": "",
            "talkerAuthenticated": False,
            "error": "M17 connector state is stale.",
        }
    return {
        "ok": True,
        "bridgeId": bridge["id"],
        "linkState": str(payload.get("linkState", "unavailable")),
        "digitalLinked": payload.get("digitalLinked") is True,
        "allstarLinked": payload.get("allstarLinked"),
        "allstarLinkState": str(payload.get("allstarLinkState", "unknown")),
        "requestedTarget": payload.get("requestedTarget"),
        "confirmedTarget": payload.get("confirmedTarget"),
        "talker": str(payload.get("talker", ""))[:9],
        "talkerAuthenticated": False,
        "lastKeepaliveEpoch": payload.get("lastKeepaliveEpoch", 0),
        "updatedEpoch": updated,
        "error": str(payload.get("lastError", ""))[:160],
        "audioReady": payload.get("audioReady") is True,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-m17-control-") as directory:
        root = Path(directory)
        state_path = root / "state.json"
        command_path = root / "command.json"
        audit_path = root / "audit.log"
        target = {
            "reflector": "M17-M17",
            "host": "ref.m17.example",
            "port": 17000,
            "module": "C",
            "encrypted": False,
        }
        raw_bridge = {
            "id": "primary_bridge",
            "mode": "m17",
            "node": "1996",
            "cardType": "m17_net",
            "bridgePermission": "approved",
            "m17Callsign": "N0CALL",
            "m17BindAddress": "127.0.0.1",
            "m17BindPort": 17101,
            "m17UsrpBindAddress": "127.0.0.1",
            "m17UsrpRxPort": 32101,
            "m17UsrpRemoteAddress": "127.0.0.1",
            "m17UsrpTxPort": 32102,
            "approvedDestinations": [target],
        }
        test_config = {"node": "123456", "bridges": [raw_bridge]}
        bridge = validate_bridge(raw_bridge, test_config)
        bridge["statePath"] = state_path
        bridge["commandPath"] = command_path
        assert approved_destination(bridge, "m17-m17", "c") == target
        try:
            validate_ip("::1", "test bind address")
        except ControlError:
            pass
        else:
            raise AssertionError("IPv6 was accepted for an IPv4-only socket")
        for bad_permission in (None, "", "denied", "unknown", True):
            candidate = dict(raw_bridge, bridgePermission=bad_permission)
            try:
                validate_bridge(candidate, {"node": "123456", "bridges": [candidate]})
            except ControlError:
                pass
            else:
                raise AssertionError("unapproved permission was accepted")
        try:
            approved_destination(bridge, "M17-M17", "D")
        except ControlError:
            pass
        else:
            raise AssertionError("unapproved Net destination was accepted")
        encrypted = dict(target, module="E", encrypted=True)
        candidate = dict(raw_bridge, approvedDestinations=[encrypted])
        try:
            validate_bridge(candidate, {"node": "123456", "bridges": [candidate]})
        except ControlError:
            pass
        else:
            raise AssertionError("encrypted target was accepted")

        other = dict(
            raw_bridge,
            id="other_bridge",
            node="1995",
            m17Callsign="N0CALL-M",
            m17BindPort=32101,
            m17UsrpRxPort=32103,
            m17UsrpTxPort=32104,
        )
        try:
            validate_bridge(raw_bridge, {"node": "123456", "bridges": [raw_bridge, other]})
        except ControlError:
            pass
        else:
            raise AssertionError("cross-protocol receive-port overlap was accepted")

        encoded = encode_callsign("N0CALL")
        assert decode_callsign(encoded) == "N0CALL"
        assert encode_callsign("AB1CD") == bytes.fromhex("0000009fdd51")
        assert conn_packet("N0CALL", "C")[:4] == b"CONN"
        assert len(conn_packet("N0CALL", "C")) == 11
        assert len(disc_packet("N0CALL")) == 10
        assert parse_control_packet(b"PING" + encoded)["callsign"] == "N0CALL"
        assert parse_control_packet(b"DISC")["magic"] == "DISC"
        assert parse_control_packet(b"DISC" + encoded)["callsign"] == "N0CALL"

        machine = M17LinkState()
        machine.request(target, now=100.0)
        assert machine.state["linkState"] == "connecting"
        assert machine.state["confirmedTarget"] is None
        machine.handle_control(b"ACKN", now=101.0)
        assert machine.state["linkState"] == "digital_linked"
        assert machine.state["digitalLinked"] is True
        machine.mark_combined_linked()
        assert machine.state["linkState"] == "linked"
        assert machine.state["allstarLinked"] is True
        assert machine.state["confirmedTarget"] == target
        assert machine.handle_control(b"PING" + encoded, now=102.0) == "PONG"
        machine.note_stream("N0CALL", 42, False, now=103.0)
        assert machine.state["talker"] == "N0CALL"
        machine.note_stream("N0CALL", 42, True, now=104.0)
        assert machine.state["talker"] == ""
        assert machine.tick(now=133.0) is True
        assert machine.state["linkState"] == "timed_out"
        assert machine.state["confirmedTarget"] is None
        machine.request(target, now=200.0)
        machine.handle_control(b"NACK", now=201.0)
        assert machine.state["linkState"] == "rejected"
        assert machine.state["confirmedTarget"] is None

        disconnecting = M17LinkState()
        disconnecting.request(target, now=300.0)
        disconnecting.handle_control(b"ACKN", now=301.0)
        disconnecting.mark_combined_linked()
        disconnecting.begin_disconnect(now=302.0)
        assert disconnecting.tick(now=313.0) is False
        assert disconnecting.state["linkState"] == "disconnect_failed"
        assert disconnecting.state["digitalLinked"] is True
        assert disconnecting.state["allstarLinked"] is True
        disconnecting.handle_control(b"DISC", now=314.0)
        assert disconnecting.state["linkState"] == "digital_disconnected"
        assert disconnecting.state["digitalLinked"] is False
        assert disconnecting.state["allstarLinked"] is True

        command = queue_command(bridge, "connect", target, "tester\nsecret")
        assert command["action"] == "connect"
        assert json.loads(command_path.read_text(encoding="utf-8"))["target"] == target
        audit(
            bridge["id"], "tester\nsecret", "connect", target, "queued", audit_path,
            expected_uid=os.geteuid(),
        )
        audit_record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
        assert audit_record["user"] == "tester_secret"
        assert "host" not in audit_record and "port" not in audit_record
        assert stat.S_IMODE(audit_path.stat().st_mode) == 0o640
        unsafe_audit = root / "unsafe-audit.log"
        unsafe_audit.write_text("unsafe\n", encoding="utf-8")
        unsafe_audit.chmod(0o666)
        try:
            audit(
                bridge["id"], "tester", "connect", target, "blocked", unsafe_audit,
                expected_uid=os.geteuid(),
            )
        except ControlError:
            pass
        else:
            raise AssertionError("unsafe audit-log permissions were accepted")
        status_state = dict(machine.state, updatedEpoch=300.0, audioReady=False)
        state_path.write_text(json.dumps(status_state), encoding="utf-8")
        assert read_public_status(bridge, now=301.0)["linkState"] == "rejected"
        assert read_public_status(bridge, now=400.0)["linkState"] == "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default="")
    parser.add_argument("--user", default=os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown")
    parser.add_argument("--self-test", action="store_true")
    subparsers = parser.add_subparsers(dest="action")
    connect_parser = subparsers.add_parser("connect")
    connect_parser.add_argument("--reflector", required=True)
    connect_parser.add_argument("--module", required=True)
    subparsers.add_parser("disconnect")
    subparsers.add_parser("status")
    subparsers.add_parser("validate")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("M17 bridge control self-test passed")
        return 0
    if not args.bridge or not args.action:
        parser.error("--bridge and an action are required")
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "error": "M17 bridge control must run as root."}, separators=(",", ":")))
        return 1
    try:
        bridge = bridge_config(args.bridge, CONFIG_PATH)
        if args.action == "validate":
            print(json.dumps({"ok": True, "bridgeId": bridge["id"], "cardType": bridge["cardType"]}))
        elif args.action == "status":
            print(json.dumps(read_public_status(bridge), separators=(",", ":")))
        elif args.action == "connect":
            target = approved_destination(bridge, args.reflector, args.module)
            queue_command(bridge, "connect", target, args.user)
            audit(bridge["id"], args.user, "connect", target, "queued", AUDIT_LOG)
            print(json.dumps({
                "ok": True,
                "bridgeId": bridge["id"],
                "queued": True,
                "linkState": "unchanged_until_connector_confirms",
                "target": {"reflector": target["reflector"], "module": target["module"]},
            }, separators=(",", ":")))
        elif args.action == "disconnect":
            queue_command(bridge, "disconnect", None, args.user)
            audit(bridge["id"], args.user, "disconnect", None, "queued", AUDIT_LOG)
            print(json.dumps({
                "ok": True,
                "bridgeId": bridge["id"],
                "queued": True,
                "linkState": "unchanged_until_connector_confirms",
            }, separators=(",", ":")))
    except ControlError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
