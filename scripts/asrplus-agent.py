#!/usr/bin/env python3
"""Publish remote hub connections and execute queued ASR Plus commands."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


TALKER_FRESH_SECONDS = 20
TALKER_HISTORY_LIMIT = 25
TALKER_HEARTBEAT_SECONDS = 5


def run_ssh(config: dict, command: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    control_path = config.get("controlPath", "/run/asrplus/ssh-%C")
    Path(control_path).parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run([
        "/usr/bin/ssh", "-i", config["key"], "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={config['knownHosts']}",
        "-o", "ControlMaster=auto", "-o", "ControlPersist=15", "-o", f"ControlPath={control_path}",
        "-o", "ConnectTimeout=6", config.get("server", "root@44.30.157.21"), command,
    ], input=stdin, capture_output=True, text=True, timeout=12, check=False)


def labels(path: str) -> dict[str, str]:
    result = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("|")
            if len(parts) >= 4 and parts[0].isdigit():
                result[parts[0]] = " ".join(value for value in parts[1:4] if value).strip()[:140]
    except OSError:
        pass
    return result


def node_metadata(path: str, node: str) -> dict[str, str]:
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("|")
            if len(parts) >= 4 and parts[0] == node:
                return {"callsign": parts[1].strip()[:32], "description": parts[2].strip()[:80],
                    "location": parts[3].strip()[:80]}
    except OSError:
        pass
    return {"callsign": "", "description": "", "location": ""}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def manager_credentials(path: str) -> tuple[str, str]:
    section = ""
    values: dict[str, dict[str, str]] = {}
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().replace("\u00a0", "")
        if not line or line.startswith((";", "#")):
            continue
        match = re.fullmatch(r"\[([^]]+)\]", line)
        if match:
            section = match.group(1).strip()
            values.setdefault(section, {})
        elif "=" in line and section and section != "general":
            key, value = line.split("=", 1)
            values[section][key.strip().lower()] = value.split(";", 1)[0].strip()
    for username, settings in values.items():
        if settings.get("secret"):
            return username, settings["secret"]
    raise RuntimeError("no AMI account is available")


class AmiClient:
    def __init__(self, config: dict):
        self.config = config
        self.sock: socket.socket | None = None
        self.buffer = b""

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buffer = b""

    def read_quiet(self, first: float = 1.0, quiet: float = 0.12) -> str:
        if self.sock is None:
            raise RuntimeError("AMI is disconnected")
        chunks = []
        self.sock.settimeout(first)
        while True:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                raise ConnectionError("AMI closed the connection")
            chunks.append(chunk)
            self.sock.settimeout(quiet)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def action(self, action: str, **fields: str) -> str:
        if self.sock is None:
            raise RuntimeError("AMI is disconnected")
        lines = [f"Action: {action}", f"ActionID: asrplus-{int(time.time() * 1000)}"]
        lines.extend(f"{key}: {value}" for key, value in fields.items())
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return self.read_quiet()

    def connect(self) -> None:
        self.close()
        username, secret = manager_credentials(self.config.get("managerConf", "/etc/asterisk/manager.conf"))
        self.sock = socket.create_connection(("127.0.0.1", 5038), timeout=2)
        self.read_quiet(0.5)
        response = self.action("Login", Username=username, Secret=secret, Events="off")
        if not re.search(r"(?im)^Response:\s*Success\s*$", response):
            raise RuntimeError("AMI login failed")

    def status(self, command: str) -> str:
        return self.action("RptStatus", Command=command, Node=str(self.config["node"]))

    def enable_events(self) -> None:
        response = self.action("Events", EventMask="on")
        if not re.search(r"(?im)^Response:\s*Success\s*$", response):
            raise RuntimeError("AMI event subscription failed")

    def events(self) -> list[dict[str, str]]:
        if self.sock is None:
            raise RuntimeError("AMI is disconnected")
        self.sock.settimeout(1.0)
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return []
        if not chunk:
            raise ConnectionError("AMI closed the connection")
        self.buffer += chunk
        events = []
        while b"\r\n\r\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
            frame = {}
            for line in raw.decode(errors="replace").split("\r\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    frame[key.strip()] = value.strip()
            if frame.get("Event"):
                frame["_received_epoch_ms"] = str(time.time_ns() // 1_000_000)
                events.append(frame)
        return events


def parse_talker_status(xstat: str, sawstat: str) -> tuple[bool, dict[str, dict]]:
    match = re.search(r"(?im)^Var:\s*RPT_RXKEYED=(\d+)", xstat)
    local_keyed = bool(match and match.group(1) != "0")
    remotes = {}
    for line in sawstat.splitlines():
        match = re.match(r"^Conn:\s*(\S+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", line.strip(), re.I)
        if match:
            remotes[match.group(1)] = {"keyed": match.group(2) != "0",
                "last_keyed_seconds": int(match.group(3)), "last_unkeyed_seconds": int(match.group(4))}
    return local_keyed, remotes


def parse_alinks(value: str) -> dict[str, dict]:
    remotes = {}
    for token in [part.strip() for part in value.split(",") if part.strip()][1:]:
        match = re.fullmatch(r"(.+?)([TRC]?)([KU])", token, re.I)
        if match:
            node, mode, state = match.groups()
            remotes[node] = {"keyed": state.upper() == "K", "mode": mode.upper() or "T"}
    return remotes


class TalkerState:
    def __init__(self, config: dict):
        self.config = config
        self.path = Path(config.get("talkerStatus", "/run/asrplus/talkers.json"))
        self.metadata = node_metadata(config.get("astdb", "/var/lib/asterisk/astdb.txt"), str(config["node"]))
        self.labels = labels(config.get("astdb", "/var/lib/asterisk/astdb.txt"))
        self.keyed: dict[str, bool] = {}
        self.started: dict[str, int] = {}
        self.history: list[dict] = []
        self.last_write = 0
        self.sequence = 0
        self.healthy = False
        self.watcher_instance = uuid.uuid4().hex
        self.event_times = {"asterisk_event_epoch_ms": 0, "watcher_detected_epoch_ms": 0,
            "state_published_epoch_ms": 0}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved.get("last_talkers"), list):
                self.history = saved["last_talkers"][:TALKER_HISTORY_LIMIT]
            self.sequence = max(0, int(saved.get("talker_sequence") or 0))
        except (OSError, ValueError, AttributeError):
            pass

    def entry(self, talker_id: str, started: int, ended: int | None = None) -> dict:
        local = talker_id == "local-rf"
        node = str(self.config["node"]) if local else talker_id
        label = " ".join(value for value in (self.metadata.get("callsign"), self.metadata.get("description")) if value) if local else self.labels.get(node, f"Node {node}")
        entry = {"node": node, "label": label, "keyed_started_epoch": started,
            "event_epoch": ended or started, "originated_locally": local}
        if local:
            entry.update(self.metadata)
        if ended is not None:
            entry["duration_seconds"] = max(0, ended - started)
            entry["last_keyed"] = f"{entry['duration_seconds'] // 60:02d}:{entry['duration_seconds'] % 60:02d}"
        return entry

    def publish(self, payload: dict, changed: bool, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        now_ms = time.time_ns() // 1_000_000
        if changed:
            self.event_times["state_published_epoch_ms"] = now_ms
        atomic_json(self.path, payload | {"healthy": self.healthy, "updated_epoch": now,
            "talker_sequence": self.sequence, "watcher_instance": self.watcher_instance} | self.event_times)
        self.last_write = now
        if changed:
            event_path = Path(self.config.get("talkerEvent", "/run/asrplus/talker-event"))
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_path.touch()

    def update(self, local_keyed: bool, remotes: dict[str, dict], detected_epoch_ms: int = 0,
            asterisk_epoch_ms: int = 0) -> bool:
        now = int(time.time())
        observed = {node: bool(info.get("keyed")) for node, info in remotes.items()}
        observed["local-rf"] = local_keyed
        changed = observed != self.keyed or not self.healthy
        for talker_id in set(self.keyed) | set(observed):
            was_keyed, is_keyed = bool(self.keyed.get(talker_id)), bool(observed.get(talker_id))
            if is_keyed and not was_keyed:
                seconds = int(remotes.get(talker_id, {}).get("last_keyed_seconds", 0) or 0)
                self.started[talker_id] = now - seconds if 0 < seconds < 86400 else now
            elif was_keyed and not is_keyed:
                started = self.started.pop(talker_id, now)
                self.history.insert(0, self.entry(talker_id, started, now))
                self.history = self.history[:TALKER_HISTORY_LIMIT]
        self.keyed = observed
        self.healthy = True
        if changed:
            self.sequence += 1
            self.event_times["asterisk_event_epoch_ms"] = max(0, int(asterisk_epoch_ms or 0))
            self.event_times["watcher_detected_epoch_ms"] = max(0, int(detected_epoch_ms or time.time_ns() // 1_000_000))
        active = sorted((node for node, keyed in observed.items() if keyed),
            key=lambda node: self.started.get(node, now), reverse=True)
        current = self.entry(active[0], self.started.get(active[0], now)) if active else {}
        if current:
            current["last_keyed"] = f"{(now - current['keyed_started_epoch']) // 60:02d}:{(now - current['keyed_started_epoch']) % 60:02d}"
        if changed or now - self.last_write >= TALKER_HEARTBEAT_SECONDS:
            self.publish({"current_talker": current, "last_talkers": self.history}, changed, now)
        return changed

    def failed(self, error: Exception) -> bool:
        changed = self.healthy
        self.healthy = False
        if changed:
            self.sequence += 1
            self.event_times["watcher_detected_epoch_ms"] = time.time_ns() // 1_000_000
        self.publish({"error": str(error)[:160], "current_talker": {},
            "last_talkers": self.history}, changed)
        return changed


class EventPublisher:
    """Serialize transition pushes without losing changes during an active push."""

    def __init__(self, config: dict):
        self.config = config
        self.condition = threading.Condition()
        self.pending = False
        threading.Thread(target=self.run, name="asrplus-event-publisher", daemon=True).start()

    def trigger(self) -> None:
        with self.condition:
            self.pending = True
            self.condition.notify()

    def run(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending)
                self.pending = False
            try:
                publish_transition(self.config)
            except Exception as error:
                print(f"event publication failed: {str(error)[:160]}", file=sys.stderr, flush=True)


def watch_talkers(config: dict) -> int:
    state, client, publisher, backoff = TalkerState(config), AmiClient(config), EventPublisher(config), 1.0
    local_keyed, remotes = False, {}
    while True:
        try:
            if client.sock is None:
                client.connect()
                local_keyed, remotes = parse_talker_status(client.status("XStat"), client.status("SawStat"))
                if state.update(local_keyed, remotes):
                    publisher.trigger()
                client.enable_events()
                backoff = 1.0
            for event in client.events():
                if event.get("Node") != str(config["node"]):
                    continue
                if event.get("Event") == "RPT_ALINKS":
                    parsed = parse_alinks(event.get("EventValue", ""))
                    remotes = parsed
                elif event.get("Event") == "RPT_RXKEYED":
                    local_keyed = event.get("EventValue", "").strip().lower() not in {"", "0", "false"}
                else:
                    continue
                event_tv = event.get("EventTV") or event.get("Timestamp") or ""
                try:
                    asterisk_epoch_ms = int(float(event_tv) * 1000)
                except (TypeError, ValueError):
                    asterisk_epoch_ms = 0
                if state.update(local_keyed, remotes, int(event.get("_received_epoch_ms") or 0), asterisk_epoch_ms):
                    publisher.trigger()
            if state.update(local_keyed, remotes):
                publisher.trigger()
        except Exception as error:
            if state.failed(error):
                publisher.trigger()
            client.close()
            time.sleep(backoff)
            backoff = min(30.0, backoff * 2)


def collect(config: dict) -> dict:
    result = subprocess.run(["/usr/sbin/asterisk", "-rx", f"rpt lstats {config['node']}"], capture_output=True, text=True, timeout=8, check=False)
    if result.returncode != 0:
        raise RuntimeError("rpt lstats failed")
    names = labels(config.get("astdb", "/var/lib/asterisk/astdb.txt"))
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 6:
            node, _peer, _reconnects, direction, connected, state = fields
        elif len(fields) == 5:
            node, _reconnects, direction, connected, state = fields
        else:
            continue
        if state != "ESTABLISHED":
            continue
        if node in {"1", str(config["node"])}:
            continue
        rows.append({"node": node, "label": names.get(node, f"AllStar node {node}"),
            "direction": direction, "mode": "T", "connected": connected,
            "received": "", "keyed": False})
    bridges = {}
    public_path = Path(config.get("publicStatus", "/var/www/html/allscan/public-status.json"))
    try:
        public = json.loads(public_path.read_text(encoding="utf-8"))
        if str(public.get("node", {}).get("number")) == str(config["node"]) and isinstance(public.get("bridges"), dict):
            bridges = public["bridges"]
    except (OSError, ValueError):
        pass
    talkers = {}
    try:
        candidate = json.loads(Path(config.get("talkerStatus", "/run/asrplus/talkers.json")).read_text(encoding="utf-8"))
        if candidate.get("healthy") is True and int(time.time()) - int(candidate.get("updated_epoch") or 0) <= TALKER_FRESH_SECONDS:
            talkers = candidate
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    current_node = str(talkers.get("current_talker", {}).get("node", ""))
    for row in rows:
        if row["node"] == current_node:
            row["keyed"] = True
            row["received"] = str(talkers["current_talker"].get("last_keyed", ""))[:24]
    telemetry = {key: talkers.get(key) for key in ("talker_sequence", "watcher_instance",
        "asterisk_event_epoch_ms", "watcher_detected_epoch_ms", "state_published_epoch_ms")}
    return {"hub": config["hub"], "node": str(config["node"]), "generated_epoch": int(time.time()),
        "connections": rows, "bridges": bridges, "current_talker": talkers.get("current_talker", {}),
        "last_talkers": talkers.get("last_talkers", [])} | telemetry


def stamped_status(config: dict) -> dict:
    started_ms = time.time_ns() // 1_000_000
    return collect(config) | {"agent_started_epoch_ms": started_ms,
        "agent_transmit_epoch_ms": time.time_ns() // 1_000_000}


def publish_transition(config: dict) -> None:
    pushed = run_ssh(config, "asrplus-status", json.dumps(stamped_status(config)))
    if pushed.returncode != 0:
        raise RuntimeError("transition status push failed")


def execute(config: dict, command: dict) -> dict:
    if command.get("hub") != config["hub"] or str(command.get("localNode")) != str(config["node"]):
        return {"ok": False, "exitCode": 2, "message": "command identity mismatch"}
    action = command.get("action")
    remote = str(command.get("node", ""))
    if action not in {"connect", "disconnect"} or not re.fullmatch(r"[0-9]{3,10}", remote):
        return {"ok": False, "exitCode": 2, "message": "command validation failed"}
    ilink = "13" if action == "connect" and command.get("permanent") is True else "3" if action == "connect" else "11"
    result = subprocess.run(["/usr/sbin/asterisk", "-rx", f"rpt cmd {config['node']} ilink {ilink} {remote}"], capture_output=True, text=True, timeout=10, check=False)
    return {"ok": result.returncode == 0, "exitCode": result.returncode, "message": (result.stdout or result.stderr).strip()[:300]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--watch-talkers", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.watch_talkers:
        return watch_talkers(config)
    status = stamped_status(config)
    synced = run_ssh(config, "asrplus-sync", json.dumps(status))
    if synced.returncode != 0:
        pushed = run_ssh(config, "asrplus-status", json.dumps(status))
        if pushed.returncode != 0:
            raise SystemExit("status push failed")
        synced = run_ssh(config, "asrplus-poll")
    if synced.returncode != 0:
        raise SystemExit("status sync failed")
    response = json.loads(synced.stdout or "{}")
    command = response.get("command")
    if isinstance(command, dict):
        outcome = execute(config, command)
        acknowledged = run_ssh(config, f"asrplus-ack {command['id']}", json.dumps(outcome))
        if acknowledged.returncode != 0:
            raise SystemExit("command acknowledgement failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
