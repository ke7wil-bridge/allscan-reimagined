#!/usr/bin/env python3
"""Non-destructive tests for the managed updater. All paths are temporary."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
from unittest import mock

spec = importlib.util.spec_from_file_location("updater", Path(__file__).with_name("asr-updater.py"))
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)


def package(version="1.0.0-beta.8", unsafe=False):
    output = io.BytesIO()
    root = "allscan-reimagined-" + version
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, data in {
            "install.sh": b'ASR_VERSION=$(cat "$SCRIPT_DIR/package.json")',
            "package.json": json.dumps({"version": version}).encode(),
            "payload/server/asr-api.php": ("const ASR_VERSION = '" + version + "';").encode(),
            "payload/scripts/asr-reapply.sh": b"test",
            "payload/web/index.html": b"test",
        }.items():
            info = tarfile.TarInfo(root + "/" + name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if unsafe:
            info = tarfile.TarInfo(root + "/bad")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return output.getvalue()


def fails(call):
    try:
        call()
    except up.UpdateError:
        return
    raise AssertionError("Unsafe input was accepted")


def self_test():
    with tempfile.TemporaryDirectory(prefix="asr-updater-test-") as tmp:
        root = Path(tmp)
        up.JOB_ROOT = root / "jobs"
        up.PERSIST_ROOT = root / "persistent"
        up.ROLLBACK_JOBS = root / "rollback"
        up.BACKUPS = root / "backups"
        up.WORK_ROOT = root
        up.GATE = root / "gate.lock"
        up.INSTALL_LOCK = root / "install.lock"
        up.JOB_ROOT.mkdir()
        up.PERSIST_ROOT.mkdir()
        up.BACKUPS.mkdir()
        up.group_exists = lambda _g: False
        job = "20260924-120000-aabbccdd"

        # Status includes fixed keys and messages only; invalid IDs and raw details fail.
        data = up.status(job, "queued")
        assert up.read_status(job) == data
        fails(lambda: up.read_status("../../etc/passwd"))
        fails(lambda: up.status(job, "failed", message="password=secret"))
        assert "secret" not in (up.JOB_ROOT / (job + ".json")).read_text()
        assert up.active_jobs(up.JOB_ROOT)
        up.status(job, "complete", message="Update complete.")
        assert not up.active_jobs(up.JOB_ROOT)

        # Both the shared lock and queued rollback jobs reject another request.
        with up.guard():
            fails(up.guard)
        (up.ROLLBACK_JOBS / "test.json").parent.mkdir()
        (up.ROLLBACK_JOBS / "test.json").write_text('{"state":"queued"}')
        with mock.patch.object(up.os, "geteuid", return_value=0):
            fails(up.queue)
        (up.ROLLBACK_JOBS / "test.json").unlink()
        with mock.patch.object(up.os, "geteuid", return_value=0), mock.patch.object(
                up, "owned_jobs"), mock.patch.object(up, "install_busy", return_value=False), mock.patch.object(
                up.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            queued = up.queue()
            assert queued["state"] == "queued"
            fails(up.queue)
        up.status(queued["jobId"], "complete")

        # A published tag alone is insufficient: URLs must match exactly and
        # the checksum asset must name the selected archive.
        version = "1.0.0-beta.8"
        name = "allscan-reimagined-" + version + ".tar.gz"
        base = "https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v" + version + "/"
        release = {"tag_name": "v" + version, "draft": False, "assets": [
            {"name": name, "browser_download_url": base + name},
            {"name": name + ".sha256", "browser_download_url": base + name + ".sha256"}]}
        def fetch(url, _accept, _limit):
            if url == up.API:
                return json.dumps([release]).encode()
            return (hashlib.sha256(package()).hexdigest() + "  " + name + "\n").encode()
        with mock.patch.object(up, "fetch", side_effect=fetch):
            chosen = up.release_for_update("1.0.0-beta.7.6")
            assert chosen["version"] == version
            assert chosen["url"] == base + name
            assert chosen["sha256"] == hashlib.sha256(package()).hexdigest()
            release["assets"][0]["browser_download_url"] = "https://example.invalid/" + name
            fails(lambda: up.release_for_update("1.0.0-beta.7.6"))

        with mock.patch.object(up, "current_version", return_value="1.0.0-beta.7.6"), mock.patch.object(
                up, "release_for_update", side_effect=up.UpdateError("ASR is up to date")):
            assert up.check_available()["updateAvailable"] is False
        with mock.patch.object(up, "current_version", return_value="1.0.0-beta.7.6"), mock.patch.object(
                up, "release_for_update", return_value={"version": version}):
            assert up.check_available()["availableVersion"] == version

        archive = root / "release.tar.gz"
        archive.write_bytes(package())
        up.archive_valid(archive, version)
        staged = up.stage_release(archive, root / "stage", version)
        assert (staged / "install.sh").is_file()
        assert (staged / "install.sh").stat().st_mode & 0o777 == 0o644
        archive.write_bytes(package(unsafe=True))
        fails(lambda: up.archive_valid(archive, version))
        archive.write_bytes(package())
        assert hashlib.sha256(archive.read_bytes()).hexdigest() != "0" * 64

        # Preflight verifies configuration, space, backend compatibility,
        # backup storage, and service readiness without changing the host.
        up.WEB_ROOT = root / "web"
        stock = up.WEB_ROOT / "allscan/include/common.php"
        stock.parent.mkdir(parents=True)
        stock.write_text('$AllScanVersion = "v1.01";\n')
        (up.WEB_ROOT / "asr").mkdir()
        (up.WEB_ROOT / "asr/asr-api.php").write_text("test")
        up.RELEASE_ROOT = root / "current"
        (up.RELEASE_ROOT / "compat/allscan-v1.01").mkdir(parents=True)
        config_dir = root / "config"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"node":"12345"}')
        real_read_text = Path.read_text
        def local_config_read(path, *args, **kwargs):
            if str(path) == "/etc/allscan-reimagined/config.json":
                return real_read_text(config_dir / "config.json", *args, **kwargs)
            return real_read_text(path, *args, **kwargs)
        with mock.patch.object(up, "current_version", return_value="1.0.0-beta.7.6"), mock.patch.object(
                up, "release_for_update", return_value={"version": version}), mock.patch.object(
                up, "fetch", return_value=b'$AllScanVersion = "v1.01";'), mock.patch.object(
                up.shutil, "which", return_value="/usr/bin/test"), mock.patch.object(
                up.subprocess, "run", return_value=SimpleNamespace(returncode=0)), mock.patch.object(
                Path, "read_text", local_config_read):
            assert up.preflight()["rebootRequired"] is False
            stock.write_text('$AllScanVersion = "v1.00";\n')
            fails(up.preflight)
            stock.write_text('$AllScanVersion = "v1.01";\n')

        # Installer output stays in a private log, not the browser status.
        local_installer = root / "local-installer"
        local_installer.mkdir()
        (local_installer / "install.sh").write_text(
            '#!/bin/bash\necho "private-config-value" >&2\nexit 2\n')
        assert up.install_release(local_installer, job) == 2
        private_log = up.JOB_ROOT / (job + ".log")
        assert private_log.stat().st_mode & 0o777 == 0o600
        assert "private-config-value" in private_log.read_text()
        assert "private-config-value" not in json.dumps(up.read_status(job))

        backup = up.BACKUPS / "20260924-120001"
        backup.mkdir()
        (backup / "manifest.json").write_text("{}")
        with mock.patch.object(up, "healthy", side_effect=[False, True]), mock.patch.object(
                up.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as rollback:
            assert up.recover("1.0.0-beta.7.6", set())
            assert rollback.call_args.args[0] == [
                "/usr/local/sbin/allscan-reimagined-rollback", "rollback",
                "20260924-120001"]
        assert up.backup_since({"20260924-120001"}) is None

        # Failure after the installer boundary calls recovery; never expose
        # subprocess diagnostics in browser status.
        up.status(job, "queued")
        checks = {"installedVersion": "1.0.0-beta.7.6",
                  "release": {"version": version, "url": base + name, "sha256": "0" * 64}}
        with mock.patch.object(up.os, "geteuid", return_value=0), mock.patch.object(
                up, "preflight", return_value=checks), mock.patch.object(
                up, "install_busy", return_value=False), mock.patch.object(
                up, "fetch", return_value=package()), mock.patch.object(
                up, "recover", return_value=True) as recovery:
            assert up.run(job) == 1
            recovery.assert_called_once()
        assert up.read_status(job)["message"] == "Update failed; previous installation restored."
        assert "secret" not in json.dumps(up.read_status(job))

        # Post-staging installer failure reaches the same rollback boundary.
        up.status(job, "queued")
        checks["release"]["sha256"] = hashlib.sha256(package()).hexdigest()
        staged_for_failure = root / "staged-for-failure"
        (staged_for_failure / "payload/compat/allscan-v1.01").mkdir(parents=True)
        (staged_for_failure / "install.sh").write_text("exit 1")
        with mock.patch.object(up.os, "geteuid", return_value=0), mock.patch.object(
                up, "preflight", return_value=checks), mock.patch.object(
                up, "install_busy", return_value=False), mock.patch.object(
                up, "fetch", return_value=package()), mock.patch.object(
                up, "stage_release", return_value=staged_for_failure), mock.patch.object(
                up, "install_release", return_value=1) as installer, mock.patch.object(
                up, "recover", return_value=True) as recovery:
            assert up.run(job) == 1
            installer.assert_called_once_with(staged_for_failure, job)
            recovery.assert_called_once()

        # A successful installer followed by failed health verification also
        # crosses the automatic rollback boundary.
        up.status(job, "queued")
        with mock.patch.object(up.os, "geteuid", return_value=0), mock.patch.object(
                up, "preflight", return_value=checks), mock.patch.object(
                up, "install_busy", return_value=False), mock.patch.object(
                up, "fetch", return_value=package()), mock.patch.object(
                up, "stage_release", return_value=staged_for_failure), mock.patch.object(
                up, "install_release", return_value=0), mock.patch.object(
                up, "healthy", return_value=False), mock.patch.object(
                up, "recover", return_value=True) as recovery:
            assert up.run(job) == 1
            recovery.assert_called_once()
        assert up.read_status(job)["state"] == "failed"

        # Persistent journal survives loss of /run, then browser recovery
        # refuses a live unit and repairs a stopped interrupted job.
        up.status(job, "installing")
        (up.PERSIST_ROOT / (job + ".meta.json")).write_text(json.dumps({
            "previous": "1.0.0-beta.7.6", "before": []}))
        (up.JOB_ROOT / (job + ".json")).unlink()
        persistent_status = up.PERSIST_ROOT / (job + ".json")
        old_status = json.loads(persistent_status.read_text())
        old_status["updatedAt"] = "2026-01-01T00:00:00+00:00"
        persistent_status.write_text(json.dumps(old_status))
        assert up.read_status(job)["state"] == "installing"
        with mock.patch.object(up.os, "geteuid", return_value=0), mock.patch.object(
                up, "owned_jobs"), mock.patch.object(
                up, "install_busy", return_value=False), mock.patch.object(
                up.subprocess, "run", return_value=SimpleNamespace(returncode=3)), mock.patch.object(
                up, "recover", return_value=True) as recovery:
            assert up.recover_interrupted()["status"] == "recovery_checked"
            recovery.assert_called_once_with("1.0.0-beta.7.6", set())
        assert up.read_status(job)["message"] == "Update failed; previous installation restored."

    print("ASR updater self-test: ok")


if __name__ == "__main__":
    self_test()
