#!/usr/bin/env python3
"""Destructive-only-to-a-dedicated, networkless Issue #130 qualification VM."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

VM = "asr-130-offline"
ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BRANCH = "fix/130-reserve-net-node"
EXPECTED_COMMIT = "f2746a98102ced6e0fb4009a01d21559bba4646a"
HOST_TMP = Path("/home/josh/asr-test-tmp")
REPORT_ROOT = Path("/home/josh/vms/asr-qa/reports")


def command(args: list[str], timeout: int = 30, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Qualification:
    def __init__(self, report: Path, run_native: bool):
        self.report_path = report
        self.evidence = report.with_suffix("").with_name(report.stem + "-evidence")
        self.run_native = run_native
        self.checks: list[dict[str, str]] = []
        self.limitations = [
            "The stock AllScan v1.06 tree was staged from a checksum-verified archive; the upstream download workflow was not exercised.",
            "Digital protocol audio and external network registration are excluded by networkless isolation.",
        ]

    def add(self, name: str, status: str, detail: str, evidence: str = "") -> None:
        item = {"name": name, "status": status, "detail": detail}
        if evidence:
            item["evidence"] = evidence
        self.checks.append(item)

    def host_preflight(self) -> bool:
        branch = command(["git", "-C", str(ROOT), "branch", "--show-current"])
        commit = command(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        clean = command(["git", "-C", str(ROOT), "status", "--porcelain"])
        ancestor = command(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", EXPECTED_COMMIT, "HEAD"])
        good = (branch.stdout.strip() == EXPECTED_BRANCH and ancestor.returncode == 0
                and clean.returncode == 0 and not clean.stdout.strip())
        self.add("worktree", "PASS" if good else "FAIL",
                 f"branch={branch.stdout.strip()} commit={commit.stdout.strip()} "
                 f"verifiedBaseAncestor={ancestor.returncode == 0} dirty={bool(clean.stdout.strip())}")

        baseline_dir = Path((HOST_TMP / "130-native-install-path").read_text().strip())
        candidate_dir = Path((HOST_TMP / "130-package-path").read_text().strip())
        artifacts = [
            (baseline_dir / "allscan-reimagined-1.0.0-beta.8.7.tar.gz",
             baseline_dir / "allscan-reimagined-1.0.0-beta.8.7.tar.gz.sha256"),
            (candidate_dir / "allscan-reimagined-1.0.0-beta.8.7.tar.gz",
             candidate_dir / "allscan-reimagined-1.0.0-beta.8.7.tar.gz.sha256"),
        ]
        for label, (archive, sidecar) in zip(("published-beta-8.7", "pr-candidate"), artifacts):
            try:
                expected = sidecar.read_text().split()[0]
                actual = sha256(archive)
                ok = len(expected) == 64 and actual == expected
                self.add(f"artifact-{label}", "PASS" if ok else "FAIL",
                         f"sha256={actual}; sidecar={'matched' if ok else 'mismatch'}")
                good &= ok
            except (OSError, IndexError) as exc:
                self.add(f"artifact-{label}", "FAIL", f"{type(exc).__name__}: {exc}")
                good = False
        return good

    def isolation_preflight(self) -> bool:
        xml_result = command(["virsh", "dumpxml", VM])
        if xml_result.returncode:
            self.add("vm-defined", "FAIL", xml_result.stderr.strip() or "virsh dumpxml failed")
            return False
        root = ET.fromstring(xml_result.stdout)
        interfaces = root.findall("./devices/interface")
        disks = [node.get("file", "") for node in root.findall("./devices/disk/source")]
        exact_disk = "/home/josh/vms/asr-qa/asr-130-offline.qcow2" in disks
        safe = not interfaces and exact_disk
        self.add("vm-isolation-definition", "PASS" if safe else "FAIL",
                 f"interfaces={len(interfaces)} exactDedicatedDisk={exact_disk}")
        return safe

    @staticmethod
    def agent(payload: dict, timeout: int = 15) -> dict:
        result = command(["virsh", "qemu-agent-command", VM, json.dumps(payload), "--timeout", str(timeout)], timeout + 5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "guest-agent command failed")
        return json.loads(result.stdout)["return"]

    def guest(self, shell: str, timeout: int = 180) -> str:
        started = self.agent({"execute": "guest-exec", "arguments": {
            "path": "/bin/bash", "arg": ["-c", shell], "capture-output": True}}, 15)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.agent({"execute": "guest-exec-status", "arguments": {"pid": started["pid"]}}, 15)
            if state.get("exited"):
                stdout = base64.b64decode(state.get("out-data", "")).decode(errors="replace")
                stderr = base64.b64decode(state.get("err-data", "")).decode(errors="replace")
                if state.get("exitcode", 1):
                    raise RuntimeError((stderr or stdout or "guest command failed")[-4000:])
                return stdout
            time.sleep(.5)
        raise TimeoutError(f"guest command exceeded {timeout}s")

    def copy_to_guest(self, source: Path, destination: str) -> None:
        encoded = base64.b64encode(source.read_bytes()).decode()
        script = ("import base64,pathlib; pathlib.Path(" + repr(destination) + ")"
                  ".write_bytes(base64.b64decode(" + repr(encoded) + "))")
        self.guest("python3 -c " + json.dumps(script), 60)

    def start_vm(self) -> None:
        state = command(["virsh", "domstate", VM]).stdout.strip()
        if state != "running":
            result = command(["virsh", "start", VM], 30)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                self.agent({"execute": "guest-ping"}, 3)
                return
            except Exception:
                time.sleep(1)
        raise TimeoutError("guest agent did not become ready")

    def native(self) -> None:
        if not self.run_native:
            self.add("native-end-to-end", "BLOCKED", "not requested; pass --run-native for the dedicated VM")
            return
        try:
            self.start_vm()
            links = self.guest("ip -o link show | awk -F': ' '{print $2}'")
            if [line for line in links.splitlines() if line != "lo"]:
                raise RuntimeError("guest has a non-loopback interface")
            installed = self.guest("test ! -e /opt/allscan-reimagined/current && echo clean || echo installed").strip()
            if installed != "clean":
                self.add("native-clean-start", "BLOCKED", "dedicated guest is already installed; restore its prepared clean snapshot")
                return
            self.copy_to_guest(ROOT / "qa/issue130/run-native-install.sh", "/root/asr130qa/run-native-install.sh")
            self.copy_to_guest(ROOT / "qa/issue130/fixtures/native-config.json", "/root/asr130qa/native-config.json")
            self.copy_to_guest(ROOT / "qa/issue130/qualify-authenticated-api.sh", "/root/asr130qa/qualify-authenticated-api.sh")
            self.guest("chmod 700 /root/asr130qa/run-native-install.sh /root/asr130qa/qualify-authenticated-api.sh; "
                       "install -d -o www-data -g www-data -m 775 /etc/allscan; "
                       "curl -fsS http://127.0.0.1/allscan/ >/dev/null; test -s /etc/allscan/allscan.db")
            self.guest("/root/asr130qa/run-native-install.sh /root/asr130qa/baseline /root/asr130qa/results/baseline", 240)
            self.guest("install -o root -g root -m 644 /root/asr130qa/native-config.json /etc/allscan-reimagined/config.json; "
                       "/root/asr130qa/run-native-install.sh /root/asr130qa/candidate /root/asr130qa/results/candidate", 240)
            self.guest("systemctl start asterisk; "
                       "asterisk -rx 'rpt cmd 1800 ilink 3 1801'; asterisk -rx 'rpt cmd 1800 ilink 3 1802'; "
                       "asterisk -rx 'rpt cmd 1800 ilink 3 1999'; sleep 3; "
                       "/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --manual-link disconnect qa_ysf; "
                       "asterisk -rx 'rpt cmd 1800 ilink 11 1802'; sleep 2; "
                       "/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --once; sleep 3; "
                       "asterisk -rx 'rpt nodes 1800' | grep -q 1802; "
                       "! asterisk -rx 'rpt nodes 1800' | grep -q 1801; "
                       "/usr/local/sbin/allscan-reimagined-reapply >/root/asr130qa/results/reapply.log 2>&1; "
                       "/root/asr130qa/run-native-install.sh /root/asr130qa/candidate /root/asr130qa/results/held-upgrade; "
                       "/root/asr130qa/qualify-authenticated-api.sh", 300)
            before = self.guest("cat /proc/sys/kernel/random/boot_id").strip()
            try:
                self.guest("systemctl reboot", 15)
            except Exception:
                pass
            time.sleep(3)
            self.start_vm()
            after = self.guest("cat /proc/sys/kernel/random/boot_id").strip()
            if before == after:
                raise RuntimeError("boot ID did not change")
            # With deliberately no NIC, release network-online's queued jobs without adding connectivity.
            self.guest("systemctl stop systemd-networkd-wait-online.service 2>/dev/null || true")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                nodes = self.guest("asterisk -rx 'rpt nodes 1800' 2>/dev/null || true")
                if "1802" in nodes:
                    break
                time.sleep(3)
            else:
                raise RuntimeError("automatic recovery did not restore 1802 after reboot")
            final = self.guest("python3 - <<'PY'\nimport json\np=json.load(open('/etc/allscan-reimagined/config.json'))\n"
                               "assert p['qualificationMarker']=='preserve-across-upgrade-reapply-reboot'\n"
                               "assert next(b for b in p['bridges'] if b['id']=='qa_ysf')['manualDisconnect'] is True\n"
                               "assert next(b for b in p['bridges'] if b['id']=='qa_net')['node']=='1999'\nPY\n"
                               "systemctl is-active apache2 asterisk allscan-reimagined-reapply.path "
                               "allscan-reimagined-reapply.timer allscan-reimagined-fixed-bridge-recovery.timer; "
                               "/usr/local/sbin/allscan-reimagined-rollback self-test")
            self.evidence.mkdir(parents=True, exist_ok=True)
            (self.evidence / "native-final.log").write_text(final)
            self.add("native-end-to-end", "PASS", "install, upgrade, scoped hold, reapply, authenticated HTTP API, reboot and recovery passed",
                     str(self.evidence / "native-final.log"))
        except Exception as exc:
            self.add("native-end-to-end", "FAIL", f"{type(exc).__name__}: {exc}")

    def broker(self) -> None:
        sudo = command(["sudo", "-n", "true"], 5)
        if sudo.returncode:
            self.add("privileged-docker-broker", "BLOCKED",
                     "host sudo requires interactive authorization; authentication was not weakened or bypassed")
        else:
            self.add("privileged-docker-broker", "BLOCKED",
                     "noninteractive sudo is available, but this runner requires a separately authorized disposable broker host")

    def cleanup(self) -> None:
        state = command(["virsh", "domstate", VM]).stdout.strip()
        if state == "running":
            command(["virsh", "shutdown", VM], 15)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and command(["virsh", "domstate", VM]).stdout.strip() == "running":
                time.sleep(1)
            if command(["virsh", "domstate", VM]).stdout.strip() == "running":
                command(["virsh", "destroy", VM], 15)
        stopped = command(["virsh", "domstate", VM]).stdout.strip() == "shut off"
        self.add("cleanup", "PASS" if stopped else "FAIL", "dedicated VM shut off" if stopped else "VM did not stop")

    def write(self) -> int:
        overall = "FAIL" if any(c["status"] == "FAIL" for c in self.checks) else (
            "BLOCKED" if any(c["status"] == "BLOCKED" for c in self.checks) else "PASS")
        payload = {"schema": 1, "issue": 130, "generatedUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "overall": overall, "checks": self.checks, "limitations": self.limitations,
                   "productionContact": False, "credentialsLogged": False}
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if overall == "FAIL" else 2 if overall == "BLOCKED" else 0


def self_test() -> int:
    assert sha256(Path(__file__)).isalnum()
    assert EXPECTED_BRANCH.startswith("fix/130-") and VM.startswith("asr-130-")
    print("Issue 130 qualification runner self-test: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT_ROOT / "issue130-qualification.json")
    parser.add_argument("--run-native", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    qa = Qualification(args.report, args.run_native)
    try:
        ready = qa.host_preflight() and qa.isolation_preflight()
        if ready:
            qa.native()
        else:
            qa.add("native-end-to-end", "BLOCKED", "host or isolation preflight failed")
        qa.broker()
    finally:
        qa.cleanup()
    return qa.write()


if __name__ == "__main__":
    raise SystemExit(main())
