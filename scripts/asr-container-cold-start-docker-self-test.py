#!/usr/bin/env python3
"""Opt-in, isolated Docker smoke test for late host broker startup.

Uses the actual layered entrypoint and current reapply script with a local
release image, no external networking, no real host sockets or bridge state.
The socket peer is a read-only protocol fixture, not a privileged broker.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "allscan-reimagined:1.0.0-beta.8.7.1"
RUNTIME = ("bridge-control", "ysf-bridge-control", "p25-bridge-control",
           "nxdn-bridge-control", "m17", "host")


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed: {result.stderr[-1000:]}")
    return result


def main():
    if os.geteuid() == 0:
        raise SystemExit("Run as the Docker-authorized development user, not root")
    docker("image", "inspect", BASE)
    name = f"asr316-cold-{os.getpid()}"
    volumes = [f"{name}-{key}" for key in ("web", "allscan", "config", "state")]
    with tempfile.TemporaryDirectory(prefix="asr316-cold-") as tmp:
        root = Path(tmp)
        db = root / "node-db"
        db.mkdir()
        (db / "astdb.txt").write_text("".join(f"{n}|N0CALL|Isolated test node|QA\n" for n in range(100001, 100061)))
        # Fresh Docker-created host directories are often 0755, not the
        # eventual asr-bridge ownership/2750 expected by the host installer.
        before = {}
        for mode in RUNTIME:
            path = root / mode
            path.mkdir(mode=0o755)
            (path / "sentinel").write_text(mode)
            info = path.stat()
            before[mode] = (info.st_uid, info.st_gid, info.st_mode)
        script = root / "layered-reapply.sh"
        script.write_bytes((ROOT / "docker/layered-reapply.sh").read_bytes())
        script.chmod(0o755)
        seed = root / "config.seed.json"
        config = json.loads((ROOT / "personalization/config.seed.json").read_text())
        config.update(node="100001", callsign="N0CALL", bridges=[], requireLogin=True)
        seed.write_text(json.dumps(config))
        seed.chmod(0o644)
        args = ["run", "-d", "--name", name, "--network", "none",
                "-e", "ASR_UPDATE_MODE=container",
                "-e", "ASR_NODE_NUMBER=100001", "-e", "ASR_CALLSIGN=N0CALL",
                "-e", "ASR_AMI_HOST=127.0.0.1", "-e", "ASR_AMI_PASS=offline-qa-only",
                "-e", "ASR_REQUIRE_LOGIN=1",
                "-v", f"{db}:/var/lib/asterisk:ro",
                "-v", f"{script}:/usr/local/sbin/allscan-reimagined-docker-reapply:ro",
                "-v", f"{seed}:/opt/asr-source/personalization/config.seed.json:ro"]
        for mode in RUNTIME:
            args += ["-v", f"{root / mode}:/run/allscan-reimagined-{mode}:ro"]
        for volume, dest in zip(volumes, ("/var/www/html", "/etc/allscan",
                                         "/etc/allscan-reimagined", "/var/lib/allscan-reimagined")):
            args += ["-v", f"{volume}:{dest}"]
        try:
            docker(*args, BASE)
            for _ in range(60):
                result = docker("exec", name, "php", "-r",
                    '$v=@file_get_contents("http://127.0.0.1/asr/");exit(is_string($v)&&str_contains($v,"assets/index-")?0:1);',
                    check=False)
                if result.returncode == 0:
                    break
                if docker("inspect", "--format", "{{.State.Running}}", name).stdout.strip() != "true":
                    raise AssertionError("Container stopped: " + docker("logs", "--tail", "25", name).stderr)
                time.sleep(1)
            else:
                raise AssertionError("Clean layered startup failed: " + docker("logs", "--tail", "25", name).stderr)
            print("Clean layered web startup with read-only host mounts and no broker: PASS", flush=True)
            for mode in RUNTIME:
                path = root / mode
                info = path.stat()
                assert before[mode] == (info.st_uid, info.st_gid, info.st_mode)
                assert (path / "sentinel").read_text() == mode
            print("Read-only runtime ownership, mode and content unchanged: PASS", flush=True)
            docker("exec", name, "test", "-x", "/usr/local/sbin/allscan-reimagined-updater")
            result = docker("exec", "-u", "www-data", name, "sudo", "-n",
                "/usr/local/sbin/allscan-reimagined-updater", "--check-json", check=False)
            assert result.returncode != 0 and "Container updater unavailable" in result.stdout, result.stdout
            print("Updater fails closed with absent host broker: PASS", flush=True)

            # The late socket is a fixed read-only responder. No host control,
            # shell, Docker API or production path is exposed to the container.
            observed = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                endpoint = root / "host" / "bridge-setup.sock"
                listener.bind(str(endpoint))
                endpoint.chmod(0o666)
                listener.listen(4)
                listener.settimeout(10)
                def reply():
                    with listener.accept()[0] as peer:
                        peer.settimeout(5)
                        data = b""
                        while chunk := peer.recv(4096):
                            data += chunk
                        request = json.loads(data)
                        observed.append(request)
                        result = {"schema": 1, "exitCode": 0 if request == {"schema": 1, "update": "check"} else 1,
                                  "stdout": json.dumps({"ok": True, "updateAvailable": False}), "stderr": ""}
                        peer.sendall(json.dumps(result).encode())
                worker = threading.Thread(target=reply, daemon=True)
                worker.start()
                result = docker("exec", "-u", "www-data", name, "sudo", "-n",
                    "/usr/local/sbin/allscan-reimagined-updater", "--check-json")
                worker.join(10)
                assert observed == [{"schema": 1, "update": "check"}] and json.loads(result.stdout)["ok"]
            print("Late broker socket becomes usable without web restart: PASS (read-only protocol fixture)", flush=True)
        finally:
            docker("rm", "-f", name, check=False)
            for volume in volumes:
                docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    main()
