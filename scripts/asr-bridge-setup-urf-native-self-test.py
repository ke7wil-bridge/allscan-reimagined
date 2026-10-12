#!/usr/bin/env python3
"""Focused offline regression tests for native and container URF runtimes."""
from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


planner = load("asr_urf_native_test_plan", "asr-bridge-setup-urf.py")
installer = load("asr_urf_native_test_install", "asr-bridge-setup-urf-install.py")


def put(root: Path, logical: str, content: str) -> Path:
    path = root / logical.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def snapshot(root: Path) -> dict[str, bytes]:
    return {item.relative_to(root).as_posix(): item.read_bytes()
            for item in root.rglob("*") if item.is_file()
            and "/backups/" not in f"/{item.relative_to(root).as_posix()}"}


def fixture(root: Path):
    put(root, "/etc/os-release", "ID=debian\nVERSION_ID=12\n")
    put(root, planner.CONFIG, json.dumps({
        "node": "1999",
        "netBridgeMode": "dmr",
        "bridges": [{
            "id": "dmr_net", "mode": "dmr", "node": "1999",
            "cardType": "dmr_net",
        }],
    }) + "\n")
    put(root, planner.RPT, (
        "[nodes]\n1999 = radio@127.0.0.1/1999,NONE\n"
        "; ASR BRIDGE SETUP BEGIN dmr_net\n"
        "[1999] ; DMR/URF Bridge (dmr_net)\n"
        "rxchannel = USRP/127.0.0.1:52001:52000\n"
        "; ASR BRIDGE SETUP END dmr_net\n"
    ))
    put(root, planner.MODULES, "load = app_rpt.so\n")
    return planner.UrfSettings("qa_dmr", "NODE1999", 1234567, 9990,
                               "URF199", "QA DMR", 1002, "standard")


def rejected(function, *args, contains: str = "", **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except Exception as exc:
        assert not contains or contains in str(exc), exc
        return
    raise AssertionError("invalid operation was accepted")


def main() -> None:
    native_facts = SimpleNamespace(systemd=True, docker=True, docker_compose=True)
    # Docker being installed must not affect native selection/requirements.
    planner.validate_runtime_requirements(
        native_facts, False, True, {"ID": "debian", "VERSION_ID": "12"}, "x86_64")
    rejected(planner.validate_runtime_requirements,
             SimpleNamespace(systemd=True, docker=False, docker_compose=False),
             True, True, {"ID": "debian", "VERSION_ID": "12"}, "x86_64",
             contains="requires Docker")

    with tempfile.TemporaryDirectory(prefix="asr-native-urf-test-") as temporary:
        root = Path(temporary)
        settings = fixture(root)
        plan = planner.plan(root, settings)
        assert plan["backend"] == "native" and not plan["images"]
        assert planner.tgif_network_id(3224939, 1001, 50) == 322493950
        test_settings = planner.UrfSettings(
            "dmr_tgif", "KE7WIL", 3224939, 86753, "URFWIL", "TGIF DMR", 1001,
            "standard", 50)
        test_plan = planner.plan(root, test_settings)
        assert test_plan["settings"]["dmr_id"] == 3224939
        assert test_plan["settings"]["tgif_essid"] == 50
        assert test_plan["dmrNetworkId"] == 322493950
        assert b"Environment=DMR_NETWORK_ID=322493950" in installer.native_units(test_plan)[
            "/etc/systemd/system/allscan-reimagined-tgif-dmr_tgif.service"][0]
        assert len(plan["services"]) == 3
        provenance = plan["runtimeRequirements"]["provenance"]
        assert provenance["architecture"] == installer.native_sources.architecture()
        if provenance["architecture"] != "armhf":
            assert "gcc-arm-linux-gnueabihf" in provenance["packages"]
        assert provenance["sources"]["urfd"]["commit"] == installer.native_sources.URFD[1]
        assert "FilePath = /data/" not in planner.urfd_ini(plan)
        outputs = installer.file_spec(root, plan, "never-log-this-secret")
        assert b"ASR BRIDGE SETUP BEGIN dmr_net" in outputs[planner.RPT][0]
        secret = plan["resources"]["tgifSecret"]
        assert outputs[secret][1] == 0o640
        assert all(b"never-log-this-secret" not in data
                   for path, (data, _mode) in outputs.items() if path != secret)
        units = {path: data.decode() for path, (data, _mode) in outputs.items()
                 if path.startswith("/etc/systemd/system/")}
        assert len(units) == 4
        component_units = [text for text in units.values() if "User=asr-bridge" in text]
        assert len(component_units) == 3
        assert all("ProtectSystem=strict" in text and "NoNewPrivileges=true" in text
                   and "CapabilityBoundingSet=" in text for text in component_units)
        assert not any("docker" in text.lower() for text in units.values())

        original = snapshot(root)
        rejected(installer.install, root, settings, "never-log-this-secret",
                 expected_digest="0" * 64, contains="stale")
        assert snapshot(root) == original
        rejected(installer.install, root, settings, "never-log-this-secret",
                 expected_digest=plan["digest"], fail_after=4, contains="injected")
        assert not (root / plan["resources"]["root"].lstrip("/")).exists()
        for logical in installer.file_spec(root, plan, "never-log-this-secret"):
            assert not (root / logical.lstrip("/")).exists() or logical in {
                planner.CONFIG, planner.RPT, planner.MODULES}
        after_failure = snapshot(root)
        assert {key: value for key, value in after_failure.items()
                if not key.startswith("var/lib/")} == original

        plan = planner.plan(root, settings)
        first_result = installer.install(
            root, settings, "never-log-this-secret", expected_digest=plan["digest"])
        assert first_result["changedFiles"] > 0
        secret_path = root / secret.lstrip("/")
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o640
        assert "never-log-this-secret" not in json.dumps(first_result)
        first = snapshot(root)
        plan = planner.plan(root, settings)
        second = installer.install(
            root, settings, "never-log-this-secret", expected_digest=plan["digest"])
        assert second["changedFiles"] == 0 and snapshot(root) == first

        container_plan = dict(plan)
        container_plan["backend"] = "container"
        container_plan["services"] = [installer.service_name(settings.bridge_id)]
        container_plan["images"] = {
            "urf": "allscan-reimagined/urf:managed",
            "tcd": "allscan-reimagined/urf-tcd:managed",
        }
        container_outputs = installer.file_spec(root, container_plan, "container-secret")
        compose = container_outputs[plan["resources"]["compose"]][0].decode()
        wrapper = container_outputs[installer.unit_path(settings.bridge_id)][0].decode()
        assert "docker compose" in wrapper and "image: allscan-reimagined/urf:managed" in compose
        assert container_outputs[secret][1] == 0o600
        assert all(b"container-secret" not in data
                   for path, (data, _mode) in container_outputs.items() if path != secret)

    with tempfile.TemporaryDirectory(prefix="asr-native-paths-test-") as temporary:
        root = Path(temporary)
        (root / "secret").write_text("not-a-real-secret\n", encoding="utf-8")
        path_plan = {"resources": {"data": "/data", "tgifRun": "/run", "tgifSecret": "/secret"}}
        with mock.patch.object(installer, "rooted", side_effect=lambda _root, logical: root / logical.lstrip("/")), \
                mock.patch.object(installer.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1001, pw_gid=1001)), \
                mock.patch.object(installer.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=33)), \
                mock.patch.object(installer.os, "chown") as chown:
            installer.ensure_native_runtime_paths(path_plan)
        assert chown.call_args_list[:2] == [mock.call(root / "data", 1001, 33), mock.call(root / "run", 1001, 1001)]
        assert stat.S_IMODE((root / "data").stat().st_mode) == 0o750
        assert stat.S_IMODE((root / "run").stat().st_mode) == 0o750

    with tempfile.TemporaryDirectory(prefix="asr-native-startup-test-") as temporary:
        run = Path(temporary)
        for name in ("adapter.pid", "mmdvm.pid"):
            put(run, name, "123\n")
        (run / "tgif-health").touch()
        put(run, "urf-identity.json", json.dumps({"state": "rejected"}))

        def database_becomes_ready(_interval):
            put(run, "urf-identity.json", json.dumps({"state": "authenticated"}))
            (run / "urf-health").touch()

        with mock.patch.object(installer.os, "kill"), \
                mock.patch.object(installer.time, "sleep", side_effect=database_becomes_ready) as sleeper:
            ready, error, state = installer.poll_native_runtime(run, attempts=3, interval=0)
        assert ready and not error and state == "authenticated" and sleeper.call_count == 1

        (run / "urf-health").unlink()
        put(run, "urf-identity.json", json.dumps({"state": "rejected"}))
        with mock.patch.object(installer.os, "kill"), mock.patch.object(installer.time, "sleep"):
            ready, error, state = installer.poll_native_runtime(run, attempts=3, interval=0)
        assert not ready and state == "rejected" and error
        assert str(installer.native_runtime_failure(state, "", error)) == (
            "URFD rejected the configured DMR identity; verify the DMR ID and callsign")

        (run / "urf-health").touch()
        put(run, "urf-identity.json", json.dumps({"state": "authenticated"}))
        with mock.patch.object(installer.os, "kill"), \
                mock.patch.object(installer.time, "sleep") as sleeper:
            ready, error, state = installer.poll_native_runtime(run, attempts=3, interval=0)
        assert ready and not error and state == "authenticated" and not sleeper.called

    affected = ("wrapper.service", "urfd.service", "tcd.service", "tgif.service")
    with mock.patch.object(installer.subprocess, "run") as systemctl:
        installer.reload_and_reset_failed(affected)
    commands = [call.args[0] for call in systemctl.call_args_list]
    assert commands[0] == ["systemctl", "daemon-reload"]
    assert commands[1:] == [["systemctl", "reset-failed", unit] for unit in affected]
    assert all("unrelated.service" not in command for command in commands)

    source = (HERE / "asr-bridge-urf-native-runtime.py").read_text(encoding="utf-8")
    for revision in (planner.load("native_source_constants", "asr-bridge-urf-native-runtime.py").URFD[1],
                     "ff104384b3d1c51bf8f7f9f83d7940dffda0b076",
                     "03423cb00b8e9ba1be164493e8b98b5bd7c1508c",
                     "0ebd2761d441db354f859280873d4286856f3e73"):
        assert revision in source
    print("native/container URF runtime self-test: PASS")


if __name__ == "__main__":
    main()
