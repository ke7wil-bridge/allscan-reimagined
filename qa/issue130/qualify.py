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
import shlex
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET

VM = "asr-130-offline"
ROOT = Path(__file__).resolve().parents[2]
EXPECTED_BRANCH = "fix/130-reserve-net-node"
TESTED_BASE_COMMIT = "f2746a98102ced6e0fb4009a01d21559bba4646a"
HOST_TMP = Path.home() / "asr-test-tmp"
VM_DISK = Path.home() / "vms/asr-qa/asr-130-offline.qcow2"
BASELINE_SHA = "9d86578d6ac51a338e9a4cb6cbf93d1d081c49da1e5cda2f07d69c97c23e292a"
REPORT_ROOT = Path.home() / "vms/asr-qa/reports"


def command(args: list[str], timeout: int = 30, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive_tree(archive: Path, tree: Path) -> None:
    """Verify actual prepared release files, not an unrelated archive sidecar."""
    expected = set()
    with tarfile.open(archive, "r:gz") as payload:
        for item in payload:
            parts = Path(item.name).parts
            if not parts or Path(item.name).is_absolute() or ".." in parts:
                raise ValueError("unsafe release archive path")
            if len(parts) == 1 and item.isdir():
                continue
            if len(parts) < 2 or not (item.isdir() or item.isfile()):
                raise ValueError("unexpected release archive entry")
            relative = Path(*parts[1:])
            target = tree / relative
            if any(parent.is_symlink() for parent in (target, *target.parents) if parent != tree.parent):
                raise ValueError("symlink in prepared release tree")
            if item.isdir():
                if not target.is_dir():
                    raise ValueError("missing release directory")
                continue
            if relative in expected or not target.is_file():
                raise ValueError("duplicate or missing release file")
            expected.add(relative)
            with payload.extractfile(item) as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if sha256(target) != digest:
                raise ValueError(f"prepared release file differs: {relative}")
    if not expected:
        raise ValueError("empty release archive")
    actual = {p.relative_to(tree) for p in tree.rglob("*") if not p.is_dir() or p.is_symlink()}
    if actual != expected:
        raise ValueError("unexpected file in prepared release tree")


def safe_definition(xml: str, disk: Path, media: Path) -> bool:
    root = ET.fromstring(xml)
    devices = root.find("devices")
    if devices is None or any(devices.findall(tag) for tag in ("interface", "hostdev", "filesystem", "redirdev", "shmem", "smartcard")):
        return False
    disks = devices.findall("disk")
    if len(disks) != 2:
        return False
    found = {}
    for item in disks:
        source, target = item.find("source"), item.find("target")
        if item.get("type") != "file" or source is None or target is None or not source.get("file"):
            return False
        found[target.get("dev")] = (Path(source.get("file")), item.find("readonly") is not None)
    return found == {"vda": (disk, False), "vdb": (media, True)}


class Qualification:
    def __init__(self, report: Path, run_native: bool, candidate_sha: str | None = None):
        self.report_path = report
        self.evidence = report.with_suffix("").with_name(report.stem + "-evidence")
        self.run_native = run_native
        self.candidate_sha = candidate_sha
        self.started_vm = False
        self.media: Path | None = None
        self.artifact_digests: dict[str, str] = {}
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
        ancestor = command(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", TESTED_BASE_COMMIT, "HEAD"])
        good = (branch.stdout.strip() == EXPECTED_BRANCH and ancestor.returncode == 0
                and clean.returncode == 0 and not clean.stdout.strip())
        self.add("worktree", "PASS" if good else "FAIL",
                 f"branch={branch.stdout.strip()} commit={commit.stdout.strip()} "
                 f"verifiedBaseAncestor={ancestor.returncode == 0} dirty={bool(clean.stdout.strip())}")

        if not self.run_native:
            return good
        try:
            staging = Path((HOST_TMP / "130-native-install-path").read_text().strip()).resolve()
            if HOST_TMP.resolve() not in staging.parents or not staging.name.startswith("130-native-install."):
                raise ValueError("unsafe QA staging directory")
            self.media = staging / "asr130-install.iso"
            if not self.media.is_file() or self.media.is_symlink():
                raise ValueError("read-only QA media missing")
            for label, expected in (("baseline", BASELINE_SHA), ("candidate", self.candidate_sha)):
                archive = staging / "iso" / f"{label}.tar.gz"
                actual = sha256(archive)
                ok = expected is not None and actual == expected
                self.artifact_digests[label] = actual
                self.add(f"artifact-{label}", "PASS" if ok else "FAIL", f"sha256={actual}; trusted digest {'matched' if ok else 'missing or mismatched'}")
                good &= ok
        except (OSError, ValueError) as exc:
            self.add("artifact-preflight", "FAIL", str(exc)); return False
        return good

    def isolation_preflight(self) -> bool:
        if not self.run_native:
            return True
        xml_result = command(["virsh", "dumpxml", VM, "--inactive"])
        state = command(["virsh", "domstate", VM])
        safe = (xml_result.returncode == 0 and state.returncode == 0
                and state.stdout.strip() == "shut off" and self.media is not None
                and safe_definition(xml_result.stdout, VM_DISK, self.media)
                and VM_DISK.is_file() and not VM_DISK.is_symlink())
        self.add("vm-isolation-definition", "PASS" if safe else "FAIL",
                 "dedicated stopped VM, exact disks, read-only media, no external devices" if safe else "VM definition or state not safe; no mutation authorized")
        return safe

    @staticmethod
    def agent(payload: dict, timeout: int = 15) -> dict:
        result = command(["virsh", "qemu-agent-command", VM, json.dumps(payload), "--timeout", str(timeout)], timeout + 5)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "guest-agent command failed")
        return json.loads(result.stdout)["return"]

    def guest(self, shell: str, timeout: int = 180) -> str:
        started = self.agent({"execute": "guest-exec", "arguments": {
            "path": "/bin/bash", "arg": ["-c", "set -euo pipefail\n" + shell], "capture-output": True}}, 15)
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
        self.guest("python3 -c " + shlex.quote(script), 60)

    def start_vm(self) -> None:
        state = command(["virsh", "domstate", VM]).stdout.strip()
        if state != "running":
            if state != "shut off":
                raise RuntimeError("unexpected VM state: " + state)
            result = command(["virsh", "start", VM], 30)
            if result.returncode:
                raise RuntimeError(result.stderr.strip())
            self.started_vm = True
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
            if links.splitlines() != ["lo"]:
                raise RuntimeError("guest has a non-loopback interface")
            installed = self.guest("test ! -e /opt/allscan-reimagined/current && echo clean || echo installed").strip()
            if installed != "clean":
                self.add("native-clean-start", "BLOCKED", "dedicated guest is already installed; restore its prepared clean snapshot")
                return
            # Check actual mounted archives and the extracted trees before any install.
            self.copy_to_guest(Path(__file__), "/root/asr130qa/qualify.py")
            self.guest("mkdir -p /mnt/asr130qa; mountpoint -q /mnt/asr130qa || mount -o ro /dev/vdb /mnt/asr130qa")
            for label, digest in self.artifact_digests.items():
                script = ("import sys;sys.path.insert(0,'/root/asr130qa');from qualify import sha256,verify_archive_tree;from pathlib import Path;"
                          f"a=Path('/mnt/asr130qa/{label}.tar.gz');assert sha256(a)=={digest!r};"
                          f"verify_archive_tree(a,Path('/root/asr130qa/{label}'))")
                self.guest("python3 -c " + shlex.quote(script), 120)
            self.guest("php -r 'exit(PHP_VERSION_ID >= 80235 ? 0 : 1);'; python3 -c 'import socketio,websocket'; "
                       "systemctl stop systemd-networkd-wait-online.service; systemctl start apache2 asterisk; "
                       "test -f /var/www/html/allscan/include/common.php")
            self.copy_to_guest(ROOT / "qa/issue130/run-native-install.sh", "/root/asr130qa/run-native-install.sh")
            self.copy_to_guest(ROOT / "qa/issue130/fixtures/native-config.json", "/root/asr130qa/native-config.json")
            self.copy_to_guest(ROOT / "qa/issue130/qualify-authenticated-api.sh", "/root/asr130qa/qualify-authenticated-api.sh")
            self.guest("chmod 700 /root/asr130qa/run-native-install.sh /root/asr130qa/qualify-authenticated-api.sh; "
                       "install -d -o www-data -g www-data -m 775 /etc/allscan; "
                       "curl -fsS http://127.0.0.1/allscan/ >/dev/null; test -s /etc/allscan/allscan.db")
            self.guest("/root/asr130qa/run-native-install.sh /root/asr130qa/baseline /root/asr130qa/results/baseline", 240)
            self.guest("install -o root -g root -m 644 /root/asr130qa/native-config.json /etc/allscan-reimagined/config.json; "
                       "sha256sum /etc/allscan-reimagined/config.json >/root/asr130qa/results/config-before.sha256; "
                       "/root/asr130qa/run-native-install.sh /root/asr130qa/candidate /root/asr130qa/results/candidate; "
                       "sha256sum -c /root/asr130qa/results/config-before.sha256", 240)
            self.guest("systemctl start asterisk; "
                       "asterisk -rx 'rpt cmd 1800 ilink 3 1801'; asterisk -rx 'rpt cmd 1800 ilink 3 1802'; "
                       "asterisk -rx 'rpt cmd 1800 ilink 3 1999'; sleep 3; "
                       "/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --manual-link disconnect qa_ysf; "
                       "asterisk -rx 'rpt cmd 1800 ilink 11 1802'; sleep 2; "
                       "/usr/local/sbin/allscan-reimagined-fixed-bridge-recovery --once; sleep 3; "
                       "asterisk -rx 'rpt lstats 1800' | awk '$1==1802 && $NF==\"ESTABLISHED\" {ok=1} END {exit !ok}'; "
                       "asterisk -rx 'rpt lstats 1800' | awk '$1==1801 {bad=1} END {exit bad}'; "
                       "asterisk -rx 'rpt lstats 1800' | awk '$1==1999 && $NF==\"ESTABLISHED\" {ok=1} END {exit !ok}'; "
                       "/usr/local/sbin/allscan-reimagined-reapply >/root/asr130qa/results/reapply.log 2>&1; "
                       "/root/asr130qa/run-native-install.sh /root/asr130qa/candidate /root/asr130qa/results/held-upgrade; "
                       "/root/asr130qa/qualify-authenticated-api.sh", 300)
            before = self.guest("cat /proc/sys/kernel/random/boot_id").strip()
            try:
                self.guest("systemctl reboot", 15)
            except Exception:
                pass
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    after = self.guest("cat /proc/sys/kernel/random/boot_id", 10).strip()
                    if before != after and after:
                        break
                except (RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("boot ID did not change")
            # With deliberately no NIC, release network-online's queued jobs without adding connectivity.
            self.guest("systemctl stop systemd-networkd-wait-online.service 2>/dev/null || true")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                nodes = self.guest("asterisk -rx 'rpt lstats 1800' 2>/dev/null || true")
                if any(line.split() and line.split()[0] == "1802" and line.split()[-1] == "ESTABLISHED" for line in nodes.splitlines()):
                    break
                time.sleep(3)
            else:
                raise RuntimeError("automatic recovery did not restore 1802 after reboot")
            final = self.guest("asterisk -rx 'rpt lstats 1800' | awk '$1==1801 {bad=1} END {exit bad}'; python3 - <<'PY'\nimport json\np=json.load(open('/etc/allscan-reimagined/config.json'))\n"
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
        self.add("privileged-docker-broker", "BLOCKED", "requires a separately authorized disposable broker host; this runner does not use host sudo")

    def cleanup(self) -> None:
        if not self.started_vm:
            self.add("cleanup", "PASS", "no VM started by this runner; no VM state changed")
            return
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
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp); tree = p / "tree"; tree.mkdir(); (tree / "example").write_text("verified")
        archive = p / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as out:
            out.add(tree, arcname="release")
        verify_archive_tree(archive, tree)
        (tree / "example").write_text("changed")
        try:
            verify_archive_tree(archive, tree)
        except ValueError:
            pass
        else:
            raise AssertionError("wrong extracted file accepted")
        disk = p / "disk"; media = p / "media"
        xml = f"<domain><devices><disk type='file'><source file='{disk}'/><target dev='vda'/></disk><disk type='file'><source file='{media}'/><target dev='vdb'/><readonly/></disk></devices></domain>"
        assert safe_definition(xml, disk, media)
        assert not safe_definition(xml.replace("<readonly/>", ""), disk, media)
        assert not safe_definition(xml.replace("</devices>", "<interface/></devices>"), disk, media)
        assert not safe_definition(xml.replace("</devices>", "<hostdev/></devices>"), disk, media)
        qa = Qualification(p / "report.json", False)
        qa.cleanup()  # must not invoke virsh on a preflight-only run
        assert qa.checks[-1]["status"] == "PASS"
    print("Issue 130 qualification runner self-test: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT_ROOT / "issue130-qualification.json")
    parser.add_argument("--run-native", action="store_true")
    parser.add_argument("--candidate-sha256", help="trusted digest of exact CI candidate on attached QA media")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.run_native and (not args.candidate_sha256 or len(args.candidate_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.candidate_sha256)):
        parser.error("--run-native requires the trusted --candidate-sha256 digest")
    qa = Qualification(args.report, args.run_native, args.candidate_sha256)
    try:
        ready = qa.host_preflight() and qa.isolation_preflight()
        if ready:
            qa.native()
        else:
            qa.add("native-end-to-end", "BLOCKED", "host or isolation preflight failed")
        qa.broker()
    except Exception as exc:
        qa.add("qualification", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            qa.cleanup()
        except Exception as exc:
            qa.add("cleanup", "FAIL", f"{type(exc).__name__}: {exc}")
    return qa.write()


if __name__ == "__main__":
    raise SystemExit(main())
