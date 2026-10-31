#!/usr/bin/env python3
"""Security and routing regression tests for container-host provisioning.

The test is deliberately host-independent: Docker inspection and privileged
commands are represented by exact in-memory responses, while filesystem and
Unix-socket checks use a disposable tree.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import platform
import socket
import stat
import subprocess
import sys
import tempfile
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


def rejected(call, *args, **kwargs) -> None:
    try:
        call(*args, **kwargs)
    except (RuntimeError, SystemExit, TypeError, ValueError):
        return
    raise AssertionError(f"unsafe input was accepted by {call.__name__}")


def test_detection(detect) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-detect-") as temporary:
        root = Path(temporary)
        assert detect.detect(root)["backend"] == "unsupported"

        (root / "etc/asterisk").mkdir(parents=True)
        (root / "etc/asterisk/rpt.conf").write_text("[nodes]\n")
        (root / "run/systemd/system").mkdir(parents=True)
        (root / "usr/sbin").mkdir(parents=True)
        (root / "usr/sbin/asterisk").write_text("")
        result = detect.detect(root)
        assert result["supported"] is True and result["backend"] == "native-asl3"

        (root / ".dockerenv").touch()
        result = detect.detect(root)
        assert result["supported"] is False and result["backend"] == "unsupported"
        broker = root / "run/allscan-reimagined-host/bridge-setup.sock"
        broker.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(broker))
            result = detect.detect(root)
            assert result["supported"] is True and result["backend"] == "container-client"
        finally:
            listener.close()

    with tempfile.TemporaryDirectory(prefix="asr-cgroup-") as temporary:
        root = Path(temporary)
        (root / "proc/1").mkdir(parents=True)
        (root / "proc/1/cgroup").write_text("0::/docker/deadbeef\n")
        assert detect.in_container(root)


def profile_value(ast: Path, asr: Path, proc: Path, runtime: Path | None = None, **changes):
    value = {
        "schema": 1,
        "backend": "container-host",
        "asteriskContainer": "allstarlink3",
        "asteriskConfig": str(ast),
        "asrConfig": str(asr),
        "clientUid": 33,
        "clientGid": 33,
        "hostProc": str(proc),
        "runtimeHostRoot": str(runtime or (ast.parent / "runtime")),
    }
    value.update(changes)
    return value


def test_profile_and_mapping(backend) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-profile-") as temporary:
        root = Path(temporary)
        ast, asr, proc, runtime = root / "asterisk", root / "asr", root / "proc", root / "runtime"
        ast.mkdir(); asr.mkdir(); runtime.mkdir(); (proc / "self").mkdir(parents=True)
        (proc / "self/status").write_text("Name:\tself\n")
        profile = backend.ContainerProfile.parse(profile_value(ast, asr, proc))
        assert profile.asterisk_config == ast.resolve()
        assert profile.asr_config == asr.resolve()

        original = backend.load_profile
        backend.load_profile = lambda path=None: profile
        try:
            assert backend.map_path(Path("/"), "/etc/asterisk/rpt.conf") == ast / "rpt.conf"
            assert backend.map_path(Path("/"), "/etc/allscan-reimagined/bridges.json") == asr / "bridges.json"
            assert backend.map_path(root, "/etc/asterisk/rpt.conf") == root / "etc/asterisk/rpt.conf"
            rejected(backend.map_path, Path("/"), "/etc/asterisk/../shadow")
            rejected(backend.map_path, Path("/"), "etc/asterisk/rpt.conf")
            outside = root / "outside"; outside.mkdir()
            (ast / "redirect").symlink_to(outside, target_is_directory=True)
            rejected(backend.map_path, Path("/"), "/etc/asterisk/redirect/owned.conf")
        finally:
            backend.load_profile = original

        bad = [
            {"schema": 2}, {"backend": "native"},
            {"asteriskContainer": "allstarlink3;id"},
            {"asteriskContainer": "../allstarlink3"},
            {"asteriskConfig": "relative/path"},
            {"asteriskConfig": str(ast / ".." / "escape")},
            {"asrConfig": str(ast)}, {"clientUid": 0}, {"clientUid": 65536},
            {"clientGid": 0}, {"clientGid": 65536}, {"clientGid": "33"},
            {"clientUid": "33"}, {"hostProc": "proc"},
            {"runtimeHostRoot": "runtime"}, {"runtimeHostRoot": "/"},
        ]
        for change in bad:
            rejected(backend.ContainerProfile.parse, profile_value(ast, asr, proc, **change))
        extra = profile_value(ast, asr, proc); extra["unexpected"] = True
        rejected(backend.ContainerProfile.parse, extra)

        link = root / "asterisk-link"
        link.symlink_to(ast, target_is_directory=True)
        rejected(backend.ContainerProfile.parse, profile_value(link, asr, proc))
        missing = root / "missing"
        rejected(backend.ContainerProfile.parse, profile_value(missing, asr, proc))
        rejected(backend.mounted_source, {"Mounts": [{
            "Destination": "/etc/asterisk", "Source": "/",
            "RW": True, "Type": "bind",
        }]}, "/etc/asterisk")

        # An unprivileged process cannot create the root-owned immutable profile
        # required by load_profile; it must fail closed rather than trusting it.
        profile_path = root / "profile.json"
        profile_path.write_text(json.dumps(profile_value(ast, asr, proc)))
        if profile_path.stat().st_uid != 0:
            rejected(backend.load_profile, profile_path)


def test_discovery(backend) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-discover-") as temporary:
        root = Path(temporary)
        ast, asr, proc, runtime = root / "asterisk", root / "asr", root / "proc", root / "runtime"
        ast.mkdir(); asr.mkdir(); runtime.mkdir(); (proc / "self").mkdir(parents=True)
        (ast / "rpt.conf").write_text("[nodes]\n")
        (ast / "modules.conf").write_text("[modules]\n")
        (asr / "config.json").write_text('{"node":"641890","bridges":[]}\n')
        (proc / "self/status").write_text("ok\n")
        (ast / "rpt.conf").write_text("[nodes]\n")
        (ast / "modules.conf").write_text("[modules]\n")
        (asr / "config.json").write_text("{\"bridges\":[]}\n")

        def row(destination: str, source: Path, *, running=True, rw=True):
            return {"State": {"Running": running}, "Mounts": [{
                "Destination": destination, "Source": str(source),
                "RW": rw, "Type": "bind",
            }]}

        rows = {"web": row("/etc/allscan-reimagined", asr),
                "asterisk": row("/etc/asterisk", ast)}

        def runner(argv, **_kwargs):
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps([rows[argv[-1]]]), "")

        profile = backend.discover("web", "asterisk", runner, client_uid=33,
                                   client_gid=33, host_proc=proc, runtime_host_root=runtime)
        assert profile.asterisk_config == ast.resolve()
        assert profile.asr_config == asr.resolve()
        assert profile.asterisk_container == "asterisk"
        rejected(backend.discover, "same", "same", runner, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)
        rejected(backend.discover, "web;id", "asterisk", runner, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)

        rows["asterisk"] = row("/etc/asterisk", ast, running=False)
        rejected(backend.discover, "web", "asterisk", runner, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)
        rows["asterisk"] = row("/etc/asterisk", ast, rw=False)
        rejected(backend.discover, "web", "asterisk", runner, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)
        rows["asterisk"] = row("/etc/asterisk", ast)
        rows["web"] = row("/etc/allscan-reimagined", ast)
        rejected(backend.discover, "web", "asterisk", runner, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)
        rows["web"] = row("/etc/allscan-reimagined", asr)

        def ambiguous(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        rejected(backend.discover, "web", "asterisk", ambiguous, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)

        def broken(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "denied")
        rejected(backend.discover, "web", "asterisk", broken, client_uid=33,
                 client_gid=33, host_proc=proc, runtime_host_root=runtime)


def test_active_profile(backend) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-active-") as temporary:
        root = Path(temporary)
        ast, asr, proc, runtime = root / "asterisk", root / "asr", root / "proc", root / "runtime"
        ast.mkdir(); asr.mkdir(); runtime.mkdir(); (proc / "self").mkdir(parents=True)
        (ast / "rpt.conf").write_text("[nodes]\n")
        (ast / "modules.conf").write_text("[modules]\n")
        (asr / "config.json").write_text('{"node":"641890","bridges":[]}\n')
        (proc / "self/status").write_text("ok\n")
        profile = backend.ContainerProfile.parse(profile_value(
            ast, asr, proc, runtime, asteriskContainer="asterisk"))
        rows = {
            "asterisk": {"State": {"Running": True}, "Mounts": [{
                "Destination": "/etc/asterisk", "Source": str(ast), "RW": True, "Type": "bind"}]},
            "web": {"State": {"Running": True}, "Mounts": [{
                "Destination": "/etc/allscan-reimagined", "Source": str(asr), "RW": True, "Type": "bind"}]},
        }

        def runner(argv, **_kwargs):
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "ps":
                return subprocess.CompletedProcess(argv, 0, "web\n", "")
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps([rows[argv[-1]]]), "")

        backend.validate_active_profile(profile, runner)
        rows["asterisk"]["State"]["Running"] = False
        rejected(backend.validate_active_profile, profile, runner)
        rows["asterisk"]["State"]["Running"] = True

        def duplicate(argv, **_kwargs):
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "ps":
                return subprocess.CompletedProcess(argv, 0, "web\nweb2\n", "")
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            name = "web" if argv[-1] == "web2" else argv[-1]
            return subprocess.CompletedProcess(argv, 0, json.dumps([rows[name]]), "")
        rejected(backend.validate_active_profile, profile, duplicate)

        def missing(argv, **_kwargs):
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "ps":
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "exec":
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps([rows[argv[-1]]]), "")
        rejected(backend.validate_active_profile, profile, missing)


def request(broker, value):
    return broker.read_request(io.BytesIO(json.dumps(value).encode()))


def test_broker(broker) -> None:
    setup = request(broker, {"schema": 1, "command": "dmr-plan", "payload": {"bridgeId": "qa_dmr"}})
    assert setup["kind"] == "setup"
    unified_setup = request(broker, {"schema": 1, "command": "net-bridge-plan", "payload": {"modes": {}}})
    assert unified_setup["kind"] == "setup"
    unified_reset = request(broker, {
        "schema": 1, "command": "net-bridge-reset",
        "payload": {"confirmation": "RESET NET BRIDGE"},
    })
    assert unified_reset["kind"] == "setup"
    profile = SimpleNamespace(asterisk_container="asterisk", runtime_host_root=Path("/runtime"))
    environment = broker.child_environment(Path("/profile.json"), profile)
    assert environment["HOME"] == "/var/cache/allscan-reimagined/docker-home"
    assert environment["DOCKER_CONFIG"] == "/var/cache/allscan-reimagined/docker-config"
    assert environment["BUILDX_CONFIG"] == "/var/cache/allscan-reimagined/buildx"
    assert "/root" not in "\n".join(environment.values())
    valid_controls = (
        ("asterisk-read", ["-rx", "rpt lstats 641890"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--connect", "11111"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--disconnect"]),
        ("ysf", ["--connect", "qa_ysf", "12345", "--user", "N0CALL"]),
        ("ysf", ["--import-hosts", "qa_ysf"]),
        ("p25", ["connect", "qa_p25", "10200", "--user", "N0CALL"]),
        ("nxdn", ["status", "qa_nxdn"]),
        ("m17", ["--bridge", "qa_m17", "--user", "N0CALL", "connect", "--reflector", "M17-M17", "--module", "C"]),
        ("m17", ["catalog"]),
        ("net-mode", ["--mode", "dmr"]),
    )
    for program, args in valid_controls:
        if program == "ysf" and args[0] == "--import-hosts":
            continue
        result = request(broker, {"schema": 1, "program": program, "args": args})
        assert result["kind"] == "control"
    imported = request(broker, {
        "schema": 1, "program": "ysf", "args": ["--import-hosts", "qa_ysf"],
        "stdinBase64": "MTIzNDU7VVMtVEVTVDtUZXN0O2V4YW1wbGUubmV0OzQyMDAwOzAwMDtcblxu",
    })
    assert imported["kind"] == "control" and imported["controlStdin"].startswith(b"12345;")

    installer_source = (HERE / "asr-container-host-install.py").read_text(encoding="utf-8")
    assert "--proc-root {profile.host_proc} --user {SERVICE_USER}" in installer_source
    assert "It immediately setuid/setgid drops to" in installer_source
    broker_source = (HERE / "asr-container-host-broker.py").read_text(encoding="utf-8")
    assert 'request["program"] in {"p25", "nxdn"}' in broker_source
    assert '"--proc-root", str(profile.host_proc)' in broker_source
    assert 'environment["ASR_CONTAINER_WATCH_CONFIG"]' in broker_source
    # YSF catalog uploads arrive as bytes. Keep subprocess in binary mode for
    # that path, then normalize its output before building the JSON response.
    assert "text_mode = isinstance(input_data, str)" in broker_source
    assert "text=text_mode" in broker_source
    assert 'result.stdout.decode("utf-8", "replace")' in broker_source
    assert 'result.stderr.decode("utf-8", "replace")' in broker_source

    layered_source = (HERE.parent / "docker/layered-reapply.sh").read_text(encoding="utf-8")
    assert "asr-container-host-control-client.py" in layered_source
    assert "/usr/local/libexec/allscan-reimagined/asr-container-host-control-client.py" in layered_source
    assert "allscan-reimagined-ysf-bridge-control" in layered_source
    reapply_source = (HERE / "asr-reapply.sh").read_text(encoding="utf-8")
    assert "allscan-reimagined-ysf-bridge-control --import-hosts" in reapply_source
    assert "allscan-reimagined-ysf-bridge-control --catalog-status" in reapply_source

    malformed = [
        None, [], {"schema": 1},
        {"schema": 2, "command": "dmr-plan", "payload": {}},
        {"schema": 1, "command": "../../bin/sh", "payload": {}},
        {"schema": 1, "command": "dmr-plan", "payload": []},
        {"schema": 1, "command": "dmr-plan", "payload": {}, "extra": True},
        {"schema": 1, "program": "../../bin/sh", "args": []},
        {"schema": 1, "program": "managed-dmr", "args": "--status"},
    ]
    for value in malformed:
        rejected(request, broker, value)
    rejected(broker.read_request, io.BytesIO(b"{"))
    rejected(broker.read_request, io.BytesIO(b"x" * (broker.MAX_REQUEST + 1)))
    for program, args in (
        ("asterisk-read", ["-rx", "rpt lstats 641890; id"]),
        ("managed-dmr", ["--bridge", "qa_dmr;id", "--status"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--connect", "11111;id"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--connect", "4000"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--connect", "16777216"]),
        ("managed-dmr", ["--bridge", "qa_dmr", "--status\n/bin/id"]),
        ("ysf", ["--connect", "../qa", "12345", "--user", "N0CALL"]),
        ("p25", ["connect", "qa_p25", "1", "--user", "$(id)"]),
        ("p25", ["connect", "qa_p25", "9999", "--user", "N0CALL"]),
        ("p25", ["connect", "qa_p25", "65535", "--user", "N0CALL"]),
        ("nxdn", ["connect", "qa_nxdn", "10", "--user", "N0CALL"]),
        ("nxdn", ["disconnect", "QA", "--user", "N0CALL"]),
        ("m17", ["--bridge", "qa_m17", "--user", "N0CALL", "connect", "--reflector", "M17-ABC;id", "--module", "A"]),
    ):
        rejected(broker.validate_control, program, args)

    original_backend, original_peer, original_helper = broker.load_backend, broker.peer_uid, broker.HELPER
    with tempfile.TemporaryDirectory(prefix="asr-broker-") as temporary:
        helper = Path(temporary) / "helper"
        helper.write_text("#!/bin/sh\nexit 0\n"); helper.chmod(0o755)
        broker.HELPER = helper
        broker.load_backend = lambda: SimpleNamespace(
            load_profile=lambda _path: SimpleNamespace(
                client_uid=33, asterisk_container="asterisk", runtime_host_root=Path(temporary)),
            validate_active_profile=lambda _profile: None,
        )
        broker.peer_uid = lambda: 34
        try:
            rejected(broker.execute, setup, Path(temporary) / "profile")
            broker.peer_uid = lambda: 33
            helper.chmod(0o777)
            rejected(broker.execute, setup, Path(temporary) / "profile")
        finally:
            broker.load_backend, broker.peer_uid, broker.HELPER = original_backend, original_peer, original_helper


def test_asterisk_adapter(adapter) -> None:
    for args in (
        ["-rx", "rpt stats 1001"],
        ["-rx", "rpt lstats 123456"],
        ["-rx", "rpt show channels 1999"],
        ["-rx", "rpt cmd 123456 ilink 3 1001"],
        ["-rx", "rpt cmd 123456 ilink 11 1001"],
        ["-rx", "module show like chan_usrp.so"],
    ):
        assert adapter.validate_command(args)
    for args in (
        [], ["-r"], ["-rx", "core stop now"],
        ["-rx", "rpt cmd 1 ilink 3 1001"],
        ["-rx", "rpt cmd 123456 ilink 3 1001;id"],
        ["-rx", "rpt lstats 1001\ncore stop now"],
        ["-rx", "rpt cmd 123456 ilink 4 1001"],
        ["-rx", "channel request hangup usrp/127.0.0.1:31001:31002"],
        ["-rx", "rpt stats 1001", "extra"],
    ):
        rejected(adapter.validate_command, args)
    profile = SimpleNamespace(asr_config=Path("/state/asr"))
    records = Path("/state/installations")
    values = {
        Path("/state/asr/config.json"): {
            "node": 123456,
            "bridges": [{"id": "qa_m17", "node": "1001"},
                        {"id": "unowned", "node": "1002"},
                        {"id": "qa_dmr", "node": "1003", "cardType": "dmr_net",
                         "backendMode": "managed", "managedNetControl": True,
                         "setupPorts": {"usrp_rx": 31001, "usrp_tx": 31002},
                         "managedTargetFile": "/opt/allscan-reimagined-bridges/urf/qa_dmr/tgif-run/net-target"}],
        },
        records / "qa_m17.json": {"bridgeId": "qa_m17", "bridgeNode": 1001},
    }
    loader = lambda path: values[path]
    for command in ("module show like chan_usrp.so", "rpt stats 123456",
                    "rpt lstats 123456", "rpt cmd 123456 ilink 3 1001",
                    "rpt cmd 123456 ilink 11 1001",
                    "rpt cmd 1001 ilink 3 123456"):
        adapter.authorize_command(command, profile, loader, records)
    for command in ("rpt stats 1001", "rpt lstats 999999",
                    "rpt cmd 123456 ilink 3 1002",
                    "rpt cmd 123456 ilink 3 9999"):
        rejected(adapter.authorize_command, command, profile, loader, records)
    adapter.authorize_command("rpt stats 1003", profile, loader, records,
                              allow_pending=True)
    adapter.authorize_command("rpt show channels 1003", profile, loader, records,
                              allow_pending=True)
    rejected(adapter.authorize_command, "rpt stats 1003", profile, loader, records)
    values[Path("/state/asr/config.json")]["bridges"][-1]["managedTargetFile"] = "/tmp/net-target"
    rejected(adapter.authorize_command, "rpt stats 1003", profile, loader, records,
             allow_pending=True)
    values[Path("/state/asr/config.json")] = {
        "node": 123456, "netBridgeMode": "m17",
        "bridges": [
            {"id": "qa_m17", "node": "1999", "mode": "m17", "cardType": "m17_net"},
            {"id": "qa_p25", "node": "1999", "mode": "p25", "cardType": "p25_net"},
        ],
    }
    adapter.authorize_command("rpt cmd 123456 ilink 3 1999", profile, loader, records)
    values[Path("/state/asr/config.json")]["netBridgeMode"] = "p25"
    rejected(adapter.authorize_command, "rpt cmd 123456 ilink 3 1999", profile, loader, records)


def test_payload_validators(digital, urf) -> None:
    good = digital.DigitalSettings("p25", "qa_p25", "N0CALL", 1234567, 10200,
                                   "reflector.example", 41000, "P25", 1002, "net")
    assert digital.validate(good).bridge_id == "qa_p25"
    for change in (
        {"bridge_id": "../p25"}, {"bridge_id": "qa;id"},
        {"destination": 1}, {"destination": 9999},
        {"reflector_host": "host;id"}, {"reflector_host": "/tmp/socket"},
        {"reflector_port": 0}, {"bridge_node": 1000}, {"bridge_node": 2000},
        {"bridge_role": "container"}, {"title": "bad\nunit"},
    ):
        rejected(digital.validate, digital.DigitalSettings(**{**good.__dict__, **change}))
    dmr = urf.UrfSettings("qa_dmr", "N0CALL", 1234567, 11111,
                          "URFASR", "DMR", 1002, "net")
    assert urf.validate(dmr).bridge_id == "qa_dmr"
    assert urf.tgif_network_id(1234567, 1004) == 123456704
    assert urf.tgif_network_id(1234567, 1100) == 123456799
    for change in (
        {"bridge_id": "qa_dmr.service"}, {"bridge_id": "qa_dmr;id"},
        {"dmr_id": 0}, {"tgif_tg": 0}, {"tgif_tg": 16_777_216},
        {"reflector": "URF;ID"}, {"bridge_node": 2000},
        {"bridge_role": "docker"}, {"title": "bad\nunit"},
    ):
        rejected(urf.validate, urf.UrfSettings(**{**dmr.__dict__, **change}))


def test_urf_port_folding(urf) -> None:
    """Every CRC bucket remains isolated and within URFD's port ceiling."""
    original = urf.zlib.crc32
    blocks: list[tuple[int, ...]] = []
    try:
        for bucket in range(900):
            urf.zlib.crc32 = lambda _value, bucket=bucket: bucket
            ports = urf.port_block("qa_dmr")
            block = tuple(ports.values())
            assert len(block) == 18 and len(set(block)) == 18
            assert ports["transcoder"] <= 45000
            assert all(1024 <= port <= 65535 for port in block)
            blocks.append(block)
    finally:
        urf.zlib.crc32 = original
    assert len(set(blocks)) == 900
    assert blocks[749][0] == 44980 and blocks[749][2] == 44982
    assert blocks[750][0] == 27000 and blocks[750][2] == 27002
    assert blocks[899][0] == 29980 and blocks[899][2] == 29982


def test_urf_docker_environment(installer) -> None:
    original_profile = os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE")
    original_docker = os.environ.get("DOCKER_CONFIG")
    try:
        os.environ.pop("ASR_CONTAINER_PROVISIONING_PROFILE", None)
        os.environ["DOCKER_CONFIG"] = "/native/docker"
        assert installer.docker_environment()["DOCKER_CONFIG"] == "/native/docker"
        os.environ["ASR_CONTAINER_PROVISIONING_PROFILE"] = "/profile.json"
        environment = installer.docker_environment()
        assert environment["HOME"] == "/var/cache/allscan-reimagined/docker-home"
        assert environment["DOCKER_CONFIG"] == "/var/cache/allscan-reimagined/docker-config"
        assert environment["BUILDX_CONFIG"] == "/var/cache/allscan-reimagined/buildx"
        rejected(installer.docker_run, ["sh", "-c", "docker version"])
    finally:
        if original_profile is None:
            os.environ.pop("ASR_CONTAINER_PROVISIONING_PROFILE", None)
        else:
            os.environ["ASR_CONTAINER_PROVISIONING_PROFILE"] = original_profile
        if original_docker is None:
            os.environ.pop("DOCKER_CONFIG", None)
        else:
            os.environ["DOCKER_CONFIG"] = original_docker


def test_managed_urf_image_repair_selection(host_installer) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-image-repair-") as temporary:
        config_root = Path(temporary)
        profile = SimpleNamespace(asr_config=config_root)
        (config_root / "config.json").write_text(json.dumps({"bridges": []}))
        assert host_installer.managed_urf_images_required(profile) is False
        (config_root / "config.json").write_text(json.dumps({"bridges": [{
            "id": "dmr_net", "cardType": "dmr_net", "backendMode": "display_only",
        }]}))
        assert host_installer.managed_urf_images_required(profile) is False
        (config_root / "config.json").write_text(json.dumps({"bridges": [{
            "id": "dmr_net", "cardType": "dmr_net", "backendMode": "managed",
        }]}))
        assert host_installer.managed_urf_images_required(profile) is True


def test_mqtt_secrets_metadata_migration(host_installer) -> None:
    profile = SimpleNamespace(client_uid=33, client_gid=33)
    regular = SimpleNamespace(st_uid=33, st_gid=33, st_mode=stat.S_IFREG | 0o600, st_nlink=1)
    parent = SimpleNamespace(st_uid=0, st_gid=33, st_mode=stat.S_IFDIR | 0o775)
    assert host_installer.mqtt_secrets_metadata_is_safe(regular, parent, profile)
    assert host_installer.mqtt_secrets_metadata_is_safe(
        SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o600, st_nlink=1),
        parent, profile,
    )
    for unsafe_file in (
        SimpleNamespace(st_uid=34, st_gid=33, st_mode=stat.S_IFREG | 0o600, st_nlink=1),
        SimpleNamespace(st_uid=33, st_gid=34, st_mode=stat.S_IFREG | 0o600, st_nlink=1),
        SimpleNamespace(st_uid=33, st_gid=33, st_mode=stat.S_IFREG | 0o640, st_nlink=1),
        SimpleNamespace(st_uid=33, st_gid=33, st_mode=stat.S_IFREG | 0o600, st_nlink=2),
        SimpleNamespace(st_uid=33, st_gid=33, st_mode=stat.S_IFLNK | 0o600, st_nlink=1),
    ):
        assert not host_installer.mqtt_secrets_metadata_is_safe(unsafe_file, parent, profile)
    assert not host_installer.mqtt_secrets_metadata_is_safe(
        regular, SimpleNamespace(st_uid=0, st_gid=33, st_mode=stat.S_IFDIR | 0o777), profile,
    )
    assert not host_installer.mqtt_secrets_metadata_is_safe(
        regular, SimpleNamespace(st_uid=0, st_gid=34, st_mode=stat.S_IFDIR | 0o775), profile,
    )


def test_mosquitto_credential_metadata_migration(host_installer) -> None:
    safe_root = SimpleNamespace(st_uid=0, st_gid=188, st_mode=stat.S_IFREG | 0o640, st_nlink=1)
    safe_daemon = SimpleNamespace(st_uid=188, st_gid=188, st_mode=stat.S_IFREG | 0o640, st_nlink=1)
    assert host_installer.mosquitto_credential_metadata_is_safe(safe_root, 188, 188)
    assert host_installer.mosquitto_credential_metadata_is_safe(safe_daemon, 188, 188)
    for unsafe in (
        SimpleNamespace(st_uid=33, st_gid=188, st_mode=stat.S_IFREG | 0o640, st_nlink=1),
        SimpleNamespace(st_uid=0, st_gid=189, st_mode=stat.S_IFREG | 0o640, st_nlink=1),
        SimpleNamespace(st_uid=0, st_gid=188, st_mode=stat.S_IFREG | 0o644, st_nlink=1),
        SimpleNamespace(st_uid=0, st_gid=188, st_mode=stat.S_IFREG | 0o640, st_nlink=2),
        SimpleNamespace(st_uid=0, st_gid=188, st_mode=stat.S_IFLNK | 0o640, st_nlink=1),
    ):
        assert not host_installer.mosquitto_credential_metadata_is_safe(unsafe, 188, 188)


def test_ysf_net_startup_migration(host_installer) -> None:
    legacy = "[General]\nCallsign=KE7WIL\n\n[Network]\n# Startup=EXAMPLE\nStartup=KE7WIL-ASR\nInactivityTimeout=0\n\n[YSF Network]\nEnable=1\n"
    migrated, changed = host_installer.disconnected_ysf_net_config(legacy)
    assert changed
    assert "# Startup=EXAMPLE\nStartup=\n" in migrated
    assert "Startup=KE7WIL-ASR" not in migrated
    stable, changed = host_installer.disconnected_ysf_net_config(migrated)
    assert not changed and stable == migrated
    for invalid in (
        "[Network]\nInactivityTimeout=0\n",
        "[Network]\nStartup=ONE\nStartup=TWO\n",
        "[General]\nStartup=WRONG-SECTION\n",
    ):
        try:
            host_installer.disconnected_ysf_net_config(invalid)
            raise AssertionError("invalid YSF startup configuration was accepted")
        except RuntimeError:
            pass


def test_managed_dmr_live_mount_migration(host_installer) -> None:
    root = Path("/opt/allscan-reimagined-bridges/urf/qa_dmr")
    legacy = f"""services:
  urfd:
    volumes:
      - {root}/config:/config:ro
      - {root}/data:/data
"""
    migrated, changed = host_installer.managed_dmr_live_mounts(legacy, root)
    assert changed is True
    assert migrated.count(f"      - {root}/data:/run/urf-wil\n") == 1
    stable, changed = host_installer.managed_dmr_live_mounts(migrated, root)
    assert changed is False and stable == migrated
    rejected(host_installer.managed_dmr_live_mounts, "services: {}\n", root)


def test_managed_m17_bind_migration(host_installer) -> None:
    config = {"bridges": [
        {"id": "m17_net", "mode": "m17", "cardType": "m17_net",
         "backendMode": "managed", "m17BindAddress": "127.0.0.1",
         "m17UsrpBindAddress": "127.0.0.1"},
        {"id": "m17_display", "mode": "m17", "backendMode": "display_only",
         "m17BindAddress": "127.0.0.1"},
    ]}
    assert host_installer.managed_m17_external_bind(config) is True
    assert config["bridges"][0]["m17BindAddress"] == "0.0.0.0"
    assert config["bridges"][0]["m17UsrpBindAddress"] == "127.0.0.1"
    assert config["bridges"][1]["m17BindAddress"] == "127.0.0.1"
    assert host_installer.managed_m17_external_bind(config) is False


def test_zello_relay_startup_reset(zello_installer) -> None:
    upstream = '''import asyncio\n\ndef _write_relay_state(active):\n    pass\n\nclass USRPController:\n    def __init__(self, usrp_ptt, zello_ptt):\n        self._transport = None\n\n        self._usrp_ptt = usrp_ptt\n        self._zello_ptt = zello_ptt\n\n    def datagram_received(self, data, addr):\n        ptt = self._frame_ptt_state(data)\n        if not ptt:\n            if self._usrp_ptt.is_set():\n                _write_relay_state(False)\n            self._usrp_ptt.clear()\n            return\n\n        if not self._usrp_ptt.is_set():\n            _write_relay_state(True)\n        self._usrp_ptt.set()\n'''
    with tempfile.TemporaryDirectory(prefix="asr-zello-runtime-test-") as temporary:
        root = Path(temporary)
        source = root / "source"
        (source / "asl_zello_bridge").mkdir(parents=True)
        (source / "asl_zello_bridge/usrp.py").write_text(upstream, encoding="utf-8")
        first = root / "first"
        zello_installer.prepare_runtime_source(source, first)
        patched = (first / "asl_zello_bridge/usrp.py").read_text(encoding="utf-8")
        assert "self._zello_ptt = zello_ptt\n        _write_relay_state(False)" in patched
        assert "def _commit_usrp_off(self):" in patched
        second = root / "second"
        zello_installer.prepare_runtime_source(first, second)
        assert (second / "asl_zello_bridge/usrp.py").read_text(encoding="utf-8") == patched


def test_urf_image_transaction(installer) -> None:
    finals = (
        "allscan-reimagined/urf:managed",
        "allscan-reimagined/urf-tcd:managed",
        "allscan-reimagined/urf-tgif:managed",
    )
    images = {tag: f"sha256:{index:064x}" for index, tag in enumerate(finals, 1)}
    original_ids = dict(images)
    commands = []
    original = (installer.RUNTIME_SOURCE, installer.docker_run,
                installer.sources.binary_artifacts)
    with tempfile.TemporaryDirectory(prefix="asr-urf-images-") as temporary:
        installer.RUNTIME_SOURCE = Path(temporary)
        installer.sources.binary_artifacts = lambda: {
            "MMDVM_Bridge": {"url": "https://example.invalid/mmdvm", "sha256": "a" * 64}
        }

        def runner(argv, **_kwargs):
            commands.append(argv)
            if argv[1:3] == ["image", "inspect"]:
                image = images.get(argv[3], "")
                return subprocess.CompletedProcess(argv, 0 if image else 1,
                                                   image + ("\n" if image else ""),
                                                   "" if image else "No such image")
            if argv[1:3] == ["image", "tag"]:
                if argv[3] not in images:
                    return subprocess.CompletedProcess(argv, 1, "", "No such image: fixture")
                images[argv[4]] = images[argv[3]]
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1:3] == ["image", "rm"]:
                images.pop(argv[3], None)
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "build":
                assert "--load" in argv
                candidate = argv[argv.index("-t") + 1]
                images[candidate] = f"sha256:{(len(commands) + 100):064x}"
                return subprocess.CompletedProcess(argv, 0, "loaded", "")
            raise AssertionError(argv)

        installer.docker_run = runner
        try:
            transaction = installer.build_images()
            assert all(images[tag] != original_ids[tag] for tag in finals)
            assert all(images[state["candidate"]] == state["candidateId"]
                       for state in transaction.values())
            assert all(images[state["backup"]] == state["priorId"]
                       for state in transaction.values())
            assert installer.cleanup_image_transaction(transaction) == []
            assert not any(":asr-build-" in tag or ":asr-previous-" in tag
                           for tag in images)
            build_count = sum(1 for command in commands if command[1] == "build")
            assert installer.ensure_managed_images() is False
            assert sum(1 for command in commands if command[1] == "build") == build_count
            images.pop(finals[1])
            assert installer.ensure_managed_images() is True
            assert all(tag in images for tag in finals)
            assert not any(":asr-build-" in tag or ":asr-previous-" in tag
                           for tag in images)

            promoted = dict(images)
            transaction = installer.build_images()
            installer.restore_images(transaction)
            assert all(images[tag] == promoted[tag] for tag in finals)
            try:
                installer.tag_image("missing:image", finals[0], "fixture promotion")
            except installer.InstallError as exc:
                assert "No such image: fixture" in str(exc)
            else:
                raise AssertionError("Docker tag stderr was not propagated")
        finally:
            (installer.RUNTIME_SOURCE, installer.docker_run,
             installer.sources.binary_artifacts) = original


def put(root: Path, logical: str, content: str) -> None:
    target = root / logical.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_p25_no_emulator_topology(digital, renderer) -> None:
    with tempfile.TemporaryDirectory(prefix="asr-p25-topology-") as temporary:
        root = Path(temporary)
        put(root, "/etc/os-release", "ID=debian\n")
        put(root, digital.CONFIG, json.dumps({"node": "641890", "bridges": []}) + "\n")
        put(root, digital.RPT, "[nodes]\n641890 = radio@127.0.0.1/641890,NONE\n[641890]\n")
        put(root, digital.MODULES, "load=app_rpt.so\n")
        p25 = digital.plan(root, digital.DigitalSettings(
            "p25", "qa_p25", "N0CALL", 1234567, 10200,
            "reflector.example", 41000, "P25", None, "net"))
        assert "emulator" not in p25["resources"]
        assert not any("md380-emu" in service for service in p25["services"])
        assert set(p25["services"]) == {
            "p25gateway-qa_p25.service", "mmdvm-bridge-qa_p25.service",
            "analog-bridge-qa_p25.service", "asr-mqtt-qa_p25.service",
        }
        units = renderer.service_units(p25)
        assert set(units) == {f"/etc/systemd/system/{service}" for service in p25["services"]}
        assert not any("md380-emu" in path or "md380-emu" in text
                       for path, text in units.items())

        nxdn = digital.plan(root, digital.DigitalSettings(
            "nxdn", "qa_nxdn", "N0CALL", 1234567, 10200,
            "reflector.example", 41000, "NXDN", None, "net"))
        assert "emulator" in nxdn["resources"]
        assert "md380-emu-qa_nxdn.service" in nxdn["services"]


class FakeClientSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = b""
        self.connected = ""

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def settimeout(self, _seconds): pass
    def connect(self, path): self.connected = path
    def sendall(self, data): self.sent += data
    def shutdown(self, _how): pass
    def recv(self, _size):
        result, self.response = self.response, b""
        return result


def run_client(module, argv: list[str], response: dict, payload: bytes = b"{}"):
    fake = FakeClientSocket(json.dumps(response).encode())
    stdout, stderr = io.StringIO(), io.StringIO()
    old = (sys.argv, sys.stdin, sys.stdout, sys.stderr,
           module.socket.socket, os.environ.get("ASR_BROKER_TESTING"),
           os.environ.get("ASR_CONTAINER_HOST_SOCKET"), module.os.geteuid)
    try:
        sys.argv = argv
        sys.stdin = SimpleNamespace(buffer=io.BytesIO(payload))
        sys.stdout, sys.stderr = stdout, stderr
        module.socket.socket = lambda *_args, **_kwargs: fake
        module.os.geteuid = lambda: 1000
        os.environ["ASR_BROKER_TESTING"] = "1"
        os.environ["ASR_CONTAINER_HOST_SOCKET"] = "/tmp/asr-test-broker.sock"
        code = module.main()
    finally:
        (sys.argv, sys.stdin, sys.stdout, sys.stderr, module.socket.socket) = old[:5]
        module.os.geteuid = old[7]
        for name, value in (("ASR_BROKER_TESTING", old[5]),
                            ("ASR_CONTAINER_HOST_SOCKET", old[6])):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return code, stdout.getvalue(), stderr.getvalue(), fake


def test_client_error_forwarding(setup_client, control_client) -> None:
    success = {"schema": 1, "exitCode": 0, "stdout": '{"ok":true}\n', "stderr": ""}
    code, stdout, stderr, fake = run_client(
        setup_client, ["asr-container-host-client.py", "net-bridge-reset"], success,
        payload=b'{"confirmation":"RESET NET BRIDGE"}')
    assert code == 0 and stdout == '{"ok":true}\n' and stderr == ""
    sent = json.loads(fake.sent)
    assert sent["command"] == "net-bridge-reset"
    assert sent["payload"] == {"confirmation": "RESET NET BRIDGE"}

    responses = (
        ({"schema": 1, "exitCode": 17, "stdout": "fallback detail\n",
          "stderr": "precise broker detail\n"}, "precise broker detail\n"),
        ({"schema": 1, "exitCode": 18, "stdout": "fallback broker detail\n",
          "stderr": ""}, "fallback broker detail\n"),
    )
    for response, expected in responses:
        code, stdout, stderr, fake = run_client(
            setup_client, ["asr-container-host-client.py", "p25-plan"], response)
        assert code == response["exitCode"] and stdout == "" and stderr == expected
        sent = json.loads(fake.sent)
        assert sent["command"] == "p25-plan" and sent["payload"] == {}

        code, stdout, stderr, fake = run_client(
            control_client,
            ["allscan-reimagined-p25-bridge-control", "status", "qa_p25"], response)
        assert code == response["exitCode"] and stdout == "" and stderr == expected
        sent = json.loads(fake.sent)
        assert sent["program"] == "p25" and sent["args"] == ["status", "qa_p25"]


def test_recovery(recovery) -> None:
    dmr = {"bridgeType": "dmr", "bridgeId": "qa_dmr",
           "service": "allscan-reimagined-urf-tgif-qa_dmr.service"}
    assert recovery.expected_units(dmr) == ["allscan-reimagined-urf-tgif-qa_dmr.service"]
    assert recovery.expected_units({**dmr, "service": "unrelated.service"}) == []
    m17 = {"bridgeType": "m17", "bridgeId": "qa_m17"}
    assert recovery.expected_units(m17) == ["allscan-reimagined-m17-bridge@qa_m17.service"]
    assert recovery.expected_units({**m17, "qualificationRequired": True}) == []
    assert recovery.expected_units({**m17, "qualificationRequired": True,
                                    "service": "allscan-reimagined-m17-bridge@qa_m17.service"}) == []
    digital = {"bridgeType": "p25", "bridgeId": "qa_p25", "services": [
        "asr-mqtt-qa_p25.service", "p25gateway-qa_p25.service", "mmdvm-bridge-qa_p25.service",
        "analog-bridge-qa_p25.service",
    ]}
    assert set(recovery.expected_units(digital)) == set(digital["services"])
    assert recovery.expected_units({**digital, "services": [*digital["services"], "unrelated.service"]}) == []
    for malformed in (
        {}, {"bridgeType": "p25", "bridgeId": "../bad", "services": []},
        {"bridgeType": "p25", "bridgeId": "qa_p25", "services": "bad"},
        {"bridgeType": "unknown", "bridgeId": "qa_test", "services": []},
    ):
        assert recovery.expected_units(malformed) == []
    with tempfile.TemporaryDirectory(prefix="asr-recovery-records-") as temporary:
        installations = Path(temporary)
        records = {
            "qa_dmr": dmr,
            "qa_m17": {**m17, "qualificationRequired": True},
            "qa_p25": digital,
        }
        for bridge_id, record in records.items():
            (installations / f"{bridge_id}.json").write_text(json.dumps(record))
        (installations / "bad;name.json").write_text(json.dumps(dmr))
        original = recovery.INSTALLATIONS
        recovery.INSTALLATIONS = installations
        try:
            units = recovery.owned_units()
        finally:
            recovery.INSTALLATIONS = original
        if os.geteuid() == 0:
            assert "allscan-reimagined-urf-tgif-qa_dmr.service" in units
            assert "allscan-reimagined-m17-bridge@qa_m17.service" not in units
            assert not any("md380-emu-qa_p25" in unit for unit in units)
            assert set(digital["services"]).issubset(units)
        else:
            # Production records are root-owned. A developer-owned fixture must
            # exercise the recovery scanner's ownership rejection instead.
            assert units == []
        asr_config = installations / "asr"
        asr_config.mkdir()
        (asr_config / "config.json").write_text(json.dumps({
            "netBridgeMode": "p25", "bridges": [
                {"id": "qa_dmr", "mode": "dmr", "cardType": "dmr_net"},
                {"id": "qa_p25", "mode": "p25", "cardType": "p25_net"},
            ],
        }))
        original_owned = recovery.owned_units
        recovery.owned_units = lambda: [
            "allscan-reimagined-urf-tgif-qa_dmr.service",
            "p25gateway-qa_p25.service",
        ]
        try:
            assert recovery.recovery_units(SimpleNamespace(asr_config=asr_config)) == [
                "p25gateway-qa_p25.service"
            ]
        finally:
            recovery.owned_units = original_owned


def test_adapter_allowlists(systemctl) -> None:
    allowed = (
        "allscan-reimagined-urf-tgif-qa_dmr.service",
        "allscan-reimagined-m17-bridge@qa_m17.service",
        "p25gateway-qa_p25.service", "nxdngateway-qa_nxdn.service",
        "ysfgateway-qa_ysf.service", "mmdvm-bridge-qa_p25.service",
        "analog-bridge-qa_p25.service", "md380-emu-qa_p25.service",
        "asr-mqtt-qa_p25.service",
    )
    assert all(systemctl.SERVICE.fullmatch(unit) for unit in allowed)
    assert not any(systemctl.SERVICE.fullmatch(unit) for unit in (
        "asterisk.service", "docker.service", "unrelated.service",
        "p25gateway-qa.service;id", "p25gateway-../qa.service",
        "allscan-reimagined-container-provisioning.service",
    ))


def main() -> int:
    # Runtime-source selection supports deployment architectures, not the
    # developer workstation's macOS arm64 value. Keep this host-independent
    # self-test on a representative supported target architecture.
    platform.machine = lambda: "x86_64"
    installer_source = (HERE / "asr-container-host-install.py").read_text(encoding="utf-8")
    compose_source = (HERE.parent / "docker-compose.yml").read_text(encoding="utf-8")
    layered_reapply = (HERE.parent / "docker/layered-reapply.sh").read_text(encoding="utf-8")
    assert "/run/allscan-reimagined-bridge-control:/run/allscan-reimagined-bridge-control:ro" in compose_source
    assert "/run/allscan-reimagined-m17:/run/allscan-reimagined-m17:ro" in compose_source
    assert "layered-asr-state:/var/lib/allscan-reimagined" in compose_source
    host_boundary = "if [ ! -S /run/allscan-reimagined-host/bridge-setup.sock ]; then"
    assert layered_reapply.index(host_boundary) < layered_reapply.index(
        "install -d -o root -g root -m 755 /run/allscan-reimagined-m17"
    ) < layered_reapply.index("\nfi\n", layered_reapply.index(host_boundary))
    m17_exec = "--proc-root {profile.host_proc} -- /usr/local/sbin/allscan-reimagined-m17-usrp-connector"
    assert m17_exec in installer_source
    assert "--user {SERVICE_USER} -- /usr/local/sbin/allscan-reimagined-m17-usrp-connector" not in installer_source
    assert "ProtectHome=read-only\nProtectSystem=strict\nBindReadOnlyPaths={profile.asterisk_config}" in installer_source
    assert 'm17_audit_directory = Path("/var/log/allscan-reimagined/m17")' in installer_source
    assert '"asr-m17-hosts-update.py"' in installer_source
    assert "ReadWritePaths=/run/allscan-reimagined-m17 /var/log/allscan-reimagined /var/lib/allscan-reimagined/m17" in installer_source
    assert 'watch_prefix = "+"' in installer_source
    assert "ExecStart={watch_prefix}{watch_command}" in installer_source
    assert "Group={watch_group}" in installer_source
    assert "SupplementaryGroups={SERVICE_USER}" in installer_source
    assert "RuntimeDirectoryMode=2750" in installer_source
    assert "RuntimeDirectoryPreserve=yes" in installer_source
    m17_runtime = "RuntimeDirectory=allscan-reimagined-m17\nRuntimeDirectoryMode=0755\nRuntimeDirectoryPreserve=yes"
    assert m17_runtime in installer_source
    assert "allscan-reimagined-m17-hosts-update.timer" in installer_source
    backend = load("asr_container_test_backend", "asr-provisioning-backend.py")
    detect = load("asr_container_test_detect", "asr-provisioning-detect.py")
    broker = load("asr_container_test_broker", "asr-container-host-broker.py")
    asterisk = load("asr_container_test_asterisk", "asr-container-asterisk.py")
    recovery = load("asr_container_test_recovery", "asr-container-bridge-recovery.py")
    systemctl = load("asr_container_test_systemctl", "asr-container-systemctl.py")
    digital = load("asr_container_test_digital", "asr-bridge-setup-digital.py")
    urf = load("asr_container_test_urf", "asr-bridge-setup-urf.py")
    urf_installer = load("asr_container_test_urf_installer", "asr-bridge-setup-urf-install.py")
    zello_installer = load("asr_container_test_zello_installer", "asr-bridge-setup-zello-install.py")
    host_installer = load("asr_container_test_host_installer", "asr-container-host-install.py")
    renderer = load("asr_container_test_renderer", "asr-bridge-setup-digital-render.py")
    setup_client = load("asr_container_test_setup_client", "asr-container-host-client.py")
    control_client = load("asr_container_test_control_client", "asr-container-host-control-client.py")
    test_detection(detect)
    test_profile_and_mapping(backend)
    test_discovery(backend)
    test_active_profile(backend)
    test_broker(broker)
    test_asterisk_adapter(asterisk)
    test_payload_validators(digital, urf)
    test_urf_port_folding(urf)
    test_urf_docker_environment(urf_installer)
    test_managed_urf_image_repair_selection(host_installer)
    test_mqtt_secrets_metadata_migration(host_installer)
    test_mosquitto_credential_metadata_migration(host_installer)
    test_ysf_net_startup_migration(host_installer)
    test_managed_dmr_live_mount_migration(host_installer)
    test_managed_m17_bind_migration(host_installer)
    test_zello_relay_startup_reset(zello_installer)
    test_urf_image_transaction(urf_installer)
    test_p25_no_emulator_topology(digital, renderer)
    test_client_error_forwarding(setup_client, control_client)
    test_recovery(recovery)
    test_adapter_allowlists(systemctl)
    print("container provisioning self-test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
