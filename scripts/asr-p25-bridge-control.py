#!/usr/bin/env python3
"""Fail-closed controller for current G4KLX P25/NXDN MQTT gateways.

The NXDN entry point imports this file and supplies its own ModeSpec.  A
"confirmedTarget" means that the gateway accepted the selection; it is not
evidence that the remote reflector is reachable.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
MQTT_SECRETS_PATH = Path("/etc/allscan-reimagined/bridge-mqtt-secrets.json")
ASTERISK_BIN = "/usr/sbin/asterisk"
BRIDGE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
NODE_RE = re.compile(r"^[0-9]{3,10}$")
INSTANCE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9@_.-]{0,79}\.service$")
MQTT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
USER_RE = re.compile(r"[^A-Za-z0-9_.@+-]")
SAFE_PERMISSIONS = {"self_owned", "approved"}
DISCONNECT_TG = 9999
COMMAND_TIMEOUT = 4.0
VERIFY_TIMEOUT = 5.0
ALLSTAR_VERIFY_TIMEOUT = 10.0
FUTURE_SKEW = 30
WATCH_INTERVAL_MIN = 1.0
WATCH_INTERVAL_MAX = 60.0
PRODUCTION_CONFIG_DIR_MODE = 0o775
PRODUCTION_CONFIG_MODE = 0o664
WEB_GROUP_CANDIDATES = ("www-data", "apache", "http")


class ControlError(RuntimeError):
    pass


class PartialControlError(ControlError):
    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


@dataclass(frozen=True)
class ModeSpec:
    mode: str
    label: str
    gateway_dir: str
    gateway_ini: str
    run_dir: Path
    audit_log: Path
    reserved: frozenset[int]
    emulator_allowed: bool


P25_SPEC = ModeSpec(
    mode="p25",
    label="P25",
    gateway_dir="P25Gateway",
    gateway_ini="P25Gateway.ini",
    run_dir=Path("/run/allscan-reimagined-p25-bridge-control"),
    audit_log=Path("/var/log/allscan-reimagined/p25-bridge-control.jsonl"),
    reserved=frozenset({*range(1, 11), 20, 9999, 10999}),
    emulator_allowed=False,
)


Runner = Callable[[list[str], float], subprocess.CompletedProcess[str]]


def default_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError("Required local control command failed.") from exc


def secure_regular_file(path: Path, label: str, expected_uid: int = 0) -> None:
    secure_directory(path.parent, expected_uid)
    try:
        info = path.lstat()
    except OSError as exc:
        raise ControlError(f"{label} could not be read securely.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ControlError(f"{label} must be a regular file, not a link.")
    if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise ControlError(f"{label} must be owner-controlled and not group/world-writable.")


def production_web_gid() -> int:
    for name in WEB_GROUP_CANDIDATES:
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            continue
    # Mirrors asr-reapply.sh's last-resort group when no supported web group exists.
    return os.getgid()


def load_trusted_config(
    path: Path,
    expected_uid: int,
    expected_gid: int,
    expected_directory_mode: int,
    expected_file_mode: int,
) -> dict:
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise ControlError("ASR bridge configuration directory is unsafe.") from exc
    try:
        directory_info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != expected_uid
            or directory_info.st_gid != expected_gid
            or stat.S_IMODE(directory_info.st_mode) != expected_directory_mode
            or stat.S_IMODE(directory_info.st_mode) & 0o002
        ):
            raise ControlError("ASR bridge configuration directory metadata is unsafe.")
        file_flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path.name, file_flags, dir_fd=directory_fd)
        except OSError as exc:
            raise ControlError("ASR bridge configuration file is unsafe.") from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_uid != expected_uid
                or opened.st_gid != expected_gid
                or stat.S_IMODE(opened.st_mode) != expected_file_mode
                or stat.S_IMODE(opened.st_mode) & 0o002
            ):
                raise ControlError("ASR bridge configuration file metadata is unsafe.")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                try:
                    value = json.load(handle)
                except (OSError, json.JSONDecodeError) as exc:
                    raise ControlError("ASR bridge configuration is invalid.") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    finally:
        os.close(directory_fd)
    if not isinstance(value, dict) or not isinstance(value.get("bridges", []), list):
        raise ControlError("ASR bridge configuration is invalid.")
    return value


def load_production_config() -> dict:
    return load_trusted_config(
        CONFIG_PATH,
        0,
        production_web_gid(),
        PRODUCTION_CONFIG_DIR_MODE,
        PRODUCTION_CONFIG_MODE,
    )


def load_mqtt_credentials(bridge: dict) -> dict[str, str]:
    expected_web_gid = production_web_gid()
    directory_flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(MQTT_SECRETS_PATH.parent, directory_flags)
    except OSError as exc:
        raise ControlError("Root-only MQTT credential directory is unavailable.") from exc
    try:
        parent = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or parent.st_gid != expected_web_gid
            or stat.S_IMODE(parent.st_mode) != PRODUCTION_CONFIG_DIR_MODE
            or stat.S_IMODE(parent.st_mode) & 0o002
        ):
            raise ControlError("Root-only MQTT credential directory metadata is unsafe.")
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(MQTT_SECRETS_PATH.name, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise ControlError("Root-only MQTT credential file is unavailable.") from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != 0
                or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ControlError("Root-only MQTT credential file metadata is unsafe.")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                try:
                    payload = json.load(handle)
                except (OSError, json.JSONDecodeError) as exc:
                    raise ControlError("Root-only MQTT credential file is invalid.") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    finally:
        os.close(directory_fd)
    entries = payload.get("bridges") if isinstance(payload, dict) else None
    entry = entries.get(bridge["id"]) if isinstance(entries, dict) else None
    if not isinstance(entry, dict) or entry.get("aclEnforced") is not True:
        raise ControlError(
            "MQTT control is blocked until authenticated per-topic ACL enforcement is root-qualified."
        )
    if (
        entry.get("gatewayMqttName") != bridge["mqttName"]
        or entry.get("mmdvmMqttName") != bridge["mmdvmMqttName"]
    ):
        raise ControlError("Root-qualified MQTT topic names do not match this bridge.")
    username = str(entry.get("username", ""))
    password = str(entry.get("password", ""))
    if (
        not 1 <= len(username.encode("utf-8")) <= 128
        or not 1 <= len(password.encode("utf-8")) <= 512
        or any(character in username + password for character in "\x00\r\n")
    ):
        raise ControlError("Root-only MQTT credentials are invalid.")
    return {"username": username, "password": password}


def secure_directory(path: Path, expected_uid: int = 0) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ControlError(f"{path} is unavailable.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ControlError(f"{path} must be a real directory.")
    if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) & 0o022:
        raise ControlError(f"{path} must be owner-controlled and not group/world-writable.")


def ensure_runtime(paths: ModeSpec, expected_uid: int = 0) -> None:
    paths.run_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    secure_directory(paths.run_dir, expected_uid)


def load_config(path: Path, expected_uid: int = 0) -> dict:
    if path == CONFIG_PATH and expected_uid == 0:
        return load_production_config()
    secure_regular_file(path, "ASR bridge configuration", expected_uid)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError("ASR bridge configuration is invalid.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("bridges", []), list):
        raise ControlError("ASR bridge configuration is invalid.")
    return value


def designator(value: object, spec: ModeSpec) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,5}", text):
        raise ControlError(f"{spec.label} destination must be an integer from 11 through 65534.")
    number = int(text)
    if not 11 <= number <= 65534 or number in spec.reserved:
        raise ControlError(f"{spec.label} destination is out of range or reserved.")
    return number


def normalized_destinations(value: object, spec: ModeSpec) -> set[int]:
    if not isinstance(value, list):
        raise ControlError("Approved destinations must be an array.")
    return {designator(item, spec) for item in value}


def gateway_path(instance: str, spec: ModeSpec) -> Path:
    return Path(f"/opt/{spec.gateway_dir}_{instance}/{spec.gateway_ini}")


def validate_bridge(raw: dict, config: dict, spec: ModeSpec) -> dict:
    bridge_id = str(raw.get("id", ""))
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    if raw.get("digitalMode") != spec.mode:
        raise ControlError(f"Selected bridge is not configured for {spec.label}.")
    role = str(raw.get("bridgeRole", ""))
    if role not in {"standard", "net"}:
        raise ControlError("Bridge role must be standard or net.")
    expected_card = "standard" if role == "standard" else f"{spec.mode}_net"
    if raw.get("cardType") != expected_card:
        raise ControlError("Bridge card type and bridge role do not agree.")
    instance = str(raw.get("instance", ""))
    if not INSTANCE_RE.fullmatch(instance):
        raise ControlError("Bridge instance name is invalid.")
    expected_path = gateway_path(instance, spec)
    if str(raw.get("gatewayConfig", "")) != str(expected_path):
        raise ControlError("Gateway configuration path is not the dedicated allowed path.")
    services: list[str] = []
    for field in ("gatewayService", "mmdvmService", "analogBridgeService"):
        value = str(raw.get(field, ""))
        if not SERVICE_RE.fullmatch(value):
            raise ControlError(f"Configured {field} is invalid.")
        services.append(value)
    gateway_service = str(raw["gatewayService"])
    allowed_gateway_services = {
        f"{spec.mode}gateway-{instance}.service",
        f"{spec.mode}gateway_{instance}.service",
        f"{spec.mode}gateway@{instance}.service",
    }
    if gateway_service not in allowed_gateway_services:
        raise ControlError("Gateway service does not identify the dedicated bridge instance.")
    emulator = str(raw.get("emulatorService", "") or "")
    if emulator:
        if not spec.emulator_allowed:
            raise ControlError(f"{spec.label} must not use an MD380 emulator service.")
        if not SERVICE_RE.fullmatch(emulator):
            raise ControlError("Configured emulatorService is invalid.")
        services.append(emulator)
    mqtt_host = str(raw.get("mqttHost", "127.0.0.1"))
    if mqtt_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ControlError("MQTT broker must be local to the bridge host.")
    try:
        mqtt_port = int(raw.get("mqttPort", 1883))
    except (TypeError, ValueError) as exc:
        raise ControlError("MQTT port is invalid.") from exc
    mqtt_name = str(raw.get("mqttName", ""))
    mmdvm_mqtt_name = str(raw.get("mmdvmMqttName", ""))
    if (
        not 1 <= mqtt_port <= 65535
        or not MQTT_NAME_RE.fullmatch(mqtt_name)
        or not MQTT_NAME_RE.fullmatch(mmdvm_mqtt_name)
        or mqtt_name == mmdvm_mqtt_name
    ):
        raise ControlError("MQTT endpoint configuration is invalid.")
    if raw.get("mqttUsername") not in (None, "") or raw.get("mqttPassword") not in (None, ""):
        raise ControlError("MQTT credentials are not accepted by this local-only controller.")
    if raw.get("bridgePermission") not in SAFE_PERMISSIONS:
        raise ControlError("Bridge control is blocked until reflector permission is self-owned or approved.")
    local_node = str(config.get("node", ""))
    bridge_node = str(raw.get("node", ""))
    if (
        not NODE_RE.fullmatch(local_node)
        or not NODE_RE.fullmatch(bridge_node)
        or local_node == bridge_node
    ):
        raise ControlError("Main and bridge AllStar node numbers must be distinct 3-10 digit values.")
    fixed: int | None = None
    approved: set[int] = set()
    if role == "standard":
        fixed = designator(raw.get("fixedDestination"), spec)
    else:
        if raw.get("allowTune") is not True:
            raise ControlError(f"{spec.label} Net Bridge tuning is disabled.")
        approved = normalized_destinations(raw.get("approvedDestinations"), spec)
    used: dict[str, set[str]] = {
        "instance": {instance}, "gatewayConfig": {str(expected_path)},
        "mqttName": {mqtt_name, mmdvm_mqtt_name}, "services": set(services),
    }
    for other in config.get("bridges", []):
        if not isinstance(other, dict) or other is raw:
            continue
        collisions = [
            str(other.get("id", "")) == bridge_id,
            str(other.get("node", "")) == bridge_node,
            str(other.get("instance", "")) in used["instance"],
            str(other.get("gatewayConfig", "")) in used["gatewayConfig"],
            str(other.get("mqttName", "")) in used["mqttName"],
            str(other.get("mmdvmMqttName", "")) in used["mqttName"],
            any(str(other.get(key, "")) in used["services"] for key in (
                "gatewayService", "mmdvmService", "analogBridgeService", "emulatorService"
            )),
        ]
        if any(collisions):
            raise ControlError("Bridge instance resources overlap another configured bridge.")
    return {
        **raw, "id": bridge_id, "role": role, "instance": instance,
        "localNode": local_node, "bridgeNode": bridge_node,
        "gatewayPath": expected_path, "services": services,
        "mqttHost": mqtt_host, "mqttPort": mqtt_port, "mqttName": mqtt_name,
        "mmdvmMqttName": mmdvm_mqtt_name,
        "fixedDestination": fixed, "approvedDestinations": approved,
    }


def bridge_config(bridge_id: str, path: Path, spec: ModeSpec, expected_uid: int = 0) -> dict:
    if not BRIDGE_ID_RE.fullmatch(bridge_id):
        raise ControlError("Invalid bridge ID.")
    config = load_config(path, expected_uid)
    matches = [item for item in config["bridges"] if isinstance(item, dict) and item.get("id") == bridge_id]
    if len(matches) != 1:
        raise ControlError("Configured bridge was not found uniquely.")
    return validate_bridge(matches[0], config, spec)


def parse_ini(bridge: dict, spec: ModeSpec, expected_uid: int = 0) -> None:
    secure_regular_file(bridge["gatewayPath"], f"Configured {spec.label} Gateway file", expected_uid)
    try:
        text = bridge["gatewayPath"].read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ControlError(f"Configured {spec.label} Gateway file could not be read.") from exc
    section = ""
    values: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
        elif "=" in stripped:
            key, value = stripped.split("=", 1)
            values[(section, key.strip().lower())] = value.strip()
    if values.get(("mqtt", "name")) != bridge["mqttName"]:
        raise ControlError("Gateway MQTT Name does not match the configured instance.")
    if values.get(("mqtt", "address"), "127.0.0.1") not in {"127.0.0.1", "::1", "localhost"}:
        raise ControlError("Gateway MQTT broker is not local.")
    try:
        ini_port = int(values.get(("mqtt", "port"), "1883"))
    except ValueError as exc:
        raise ControlError("Gateway MQTT Port is invalid.") from exc
    if ini_port != bridge["mqttPort"]:
        raise ControlError("Gateway MQTT Port does not match the configured instance.")
    if values.get(("mqtt", "auth")) != "1":
        raise ControlError("Gateway MQTT authentication must be enabled.")
    if not values.get(("mqtt", "username")) or not values.get(("mqtt", "password")):
        raise ControlError("Gateway MQTT credentials are missing from its root-owned INI.")
    if values.get(("remote commands", "enable")) != "1":
        raise ControlError("Gateway MQTT remote commands are not enabled.")


def service_state(bridge: dict, runner: Runner) -> dict:
    states: dict[str, dict] = {}
    gateway_started = 0.0
    now_wall, now_mono = time.time(), time.monotonic()
    for service in bridge["services"]:
        completed = runner([
            "/usr/bin/systemctl", "show", service,
            "--property=ActiveState", "--property=SubState", "--property=MainPID",
            "--property=ExecMainStartTimestampMonotonic", "--no-pager",
        ], COMMAND_TIMEOUT)
        if completed.returncode != 0:
            raise ControlError(f"Service state for {service} could not be read.")
        props = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                props[key] = value
        try:
            main_pid = int(props.get("MainPID", "0") or 0)
        except ValueError as exc:
            raise ControlError(f"Service state for {service} is malformed.") from exc
        active = props.get("ActiveState") == "active" and main_pid > 0
        states[service] = {"active": active, "subState": props.get("SubState", "unknown")}
        if service == bridge["gatewayService"]:
            try:
                mono = int(props.get("ExecMainStartTimestampMonotonic", "0")) / 1_000_000
            except ValueError:
                mono = 0
            if mono > 0 and mono <= now_mono + FUTURE_SKEW:
                gateway_started = now_wall - max(0.0, now_mono - mono)
    return {"ready": all(item["active"] for item in states.values()), "services": states, "gatewayStartEpoch": gateway_started}


def parse_lstats_links(output: str) -> set[tuple[str, str]]:
    if not re.search(
        r"^NODE\s+PEER\s+RECONNECTS\s+DIRECTION\s+CONNECT TIME\s+CONNECT STATE\s*$",
        output,
        re.MULTILINE,
    ):
        raise ControlError("Asterisk direct-link status was not recognized.")
    return set(
        re.findall(
            r"^([A-Za-z0-9_][A-Za-z0-9_-]*)[ \t]+\S+[ \t]+\d+[ \t]+(IN|OUT)[ \t]+",
            output,
            re.MULTILINE,
        )
    )


def asterisk_output(command: str, runner: Runner) -> str:
    completed = runner([ASTERISK_BIN, "-rx", command], 8.0)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ControlError("Asterisk direct-link command returned an error.")
    return completed.stdout


def direct_linked(bridge: dict, runner: Runner) -> bool:
    links = parse_lstats_links(
        asterisk_output(f"rpt lstats {bridge['localNode']}", runner)
    )
    return (bridge["bridgeNode"], "OUT") in links


def set_direct_link(bridge: dict, linked: bool, runner: Runner) -> None:
    if direct_linked(bridge, runner) == linked:
        return
    action = "3" if linked else "11"
    completed = runner(
        [
            ASTERISK_BIN,
            "-rx",
            f"rpt cmd {bridge['localNode']} ilink {action} {bridge['bridgeNode']}",
        ],
        8.0,
    )
    if completed.returncode != 0:
        raise ControlError("Asterisk bridge-link command returned an error.")
    deadline = time.monotonic() + ALLSTAR_VERIFY_TIMEOUT
    while time.monotonic() < deadline:
        if direct_linked(bridge, runner) == linked:
            return
        time.sleep(0.25)
    raise ControlError(
        f"Asterisk did not confirm the bridge-node {'link' if linked else 'unlink'}."
    )


SocketFactory = Callable[[tuple[str, int], float], socket.socket]


def mqtt_varint(value: int) -> bytes:
    if not 0 <= value <= 268_435_455:
        raise ControlError("MQTT packet length is invalid.")
    encoded = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        encoded.append(digit)
        if not value:
            return bytes(encoded)


def mqtt_field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if len(encoded) > 65_535:
        raise ControlError("MQTT field is too long.")
    return len(encoded).to_bytes(2, "big") + encoded


def mqtt_read_exact(connection: socket.socket, length: int) -> bytes:
    output = bytearray()
    while len(output) < length:
        chunk = connection.recv(length - len(output))
        if not chunk:
            raise ControlError("MQTT broker closed the authenticated connection.")
        output.extend(chunk)
    return bytes(output)


def mqtt_read_packet(connection: socket.socket) -> tuple[int, bytes]:
    header = mqtt_read_exact(connection, 1)[0]
    multiplier = 1
    remaining = 0
    for _ in range(4):
        digit = mqtt_read_exact(connection, 1)[0]
        remaining += (digit & 0x7F) * multiplier
        if not digit & 0x80:
            if remaining > 1_048_576:
                raise ControlError("MQTT packet exceeds the controller limit.")
            return header, mqtt_read_exact(connection, remaining)
        multiplier *= 128
    raise ControlError("MQTT packet length is malformed.")


def mqtt_open(
    bridge: dict,
    credentials: dict[str, str],
    socket_factory: SocketFactory = socket.create_connection,
) -> socket.socket:
    client_id = f"asr-{bridge['id']}-{os.getpid()}-{time.monotonic_ns() & 0xFFFF:x}"
    variable = mqtt_field("MQTT") + bytes([4, 0xC2]) + (10).to_bytes(2, "big")
    body = (
        variable
        + mqtt_field(client_id)
        + mqtt_field(credentials["username"])
        + mqtt_field(credentials["password"])
    )
    try:
        connection = socket_factory(
            (bridge["mqttHost"], bridge["mqttPort"]), COMMAND_TIMEOUT
        )
        connection.settimeout(COMMAND_TIMEOUT)
        connection.sendall(bytes([0x10]) + mqtt_varint(len(body)) + body)
        header, payload = mqtt_read_packet(connection)
    except (OSError, socket.timeout) as exc:
        raise ControlError("Authenticated MQTT connection failed.") from exc
    if header >> 4 != 2 or len(payload) != 2 or payload[1] != 0:
        connection.close()
        raise ControlError("MQTT broker rejected authenticated bridge control.")
    return connection


def mqtt_subscribe(connection: socket.socket, topic: str, packet_id: int = 1) -> None:
    body = packet_id.to_bytes(2, "big") + mqtt_field(topic) + bytes([1])
    connection.sendall(bytes([0x82]) + mqtt_varint(len(body)) + body)
    header, payload = mqtt_read_packet(connection)
    if (
        header >> 4 != 9
        or len(payload) < 3
        or int.from_bytes(payload[:2], "big") != packet_id
        or payload[2] == 0x80
    ):
        raise ControlError("MQTT ACL denied the requested status subscription.")


def mqtt_publish_payload(
    header: int, payload: bytes
) -> tuple[str, str, bool, int, bytes]:
    if header >> 4 != 3 or len(payload) < 2:
        raise ControlError("MQTT activity packet is malformed.")
    topic_length = int.from_bytes(payload[:2], "big")
    offset = 2 + topic_length
    if offset > len(payload):
        raise ControlError("MQTT activity topic is malformed.")
    try:
        topic = payload[2:offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("MQTT activity topic is invalid UTF-8.") from exc
    qos = (header >> 1) & 0x03
    packet_id = b""
    if qos:
        if offset + 2 > len(payload):
            raise ControlError("MQTT activity packet ID is missing.")
        packet_id = payload[offset:offset + 2]
        offset += 2
    try:
        text = payload[offset:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("MQTT activity payload is invalid UTF-8.") from exc
    return topic, text, bool(header & 0x01), qos, packet_id


def mqtt_ack_publish(connection: socket.socket, qos: int, packet_id: bytes) -> None:
    if qos == 1:
        connection.sendall(b"\x40\x02" + packet_id)
    elif qos == 2:
        connection.sendall(b"\x50\x02" + packet_id)


def mqtt_payload(
    bridge: dict,
    credentials: dict[str, str],
    socket_factory: SocketFactory = socket.create_connection,
) -> str:
    connection = mqtt_open(bridge, credentials, socket_factory)
    topic = f"{bridge['mqttName']}/json"
    try:
        mqtt_subscribe(connection, topic)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            connection.settimeout(max(0.1, deadline - time.monotonic()))
            header, payload = mqtt_read_packet(connection)
            if header >> 4 != 3:
                continue
            received_topic, text, retained, qos, packet_id = mqtt_publish_payload(
                header, payload
            )
            mqtt_ack_publish(connection, qos, packet_id)
            if not retained or received_topic != topic:
                continue
            return text
    except (OSError, socket.timeout, UnicodeDecodeError) as exc:
        raise ControlError("Gateway retained MQTT state is unavailable.") from exc
    finally:
        connection.close()
    raise ControlError("Gateway retained MQTT state is unavailable.")


def event_epoch(value: object) -> int:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (ValueError, OverflowError):
        return 0


def normalize_event(payload: str, spec: ModeSpec, requested_disconnect: bool = False) -> dict:
    try:
        root = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ControlError("Gateway MQTT state is not valid JSON.") from exc
    link = root.get("link") if isinstance(root, dict) else None
    if not isinstance(link, dict):
        raise ControlError("Gateway MQTT state does not contain a link event.")
    action = str(link.get("action", ""))
    if action not in {"linking", "relinking", "unlinked", "failed"}:
        raise ControlError("Gateway MQTT link action is invalid.")
    target: int | None = None
    if link.get("talkgroup") is not None:
        try:
            target = int(link["talkgroup"])
        except (TypeError, ValueError) as exc:
            raise ControlError("Gateway MQTT talkgroup is invalid.") from exc
    if requested_disconnect and action == "failed" and target == DISCONNECT_TG:
        action, target = "unlinked", None
    elif action in {"linking", "relinking"}:
        target = designator(target, spec)
    elif action == "unlinked":
        target = None
    return {"action": action, "target": target, "epoch": event_epoch(link.get("timestamp")), "reason": str(link.get("reason", ""))[:32]}


def parse_mmdvm_activity(
    payload: str,
    spec: ModeSpec,
    topic: str,
    expected_topic: str,
    retained: bool,
    subscribed_epoch: int,
) -> dict | None:
    if topic != expected_topic or retained:
        return None
    try:
        root = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(root, dict):
        return None
    mode_event = root.get(spec.label)
    if isinstance(mode_event, dict):
        action = str(mode_event.get("action", "")).lower()
        epoch = event_epoch(mode_event.get("timestamp"))
        if (
            epoch < subscribed_epoch - 2
            or epoch > int(time.time()) + FUTURE_SKEW
        ):
            return None
        if action in {"start", "late_entry"}:
            if mode_event.get("source") != "network":
                return None
            try:
                source_id = int(mode_event.get("src_id", 0))
            except (TypeError, ValueError):
                return None
            if source_id <= 0:
                return None
            source_info = re.sub(
                r"[\x00-\x1f\x7f]+", " ", str(mode_event.get("src_info", ""))
            ).strip()[:80]
            return {
                "kind": "start",
                "epoch": epoch,
                "talker": source_info or str(source_id),
                "sourceId": source_id,
                "destination": int(mode_event.get("dst_id", 0) or 0),
                "provenance": "mmdvm-network",
            }
        if action in {"end", "lost"}:
            return {"kind": "end", "epoch": epoch, "provenance": "mmdvm-network-eot"}
        return None
    host_event = root.get("MMDVM")
    if isinstance(host_event, dict) and str(host_event.get("mode", "")).lower() == "idle":
        epoch = event_epoch(host_event.get("timestamp"))
        if subscribed_epoch - 2 <= epoch <= int(time.time()) + FUTURE_SKEW:
            return {"kind": "end", "epoch": epoch, "provenance": "mmdvm-idle"}
    return None


class TalkerStream:
    def __init__(self, bridge: dict, spec: ModeSpec, credentials: dict[str, str]):
        self.bridge = bridge
        self.spec = spec
        self.credentials = credentials
        self.topic = f"{bridge['mmdvmMqttName']}/json"
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.connection: socket.socket | None = None
        self.state = {
            "ready": False, "active": False, "talker": None,
            "sourceId": None, "startEpoch": 0, "eotEpoch": 0,
            "eventEpoch": 0, "provenance": "", "error": "stream not connected",
        }
        self.thread = threading.Thread(
            target=self._run,
            name=f"asr-{spec.mode}-talker-{bridge['id']}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        connection = self.connection
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        self.thread.join(timeout=COMMAND_TIMEOUT + 1.0)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set_unavailable(self, message: str) -> None:
        with self.lock:
            self.state.update({
                "ready": False, "active": False, "talker": None,
                "sourceId": None, "startEpoch": 0,
                "provenance": "", "error": message[:160],
            })

    def _apply(self, event: dict) -> None:
        with self.lock:
            if event["kind"] == "start":
                self.state.update({
                    "ready": True, "active": True,
                    "talker": event["talker"], "sourceId": event["sourceId"],
                    "startEpoch": event["epoch"], "eventEpoch": event["epoch"],
                    "provenance": event["provenance"], "error": "",
                })
            else:
                self.state.update({
                    "ready": True, "active": False, "talker": None,
                    "sourceId": None, "startEpoch": 0,
                    "eotEpoch": event["epoch"], "eventEpoch": event["epoch"],
                    "provenance": event["provenance"], "error": "",
                })

    def _run(self) -> None:
        while not self.stop_event.is_set():
            subscribed_epoch = int(time.time())
            try:
                connection = mqtt_open(self.bridge, self.credentials)
                self.connection = connection
                connection.settimeout(1.0)
                mqtt_subscribe(connection, self.topic)
                with self.lock:
                    self.state.update({"ready": True, "error": ""})
                last_ping = time.monotonic()
                while not self.stop_event.is_set():
                    try:
                        header, body = mqtt_read_packet(connection)
                    except socket.timeout:
                        if time.monotonic() - last_ping >= 5.0:
                            connection.sendall(b"\xc0\x00")
                            last_ping = time.monotonic()
                        continue
                    if header >> 4 == 13:
                        continue
                    if header >> 4 == 6 and len(body) == 2:
                        connection.sendall(b"\x70\x02" + body)
                        continue
                    if header >> 4 != 3:
                        continue
                    topic, payload, retained, qos, packet_id = mqtt_publish_payload(
                        header, body
                    )
                    mqtt_ack_publish(connection, qos, packet_id)
                    event = parse_mmdvm_activity(
                        payload, self.spec, topic, self.topic, retained,
                        subscribed_epoch,
                    )
                    if event is not None:
                        self._apply(event)
            except (ControlError, OSError) as exc:
                self._set_unavailable(str(exc))
            finally:
                connection = self.connection
                self.connection = None
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self.stop_event.wait(1.0)


class TalkerManager:
    def __init__(self, spec: ModeSpec):
        self.spec = spec
        self.streams: dict[str, tuple[tuple, TalkerStream]] = {}
        self.errors: dict[str, str] = {}

    def sync(self, path: Path, expected_uid: int = 0) -> None:
        config = load_config(path, expected_uid)
        wanted: set[str] = set()
        errors: dict[str, str] = {}
        for raw in config["bridges"]:
            if not isinstance(raw, dict) or raw.get("digitalMode") != self.spec.mode:
                continue
            bridge_id = str(raw.get("id", ""))
            if not BRIDGE_ID_RE.fullmatch(bridge_id):
                continue
            wanted.add(bridge_id)
            try:
                bridge = validate_bridge(raw, config, self.spec)
                credentials = load_mqtt_credentials(bridge)
                signature = (
                    bridge["mqttHost"], bridge["mqttPort"],
                    bridge["mmdvmMqttName"], credentials["username"],
                    credentials["password"],
                )
                current = self.streams.get(bridge_id)
                if current is not None and current[0] == signature:
                    continue
                if current is not None:
                    current[1].stop()
                stream = TalkerStream(bridge, self.spec, credentials)
                self.streams[bridge_id] = (signature, stream)
                stream.start()
            except (ControlError, OSError) as exc:
                errors[bridge_id] = str(exc)
                current = self.streams.pop(bridge_id, None)
                if current is not None:
                    current[1].stop()
        for bridge_id in set(self.streams) - wanted:
            _, stream = self.streams.pop(bridge_id)
            stream.stop()
        self.errors = errors

    def augment(self, payload: dict) -> dict:
        for bridge_id, entry in payload.get("bridges", {}).items():
            stream_item = self.streams.get(bridge_id)
            if stream_item is None:
                state = {
                    "ready": False, "active": False, "talker": None,
                    "sourceId": None, "startEpoch": 0, "eotEpoch": 0,
                    "eventEpoch": 0, "provenance": "",
                    "error": self.errors.get(bridge_id, "talker stream unavailable"),
                }
            else:
                state = stream_item[1].snapshot()
            ready = state.get("ready") is True
            active = ready and state.get("active") is True
            entry.update({
                "talkerEvidenceAvailable": ready,
                "inboundTalker": state.get("talker") if active else None,
                "inboundTalkerId": state.get("sourceId") if active else None,
                "inboundTalkerActive": active if ready else None,
                "inboundTalkerStartEpoch": int(state.get("startEpoch", 0) or 0),
                "inboundTalkerEotEpoch": int(state.get("eotEpoch", 0) or 0),
                "inboundTalkerEventEpoch": int(state.get("eventEpoch", 0) or 0),
                "talkerEvidenceProvenance": state.get("provenance", ""),
                "talkerEvidenceReason": "" if ready else str(state.get("error", ""))[:160],
            })
            if not ready:
                entry["stale"] = True
                entry["connectionState"] = "stale"
        payload["stale"] = any(
            bool(entry.get("stale")) for entry in payload.get("bridges", {}).values()
        )
        payload["ok"] = not payload["stale"]
        return payload

    def stop_all(self) -> None:
        for _, stream in list(self.streams.values()):
            stream.stop()
        self.streams.clear()


def state_path(bridge: dict, spec: ModeSpec) -> Path:
    return spec.run_dir / f"{bridge['id']}.json"


def aggregate_path(spec: ModeSpec) -> Path:
    return spec.run_dir / "status.json"


def read_local_state(bridge: dict, spec: ModeSpec) -> dict:
    try:
        value = json.loads(state_path(bridge, spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_local_state(bridge: dict, spec: ModeSpec, payload: dict, expected_uid: int = 0) -> None:
    ensure_runtime(spec, expected_uid)
    path = state_path(bridge, spec)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=spec.run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_aggregate(
    spec: ModeSpec, payload: dict, expected_uid: int = 0
) -> None:
    ensure_runtime(spec, expected_uid)
    path = aggregate_path(spec)
    fd, temporary = tempfile.mkstemp(prefix="status.json.", dir=spec.run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
        secure_regular_file(path, f"{spec.label} aggregate status", expected_uid)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(
    bridge: dict,
    message: str,
    credentials: dict[str, str],
    socket_factory: SocketFactory = socket.create_connection,
) -> None:
    connection = mqtt_open(bridge, credentials, socket_factory)
    topic = f"{bridge['mqttName']}/command"
    packet_id = 1
    body = mqtt_field(topic) + packet_id.to_bytes(2, "big") + message.encode("utf-8")
    try:
        connection.sendall(bytes([0x32]) + mqtt_varint(len(body)) + body)
        header, payload = mqtt_read_packet(connection)
        if (
            header >> 4 != 4
            or payload != packet_id.to_bytes(2, "big")
        ):
            raise ControlError("MQTT ACL did not acknowledge the Gateway command.")
    except (OSError, socket.timeout) as exc:
        raise ControlError("Authenticated Gateway MQTT command failed.") from exc
    finally:
        connection.close()


def wait_event(
    bridge: dict,
    spec: ModeSpec,
    credentials: dict[str, str],
    started: int,
    target: int | None,
    disconnecting: bool,
    socket_factory: SocketFactory = socket.create_connection,
) -> dict:
    deadline = time.monotonic() + VERIFY_TIMEOUT
    last: ControlError | None = None
    while time.monotonic() < deadline:
        try:
            event = normalize_event(
                mqtt_payload(bridge, credentials, socket_factory), spec, disconnecting
            )
            if event["epoch"] >= started - 2:
                if disconnecting and event["action"] == "unlinked":
                    return event
                if not disconnecting and event["action"] in {"linking", "relinking"} and event["target"] == target:
                    return event
                if not disconnecting and event["action"] == "failed" and event["target"] == target:
                    raise ControlError("Gateway rejected the requested destination.")
        except ControlError as exc:
            last = exc
        time.sleep(0.1)
    if last and "rejected" in str(last):
        raise last
    raise ControlError("Gateway did not confirm the requested control-plane state.")


def audit(spec: ModeSpec, bridge: dict, user: str, action: str, target: int | None, result: str, expected_uid: int = 0) -> None:
    parent = spec.audit_log.parent
    secure_directory(parent, expected_uid)
    record = {
        "epoch": int(time.time()), "user": USER_RE.sub("_", user)[:80] or "unknown",
        "bridge": bridge["id"], "mode": spec.mode, "role": bridge["role"],
        "action": action, "target": target, "result": re.sub(r"[\r\n\t]+", " ", result)[:160],
    }
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(spec.audit_log, flags, 0o640)
    try:
        info = os.fstat(fd)
        if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) & 0o022:
            raise OSError("unsafe audit file")
        os.write(fd, (json.dumps(record, separators=(",", ":")) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def base_status(
    bridge: dict,
    spec: ModeSpec,
    services: dict,
    local: dict,
    event: dict | None,
    mqtt_available: bool = True,
) -> dict:
    requested = local.get("requestedTarget")
    requested = int(requested) if str(requested or "").isdigit() else None
    confirmed = None
    action = ""
    event_time = 0
    state = "offline" if not services["ready"] else "disconnected"
    if services["ready"] and not mqtt_available:
        state = "stale"
    elif event:
        action, event_time = event["action"], event["epoch"]
        fresh = (
            services["gatewayStartEpoch"] > 0
            and event_time > 0
            and event_time <= int(time.time()) + FUTURE_SKEW
            and event_time >= int(services["gatewayStartEpoch"]) - 2
        )
        if not fresh:
            state = "stale" if services["ready"] else "offline"
        elif action in {"linking", "relinking"}:
            confirmed, state = event["target"], "selected-unverified"
        elif action == "failed":
            state = "failed"
    if local.get("pending") and state not in {"offline", "stale", "failed"}:
        state = "pending"
    local_error = str(local.get("lastError", ""))[:160]
    if local_error and services["ready"] and state != "stale":
        state = "failed"
    message = (
        local_error
        if state == "failed" and local_error
        else "Gateway selection is not proof of remote reflector reachability."
    )
    return {
        "ok": True, "mode": spec.mode, "role": bridge["role"], "bridgeId": bridge["id"],
        "instance": bridge["instance"], "serviceState": services,
        "configuredTarget": bridge["fixedDestination"], "requestedTarget": requested,
        "confirmedTarget": confirmed, "gatewayAction": action,
        "gatewayEventEpoch": event_time, "reachabilityConfirmed": False,
        "talkerEvidenceAvailable": False,
        "inboundTalker": None,
        "inboundTalkerActive": None,
        "inboundTalkerEotEpoch": None,
        "talkerEvidenceReason": (
            f"{spec.label}Gateway MQTT has no talker/EOT event; MMDVMHost's "
            "non-retained activity stream is not configured for this controller."
        ),
        "connectionState": state,
        "message": message,
    }


def with_allstar_state(result: dict, linked: bool) -> dict:
    updated = dict(result)
    updated["allstarLinked"] = linked
    updated["digitalSelected"] = updated.get("confirmedTarget") is not None
    state = str(updated.get("connectionState", "stale"))
    if state == "selected-unverified" and linked:
        updated["connectionState"] = "connected-unverified"
    elif state == "selected-unverified" and not linked:
        updated["connectionState"] = "partial-digital-only"
    elif state == "disconnected" and linked:
        updated["connectionState"] = "partial-allstar-only"
    return updated


def status_for_bridge(
    bridge: dict,
    spec: ModeSpec,
    runner: Runner = default_runner,
    expected_uid: int = 0,
) -> dict:
    parse_ini(bridge, spec, expected_uid)
    credentials = load_mqtt_credentials(bridge)
    services = service_state(bridge, runner)
    event = None
    mqtt_available = not services["ready"]
    if services["ready"]:
        try:
            event = normalize_event(
                mqtt_payload(bridge, credentials),
                spec,
                read_local_state(bridge, spec).get("pendingAction") == "disconnect",
            )
            mqtt_available = True
        except ControlError:
            mqtt_available = False
    result = base_status(
        bridge, spec, services, read_local_state(bridge, spec), event, mqtt_available
    )
    return with_allstar_state(result, direct_linked(bridge, runner))


def status(bridge_id: str, path: Path, spec: ModeSpec, runner: Runner = default_runner, expected_uid: int = 0) -> dict:
    bridge = bridge_config(bridge_id, path, spec, expected_uid)
    return status_for_bridge(bridge, spec, runner, expected_uid)


def failed_watch_entry(spec: ModeSpec, bridge_id: str, message: str) -> dict:
    return {
        "ok": False,
        "mode": spec.mode,
        "role": "unknown",
        "bridgeId": bridge_id,
        "instance": "",
        "serviceState": {"ready": False, "services": {}, "gatewayStartEpoch": 0},
        "configuredTarget": None,
        "requestedTarget": None,
        "confirmedTarget": None,
        "gatewayAction": "",
        "gatewayEventEpoch": 0,
        "reachabilityConfirmed": False,
        "talkerEvidenceAvailable": False,
        "inboundTalker": None,
        "inboundTalkerActive": None,
        "inboundTalkerEotEpoch": None,
        "talkerEvidenceReason": (
            f"{spec.label}Gateway MQTT has no talker/EOT event; MMDVMHost's "
            "non-retained activity stream is not configured for this controller."
        ),
        "connectionState": "stale",
        "stale": True,
        "message": re.sub(r"[\r\n\t]+", " ", message)[:160],
    }


def collect_watch_snapshot(
    path: Path,
    spec: ModeSpec,
    runner: Runner = default_runner,
    expected_uid: int = 0,
    collector: Callable[[dict, ModeSpec, Runner, int], dict] = status_for_bridge,
    stop_event: threading.Event | None = None,
) -> dict:
    now = int(time.time())
    try:
        config = load_config(path, expected_uid)
    except ControlError as exc:
        return {
            "ok": False,
            "mode": spec.mode,
            "updatedEpoch": now,
            "stale": True,
            "bridges": {},
            "error": str(exc),
        }
    configured = [
        item for item in config["bridges"]
        if isinstance(item, dict) and item.get("digitalMode") == spec.mode
    ]
    entries: dict[str, dict] = {}
    interrupted = False
    for index, raw in enumerate(configured):
        if stop_event is not None and stop_event.is_set():
            interrupted = True
            break
        raw_id = str(raw.get("id", ""))
        key = raw_id if BRIDGE_ID_RE.fullmatch(raw_id) else f"invalid-{index + 1}"
        try:
            bridge = validate_bridge(raw, config, spec)
            entry = collector(bridge, spec, runner, expected_uid)
            entry["stale"] = entry.get("connectionState") == "stale"
            entry["collectedEpoch"] = int(time.time())
        except (ControlError, OSError) as exc:
            entry = failed_watch_entry(spec, key, str(exc))
            entry["collectedEpoch"] = int(time.time())
        entries[key] = entry
    stale = interrupted or any(bool(item.get("stale")) for item in entries.values())
    payload = {
        "ok": not stale,
        "mode": spec.mode,
        "updatedEpoch": int(time.time()),
        "stale": stale,
        "bridges": entries,
    }
    if interrupted:
        payload["error"] = "Status collection was interrupted."
    return payload


def watch(
    path: Path,
    spec: ModeSpec,
    interval: float,
    runner: Runner = default_runner,
    expected_uid: int = 0,
    stop_event: threading.Event | None = None,
    once: bool = False,
    collector: Callable[[dict, ModeSpec, Runner, int], dict] = status_for_bridge,
) -> dict | None:
    if not WATCH_INTERVAL_MIN <= interval <= WATCH_INTERVAL_MAX:
        raise ControlError("Watch interval must be between 1 and 60 seconds.")
    ensure_runtime(spec, expected_uid)
    stop = stop_event or threading.Event()
    last: dict | None = None
    talkers = TalkerManager(spec) if path == CONFIG_PATH and expected_uid == 0 else None
    try:
        while not stop.is_set():
            if talkers is not None:
                try:
                    talkers.sync(path, expected_uid)
                except (ControlError, OSError):
                    talkers.stop_all()
            snapshot = collect_watch_snapshot(
                path, spec, runner, expected_uid, collector, stop
            )
            if stop.is_set():
                break
            if talkers is not None:
                snapshot = talkers.augment(snapshot)
            write_aggregate(spec, snapshot, expected_uid)
            last = snapshot
            if once or stop.wait(interval):
                break
    finally:
        if talkers is not None:
            talkers.stop_all()
    return last


def connect(bridge_id: str, target_value: object, user: str, path: Path, spec: ModeSpec, runner: Runner = default_runner, expected_uid: int = 0) -> dict:
    bridge = bridge_config(bridge_id, path, spec, expected_uid)
    parse_ini(bridge, spec, expected_uid)
    credentials = load_mqtt_credentials(bridge)
    target = designator(target_value, spec)
    if bridge["role"] == "standard" and target != bridge["fixedDestination"]:
        raise ControlError("Standard Bridge may connect only to its configured fixed destination.")
    if bridge["role"] == "net" and target not in bridge["approvedDestinations"]:
        raise ControlError("Destination is not approved for this Net Bridge.")
    services = service_state(bridge, runner)
    if not services["ready"]:
        raise ControlError("Bridge services are not ready; no MQTT command was sent.")
    previous_target = None
    try:
        previous_event = normalize_event(mqtt_payload(bridge, credentials), spec)
        if previous_event["action"] in {"linking", "relinking"}:
            previous_target = previous_event["target"]
    except ControlError:
        pass
    ensure_runtime(spec, expected_uid)
    lock_path = spec.run_dir / f"{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another bridge-control action is running.") from exc
        started = int(time.time())
        try:
            audit(spec, bridge, user, "connect", target, "attempt", expected_uid)
        except OSError as exc:
            raise ControlError("Audit log is unavailable; no MQTT command was sent.") from exc
        write_local_state(bridge, spec, {"requestedTarget": target, "pending": True, "pendingAction": "connect", "epoch": started}, expected_uid)
        try:
            publish(bridge, f"TalkGroup {target}", credentials)
            event = wait_event(bridge, spec, credentials, started, target, False)
        except ControlError as exc:
            write_local_state(
                bridge, spec,
                {
                    "requestedTarget": target, "pending": False,
                    "pendingAction": "connect", "lastError": str(exc)[:160],
                    "epoch": started,
                },
                expected_uid,
            )
            audit(spec, bridge, user, "connect", target, f"failed: {exc}", expected_uid)
            raise
        try:
            set_direct_link(bridge, True, runner)
        except ControlError as exc:
            rollback_ok = False
            rollback_message = "digital rollback unconfirmed"
            try:
                if previous_target is not None and previous_target != target:
                    publish(bridge, f"TalkGroup {previous_target}", credentials)
                    wait_event(
                        bridge, spec, credentials, int(time.time()),
                        previous_target, False,
                    )
                    rollback_message = f"restored {previous_target}"
                else:
                    publish(bridge, "TalkGroup 9999", credentials)
                    wait_event(
                        bridge, spec, credentials, int(time.time()), None, True
                    )
                    rollback_message = "digital disconnected"
                rollback_ok = True
            except ControlError:
                pass
            error = f"AllStar link failed; {rollback_message}"
            write_local_state(
                bridge, spec,
                {
                    "requestedTarget": target, "pending": False,
                    "pendingAction": "connect", "lastError": error,
                    "epoch": started,
                },
                expected_uid,
            )
            audit(spec, bridge, user, "connect", target, error, expected_uid)
            raise PartialControlError(
                error,
                {
                    "digitalRollbackConfirmed": rollback_ok,
                    "allstarLinked": False,
                    "reachabilityConfirmed": False,
                },
            ) from exc
        write_local_state(
            bridge, spec,
            {"requestedTarget": target, "pending": False, "pendingAction": "connect", "epoch": started},
            expected_uid,
        )
        audit(spec, bridge, user, "connect", target, "gateway and AllStar accepted; reachability unverified", expected_uid)
    result = with_allstar_state(
        base_status(bridge, spec, services, read_local_state(bridge, spec), event),
        True,
    )
    result["message"] = f"{spec.label} gateway selected {target} and AllStar linked; reflector reachability remains unverified."
    return result


def disconnect(bridge_id: str, user: str, path: Path, spec: ModeSpec, runner: Runner = default_runner, expected_uid: int = 0) -> dict:
    bridge = bridge_config(bridge_id, path, spec, expected_uid)
    ensure_runtime(spec, expected_uid)
    lock_path = spec.run_dir / f"{bridge_id}.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControlError("Another bridge-control action is running.") from exc
        started = int(time.time())
        try:
            audit(spec, bridge, user, "disconnect", None, "attempt", expected_uid)
        except OSError as exc:
            raise ControlError("Audit log is unavailable; no MQTT command was sent.") from exc
        write_local_state(bridge, spec, {"requestedTarget": None, "pending": True, "pendingAction": "disconnect", "epoch": started}, expected_uid)
        digital_ok = False
        digital_error = ""
        event = None
        try:
            parse_ini(bridge, spec, expected_uid)
            credentials = load_mqtt_credentials(bridge)
            services = service_state(bridge, runner)
            if not services["ready"]:
                raise ControlError("Bridge services are not ready.")
            publish(bridge, "TalkGroup 9999", credentials)
            event = wait_event(bridge, spec, credentials, started, None, True)
            digital_ok = True
        except ControlError as exc:
            digital_error = str(exc)
        allstar_ok = False
        allstar_error = ""
        try:
            set_direct_link(bridge, False, runner)
            allstar_ok = True
        except ControlError as exc:
            allstar_error = str(exc)
        if not digital_ok or not allstar_ok:
            failures = []
            if not digital_ok:
                failures.append("digital: " + digital_error)
            if not allstar_ok:
                failures.append("AllStar: " + allstar_error)
            error = "Disconnect partially failed: " + "; ".join(failures)
            write_local_state(
                bridge, spec,
                {
                    "requestedTarget": None, "pending": False,
                    "pendingAction": "disconnect", "lastError": error[:160],
                    "epoch": started,
                },
                expected_uid,
            )
            audit(spec, bridge, user, "disconnect", None, error, expected_uid)
            raise PartialControlError(
                error,
                {
                    "digitalDisconnected": digital_ok,
                    "allstarUnlinked": allstar_ok,
                    "reachabilityConfirmed": False,
                },
            )
        write_local_state(
            bridge, spec,
            {"requestedTarget": None, "pending": False, "pendingAction": "disconnect", "epoch": started},
            expected_uid,
        )
        audit(spec, bridge, user, "disconnect", None, "gateway disconnected and AllStar unlinked", expected_uid)
    result = with_allstar_state(
        base_status(bridge, spec, services, read_local_state(bridge, spec), event),
        False,
    )
    result["message"] = f"{spec.label} gateway is disconnected and AllStar is unlinked."
    return result


def self_test(spec: ModeSpec) -> None:
    uid = os.getuid()
    with tempfile.TemporaryDirectory(prefix=f"asr-{spec.mode}-control.") as directory:
        root = Path(directory)
        run = root / "run"
        audit_dir = root / "log"
        gateway = root / f"{spec.gateway_dir}_alpha" / spec.gateway_ini
        run.mkdir(mode=0o755)
        audit_dir.mkdir(mode=0o755)
        gateway.parent.mkdir(mode=0o755)
        gateway.write_text("[MQTT]\nAddress=127.0.0.1\nPort=1883\nAuth=1\nUsername=gateway\nPassword=gateway-secret\nName=test-alpha\n[Remote Commands]\nEnable=1\n", encoding="utf-8")
        os.chmod(gateway, 0o600)
        test_spec = ModeSpec(spec.mode, spec.label, spec.gateway_dir, spec.gateway_ini, run, audit_dir / "audit.jsonl", spec.reserved, spec.emulator_allowed)
        bridge = {
            "id": f"{spec.mode}_net", "digitalMode": spec.mode, "bridgeRole": "net",
            "cardType": f"{spec.mode}_net", "instance": "alpha",
            "gatewayConfig": str(gateway_path("alpha", spec)),
            "gatewayService": f"{spec.mode}gateway-alpha.service",
            "mmdvmService": f"mmdvm-{spec.mode}-alpha.service",
            "analogBridgeService": f"analog-{spec.mode}-alpha.service",
            "mqttHost": "127.0.0.1", "mqttPort": 1883, "mqttName": "test-alpha",
            "mmdvmMqttName": "mmdvm-test-alpha",
            "bridgePermission": "approved", "approvedDestinations": ["10200"], "allowTune": True,
            "node": "2001",
        }
        def test_config(*items: dict) -> dict:
            return {"node": "1001", "bridges": list(items)}
        config = root / "config.json"
        config.write_text(json.dumps(test_config(bridge)), encoding="utf-8")
        os.chmod(config, 0o600)
        trusted_dir = root / "trusted-config"
        trusted_dir.mkdir(mode=0o775)
        os.chmod(trusted_dir, 0o775)
        trusted_config = trusted_dir / "config.json"
        trusted_config.write_text(
            json.dumps(test_config(bridge)), encoding="utf-8"
        )
        os.chmod(trusted_config, 0o664)
        assert load_trusted_config(
            trusted_config, uid, os.getgid(), 0o775, 0o664
        )["bridges"][0]["id"] == bridge["id"]
        os.chmod(trusted_config, 0o666)
        try:
            load_trusted_config(
                trusted_config, uid, os.getgid(), 0o775, 0o664
            )
            raise AssertionError("world-writable trusted config accepted")
        except ControlError:
            pass
        os.chmod(trusted_config, 0o664)
        os.chmod(trusted_dir, 0o777)
        try:
            load_trusted_config(
                trusted_config, uid, os.getgid(), 0o775, 0o664
            )
            raise AssertionError("world-writable trusted config directory accepted")
        except ControlError:
            pass
        os.chmod(trusted_dir, 0o775)
        # Production-path checks are independently tested; point the validated object at the fixture.
        validated = validate_bridge(bridge, test_config(bridge), spec)
        validated["gatewayPath"] = gateway
        parse_ini(validated, spec, uid)
        assert designator("10200", spec) == 10200
        for bad in ("0", "10", "65535", "1x", *(str(item) for item in spec.reserved)):
            try:
                designator(bad, spec)
                raise AssertionError("unsafe designator accepted")
            except ControlError:
                pass
        denied = dict(bridge, bridgePermission="unknown")
        try:
            validate_bridge(denied, test_config(denied), spec)
            raise AssertionError("unknown permission accepted")
        except ControlError:
            pass
        if not spec.emulator_allowed:
            wrong_emulator = dict(bridge, emulatorService="md380-alpha.service")
            try:
                validate_bridge(wrong_emulator, test_config(wrong_emulator), spec)
                raise AssertionError("disallowed emulator accepted")
            except ControlError:
                pass
        unapproved = dict(bridge, approvedDestinations=["10201"])
        unapproved_validated = validate_bridge(
            unapproved, test_config(unapproved), spec
        )
        assert 10200 not in unapproved_validated["approvedDestinations"]
        standard = dict(
            bridge,
            id=f"{spec.mode}_fixed",
            bridgeRole="standard",
            cardType="standard",
            fixedDestination="10200",
        )
        standard.pop("approvedDestinations")
        standard.pop("allowTune")
        assert validate_bridge(standard, test_config(standard), spec)[
            "fixedDestination"
        ] == 10200
        collision = dict(bridge, id=f"{spec.mode}_collision")
        try:
            validate_bridge(bridge, test_config(bridge, collision), spec)
            raise AssertionError("overlapping bridge resources accepted")
        except ControlError:
            pass
        try:
            normalized_destinations("10200", spec)
            raise AssertionError("non-array approval accepted")
        except ControlError:
            pass
        linked = normalize_event(json.dumps({"link": {"timestamp": "2026-08-11T12:00:00Z", "action": "linking", "reason": "remote", "talkgroup": 10200}}), spec)
        assert linked["target"] == 10200 and linked["action"] == "linking"
        unlinked = normalize_event(json.dumps({"link": {"timestamp": "2026-08-11T12:00:01Z", "action": "failed", "reason": "remote", "talkgroup": 9999}}), spec, True)
        assert unlinked["action"] == "unlinked" and unlinked["target"] is None
        unsafe = root / "unsafe.json"
        unsafe.write_text("{}", encoding="utf-8")
        os.chmod(unsafe, 0o666)
        try:
            secure_regular_file(unsafe, "fixture", uid)
            raise AssertionError("writable config accepted")
        except ControlError:
            pass
        calls: list[list[str]] = []
        def fake_runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            calls.append(argv)
            if argv[0].endswith("systemctl"):
                return subprocess.CompletedProcess(argv, 0, "ActiveState=active\nSubState=running\nMainPID=42\nExecMainStartTimestampMonotonic=1\n", "")
            if argv[0] == ASTERISK_BIN:
                return subprocess.CompletedProcess(
                    argv, 0,
                    "NODE PEER RECONNECTS DIRECTION CONNECT TIME CONNECT STATE\n",
                    "",
                )
            return subprocess.CompletedProcess(argv, 1, "", "")

        class FakeSocket:
            def __init__(self, response: bytes):
                self.response = bytearray(response)
                self.sent = bytearray()
            def settimeout(self, timeout: float) -> None:
                pass
            def sendall(self, data: bytes) -> None:
                self.sent.extend(data)
            def recv(self, length: int) -> bytes:
                value = bytes(self.response[:length])
                del self.response[:length]
                return value
            def close(self) -> None:
                pass

        credentials = {"username": "controller", "password": "secret"}
        mqtt_json = json.dumps({
            "link": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "linking", "reason": "remote", "talkgroup": 10200,
            }
        })
        topic_body = mqtt_field("test-alpha/json") + mqtt_json.encode()
        subscribe_socket = FakeSocket(
            b"\x20\x02\x00\x00"
            + b"\x90\x03\x00\x01\x01"
            + bytes([0x31]) + mqtt_varint(len(topic_body)) + topic_body
        )
        assert normalize_event(
            mqtt_payload(validated, credentials, lambda address, timeout: subscribe_socket),
            spec,
        )["target"] == 10200
        publish_socket = FakeSocket(
            b"\x20\x02\x00\x00" + b"\x40\x02\x00\x01"
        )
        publish(
            validated,
            "TalkGroup 10200",
            credentials,
            lambda address, timeout: publish_socket,
        )
        assert b"secret" in publish_socket.sent
        service_snapshot = service_state(validated, fake_runner)
        current_event = {
            "action": "linking", "target": 10200,
            "epoch": int(time.time()), "reason": "remote",
        }
        assert base_status(
            validated, spec, service_snapshot, {}, current_event
        )["connectionState"] == "selected-unverified"
        assert base_status(
            validated, spec, service_snapshot, {}, None, False
        )["connectionState"] == "stale"
        failed_status = base_status(
            validated, spec, service_snapshot,
            {"pending": False, "lastError": "gateway command timed out"},
            current_event,
        )
        assert failed_status["connectionState"] == "failed"
        assert failed_status["message"] == "gateway command timed out"
        assert base_status(
            validated, spec, service_snapshot, {}, current_event
        )["reachabilityConfirmed"] is False
        evidence_status = base_status(
            validated, spec, service_snapshot, {}, current_event
        )
        assert evidence_status["talkerEvidenceAvailable"] is False
        assert evidence_status["inboundTalker"] is None
        assert evidence_status["inboundTalkerActive"] is None
        assert evidence_status["inboundTalkerEotEpoch"] is None
        activity_topic = f"{validated['mmdvmMqttName']}/json"
        activity_epoch = int(time.time())
        activity_start = json.dumps({
            spec.label: {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "network", "action": "start",
                "src_id": 12345, "src_info": "N0CALL",
                "dst_id": 10200, "group": "yes",
            }
        })
        inbound = parse_mmdvm_activity(
            activity_start, spec, activity_topic, activity_topic,
            False, activity_epoch,
        )
        assert inbound is not None and inbound["talker"] == "N0CALL"
        assert inbound["provenance"] == "mmdvm-network"
        outbound_payload = activity_start.replace('"network"', '"rf"')
        assert parse_mmdvm_activity(
            outbound_payload, spec, activity_topic, activity_topic,
            False, activity_epoch,
        ) is None
        assert parse_mmdvm_activity(
            activity_start, spec, "wrong/json", activity_topic,
            False, activity_epoch,
        ) is None
        assert parse_mmdvm_activity(
            activity_start, spec, activity_topic, activity_topic,
            True, activity_epoch,
        ) is None
        activity_end = json.dumps({
            spec.label: {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "end", "duration": 12.0,
            }
        })
        ended = parse_mmdvm_activity(
            activity_end, spec, activity_topic, activity_topic,
            False, activity_epoch,
        )
        assert ended is not None and ended["kind"] == "end"
        stream_fixture = TalkerStream(validated, spec, credentials)
        stream_fixture._apply({
            **inbound, "epoch": int(time.time()) - 20,
        })
        assert stream_fixture.snapshot()["active"] is True
        assert stream_fixture.snapshot()["talker"] == "N0CALL"
        stream_fixture._apply(ended)
        cleared = stream_fixture.snapshot()
        assert cleared["active"] is False and cleared["talker"] is None
        assert cleared["eotEpoch"] == ended["epoch"]
        retained_disconnect = normalize_event(
            json.dumps({
                "link": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "failed", "reason": "remote", "talkgroup": 9999,
                }
            }),
            spec,
            requested_disconnect=True,
        )
        assert base_status(
            validated,
            spec,
            service_snapshot,
            {"pending": False, "pendingAction": "disconnect"},
            retained_disconnect,
        )["connectionState"] == "disconnected"
        audit(test_spec, validated, "tester\nsecret", "connect", 10200, "ok\tredacted", uid)
        audit_text = test_spec.audit_log.read_text(encoding="utf-8")
        assert "\nsecret" not in audit_text and "mqttPassword" not in audit_text
        foreign = dict(
            bridge,
            id="foreign_mode",
            digitalMode="other",
            instance="foreign",
            gatewayConfig="/opt/Other_alpha/Other.ini",
            gatewayService="other-gateway.service",
            mmdvmService="other-mmdvm.service",
            analogBridgeService="other-analog.service",
            mqttName="other-gateway",
            mmdvmMqttName="other-mmdvm-gateway",
        )
        foreign["node"] = "3001"
        config.write_text(json.dumps(test_config(bridge, foreign)), encoding="utf-8")
        os.chmod(config, 0o600)

        def fake_collector(
            watched_bridge: dict,
            watched_spec: ModeSpec,
            watched_runner: Runner,
            watched_uid: int,
        ) -> dict:
            assert watched_bridge["id"] == bridge["id"]
            assert watched_spec.mode == spec.mode
            assert watched_uid == uid
            return {
                "ok": True,
                "mode": spec.mode,
                "bridgeId": watched_bridge["id"],
                "connectionState": "disconnected",
                "reachabilityConfirmed": False,
            }

        snapshot = collect_watch_snapshot(
            config, test_spec, fake_runner, uid, fake_collector
        )
        assert snapshot["ok"] is True and snapshot["stale"] is False
        assert list(snapshot["bridges"]) == [bridge["id"]]
        assert snapshot["bridges"][bridge["id"]]["stale"] is False
        write_aggregate(test_spec, snapshot, uid)
        aggregate = json.loads(
            aggregate_path(test_spec).read_text(encoding="utf-8")
        )
        assert aggregate["mode"] == spec.mode and aggregate["bridges"]
        secure_regular_file(
            aggregate_path(test_spec), f"{spec.label} aggregate fixture", uid
        )

        def failing_collector(
            watched_bridge: dict,
            watched_spec: ModeSpec,
            watched_runner: Runner,
            watched_uid: int,
        ) -> dict:
            raise ControlError("fixture state unavailable")

        failed_snapshot = collect_watch_snapshot(
            config, test_spec, fake_runner, uid, failing_collector
        )
        assert failed_snapshot["ok"] is False and failed_snapshot["stale"] is True
        failed_entry = failed_snapshot["bridges"][bridge["id"]]
        assert failed_entry["connectionState"] == "stale"
        assert failed_entry["reachabilityConfirmed"] is False
        once_snapshot = watch(
            config,
            test_spec,
            1.0,
            fake_runner,
            uid,
            once=True,
            collector=fake_collector,
        )
        assert once_snapshot is not None and once_snapshot["ok"] is True
        assert json.loads(
            aggregate_path(test_spec).read_text(encoding="utf-8")
        )["updatedEpoch"] == once_snapshot["updatedEpoch"]
        stopped = threading.Event()
        stopped.set()
        assert watch(
            config, test_spec, 1.0, fake_runner, uid, stopped, once=True
        ) is None
        try:
            watch(config, test_spec, 0.5, fake_runner, uid, once=True)
            raise AssertionError("unsafe watch interval accepted")
        except ControlError:
            pass
    print(f"{spec.label} bridge-control self-test: ok")


def main(spec: ModeSpec = P25_SPEC) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True)
    status_parser = actions.add_parser("status")
    status_parser.add_argument("bridge_id")
    connect_parser = actions.add_parser("connect")
    connect_parser.add_argument("bridge_id")
    connect_parser.add_argument("destination")
    connect_parser.add_argument("--user", required=True)
    disconnect_parser = actions.add_parser("disconnect")
    disconnect_parser.add_argument("bridge_id")
    disconnect_parser.add_argument("--user", required=True)
    watch_parser = actions.add_parser("watch")
    watch_parser.add_argument("--interval", type=float, default=2.0)
    watch_parser.add_argument("--once", action="store_true")
    actions.add_parser("self-test")
    args = parser.parse_args()
    try:
        if args.action == "self-test":
            self_test(spec)
            return 0
        raw_args = sys.argv[1:]
        if args.action == "connect" and raw_args != [
            "connect", args.bridge_id, args.destination, "--user", args.user
        ]:
            raise ControlError(
                "Connect syntax is exactly: connect BRIDGE_ID DESTINATION --user USER."
            )
        if args.action == "disconnect" and raw_args != [
            "disconnect", args.bridge_id, "--user", args.user
        ]:
            raise ControlError(
                "Disconnect syntax is exactly: disconnect BRIDGE_ID --user USER."
            )
        if os.geteuid() != 0:
            raise ControlError("Bridge control must run as root.")
        if args.action == "watch":
            stop_event = threading.Event()

            def request_stop(signum: int, frame: object) -> None:
                stop_event.set()

            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
            result = watch(
                CONFIG_PATH, spec, args.interval, stop_event=stop_event, once=args.once
            )
            if args.once and result is not None:
                print(json.dumps(result, separators=(",", ":")))
                return 0 if result.get("ok") is True else 1
            return 0
        if args.action == "status":
            result = status(args.bridge_id, CONFIG_PATH, spec)
        elif args.action == "connect":
            result = connect(
                args.bridge_id, args.destination, args.user, CONFIG_PATH, spec
            )
        else:
            result = disconnect(args.bridge_id, args.user, CONFIG_PATH, spec)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except PartialControlError as exc:
        print(json.dumps(
            {"ok": False, "error": str(exc), **exc.details},
            separators=(",", ":"),
        ))
        return 1
    except ControlError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1
    except OSError:
        print(json.dumps({"ok": False, "error": "Local bridge-control storage is unavailable."}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
