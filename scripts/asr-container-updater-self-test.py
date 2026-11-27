#!/usr/bin/env python3
"""Isolated success, failure, rollback, persistence, and authorization tests."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def completed(code: int = 0, stdout: str = ""):
    return subprocess.CompletedProcess([], code, stdout, "")


def release(version: str = "1.0.0-beta.8.7"):
    return {"version": version, "url": "https://example.invalid/release", "sha256": "0" * 64}


def exercise_job(up, root: Path, *, health: bool, restored: bool | None, backup_failure: bool = False) -> dict:
    job = "20261124-120000-aabbccdd" if health else "20261124-120001-aabbccdd"
    up.status(job, "queued")
    staged = root / ("staged-ok" if health else "staged-fail")
    (staged / "container").mkdir(parents=True, exist_ok=True)
    (staged / "container/Dockerfile.layered").write_text("FROM scratch\n")
    verifier = SimpleNamespace(
        MAX_ARCHIVE=1024,
        fetch=lambda *_args: b"payload",
        stage_release=lambda *_args: staged,
    )
    selected = SimpleNamespace(web_container="asr-web")
    checks = {
        "ok": True, "installedVersion": "1.0.0-beta.8.6.1",
        "availableVersion": "1.0.0-beta.8.7", "release": {
            **release(), "sha256": __import__("hashlib").sha256(b"payload").hexdigest()},
        "compose": {"image": "asr:old", "imageId": "sha256:" + "1" * 64,
                    "service": "layered-allscan",
                    "webContainer": "asr-web", "workingDir": str(root),
                    "configFiles": [str(root / "compose.yml")], "project": "asr"},
        "mounts": [],
    }
    backup = root / ("backup-ok" if health else "backup-fail")
    backup.mkdir(exist_ok=True)
    patches = [
        mock.patch.object(up, "preflight", return_value=checks),
        mock.patch.object(up, "native_updater", return_value=verifier),
        mock.patch.object(up, "run", return_value=completed()),
        mock.patch.object(up, "profile", return_value=selected),
        mock.patch.object(up, "create_backup", side_effect=OSError("simulated backup failure") if backup_failure else None, return_value=backup),
        mock.patch.object(up, "release_inventory", return_value={}),
        mock.patch.object(up, "host_payload", return_value={}),
        mock.patch.object(up, "backup_host_helpers"),
        mock.patch.object(up, "install_host_helpers"),
        mock.patch.object(up, "docker_compose", return_value=completed()),
        mock.patch.object(up, "healthy", return_value=health),
    ]
    if restored is not None:
        patches.append(mock.patch.object(up, "restore_backup", return_value=restored))
    for item in patches:
        item.start()
    try:
        assert up.run_job(job) == (0 if health and not backup_failure else 1)
        if not backup_failure:
            up.backup_host_helpers.assert_called_once_with(backup, {})
            up.install_host_helpers.assert_called_once_with({}, {})
        if backup_failure:
            up.run.assert_any_call(["/usr/bin/docker", "start", "asr-web"], timeout=120)
        if restored is not None:
            up.restore_backup.assert_called_once_with(
                backup, "sha256:" + "1" * 64, "1.0.0-beta.8.6.1")
    finally:
        for item in reversed(patches):
            item.stop()
    return up.read_status(job)


def main() -> int:
    up = load("asr_container_updater_test", "asr-container-updater.py")
    broker = load("asr_container_broker_update_test", "asr-container-host-broker.py")
    with tempfile.TemporaryDirectory(prefix="asr-container-update-test-") as temporary:
        root = Path(temporary)
        up.STATE = root / "state"
        up.BACKUPS = root / "backups"
        up.WORK = root / "work"
        up.GATE = root / "gate"
        up.STATE.mkdir(); up.BACKUPS.mkdir(); up.WORK.mkdir()
        (root / "compose.yml").write_text("services: {}\n")

        class FakeUpdateError(RuntimeError):
            pass
        with mock.patch.object(up, "installed_version", return_value="1.0.0-beta.8.6.1"), mock.patch.object(
                up, "native_updater", return_value=SimpleNamespace(
                    UpdateError=FakeUpdateError,
                    release_for_update=lambda _version: release())):
            available = up.release_check("asr-web")
            assert available["updateAvailable"] and available["updateMode"] == "container"
        with mock.patch.object(up, "installed_version", return_value="1.0.0-beta.8.7"), mock.patch.object(
                up, "native_updater", return_value=SimpleNamespace(
                    UpdateError=FakeUpdateError,
                    release_for_update=lambda _version: (_ for _ in ()).throw(FakeUpdateError("ASR is up to date")))):
            current = up.release_check("asr-web")
            assert not current["updateAvailable"] and current["availableVersion"] == "1.0.0-beta.8.7"

        # The supported restart policy must also match the approved snapshot.
        model = {"service": "web", "approvedModel": {"services": {"web": {"restart": "unless-stopped"}}}}
        def restart(name):
            return {"HostConfig": {"RestartPolicy": {"Name": name}}}
        up.validate_restart_policy(restart("unless-stopped"), model)
        for bad in ("always", "on-failure", "no"):
            try:
                up.validate_restart_policy(restart(bad), model)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("unsafe or mismatched restart policy accepted")
        with mock.patch.object(up, "run", side_effect=[
                completed(1), subprocess.CompletedProcess([], 1, "[]", "Error response from daemon: No such container: asr-web")]):
            assert up.stop_for_restore("asr-web")
        with mock.patch.object(up, "run", side_effect=[
                completed(1), subprocess.CompletedProcess([], 1, "", "Docker daemon unavailable")]):
            assert not up.stop_for_restore("asr-web")

        # A verified upgrade adds absent host helpers, replaces stale ones,
        # and restores both old bytes and absent files on rollback.
        host = root / "host"
        control = root / "control"
        host.mkdir(mode=0o755); control.mkdir(mode=0o755); (host / "bridge-relays").mkdir(mode=0o755)
        stage = root / "host-stage"
        with mock.patch.object(up, "HOST_DEST", host), mock.patch.object(up, "CONTROL_DEST", control):
            inventory = up.host_inventory()
            installer = stage / "payload/scripts/asr-container-host-install.py"
            installer.parent.mkdir(parents=True, exist_ok=True)
            source = (HERE / "asr-container-host-install.py").read_text()
            installer.write_text(source)
            assert up.release_inventory(stage) == inventory
            installer.write_text(source.replace("HOST_UPDATER_INFRASTRUCTURE_SCHEMA = 1", "HOST_UPDATER_INFRASTRUCTURE_SCHEMA = 2"))
            try:
                up.release_inventory(stage)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("unqualified host infrastructure migration accepted")
            for name, (relative, target, mode) in inventory.items():
                source = stage / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(("# new:" + name).encode())
                if name != "asr-container-standard-link.py":
                    target.write_bytes(("# old:" + name).encode())
                    target.chmod(mode)
            transaction = root / "host-transaction"
            transaction.mkdir()
            data = up.host_payload(stage)
            up.backup_host_helpers(transaction)
            up.install_host_helpers(data)
            assert (host / "asr-container-standard-link.py").is_file()
            assert all(target.read_bytes() == data[name] for name, (_, target, _) in inventory.items())
            up.restore_host_helpers(transaction)
            assert not (host / "asr-container-standard-link.py").exists()
            assert all(target.read_bytes() == ("# old:" + name).encode() for name, (_, target, _) in inventory.items() if name != "asr-container-standard-link.py")
            # A partial replacement must restore stale bytes and remove newly
            # introduced files. Also reject corrupt backups before touching live files.
            backup2 = root / "partial-host-transaction"
            backup2.mkdir()
            up.backup_host_helpers(backup2)
            real_write = up.atomic_host_file
            def fail_mid_install(target, content, mode):
                if target.name == "asr-fixed-bridge-recovery.py":
                    raise OSError("simulated partial host install")
                return real_write(target, content, mode)
            with mock.patch.object(up, "atomic_host_file", side_effect=fail_mid_install):
                try:
                    up.install_host_helpers(data)
                except OSError:
                    pass
                else:
                    raise AssertionError("partial host installation did not fail")
            assert (host / "asr-container-standard-link.py").exists()
            up.restore_host_helpers(backup2)
            assert not (host / "asr-container-standard-link.py").exists()
            assert all(target.read_bytes() == ("# old:" + name).encode()
                       for name, (_, target, _) in inventory.items()
                       if name != "asr-container-standard-link.py")
            up.install_host_helpers(data)
            damaged = backup2 / "host-helpers/asr-container-host-broker.py"
            damaged.write_bytes(b"corrupt")
            try:
                up.restore_host_helpers(backup2)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("corrupt host backup accepted")
            assert all(target.read_bytes() == data[name] for name, (_, target, _) in inventory.items())
            # Repeated install is idempotent; required files and modes match.
            up.install_host_helpers(data)
            assert all(target.read_bytes() == data[name] and target.stat().st_mode & 0o777 == mode
                       for name, (_, target, mode) in inventory.items())
            # Reject malformed Python and symlink targets before downtime.
            invalid = stage / inventory["asr-container-standard-link.py"][0]
            original = invalid.read_bytes()
            invalid.write_text("def invalid syntax")
            try:
                up.host_payload(stage)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("malformed host helper accepted")
            invalid.write_bytes(original)
            target = inventory["asr-container-standard-link.py"][1]
            target.unlink()
            target.symlink_to(invalid)
            try:
                up.host_payload(stage)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("symlink host helper target accepted")
            target.unlink()
            missing = stage / inventory["asr-container-standard-link.py"][0]
            missing.unlink()
            try:
                up.host_payload(stage)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("Missing release host helper was accepted")

        successful = exercise_job(up, root, health=True, restored=None)
        assert successful["state"] == "complete"
        assert successful["currentVersion"] == "1.0.0-beta.8.7"
        assert successful["availableVersion"] == "1.0.0-beta.8.7"

        failed = exercise_job(up, root, health=False, restored=True)
        assert failed["state"] == "failed" and failed["restoration"] == "succeeded"
        assert failed["manualInterventionRequired"] is False

        backup_failed = exercise_job(up, root, health=True, restored=None, backup_failure=True)
        assert backup_failed["restoration"] == "succeeded"
        assert backup_failed["manualInterventionRequired"] is False

        # Real archive/restore coverage proves all four persistent volume roots
        # survive a failed replacement, including bridge configuration/state.
        mounts = []
        expected = {}
        for index, destination in enumerate(sorted(up.PERSISTENT_DESTINATIONS)):
            source = root / f"volume-{index}"
            source.mkdir()
            (source / "sentinel.txt").write_text(f"preserved:{destination}\n")
            mounts.append({"destination": destination, "source": str(source)})
            expected[str(source)] = (source / "sentinel.txt").read_bytes()
        assert up.EPHEMERAL_WRITABLE_DESTINATIONS == {"/run/asr-standalone-admin"}
        meta = {"image": "asr:old", "imageId": "sha256:" + "1" * 64,
                "service": "layered-allscan", "webContainer": "asr-web",
                "workingDir": str(root), "configFiles": [str(root / "compose.yml")], "project": "asr"}
        rendered = {"services": {"layered-allscan": {
            "image": "allscan-reimagined:asr-update-preflight",
            "environment": {"ASR_UPDATE_MODE": "container"}}}}
        with mock.patch.object(up, "docker_compose", return_value=completed(stdout=json.dumps(rendered))):
            up.validate_compose_model(meta)
        rendered["services"]["layered-allscan"]["image"] = "hard-coded:old"
        with mock.patch.object(up, "docker_compose", return_value=completed(stdout=json.dumps(rendered))):
            try:
                up.validate_compose_model(meta)
            except up.UpdateError:
                pass
            else:
                raise AssertionError("hard-coded Compose image bypass was accepted")
        # Browser operations must not read or execute the mutable original Compose.
        snapshot = {"name": "asr", "services": {"layered-allscan": {
            "image": "asr:approved", "environment": {"ASR_UPDATE_MODE": "container",
            "LITERAL": "$SECRET"}}}}
        approved = {**meta, "approvedModel": snapshot}
        (root / "compose.yml").write_text("malicious replacement")
        def inspect_command(argv, **kwargs):
            config = Path(argv[argv.index("-f") + 1])
            assert config.parent.parent == up.WORK
            assert "--env-file" in argv and "/dev/null" in argv
            service = json.loads(config.read_text())["services"]["layered-allscan"]
            assert service["image"] == "asr:new"
            assert service["environment"]["LITERAL"] == "$$SECRET"
            assert str(root / "compose.yml") not in argv
            return completed()
        with mock.patch.object(up, "run", side_effect=inspect_command):
            up.docker_compose(approved, "asr:new", "up", "-d")
        assert snapshot["services"]["layered-allscan"]["image"] == "asr:approved"
        with mock.patch.object(up.subprocess, "run", return_value=completed()) as invoked:
            up.run(["/usr/bin/docker", "inspect", "asr-web"])
            environment = invoked.call_args.kwargs["env"]
            assert environment == up.docker_environment()
            assert environment["HOME"] != "/root"
        try:
            up.approved_compose(meta)
        except up.UpdateError:
            pass
        else:
            raise AssertionError("Unapproved Compose accepted")
        restart_failed = exercise_job(up, root, health=False, restored=None, backup_failure=True)
        assert restart_failed["restoration"] == "failed"
        assert restart_failed["manualInterventionRequired"] is True
        backup = up.create_backup("20261124-120002-aabbccdd", meta, mounts)
        for mount in mounts:
            source = Path(mount["source"])
            (source / "sentinel.txt").write_text("damaged\n")
            (source / "new-file").write_text("remove me\n")
        with mock.patch.object(up, "run", return_value=completed()), mock.patch.object(
                up, "docker_compose", return_value=completed()), mock.patch.object(
                up, "healthy", return_value=True), mock.patch.object(
                up, "profile", return_value=SimpleNamespace(web_container="asr-web")):
            assert up.restore_backup(backup, "sha256:" + "1" * 64, "1.0.0-beta.8.6.1")
            up.healthy.assert_called_once_with("asr-web", "1.0.0-beta.8.6.1")
        for source, content in expected.items():
            assert (Path(source) / "sentinel.txt").read_bytes() == content
            assert not (Path(source) / "new-file").exists()

        # A failure after one volume was replaced rolls every touched volume
        # back to its exact pre-restore contents instead of leaving a mixture.
        for mount in mounts:
            source = Path(mount["source"])
            (source / "sentinel.txt").write_text("pre-restore\n")
            (source / "new-file").write_text("retain on failed restore\n")
        real_replace = up.os.replace
        displaced_moves = 0
        def fail_second_volume(source, destination):
            nonlocal displaced_moves
            source_path, destination_path = Path(source), Path(destination)
            if (source_path.parent in {Path(item["source"]) for item in mounts}
                    and destination_path.parent.name.startswith(".asr-displaced-")):
                displaced_moves += 1
                if displaced_moves == 3:
                    raise OSError("simulated partial restoration failure")
            return real_replace(source, destination)
        with mock.patch.object(up.os, "replace", side_effect=fail_second_volume), mock.patch.object(
                up, "run", return_value=completed()):
            assert not up.restore_backup(
                backup, "sha256:" + "1" * 64, "1.0.0-beta.8.6.1")
        for mount in mounts:
            source = Path(mount["source"])
            assert (source / "sentinel.txt").read_text() == "pre-restore\n"
            assert (source / "new-file").read_text() == "retain on failed restore\n"

        for source, content in expected.items():
            path = Path(source)
            (path / "sentinel.txt").write_bytes(content)
            (path / "new-file").unlink()

        # Corrupt the last archive: no earlier volume may be erased.
        last_archive = backup / f"volume-{len(mounts)-1}.tar.gz"
        last_archive.write_bytes(b"invalid archive")
        with mock.patch.object(up, "run", return_value=completed()) as docker:
            assert not up.restore_backup(backup, "sha256:" + "1" * 64, "1.0.0-beta.8.6.1")
            docker.assert_not_called()
        for source, content in expected.items():
            assert (Path(source) / "sentinel.txt").read_bytes() == content

        # Provisioning/lifecycle operations must not overlap host replacement.
        writer_lock = up.GATE.parent / "allscan-reimagined-bridge-setup.lock"
        with writer_lock.open("a+") as writer:
            up.fcntl.flock(writer, up.fcntl.LOCK_EX | up.fcntl.LOCK_NB)
            try:
                with up.maintenance_writer_guard():
                    raise AssertionError("concurrent provisioning was not excluded")
            except up.UpdateError:
                pass
        with up.maintenance_writer_guard():
            pass

        # Interrupted jobs are recovered from the durable phase, not inferred
        # from a browser-visible status. Failure remains retryable and blocks queue.
        job = restart_failed["jobId"]
        assert up.active_jobs()
        with mock.patch.object(up, "profile", return_value=SimpleNamespace(web_container="asr-web")), mock.patch.object(
                up, "inspect", return_value={"Image": meta["imageId"]}), mock.patch.object(
                up, "run", return_value=completed()), mock.patch.object(up, "healthy", return_value=True):
            assert up.recover()["status"] == "restored"
            up.run.assert_called_once_with(["/usr/bin/docker", "start", "asr-web"], timeout=120)
        assert not up.active_jobs()
        assert up.recover()["status"] == "nothing_to_recover"
        early_job = "20261124-120010-aabbccdd"
        up.status(early_job, "building")
        assert up.recover()["status"] == "not_needed"
        queued_job = "20261124-120011-aabbccdd"
        up.status(queued_job, "queued")
        with mock.patch.object(up, "run", return_value=completed(stdout="ActiveState=activating\nJob=123\n")):
            try:
                up.recover()
            except up.UpdateError:
                pass
            else:
                raise AssertionError("active queued job was canceled")
        with mock.patch.object(up, "run", return_value=completed(stdout="ActiveState=inactive\nJob=\n")):
            assert up.recover()["status"] == "not_needed"
        assert not up.active_jobs()

        # Older state without a journal must not be guessed safe or cleared.
        legacy_job = "20261124-120012-aabbccdd"
        legacy_status = up.status(legacy_job, "installing")
        legacy_status.pop("transactionSchema")
        (up.STATE / f"{legacy_job}.json").write_text(json.dumps(legacy_status))
        try:
            up.recover()
        except up.UpdateError:
            assert up.active_jobs()
        else:
            raise AssertionError("unverified interrupted legacy update accepted")
        up.status(legacy_job, "failed", manualInterventionRequired=False)
        # Root-only recovery command is not a broker capability.
        try:
            broker.read_request(__import__("io").BytesIO(b'{"schema":1,"update":"recover-interrupted"}'))
        except broker.BrokerError:
            pass
        else:
            raise AssertionError("root-only recovery was exposed to the broker")

        # The broker rejects an otherwise valid update request from any UID
        # other than the installed web identity.
        request = broker.read_request(__import__("io").BytesIO(
            b'{"schema":1,"update":"queue"}'))
        helper = root / "asr-container-updater.py"
        helper.write_text("#!/bin/sh\n")
        helper.chmod(0o755)
        old = broker.load_backend, broker.peer_uid, broker.UPDATER
        broker.load_backend = lambda: SimpleNamespace(
            load_profile=lambda _path: SimpleNamespace(client_uid=33),
            validate_active_profile=lambda _profile: None)
        broker.peer_uid = lambda: 34
        broker.UPDATER = helper
        try:
            try:
                broker.execute(request, root / "profile")
            except broker.BrokerError as exc:
                assert "not authorized" in str(exc)
            else:
                raise AssertionError("unauthorized container update was accepted")
        finally:
            broker.load_backend, broker.peer_uid, broker.UPDATER = old

    api = (ROOT / "server/asr-api.php").read_text()
    ui = (ROOT / "src/components/UpdateAsrDialog.tsx").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "asr_require_admin();" in api and "asr_require_same_origin();" in api
    assert "ASR updates are managed outside this container." in api
    assert "hasUpdate && !externallyManaged" in ui
    assert "ASR_IMAGE" in compose and "ASR_UPDATE_MODE" in compose
    print("container updater self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
