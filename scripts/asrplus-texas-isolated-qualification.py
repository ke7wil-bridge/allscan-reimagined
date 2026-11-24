#!/usr/bin/env python3
"""Disposable Texas 674984 security, capacity, recovery and soak qualification."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import asrplus_command_integrity as integrity  # noqa: E402


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent = load("texas_qualification_agent", "asrplus-agent.py")
endpoint = load("texas_qualification_endpoint", "asrplus-remote-endpoint.py")
control = load("texas_qualification_control", "asrplus-control.py")
installer = load("texas_qualification_installer", "asrplus-texas-install.py")
local_control = load("texas_qualification_local_control", "asrplus-texas-control.py")
dashboard = load("texas_qualification_dashboard", "asrplus-status.py")


class Results:
    def __init__(self) -> None:
        self.tests: list[dict] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            raise AssertionError(f"{name}: {detail or 'failed'}")
        self.tests.append({"name": name, "result": "PASS", "detail": detail})


class AdmissionModel:
    """Model the committed GROUP(rptin) dialplan without opening channels."""

    def __init__(self, limit: int = 60) -> None:
        self.limit = limit
        self.inbound: set[str] = set()
        self.lock = threading.Lock()

    def admit(self, channel: str, *, peer_ip: str, direction: str = "inbound") -> bool:
        if peer_ip == "127.0.0.1" or direction != "inbound":
            return True
        with self.lock:
            if channel in self.inbound:
                return True
            if len(self.inbound) >= self.limit:
                return False
            self.inbound.add(channel)
            return True

    def disconnect(self, channel: str) -> None:
        with self.lock:
            self.inbound.discard(channel)


def key(root: Path, name: str, byte: int) -> tuple[Path, bytes]:
    raw = bytes([byte]) * 32
    path = root / name
    path.write_text(raw.hex() + "\n")
    path.chmod(0o600)
    return path, raw


def signed_command(raw_key: bytes, identifier: str = "a" * 32, now: int | None = None) -> dict:
    return integrity.sign_command({
        "id": identifier, "utc": "2026-11-24T00:00:00Z", "hub": "texas",
        "localNode": "674984", "action": "connect", "node": "12345",
        "permanent": False, "actor": "fixture", "kind": "", "value": "",
        "duration": "", "reason": "",
    }, raw_key, "texas-fixture-1", now=now, ttl=120)


def status(now: int, sequence: int, rows: int = 200) -> dict:
    return {"hub": "texas", "node": "674984", "generated_epoch": now,
            "talker_sequence": sequence, "watcher_instance": "texas-fixture",
            "connections": [{"node": str(10000 + index), "label": f"fixture-{index}",
                             "direction": "inbound", "mode": "T", "keyed": False}
                            for index in range(rows)],
            "bridges": {}, "current_talker": {}, "last_talkers": []}


def meminfo() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            name, value = line.split(":", 1)
            result[name] = int(value.strip().split()[0])
    except OSError:
        pass
    return result


def process_status() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            fields = value.strip().split()
            if fields and fields[0].isdigit():
                result[name] = int(fields[0])
    except OSError:
        pass
    return result


def capacity_tests(out: Results) -> None:
    dialplan = (ROOT / "ops/asrplus/texas/radio-secure-capacity.conf").read_text()
    out.check("capacity-config-60", "> 60" in dialplan and "GROUP_COUNT(${EXTEN}@rptin)" in dialplan)
    model = AdmissionModel()
    out.check("admit-1-through-60", all(model.admit(f"IAX2/{n}", peer_ip="192.0.2.1") for n in range(60)))
    out.check("reject-61", not model.admit("IAX2/61", peer_ip="192.0.2.1"))
    model.disconnect("IAX2/10")
    out.check("readmit-after-disconnect", model.admit("IAX2/61", peer_ip="192.0.2.1"))
    out.check("local-bridge-excluded", model.admit("IAX2/local", peer_ip="127.0.0.1") and len(model.inbound) == 60)
    out.check("outbound-interhub-excluded", model.admit("IAX2/out", peer_ip="198.51.100.2", direction="outbound") and len(model.inbound) == 60)
    simultaneous = AdmissionModel()
    with ThreadPoolExecutor(max_workers=64) as pool:
        accepted = list(pool.map(lambda n: simultaneous.admit(f"IAX2/{n}", peer_ip="192.0.2.2"), range(100)))
    out.check("simultaneous-admission-atomic", sum(accepted) == 60 and len(simultaneous.inbound) == 60)


def endpoint_boundaries(out: Results) -> None:
    now = int(time.time())
    cleaned = endpoint.sanitize_status("texas", status(now, 1, 220))
    out.check("telemetry-200-row-cap", len(cleaned["connection_status"]["nodes"]) == 200)
    for skew in (-120, 120):
        endpoint.sanitize_status("texas", status(now + skew, 2, 0))
    rejected = 0
    for skew in (-121, 121):
        try:
            endpoint.sanitize_status("texas", status(now + skew, 2, 0))
        except ValueError:
            rejected += 1
    out.check("clock-skew-boundary", rejected == 2)
    exact = json.dumps({"pad": "x" * (4 * 1024 * 1024 - 11)}).encode()
    exact = exact + b" " * (4 * 1024 * 1024 - len(exact))
    with patch.object(endpoint.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(exact))):
        endpoint.read_input()
    with patch.object(endpoint.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"x" * (4 * 1024 * 1024 + 1)))):
        try:
            endpoint.read_input()
            rejected_large = False
        except ValueError as error:
            rejected_large = "too large" in str(error)
    out.check("endpoint-4mib-boundary", rejected_large)


def security_and_recovery(out: Results) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        key_path, texas_key = key(root, "texas.key", 17)
        _, other_key = key(root, "other.key", 23)
        config_path = root / "integrity.json"
        config_path.write_text(json.dumps({"hubs": {"texas": {
            "mode": "required", "keyId": "texas-fixture-1", "keyFile": str(key_path)}}}))
        endpoint.QUEUE_ROOT = root / "queue"
        endpoint.RESULT_ROOT = root / "results"
        endpoint.ARCHIVE_ROOT = root / "archive"
        endpoint.STATUS_ROOT = root / "status"
        endpoint.STATUS_EVENT_PATH = root / "status-event"
        endpoint.SECURITY_AUDIT_PATH = root / "audit.jsonl"
        endpoint.INTEGRITY_CONFIG = config_path
        command = signed_command(texas_key)
        for candidate in (command | {"node": "99999"}, command | {"hub": "phoenix"},
                          signed_command(other_key)):
            try:
                integrity.verify_command(candidate, texas_key, "texas-fixture-1", "texas", "674984")
                raise AssertionError("tampered/cross-key command accepted")
            except integrity.IntegrityError:
                pass
        out.check("tamper-and-cross-key-rejected", True)
        queue = endpoint.QUEUE_ROOT / "texas"
        queue.mkdir(parents=True)
        endpoint.atomic_json(queue / f"{command['id']}.json", command, 0o600)
        leased = endpoint.poll("texas")["command"]
        ack = integrity.sign_acknowledgment({"ok": True, "exitCode": 0, "message": "fixture"},
                                            leased, texas_key, "texas-fixture-1")
        endpoint.acknowledge("texas", leased["id"], ack)
        duplicate = endpoint.acknowledge("texas", leased["id"], ack)
        out.check("signed-command-ack-duplicate", duplicate.get("duplicate") is True)

        ledger = root / "ledger.json"
        agent_config = {"hub": "texas", "node": "674984", "commandLedger": str(ledger),
                        "commandIntegrity": {"mode": "required", "keyId": "texas-fixture-1",
                                             "keyFile": str(key_path)},
                        "controlHelper": ["/fixture/control"]}
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        ledger_command = signed_command(texas_key, "b" * 32)
        with patch.object(agent.subprocess, "run", return_value=completed) as run_mock:
            first = agent.execute_once(agent_config, ledger_command)
            second = agent.execute_once(agent_config, ledger_command)
        out.check("ledger-exactly-once", first == second and run_mock.call_count == 1)
        inflight = json.loads(ledger.read_text())
        interrupted = signed_command(texas_key, "c" * 32)
        inflight["commands"][interrupted["id"]] = {"digest": interrupted["commandDigest"],
            "state": "executing", "updatedEpoch": int(time.time())}
        agent.atomic_json(ledger, inflight, 0o600)
        with patch.object(agent.subprocess, "run") as run_mock:
            uncertain = agent.execute_once(agent_config, interrupted)
        out.check("restart-interruption-no-reexecute", run_mock.call_count == 0 and uncertain["exitCode"] == 75)

        failure = subprocess.CompletedProcess([], 255, "", "unreachable")
        with patch.object(agent, "stamped_status", return_value=status(int(time.time()), 1, 0)), \
                patch.object(agent, "run_ssh", return_value=failure), \
                patch.object(agent, "execute_once") as execute_mock:
            try:
                agent.sync_once({"hub": "texas", "node": "674984", "controlPolling": True})
            except SystemExit:
                pass
        out.check("network-failures-no-execution", execute_mock.call_count == 0)

        before = root / "atomic.json"
        before.write_text('{"old":true}\n')
        with patch.object(agent.os, "replace", side_effect=OSError("disk full")):
            try:
                agent.atomic_json(before, {"new": True})
            except OSError:
                pass
        out.check("atomic-write-failure-preserves-old", json.loads(before.read_text()) == {"old": True})

        # 80 is the operational ceiling; 100 is the implementation hard stop.
        control.QUEUE_ROOT = root / "control-queue"
        control.AUDIT_PATH = root / "control-audit.jsonl"
        control.INTEGRITY_CONFIG = config_path
        q = control.QUEUE_ROOT / "texas"
        q.mkdir(parents=True)
        for index in range(80):
            (q / f"{index:032x}.json").write_text("{}")
        request = {"hub": "texas", "action": "connect", "node": "12345", "actor": "fixture"}
        output = io.StringIO()
        with patch.object(control.sys, "stdin", io.StringIO(json.dumps(request))), patch.object(control.sys, "stdout", output):
            accepted_at_80 = control.main() == 0
        for index in range(81, 100):
            (q / f"{index:032x}.json").write_text("{}")
        output = io.StringIO()
        with patch.object(control.sys, "stdin", io.StringIO(json.dumps(request))), patch.object(control.sys, "stdout", output):
            rejected_at_100 = control.main() == 1
        next(q.glob("*.json")).unlink()
        output = io.StringIO()
        with patch.object(control.sys, "stdin", io.StringIO(json.dumps(request))), patch.object(control.sys, "stdout", output):
            recovered_below_100 = control.main() == 0
        out.check("queue-80-operational-100-hard-recovery",
                  accepted_at_80 and rejected_at_100 and recovered_below_100)

        # Telemetry-only must never poll or execute.
        ok = subprocess.CompletedProcess([], 0, '{"ok":true}', "")
        with patch.object(agent, "stamped_status", return_value=status(int(time.time()), 1, 0)), \
                patch.object(agent, "run_ssh", return_value=ok) as ssh_mock, \
                patch.object(agent, "execute_once") as execute_mock:
            agent.sync_once({"hub": "texas", "node": "674984", "controlPolling": False})
        out.check("telemetry-only-no-control-poll", ssh_mock.call_args.args[1] == "asrplus-status" and execute_mock.call_count == 0)

        stale_path = root / "stale-status.json"
        stale_path.write_text(json.dumps({"generated_epoch": int(time.time()) - 181,
            "connection_status": {"nodes": []}}))
        stale = dashboard.build_hub({"id": "texas-hub", "node": "674984",
            "capacity": 60, "statusFile": str(stale_path), "staleAfterSeconds": 180},
            {}, {}, {}, int(time.time()))
        out.check("outage-dashboard-stale", stale["stale"] is True and stale["available"] is False
                  and stale["capacity"] == 60)


def install_tests(out: Results) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "etc/asterisk").mkdir(parents=True)
        (root / "etc/os-release").write_text("ID=debian\nVERSION_ID=12\n")
        stock = "[default]\nexten => i,1,Hangup\n\n[radio-secure]\nexten => _XXXX!,1,NoOp(test)\n same => n(connect),NoOp(connect)\n same => n,Rpt(${EXTEN})\n same => n,Hangup\n"
        extensions = root / "etc/asterisk/extensions.conf"
        extensions.write_text(stock)
        values = {"kentuckyAddress": "192.0.2.10", "texasSourceIp": "192.0.2.20",
                  "texasPublicKey": "ssh-ed25519 RklYVFVSRUtFWQ== fixture", "keyId": "texas-fixture-1"}
        first = installer.install(root, values)
        out.check("installer-verify", installer.verify(root)["ok"])
        emergency = subprocess.run([sys.executable, str(SCRIPTS / "asrplus-texas-install.py"),
            "emergency-isolate", "--root", str(root), "--apply"],
            capture_output=True, text=True, check=False)
        out.check("installer-emergency-isolation", emergency.returncode == 0
                  and json.loads((root / "etc/asrplus-agent.json").read_text())["controlPolling"] is False)
        tampered = root / "var/backups/asrplus-texas/tampered"
        shutil.copytree(Path(first["backup"]), tampered)
        backup_file = next((tampered / "files").rglob("*"))
        while backup_file.is_dir():
            backup_file = next(backup_file.rglob("*"))
        backup_file.write_bytes(backup_file.read_bytes() + b"tamper")
        try:
            installer.rollback(root, tampered)
            integrity_rejected = False
        except installer.InstallError:
            integrity_rejected = True
        out.check("installer-backup-integrity", integrity_rejected)
        second = installer.install(root, values)
        out.check("installer-idempotent", installer.verify(root)["ok"] and extensions.read_text().count(installer.MANAGED) == 1)
        installer.rollback(root, Path(second["backup"]))
        out.check("installer-repeat-rollback", installer.verify(root)["ok"])
        installer.rollback(root, Path(first["backup"]))
        out.check("installer-full-rollback", extensions.read_text() == stock)

        os.environ["ASRPLUS_TEST_FAIL_AFTER"] = "2"
        try:
            try:
                installer.install(root, values)
            except installer.InstallError:
                pass
        finally:
            os.environ.pop("ASRPLUS_TEST_FAIL_AFTER", None)
        out.check("installer-partial-failure-rollback", extensions.read_text() == stock)


def least_privilege_tests(out: Results) -> None:
    unit = (ROOT / "ops/asrplus/texas/asrplus-texas-agent.service").read_text()
    watcher = (ROOT / "ops/asrplus/texas/asrplus-texas-talker-watcher.service").read_text()
    authorized = (ROOT / "ops/asrplus/texas/authorized_keys.template").read_text()
    config = json.loads((ROOT / "ops/asrplus/texas/asrplus-agent.json").read_text())
    out.check("dedicated-service-account", "User=asrplus-agent" in unit
              and "CapabilityBoundingSet=" in unit and "RuntimeDirectory=asrplus" in unit)
    out.check("dedicated-talker-watcher", "User=asrplus-agent" in watcher and "--watch-talkers" in watcher)
    out.check("systemd-filesystem-sandbox", "ProtectSystem=strict" in unit and "ReadWritePaths=" in unit)
    out.check("forced-source-restricted-ssh", "restrict,from=" in authorized and 'command="/usr/local/sbin/asrplus-remote-endpoint texas"' in authorized)
    out.check("ssh-hmac-credentials-separated", config["key"] != config["commandIntegrity"]["keyFile"])
    with patch.object(local_control.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run_mock, \
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        valid = local_control.main(["helper", "disconnect", "674984", "12345", "temporary"])
        invalid = local_control.main(["helper", "connect", "674984", "12345;id", "temporary"])
    out.check("narrow-local-control-helper", valid == 0 and invalid == 2 and run_mock.call_count == 1)


def soak(out: Results, seconds: int, interval: float) -> dict:
    started_wall = time.time()
    started_cpu = time.process_time()
    start_process = process_status()
    latencies = []
    sequence = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        endpoint.STATUS_ROOT = root / "status"
        endpoint.STATUS_EVENT_PATH = root / "event"
        with patch.object(endpoint.subprocess, "run"):
            while True:
                loop = time.perf_counter()
                sequence += 1
                current = int(time.time())
                endpoint.store_status("texas", status(current, sequence, 200))
                stored = json.loads((endpoint.STATUS_ROOT / "texas.json").read_text())
                if stored["talker_sequence"] != sequence or current - stored["generated_epoch"] > 1:
                    raise AssertionError("telemetry freshness/sequence failure")
                latencies.append((time.perf_counter() - loop) * 1000)
                elapsed = time.time() - started_wall
                if elapsed >= seconds:
                    break
                time.sleep(min(interval, max(0.0, seconds - elapsed)))
        disk_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    elapsed = time.time() - started_wall
    cpu_seconds = time.process_time() - started_cpu
    end_process = process_status()
    metrics = {
        "requestedSeconds": seconds, "elapsedSeconds": round(elapsed, 3),
        "samples": sequence, "recordsPerSample": 200,
        "cpuSeconds": round(cpu_seconds, 3),
        "processCpuPercent": round(cpu_seconds / max(elapsed, .001) * 100, 3),
        "maxRssKiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "processSwapStartKiB": start_process.get("VmSwap", 0),
        "processSwapEndKiB": end_process.get("VmSwap", 0),
        "diskBytes": disk_bytes,
        "latencyMsP50": round(statistics.median(latencies), 3),
        "latencyMsMax": round(max(latencies), 3),
    }
    out.check("sustained-telemetry", elapsed >= seconds and sequence >= 1)
    out.check("soak-cpu-below-70", metrics["processCpuPercent"] < 70)
    out.check("soak-no-process-swap-growth",
              metrics["processSwapEndKiB"] <= metrics["processSwapStartKiB"])
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soak-seconds", type=int, default=1)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.soak_seconds < 1 or args.interval <= 0:
        raise SystemExit("invalid soak duration")
    out = Results()
    capacity_tests(out)
    endpoint_boundaries(out)
    security_and_recovery(out)
    install_tests(out)
    least_privilege_tests(out)
    metrics = soak(out, args.soak_seconds, args.interval)
    evidence = {
        "schemaVersion": 1, "result": "PASS", "generatedEpoch": int(time.time()),
        "scope": "disposable fixtures only; no production credentials, radio or Asterisk controls",
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip() or "standalone-package",
        "host": {"platform": platform.platform(), "python": platform.python_version(),
                 "cpuCount": os.cpu_count(), "totalMemoryKiB": meminfo().get("MemTotal", 0)},
        "tests": out.tests, "soak": metrics,
        "limitations": ["Synthetic fixture qualification is not Texas provider or hardware certification.",
                        "No live radio link, Asterisk channel, production credential or production control was used."],
    }
    raw = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    print(f"Texas isolated qualification: PASS ({len(out.tests)} checks, {metrics['elapsedSeconds']}s soak)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
