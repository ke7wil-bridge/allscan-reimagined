#!/usr/bin/env python3
"""Verify packaged AllScan build contracts and an optional offline image."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = (
    "/var/www/html",
    "/etc/allscan",
    "/etc/allscan-reimagined",
    "/var/lib/allscan-reimagined",
)


def require(source: str, needle: str, message: str) -> None:
    if needle not in source:
        raise RuntimeError(message)


def static_checks() -> dict[str, object]:
    manifest = json.loads((ROOT / "stock-allscan.json").read_text(encoding="utf-8"))
    source_dockerfile = (ROOT / "Dockerfile.layered").read_text(encoding="utf-8")
    release_dockerfile = (ROOT / "docker/Dockerfile.release").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker/layered-entrypoint.sh").read_text(encoding="utf-8")
    release_build = (ROOT / "build-release.sh").read_text(encoding="utf-8")
    helper = (ROOT / "docker/package-upstream-allscan.sh").read_text(encoding="utf-8")

    for name, dockerfile, copy_path in (
        ("source", source_dockerfile, "docker/package-upstream-allscan.sh"),
        ("release", release_dockerfile, "container/package-upstream-allscan.sh"),
    ):
        require(dockerfile, f"COPY {copy_path} /usr/local/sbin/allscan-reimagined-package-upstream",
                f"{name} Dockerfile does not copy the shared upstream packager")
        require(dockerfile,
                "/usr/local/sbin/allscan-reimagined-package-upstream /opt/asr-source/stock-allscan.json /opt/upstream-allscan",
                f"{name} Dockerfile does not package the pinned upstream tree")
        require(dockerfile, "ENV ASR_PACKAGED_ALLSCAN=1",
                f"{name} Dockerfile does not declare its verified offline cache")

    require(release_build,
            'install -m 755 docker/package-upstream-allscan.sh "$STAGE/container/package-upstream-allscan.sh"',
            "release archive omits the shared upstream packager")
    require(helper, 'python3 "$validator" manifest-version "$MANIFEST"',
            "upstream packager does not use the strict manifest validator")
    require(helper, 'git -C "$stage" fetch --depth 1 origin "$commit"',
            "upstream packager does not fetch the immutable commit")
    require(helper, 'git -C "$stage" fsck --no-dangling',
            "upstream packager does not validate the Git object graph")
    require(entrypoint, 'if [ "${ASR_PACKAGED_ALLSCAN:-0}" = "1" ]; then',
            "entrypoint does not verify packaged mode")
    require(entrypoint, 'test "$(git -C "$UPSTREAM_CACHE" rev-parse HEAD)" = "$ALLSCAN_REPO_REF"',
            "entrypoint does not verify the packaged commit")
    require(entrypoint, 'if [ "${ASR_PACKAGED_ALLSCAN:-0}" != "1" ]; then',
            "entrypoint does not keep network fetches outside packaged mode")
    return manifest


def docker(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"docker {args[0]} failed: {detail}")
    return result


def image_checks(image: str, manifest: dict[str, object]) -> None:
    inspect = json.loads(docker("image", "inspect", image).stdout)[0]
    environment = inspect.get("Config", {}).get("Env", []) or []
    if "ASR_PACKAGED_ALLSCAN=1" not in environment:
        raise RuntimeError("image does not declare ASR_PACKAGED_ALLSCAN=1")

    verify = r'''
set -eu
manifest=/opt/asr-source/stock-allscan.json
test -f "$manifest"
test -d /opt/upstream-allscan/.git
commit=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$manifest")
test "$(git -C /opt/upstream-allscan rev-parse HEAD)" = "$commit"
test -f /opt/upstream-allscan/include/common.php
'''
    docker(
        "run", "--rm", "--network", "none",
        "--memory", "192m", "--memory-swap", "192m", "--cpus", "0.5",
        "--pids-limit", "64", "--security-opt", "no-new-privileges",
        "--entrypoint", "/bin/sh", image, "-c", verify,
    )

    suffix = f"{os.getpid()}-{int(time.time())}"
    container = f"asr337-offline-{suffix}"
    volumes = [f"{container}-{index}" for index in range(len(DESTINATIONS))]
    with tempfile.TemporaryDirectory(prefix="asr337-offline-") as temporary:
        root = Path(temporary)
        astdb = root / "asterisk"
        astdb.mkdir()
        (astdb / "astdb.txt").write_text("100001|N0CALL|Offline QA|Test\n" * 48,
                                           encoding="utf-8")
        run_args = [
            "run", "-d", "--name", container, "--network", "none",
            "--memory", "640m", "--memory-swap", "640m", "--cpus", "1",
            "--pids-limit", "256", "--security-opt", "no-new-privileges",
            "--restart", "no", "-e", "ASR_UPDATE_MODE=container",
            "-e", "ASR_NODE_NUMBER=100001", "-e", "ASR_CALLSIGN=N0CALL",
            "-e", "ASR_AMI_HOST=127.0.0.1", "-e", "ASR_AMI_PASS=offline-qa-only",
            "-e", "ASR_REQUIRE_LOGIN=1", "-e", "ASR_ASTDB_WAIT_SECONDS=2",
            "-v", f"{astdb}:/var/lib/asterisk:ro",
        ]
        for volume, destination in zip(volumes, DESTINATIONS):
            run_args.extend(("-v", f"{volume}:{destination}"))
        try:
            docker(*run_args, image)
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                probe = docker(
                    "exec", container, "php", "-r",
                    '$v=@file_get_contents("http://127.0.0.1/asr/");exit(is_string($v)&&str_contains($v,"assets/index-")?0:1);',
                    check=False,
                )
                if probe.returncode == 0:
                    break
                state = docker("inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", container).stdout.strip()
                if not state.startswith("running "):
                    logs = docker("logs", "--tail", "80", container, check=False).stdout
                    raise RuntimeError(f"offline entrypoint exited ({state}): {logs[-3000:]}")
                time.sleep(1)
            else:
                logs = docker("logs", "--tail", "80", container, check=False).stdout
                raise RuntimeError(f"offline HTTP readiness timed out: {logs[-3000:]}")

            expected_commit = str(manifest["commit"])
            validation = r'''
set -eu
test "$(git -C /opt/upstream-allscan rev-parse HEAD)" = "$EXPECTED_COMMIT"
test -f /var/www/html/allscan/include/common.php
test -f /var/www/html/asr/index.php
test -f /var/www/html/asr/asr-api.php
sqlite3 /etc/allscan/allscan.db 'pragma quick_check;' | grep -Fx ok
php -l /var/www/html/asr/index.php >/dev/null
php -l /var/www/html/asr/asr-api.php >/dev/null
python3 - <<'PY'
import json
for name in ('/opt/asr-source/stock-allscan.json', '/etc/allscan-reimagined/config.json', '/run/allscan-reimagined/docker-reapply.json'):
    with open(name, encoding='utf-8') as handle:
        json.load(handle)
PY
'''
            docker("exec", "-e", f"EXPECTED_COMMIT={expected_commit}", container,
                   "/bin/sh", "-c", validation)

            sentinel = f"offline-upgrade-{suffix}"
            docker("exec", container, "/bin/sh", "-c",
                   f"printf '%s\\n' {sentinel} > /etc/allscan-reimagined/.offline-qualification")
            docker("rm", "-f", container)
            docker(*run_args, image)
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                persisted = docker("exec", container, "grep", "-Fx", sentinel,
                                   "/etc/allscan-reimagined/.offline-qualification", check=False)
                ready = docker("exec", container, "php", "-r",
                               '$v=@file_get_contents("http://127.0.0.1/asr/");exit($v===false);',
                               check=False)
                if persisted.returncode == 0 and ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("offline recreation did not preserve data and return HTTP")
        finally:
            docker("rm", "-f", container, check=False)
            for volume in volumes:
                docker("volume", "rm", volume, check=False)

    print(f"PASS: {image} packages {manifest['version']} {manifest['commit']} and initializes offline")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="also exercise this local image with networking disabled")
    args = parser.parse_args()
    manifest = static_checks()
    print("PASS: packaged AllScan Docker and release contracts")
    if args.image:
        image_checks(args.image, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
