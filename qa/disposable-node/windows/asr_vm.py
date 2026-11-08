#!/usr/bin/env python3
"""Local Windows QEMU test VM; runtime data never belongs in the source tree."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time

NAME = "thomas-asr-qa"
SSH_PORT = 22293
WEB_PORT = 18093
QMP_PORT = 49193


def runtime_root():
    return Path.home() / "Documents" / "Codex" / "ASR-VM"


def qemu_tool(name):
    return str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "qemu" / name)


def qmp(command):
    with socket.create_connection(("127.0.0.1", QMP_PORT), timeout=5) as sock:
        with sock.makefile("rwb", buffering=0) as stream:
            greeting = json.loads(stream.readline())
            if "QMP" not in greeting:
                raise RuntimeError("Unexpected QMP endpoint")

            def execute(name):
                stream.write((json.dumps({"execute": name}) + "\n").encode())
                while True:
                    reply = json.loads(stream.readline())
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                    if "return" in reply:
                        return reply["return"]

            execute("qmp_capabilities")
            if execute("query-name").get("name") != NAME:
                raise RuntimeError("Refusing to control an unrelated QEMU instance")
            return execute(command)


def running():
    try:
        return qmp("query-status")
    except ConnectionRefusedError:
        return None


def create_seed(root, instance):
    import pycdlib
    key = (root / "ssh" / "id_ed25519.pub").read_text().strip()
    if not key.startswith("ssh-ed25519 "):
        raise RuntimeError("Dedicated VM public key is missing or invalid")
    user = """#cloud-config
hostname: thomas-asr-qa
manage_etc_hosts: true
ssh_pwauth: false
disable_root: true
ssh_deletekeys: true
users:
  - name: asrqa
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: true
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - PUBLIC_KEY
bootcmd:
  - [sh, -c, 'test -f /root/asr-qa/asl-provisioned || systemctl mask asterisk.service']
final_message: "Thomas ASR QA Debian ready; production nodes are not configured."
""".replace("PUBLIC_KEY", key)
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, vol_ident="cidata")
    for filename, data in (("user-data", user), ("meta-data", json.dumps({"instance-id": instance}))):
        content = data.encode()
        iso.add_fp(io.BytesIO(content), len(content), iso_path="/" + filename.upper().replace("-", "_") + ";1", joliet_path="/" + filename)
    target = root / "seed.iso"
    iso.write(str(target))
    iso.close()


def start_args(root, template, provision):
    disk = root / ("asr-qa-asl3-template.qcow2" if template else "asr-qa-disposable.qcow2")
    net = f"user,id=n0,restrict={'off' if provision else 'on'},hostfwd=tcp:127.0.0.1:{SSH_PORT}-:22,hostfwd=tcp:127.0.0.1:{WEB_PORT}-:80"
    return [qemu_tool("qemu-system-x86_64.exe"), "-name", NAME,
            "-machine", "q35", "-accel", "whpx,kernel-irqchip=off", "-cpu", "qemu64", "-smp", "2", "-m", "2048",
            "-drive", f"file={disk},format=qcow2,if=virtio",
            "-drive", f"file={root / 'seed.iso'},media=cdrom,readonly=on",
            "-netdev", net, "-device", "virtio-net-pci,netdev=n0",
            "-display", "none", "-monitor", "none",
            "-qmp", f"tcp:127.0.0.1:{QMP_PORT},server=on,wait=off",
            "-serial", f"file:{root / 'serial.log'}", "-no-reboot"]


def ssh_args(root):
    return ["ssh", "-p", str(SSH_PORT), "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
            "-o", f"UserKnownHostsFile={root / 'ssh/known_hosts'}",
            "-i", str(root / "ssh/id_ed25519"), "asrqa@127.0.0.1"]


def check_guest(root):
    from urllib.request import urlopen
    script = Path(__file__).with_name("verify-guest.py").read_text()
    offline = not json.loads((root / "state.json").read_text()).get("provision")
    result = subprocess.run(ssh_args(root) + ["sudo python3 -" + (" --offline" if offline else "")], input=script,
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Guest verification failed: " + result.stderr.strip())
    report = json.loads(result.stdout)
    for action in ("runtime-config", "bridge-status"):
        with urlopen(f"http://127.0.0.1:{WEB_PORT}/asr/asr-api.php?action={action}", timeout=20) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            raise RuntimeError(f"ASR {action} failed")
        if action == "runtime-config" and payload.get("node") != "1999":
            raise RuntimeError("Unexpected ASR node identity")
    with urlopen(f"http://127.0.0.1:{WEB_PORT}/asr/astapi/server.php?nodes=1999", timeout=20) as response:
        status = b''.join(response.readline() for _ in range(8))
        if b'event: nodes' not in status or b'"1999"' not in status:
            raise RuntimeError("ASR live status stream unavailable")
    report["dashboardApi"] = True
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports/latest.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "start", "stop", "status", "trust", "check", "logs", "regression", "seal", "create-overlay", "reset"])
    parser.add_argument("--template", action="store_true")
    parser.add_argument("--provision", action="store_true", help="Enable software-download NAT only during reviewed provisioning")
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    state = root / "state.json"
    base = root / "asr-qa-asl3-template.qcow2"
    overlay = root / "asr-qa-disposable.qcow2"
    if args.action == "regression":
        if not running():
            raise RuntimeError("Start the VM first")
        script = Path(__file__).with_name("regression-guest.py").read_text()
        subprocess.run(ssh_args(root) + ["python3 -"], input=script, text=True, check=True)
        return
    if args.action == "trust":
        if not running():
            raise RuntimeError("VM must be running")
        git = shutil.which("git")
        bundled = Path(git).parent.parent / "usr/bin/ssh-keyscan.exe" if git else Path("missing")
        scanner = str(bundled) if bundled.is_file() else "ssh-keyscan"
        scan = subprocess.run([scanner, "-T", "5", "-p", str(SSH_PORT), "-t", "ed25519", "127.0.0.1"], capture_output=True, text=True, check=True).stdout
        with socket.create_connection(("127.0.0.1", SSH_PORT), timeout=5):
            sockets = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True)
            pid = str(json.loads(state.read_text())["pid"])
            owned = any(len(parts) == 5 and parts[1] == f"127.0.0.1:{SSH_PORT}"
                        and parts[4] == pid
                        for parts in (line.split() for line in sockets.splitlines()))
            if not owned:
                raise RuntimeError("SSH listener is not owned by this QEMU process")
        fingerprint = subprocess.run(["ssh-keygen", "-lf", "-"], input=scan, capture_output=True, text=True, check=True).stdout.split()[1]
        console_match = fingerprint in (root / "serial.log").read_text(errors="replace")
        (root / "ssh/known_hosts").write_text(scan)
        print("Pinned guest ED25519 key from owned local QEMU listener; console match=" + str(console_match))
        return
    if args.action in ("check", "logs", "seal"):
        if not running():
            raise RuntimeError("Start the VM first")
        if args.action == "logs":
            subprocess.run(ssh_args(root) + ["sudo journalctl -u asterisk -u apache2 -n 60 --no-pager"], check=True)
            return
        if args.action == "seal":
            current = json.loads(state.read_text())
            if not current.get("template") or (root / "template-verified.json").exists():
                raise RuntimeError("Only an unsealed running template can be sealed")
        report = check_guest(root)
        print(json.dumps(report, indent=2))
        if args.action == "check":
            return
        qmp("system_powerdown")
        for _ in range(60):
            time.sleep(1)
            if not running():
                break
        else:
            raise RuntimeError("Template did not stop; not sealed")
        subprocess.run([qemu_tool("qemu-img.exe"), "check", str(base)], check=True)
        with base.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        (root / "template-verified.json").write_text(json.dumps({"sha256": digest, "checks": report}, indent=2))
        print("Template validated and sealed; powered off")
        return
    if args.action == "status":
        print(json.dumps({"vm": running(), "disk": json.loads(state.read_text()) if state.exists() else None}))
        return
    if args.action == "stop":
        if running():
            qmp("system_powerdown")
            for _ in range(60):
                time.sleep(1)
                if not running():
                    print("VM stopped")
                    return
            raise RuntimeError("Guest did not shut down; template and overlay were preserved")
        print("VM already stopped")
        return
    if running():
        raise RuntimeError("VM is running; stop it before changing disks or seed")
    if args.action == "seed":
        create_seed(root, "thomas-asr-" + str(time.time_ns()))
    elif args.action in ("create-overlay", "reset"):
        if not (root / "template-verified.json").exists():
            raise RuntimeError("Template has not passed guest validation")
        if not base.is_file() or base.is_symlink():
            raise RuntimeError("Missing standalone template")
        with base.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != json.loads((root / "template-verified.json").read_text())["sha256"]:
            raise RuntimeError("Sealed template changed; refusing reset")
        info = json.loads(subprocess.check_output([qemu_tool("qemu-img.exe"), "info", "--output=json", str(base)]))
        if info.get("format") != "qcow2" or info.get("backing-filename"):
            raise RuntimeError("Template must be a standalone QCOW2")
        if overlay.exists():
            if args.action != "reset" or not args.confirm_reset:
                raise RuntimeError("Overlay exists; reset requires --confirm-reset")
            if overlay.is_symlink():
                raise RuntimeError("Overlay cannot be a symlink")
            overlay.unlink()
        subprocess.run([qemu_tool("qemu-img.exe"), "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base), str(overlay)], check=True)
        create_seed(root, "thomas-asr-disposable-" + str(time.time_ns()))
    elif args.action == "start":
        disk = base if args.template else overlay
        if not disk.is_file() or disk.is_symlink() or not (root / "seed.iso").is_file():
            raise RuntimeError("Disk/seed missing; refusing to start")
        if args.template and (root / "template-verified.json").exists():
            raise RuntimeError("Sealed template must remain powered off")
        for port in (SSH_PORT, WEB_PORT, QMP_PORT):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        with (root / "qemu.log").open("ab") as log:
            proc = subprocess.Popen(start_args(root, args.template, args.provision), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(3)
        if proc.poll() is not None:
            raise RuntimeError("QEMU failed; inspect qemu.log")
        state.write_text(json.dumps({"pid": proc.pid, "template": args.template, "provision": args.provision}))
        status = running()
        if not status or not status.get("running"):
            raise RuntimeError("Guest is not executing; inspect qemu.log and preserve its disk")
        print(json.dumps({"pid": proc.pid, "status": status, "provision": args.provision}))


if __name__ == "__main__":
    main()
