#!/usr/bin/env python3
"""Focused offline regression tests for native and container URF runtimes."""
from __future__ import annotations

import importlib.util
import json
import re
import os
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
runtime_builder = load("asr_urf_native_test_builder", "asr-bridge-urf-native-runtime.py")


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
        "[node-main](!)\nfunctions = functions\nlink_functions = functions\nmorse = morse\nphone_functions = functions\nscheduler = schedule\ntelemetry = telemetry\nwait_times = wait-times\ncontext = radio\n"
        "[1999](node-main)\n"
        "[1887](node-main)\nduplex = 0\nrxchannel = USRP/127.0.0.1:34007:32007\n"
        "; ASR BRIDGE SETUP BEGIN urfnet\n"
        "[1002]\\(node-main) ; DMR/URF Bridge (urfnet)\n"
        "rxchannel = USRP/127.0.0.1:28880:28881\n"
        "; ASR BRIDGE SETUP END urfnet\n"
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


def test_protocol_enable_source_patch() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-urf-protocol-source-test-") as temporary:
        root = Path(temporary)
        reflector = root / "reflector"
        reflector.mkdir()
        configure_lines = ["\tcounter = 0;\n"]
        sections = {
            "dcs": ("dcs", "DCS Port", "30051"),
            "dextra": ("dextra", "DExtra Port", "30001"),
            "dplus": ("dplus", "DPlus Port", "20001"),
            "dmrplus": ("dmrplus", "DMRPlus Port", "8880"),
            "m17": ("m17", "M17 Port", "17000"),
            "mmdvm": ("mmdvm", "MMDVM Port", "62030"),
            "nxdn": ("nxdn", "NDXN Port", "41400"),
            "p25": ("p25", "P25 Port", "41000"),
            "urf": ("urf", "URF Port", "10017"),
            "ysf": ("ysf", "YSF Port", "42000"),
        }
        for section, (field, label, default) in sections.items():
            configure_lines.append(
                f"case ESection::{section}:\n\t\t\t\tif (0 == key.compare(JPORT))\n"
                f"\t\t\t\t\tdata[g_Keys.{field}.port] = getUnsigned(value, \"{label}\", 1024, 65535, {default});\n"
            )
            if section not in {"mmdvm", "nxdn", "p25", "ysf"}:
                configure_lines.append("\t\t\t\telse\n")
        # Use the exact pinned-source blocks expected by the patcher.
        (reflector / "Configure.cpp").write_text("".join(configure_lines), encoding="utf-8")
        protocol_blocks = []
        for cls, init, _section in [
            ("CDextraProtocol", '"XRF", EProtocol::dextra, uint16_t(g_Configure.GetUnsigned(g_Keys.dextra.port)), DSTAR_IPV4, DSTAR_IPV6', "DExtra"),
            ("CDplusProtocol", '"REF", EProtocol::dplus, uint16_t(g_Configure.GetUnsigned(g_Keys.dplus.port)), DSTAR_IPV4, DSTAR_IPV6', "DPlus"),
            ("CDcsProtocol", '"DCS", EProtocol::dcs, uint16_t(g_Configure.GetUnsigned(g_Keys.dcs.port)), DSTAR_IPV4, DSTAR_IPV6', "DCS"),
            ("CDmrmmdvmProtocol", "nullptr, EProtocol::dmrmmdvm, uint16_t(g_Configure.GetUnsigned(g_Keys.mmdvm.port)), DMR_IPV4, DMR_IPV6", "MMDVM"),
            ("CDmrplusProtocol", "nullptr, EProtocol::dmrplus, uint16_t(g_Configure.GetUnsigned(g_Keys.dmrplus.port)), DMR_IPV4, DMR_IPV6", "DMRPlus"),
            ("CYsfProtocol", '"YSF", EProtocol::ysf, uint16_t(g_Configure.GetUnsigned(g_Keys.ysf.port)), YSF_IPV4, YSF_IPV6', "YSF"),
            ("CM17Protocol", '"URF", EProtocol::m17, uint16_t(g_Configure.GetUnsigned(g_Keys.m17.port)), M17_IPV4, M17_IPV6', "M17"),
            ("CP25Protocol", '"P25", EProtocol::p25, uint16_t(g_Configure.GetUnsigned(g_Keys.p25.port)), P25_IPV4, P25_IPV6', "P25"),
            ("CNXDNProtocol", '"NXDN", EProtocol::nxdn, uint16_t(g_Configure.GetUnsigned(g_Keys.nxdn.port)), NXDN_IPV4, NXDN_IPV6', "NXDN"),
            ("CURFProtocol", '"URF", EProtocol::urf, uint16_t(g_Configure.GetUnsigned(g_Keys.urf.port)), URF_IPV4, URF_IPV6', "URF"),
        ]:
            protocol_blocks.append(
                f"\t\tm_Protocols.emplace_back(std::unique_ptr<{cls}>(new {cls}));\n"
                f"\t\tif (! m_Protocols.back()->Initialize({init}))\n\t\t\treturn false;\n"
            )
        (reflector / "Protocols.cpp").write_text("".join(protocol_blocks), encoding="utf-8")
        original_replace = runtime_builder.replace_once
        def checked_replace(path, old_text, new_text):
            count = path.read_text(encoding="utf-8").count(old_text)
            if count != 1:
                raise AssertionError(f"source patch match count {count}: {old_text!r}")
            original_replace(path, old_text, new_text)
        runtime_builder.replace_once = checked_replace
        runtime_builder.patch_protocol_enable_flags(root)
        runtime_builder.replace_once = original_replace
        configure = (reflector / "Configure.cpp").read_text(encoding="utf-8")
        protocols = (reflector / "Protocols.cpp").read_text(encoding="utf-8")
        assert all(f'"{name}.enable"' in configure for name in
                   ("DCS", "DExtra", "DPlus", "DMRPlus", "M17", "MMDVM", "NXDN", "P25", "URF", "YSF"))
        assert all(f'GetBoolean("{name}.enable")' in protocols for name in
                   ("DCS", "DExtra", "DPlus", "DMRPlus", "M17", "MMDVM", "NXDN", "P25", "URF", "YSF"))


def main() -> None:
    test_protocol_enable_source_patch()
    # Exercise the deployable runtime path even when this offline test runs on
    # an Apple Silicon workstation, which is not itself an ASL3 target.
    machine_patch = mock.patch("platform.machine", return_value="x86_64")
    machine_patch.start()
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
        netoholic_settings = planner.UrfSettings(
            "urfnet", "KN4EWT", 3135475, 67498, "URFNET", "Netoholics URF",
            1001, "standard", 11, 62030, 17000, 41000, 41400,
            None, None, 6749, 6749, None, "node-main",
            legacy_bridge_nodes=(1883, 1886))
        config_path = root / planner.CONFIG.lstrip("/")
        test_config = json.loads(config_path.read_text(encoding="utf-8"))
        test_config["bridges"].append({
            "id": "zello", "mode": "zello", "node": "1002",
            "cardType": "standard", "fixedBridgeRecovery": True,
        })
        config_path.write_text(json.dumps(test_config), encoding="utf-8")
        put(root, "/usr/local/sbin/asl-reconnect-bridges.sh", "#!/bin/sh\nBRIDGES=\"1883 1001 1889\"\n")
        put(root, "/usr/local/sbin/bridge-live-daemon.py", "DMR_NODE = \"1883\"\n")
        put(root, "/etc/systemd/system/asl-zello-bridge.service.d/override.conf",
            "[Service]\nEnvironment=USRP_RXPORT=34002\nEnvironment=USRP_TXPORT=32002\n")
        put(root, "/etc/allscan-reimagined/native-udp-admin/m17.env",
            "ASR_BRIDGE_ID=m17\nASR_BRIDGE_MODE=m17\nASR_PUBLIC_PORT=17000\nASR_UPSTREAM_PORT=28885\nASR_GLOBAL_POLICY_DIR=/var/lib/allscan-reimagined/admin\n")
        put(root, "/etc/allscan-reimagined/native-udp-admin/p25.env",
            "ASR_BRIDGE_ID=p25\nASR_BRIDGE_MODE=p25\nASR_PUBLIC_PORT=41000\nASR_UPSTREAM_PORT=28887\nASR_GLOBAL_POLICY_DIR=/var/lib/allscan-reimagined/admin\n")
        put(root, "/etc/allscan-reimagined/native-udp-admin/nxdn.env",
            "ASR_BRIDGE_ID=nxdn\nASR_BRIDGE_MODE=nxdn\nASR_PUBLIC_PORT=41400\nASR_UPSTREAM_PORT=51400\nASR_GLOBAL_POLICY_DIR=/var/lib/allscan-reimagined/admin\n")
        put(root, "/etc/allscan-reimagined/native-udp-admin/ysf.env",
            "ASR_BRIDGE_ID=ysf\nASR_BRIDGE_MODE=ysf\nASR_PUBLIC_PORT=42002\nASR_UPSTREAM_PORT=52002\nASR_GLOBAL_POLICY_DIR=/var/lib/allscan-reimagined/admin\n")
        prior_state = root / planner.STATE.lstrip("/") / "urfnet.json"
        prior_state.parent.mkdir(parents=True, exist_ok=True)
        prior_state.write_text(json.dumps({
            "schema": 2, "bridgeId": "urfnet", "bridgeNode": 1001,
            "ports": {"dmr": 62030, "m17": 28885, "p25": 28887,
                      "nxdn": 28886, "ysf": 28888, "urf": 28897},
        }), encoding="utf-8")
        netoholic_plan = planner.plan(root, netoholic_settings)
        netoholic_ini = planner.urfd_ini(netoholic_plan)
        assert netoholic_plan["publicPorts"] == {
            "dmr": 62030, "m17": 17000, "p25": 41000,
            "nxdn": 41400, "ysf": 42002,
            "urf": netoholic_plan["ports"]["urf"]}
        assert netoholic_plan["protocolIds"]["nxdn"] == 6749
        assert netoholic_plan["protocolIds"]["p25"] == 6749
        assert netoholic_plan["listenerPorts"]["m17"] == 28885
        assert netoholic_plan["listenerPorts"]["p25"] == 28887
        assert netoholic_plan["listenerPorts"]["nxdn"] == 28886
        assert netoholic_plan["proxyWarnings"] and {w["mode"] for w in netoholic_plan["proxyWarnings"]} == {"nxdn", "ysf"}
        assert {w["source"] for w in netoholic_plan["migrationWarnings"]} == {
            "/usr/local/sbin/asl-reconnect-bridges.sh", "/usr/local/sbin/bridge-live-daemon.py"}
        assert "[MMDVM]\nEnable = true\nPort = 62030" in netoholic_ini
        assert "[M17]\nEnable = true\nPort = 28885" in netoholic_ini
        assert "[P25]\nEnable = true\nPort = 28887\nAutoLinkModule = A\nReflectorID = 6749" in netoholic_ini
        assert "[NXDN]\nEnable = true\nPort = 28886\nAutoLinkModule = A\nReflectorID = 6749" in netoholic_ini
        assert "[YSF]\nEnable = true\nPort = " in netoholic_ini
        preserve_settings = planner.UrfSettings(
            "urfnet", "KN4EWT", 3135475, 67498, "URFNET", "Netoholics URF",
            None, "standard", 11)
        preserve_plan = planner.plan(root, preserve_settings)
        assert preserve_plan["bridgeNode"] == 1001
        assert preserve_plan["publicPorts"]["dmr"] == 62030
        assert preserve_plan["publicPorts"]["m17"] == 17000
        assert preserve_plan["publicPorts"]["p25"] == 41000
        assert preserve_plan["publicPorts"]["nxdn"] == 41400
        rejected(planner.validate, planner.UrfSettings(
            "urfnet", "KN4EWT", 3135475, 67498, "URFNET", "Netoholics URF",
            1002, "standard", 11, 62030, 17000, 41000, 41400,
            None, None, 67498, 6749, None, "node-main"), contains="nxdn reflector id")
        rejected(planner.plan, root, planner.UrfSettings(
            "urfnet", "KN4EWT", 3135475, 67498, "URFNET", "Netoholics URF",
            1001, "standard", 11, 62030, 17000, 41000, 41400,
            41400, None, 6749, 6749, None, "node-main"), contains="enabled URF protocol listener ports")
        assert b"Environment=DMR_NETWORK_ID=322493950" in installer.native_units(test_plan)[
            "/etc/systemd/system/allscan-reimagined-tgif-dmr_tgif.service"][0]
        assert len(plan["services"]) == 3
        provenance = plan["runtimeRequirements"]["provenance"]
        assert provenance["architecture"] == installer.native_sources.architecture()
        if provenance["architecture"] != "armhf":
            assert "gcc-arm-linux-gnueabihf" in provenance["packages"]
        assert provenance["sources"]["urfd"]["commit"] == installer.native_sources.URFD[1]
        assert "FilePath = /data/" not in planner.urfd_ini(plan)
        legacy_root = root / "legacy"
        legacy_settings = fixture(legacy_root)
        legacy_plan = planner.plan(legacy_root, legacy_settings)
        outputs = installer.file_spec(legacy_root, legacy_plan, "never-log-this-secret")
        assert b"ASR BRIDGE SETUP BEGIN dmr_net" in outputs[planner.RPT][0]
        assert b"[1002](node-main) ; DMR/URF Bridge (qa_dmr)" in outputs[planner.RPT][0]
        assert b"[1887](node-main)" in outputs[planner.RPT][0]
        netoholic_outputs = installer.file_spec(root, netoholic_plan, "never-log-this-secret")
        assert b"[1001](node-main) ; DMR/URF Bridge (urfnet)" in netoholic_outputs[planner.RPT][0]
        assert b"[1002](node-main) ; Zello Bridge (zello)" in netoholic_outputs[planner.RPT][0]
        assert b"rxchannel = USRP/127.0.0.1:34002:32002" in netoholic_outputs[planner.RPT][0]
        assert b"ASR_PUBLIC_PORT=17000" in netoholic_outputs["/etc/allscan-reimagined/native-udp-admin/m17.env"][0]
        assert b"ASR_UPSTREAM_PORT=28885" in netoholic_outputs["/etc/allscan-reimagined/native-udp-admin/m17.env"][0]
        assert b"ASR_UPSTREAM_PORT=51400" in netoholic_outputs["/etc/allscan-reimagined/native-udp-admin/nxdn.env"][0]
        assert b"ASR_UPSTREAM_PORT=52002" in netoholic_outputs["/etc/allscan-reimagined/native-udp-admin/ysf.env"][0]
        assert b"[1002]\\(node-main)" not in netoholic_outputs[planner.RPT][0]
        assert b"[1887](node-main)" in netoholic_outputs[planner.RPT][0]
        rendered_rpt = netoholic_outputs[planner.RPT][0].decode()
        assert planner.managed_block_node(rendered_rpt, "urfnet") == 1001
        assert planner.managed_block_node(rendered_rpt, "zello") == 1002
        assert re.search(r"(?m)^\s*1001\s*=", rendered_rpt)
        assert re.search(r"(?m)^\s*1002\s*=", rendered_rpt)
        # Reapply the rendered output and prove the stale [1002] URF block cannot return.
        (root / planner.RPT.lstrip("/")).write_bytes(netoholic_outputs[planner.RPT][0])
        (root / planner.CONFIG.lstrip("/")).write_bytes(netoholic_outputs[planner.CONFIG][0])
        reapplied_plan = planner.plan(root, netoholic_settings)
        reapplied_outputs = installer.file_spec(root, reapplied_plan, "never-log-this-secret")
        reapplied_rpt = reapplied_outputs[planner.RPT][0].decode()
        assert planner.managed_block_node(reapplied_rpt, "urfnet") == 1001
        assert planner.managed_block_node(reapplied_rpt, "zello") == 1002
        assert reapplied_rpt.count("ASR BRIDGE SETUP BEGIN urfnet") == 1
        assert reapplied_rpt.count("ASR BRIDGE SETUP BEGIN zello") == 1
        quiesce_data = netoholic_outputs["/var/lib/allscan-reimagined/bridge-setup/migration-quiesced.json"][0]
        assert json.loads(quiesce_data)["nodes"] == [1883, 1886]
        assert b"1887" not in quiesce_data
        secret = legacy_plan["resources"]["tgifSecret"]
        assert outputs[secret][1] == 0o640
        assert all(b"never-log-this-secret" not in data
                   for path, (data, _mode) in outputs.items() if path != secret)
        net_settings = planner.UrfSettings(
            "dmr_tgif_net", "KE7WIL", 3224939, 67498, "URFWIL",
            "DMR Net Bridge", 1999, "net")
        net_plan = planner.plan(root, net_settings)
        net_outputs = installer.file_spec(
            root, net_plan, "never-log-this-secret", "static-token-1234567890")
        static_secret = net_plan["resources"]["tgifStaticToken"]
        assert net_outputs[static_secret] == (b"static-token-1234567890\n", 0o600)
        assert net_plan["resources"]["tgifStaticToken"] == (
            "/opt/allscan-reimagined-bridges/urf/dmr_tgif_net/secrets/tgif-static-token")
        rejected(installer.file_spec, root, net_plan, "never-log-this-secret", "short",
                 contains="Static TG API token")
        units = {path: data.decode() for path, (data, _mode) in outputs.items()
                 if path.startswith("/etc/systemd/system/")}
        assert len(units) == 4
        component_units = [text for text in units.values() if "User=asr-bridge" in text]
        assert len(component_units) == 3
        assert all("ProtectSystem=strict" in text and "NoNewPrivileges=true" in text
                   and "CapabilityBoundingSet=" in text for text in component_units)
        tgif_unit = next(text for text in component_units
                         if "TGIF adapter and MMDVM bridge" in text)
        assert "SuccessExitStatus=143" in tgif_unit
        assert not any("docker" in text.lower() for text in units.values())

        original = snapshot(legacy_root)
        rejected(installer.install, legacy_root, settings, "never-log-this-secret",
                 expected_digest="0" * 64, contains="stale")
        assert snapshot(legacy_root) == original
        rejected(installer.install, legacy_root, settings, "never-log-this-secret",
                 expected_digest=legacy_plan["digest"], fail_after=4, contains="injected")
        assert not (legacy_root / legacy_plan["resources"]["root"].lstrip("/")).exists()
        for logical in installer.file_spec(legacy_root, legacy_plan, "never-log-this-secret"):
            assert not (legacy_root / logical.lstrip("/")).exists() or logical in {
                planner.CONFIG, planner.RPT, planner.MODULES}
        after_failure = snapshot(legacy_root)
        assert {key: value for key, value in after_failure.items()
                if not key.startswith("var/lib/")} == original

        plan = planner.plan(legacy_root, settings)
        first_result = installer.install(
            legacy_root, settings, "never-log-this-secret", expected_digest=plan["digest"])
        assert first_result["changedFiles"] > 0
        secret_path = legacy_root / secret.lstrip("/")
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o640
        assert "never-log-this-secret" not in json.dumps(first_result)
        config_path = legacy_root / planner.CONFIG.lstrip("/")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        installed_bridge = next(
            item for item in config["bridges"] if item.get("id") == settings.bridge_id
        )
        installed_bridge["fixedBridgeRecovery"] = True
        config_path.write_text(
            json.dumps(config, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        first = snapshot(legacy_root)
        plan = planner.plan(legacy_root, settings)
        second = installer.install(
            legacy_root, settings, "never-log-this-secret", expected_digest=plan["digest"])
        assert second["changedFiles"] == 0 and snapshot(legacy_root) == first
        config = json.loads(config_path.read_text(encoding="utf-8"))
        installed_bridge = next(
            item for item in config["bridges"] if item.get("id") == settings.bridge_id
        )
        assert installed_bridge["fixedBridgeRecovery"] is True

        container_plan = dict(plan)
        container_plan["backend"] = "container"
        container_plan["services"] = [installer.service_name(settings.bridge_id)]
        container_plan["images"] = {
            "urf": "allscan-reimagined/urf:managed",
            "tcd": "allscan-reimagined/urf-tcd:managed",
        }
        with mock.patch.dict(os.environ, {
            "ASR_URF_DOCKER_NETWORK_MODE": "container:allstarlink3",
            "ASR_URF_DOCKER_RESOLV_CONF": "/run/systemd/resolve/resolv.conf",
        }):
            container_outputs = installer.file_spec(legacy_root, container_plan, "container-secret")
        compose = container_outputs[plan["resources"]["compose"]][0].decode()
        wrapper = container_outputs[installer.unit_path(settings.bridge_id)][0].decode()
        assert "docker compose" in wrapper and "image: allscan-reimagined/urf:managed" in compose
        assert compose.count("/run/systemd/resolve/resolv.conf:/etc/resolv.conf:ro") == 3
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
    active_results = [
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=3),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0),
    ]
    with mock.patch.object(
        installer.subprocess, "run", side_effect=active_results
    ) as systemctl, mock.patch.object(installer.time, "sleep") as sleeper:
        assert installer.wait_for_active_units(affected, attempts=2, interval=0) == ()
    assert systemctl.call_count == 5 and sleeper.call_count == 1

    with mock.patch.object(
        installer.subprocess, "run", return_value=SimpleNamespace(returncode=3)
    ), mock.patch.object(installer.time, "sleep") as sleeper:
        assert installer.wait_for_active_units(
            ("missing.service",), attempts=2, interval=0
        ) == ("missing.service",)
    assert sleeper.call_count == 1

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
    machine_patch.stop()
    print("native/container URF runtime self-test: PASS")


if __name__ == "__main__":
    main()
