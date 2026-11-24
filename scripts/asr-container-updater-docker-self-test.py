#!/usr/bin/env python3
"""Disposable real-Docker acceptance test for the container updater."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DESTINATIONS = ("/var/www/html", "/etc/allscan", "/etc/allscan-reimagined",
                "/var/lib/allscan-reimagined")


def load_updater():
    path = HERE / "asr-container-updater.py"
    spec = importlib.util.spec_from_file_location("asr_container_updater_docker_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(argv: list[str], *, cwd: Path | None = None, check: bool = True):
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=check)


def write_image_context(path: Path, version: str, base: str) -> None:
    path.mkdir(parents=True)
    (path / "package.json").write_text(json.dumps({"version": version}) + "\n")
    (path / "Dockerfile").write_text(
        f"FROM {base}\n"
        "COPY package.json /opt/asr-source/package.json\n"
        "ENTRYPOINT []\n"
        'CMD ["apache2-foreground"]\n')


def build_image(context: Path, tag: str, dockerfile: str = "Dockerfile") -> None:
    command(["/usr/bin/docker", "build", "--pull=false", "--network=none",
             "-f", str(context / dockerfile), "-t", tag, str(context)])


def compose_model(project: str, service: str, container: str, image: str,
                  mounts: list[dict[str, str]]) -> dict:
    return {"name": project, "services": {service: {
        "image": image,
        "container_name": container,
        "environment": {"ASR_UPDATE_MODE": "container"},
        "volumes": [{"type": "bind", "source": item["source"],
                     "target": item["destination"]} for item in mounts],
    }}}


def run_job(up, job: str, checks: dict, stage: Path, payload: bytes,
            *, health=None, backup_error: bool = False) -> dict:
    up.status(job, "queued")
    verifier = SimpleNamespace(
        MAX_ARCHIVE=1024 * 1024,
        fetch=lambda *_args: payload,
        stage_release=lambda *_args: stage,
    )
    patches = [
        mock.patch.object(up, "preflight", return_value=checks),
        mock.patch.object(up, "native_updater", return_value=verifier),
    ]
    if health is not None:
        patches.append(mock.patch.object(up, "healthy", side_effect=health))
    if backup_error:
        patches.append(mock.patch.object(up, "create_backup",
                                         side_effect=OSError("simulated backup failure")))
    for patch in patches:
        patch.start()
    try:
        up.run_job(job)
    finally:
        for patch in reversed(patches):
            patch.stop()
    return up.read_status(job)


def main() -> int:
    if os.geteuid() == 0:
        raise SystemExit("run this test as the Docker-authorized development user, not root")
    if command(["/usr/bin/docker", "info"], check=False).returncode:
        raise SystemExit("Docker is unavailable")
    base = "allscan-reimagined:1.0.0-beta.8.7"
    if command(["/usr/bin/docker", "image", "inspect", base], check=False).returncode:
        raise SystemExit(f"required local base image is unavailable: {base}")

    up = load_updater()
    quiet_run = up.run
    def diagnostic_run(*args, **kwargs):
        result = quiet_run(*args, **kwargs)
        if result.returncode:
            print(f"docker acceptance command failed: {args[0]!r}\n{result.stderr}")
        return result
    up.run = diagnostic_run
    suffix = str(os.getpid())
    old_version = f"9.9.9-beta.190.{suffix}"
    new_version = f"9.9.9-beta.191.{suffix}"
    old_image = f"asr-updater-acceptance:{old_version}"
    new_image = f"allscan-reimagined:{new_version}"
    project = f"asr191accept{suffix}"
    service = "web"
    container = f"{project}-web"
    created_images = [old_image, new_image]

    with tempfile.TemporaryDirectory(prefix="asr191-docker-acceptance-") as temporary:
        root = Path(temporary)
        up.STATE = root / "state"
        up.BACKUPS = root / "backups"
        up.WORK = root / "work"
        up.GATE = root / "updater.lock"
        up.DOCKER_HOME = root / "docker-home"
        up.DOCKER_CONFIG = root / "docker-config"
        up.DOCKER_BUILDX = root / "buildx"
        for path in (up.STATE, up.BACKUPS, up.WORK, up.DOCKER_HOME,
                     up.DOCKER_CONFIG, up.DOCKER_BUILDX):
            path.mkdir()

        old_context = root / "old-image"
        stage = root / "release-stage"
        write_image_context(old_context, old_version, base)
        write_image_context(stage, new_version, base)
        (stage / "container").mkdir()
        shutil.move(stage / "Dockerfile", stage / "container/Dockerfile.layered")
        build_image(old_context, old_image)

        mounts = []
        for index, destination in enumerate(DESTINATIONS):
            source = root / f"volume-{index}"
            source.mkdir()
            (source / "sentinel.txt").write_text(f"persistent:{destination}\n")
            mounts.append({"source": str(source), "destination": destination})
        web_root = Path(mounts[0]["source"])
        (web_root / "asr").mkdir()
        (web_root / "asr/index.html").write_text("<html>assets/index-test.js dashboard</html>\n")

        model = compose_model(project, service, container, old_image, mounts)
        compose_file = root / "approved-compose.json"
        compose_file.write_text(json.dumps(model))
        try:
            command(["/usr/bin/docker", "compose", "--project-name", project,
                     "-f", str(compose_file), "up", "-d"])
            old_inspect = up.inspect(container)
            old_image_id = old_inspect["Image"]
            selected = SimpleNamespace(web_container=container)
            up.profile = lambda: selected
            assert up.installed_version(container) == old_version
            assert up.healthy(container, old_version)

            def checks(sha256: str, installed: str = old_version) -> dict:
                current = up.inspect(container)
                meta = {
                    "project": project, "service": service,
                    "workingDir": str(root), "configFiles": [str(compose_file)],
                    "webContainer": container, "image": current["Config"]["Image"],
                    "imageId": current["Image"], "approvedModel": model,
                }
                return {
                    "ok": True, "installedVersion": installed,
                    "availableVersion": new_version,
                    "release": {"version": new_version, "url": "https://example.invalid/release",
                                "sha256": sha256},
                    "compose": meta, "mounts": mounts,
                }

            payload = b"local verified release payload"
            good_sha = hashlib.sha256(payload).hexdigest()
            real_healthy = up.healthy

            # Forced replacement health failure must restore the immutable old
            # image identity and verify the old version before reporting success.
            def fail_new_health(web_container: str, version: str | None):
                return False if version == new_version else real_healthy(web_container, version)
            failed = run_job(up, f"20261124-130001-{suffix[-8:].zfill(8)}", checks(good_sha),
                             stage, payload, health=fail_new_health)
            assert failed["state"] == "failed" and failed["restoration"] == "succeeded", failed
            assert up.inspect(container)["Image"] == old_image_id
            assert up.installed_version(container) == old_version

            # A bad checksum cannot stop or replace the running container.
            before = up.inspect(container)["Id"]
            rejected = run_job(up, f"20261124-130002-{suffix[-8:].zfill(8)}",
                               checks("0" * 64), stage, payload)
            assert rejected["state"] == "failed" and rejected["restoration"] == "not_needed"
            assert up.inspect(container)["Id"] == before

            # Failure after the consistent stop must start and verify the
            # original container without changing its identity.
            backup_failed = run_job(up, f"20261124-130003-{suffix[-8:].zfill(8)}",
                                    checks(good_sha), stage, payload, backup_error=True)
            assert backup_failed["restoration"] == "succeeded"
            assert up.inspect(container)["Id"] == before
            assert up.installed_version(container) == old_version

            completed = run_job(up, f"20261124-130004-{suffix[-8:].zfill(8)}",
                                checks(good_sha), stage, payload)
            assert completed["state"] == "complete"
            assert completed["currentVersion"] == new_version
            assert up.installed_version(container) == new_version
            assert up.healthy(container, new_version)
            for item in mounts:
                assert (Path(item["source"]) / "sentinel.txt").is_file()

            # Reuse the unchanged root-approved snapshot for another real
            # replacement after Compose labels point at generated temp config.
            repeated = run_job(up, f"20261124-130005-{suffix[-8:].zfill(8)}",
                               checks(good_sha, new_version), stage, payload)
            assert repeated["state"] == "complete"
            assert up.installed_version(container) == new_version
            assert up.healthy(container, new_version)
            print("real Docker container updater acceptance passed")
        finally:
            command(["/usr/bin/docker", "compose", "--project-name", project,
                     "-f", str(compose_file), "down", "--remove-orphans"], check=False)
            for image in created_images:
                command(["/usr/bin/docker", "image", "rm", image], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
