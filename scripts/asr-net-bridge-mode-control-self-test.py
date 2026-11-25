#!/usr/bin/env python3
"""Focused tests for the serialized unified Net Bridge lifecycle."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "asr_net_bridge_mode_control_test", HERE / "asr-net-bridge-mode-control.py"
)
assert SPEC and SPEC.loader
control = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = control
SPEC.loader.exec_module(control)


def rejected(function, *args) -> None:
    try:
        function(*args)
    except control.ModeError:
        return
    raise AssertionError("unsafe input was accepted")


def bridge(mode: str, destination: str) -> dict:
    usrp_rx, usrp_tx = control.core.unified_net_usrp_ports()
    bridge_id = f"{mode}_net"
    value = {
        "id": bridge_id, "mode": mode, "cardType": f"{mode}_net",
        "node": str({"dmr": 1004, "ysf": 1005, "p25": 1006,
                     "nxdn": 1007, "m17": 1008}[mode]),
        "fixedDestination": destination,
    }
    if mode == "dmr":
        value.update({
            "backendMode": "managed", "managedNetControl": True,
            "architecture": "direct-dvswitch",
            "managedTargetFile": f"/opt/allscan-reimagined-bridges/{bridge_id}/tgif-run/net-target",
            "approvedDestinations": [destination],
            "setupPorts": {"usrp_rx": usrp_rx, "usrp_tx": usrp_tx},
        })
    elif mode == "m17":
        value.update({
            "m17UsrpRxPort": usrp_rx, "m17UsrpTxPort": usrp_tx,
            "m17AudioQualified": True,
            "m17Reflector": "M17-WIL", "m17Module": "A",
        })
    else:
        value.update({
            "setupPorts": {"usrp_rx": usrp_rx, "usrp_tx": usrp_tx},
            "gatewayService": f"{mode}gateway-{bridge_id}.service",
            "mmdvmService": f"mmdvm-bridge-{bridge_id}.service",
            "analogBridgeService": f"analog-bridge-{bridge_id}.service",
        })
        if mode != "p25":
            value["emulatorService"] = f"md380-emu-{bridge_id}.service"
    return value


class FakeRunner:
    def __init__(self, old_mode: str = "dmr", fail_mode: str = "",
                 unloaded_dmr_workers: bool = False):
        self.active = {mode: mode == old_mode for mode in control.MODES}
        self.fail_mode = fail_mode
        self.unloaded_dmr_workers = unloaded_dmr_workers
        self.commands: list[list[str]] = []
        self.reload_count = 0
        self.transport_linked = False
        self.channel_ports = control.selected_ports(old_mode, bridge(old_mode, "1"))

    def __call__(self, argv: list[str], _check: bool) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        joined = " ".join(argv)
        mode = next((item for item in control.MODES if item in joined), "")
        if argv[0] == control.ASTERISK and argv[-1].startswith("rpt stats "):
            node = argv[-1].rsplit(" ", 1)[-1]
            return subprocess.CompletedProcess(argv, 0, f"NODE {node} STATISTICS\n", "")
        if argv[0] == control.ASTERISK and argv[-1].startswith("rpt lstats "):
            row = "1999 1999 0 OUT 00:00:02 ESTABLISHED\n" if self.transport_linked else ""
            output = ("NODE PEER RECONNECTS DIRECTION CONNECT TIME CONNECT STATE\n" + row)
            return subprocess.CompletedProcess(argv, 0, output, "")
        if argv[0] == control.ASTERISK and "ilink 11" in argv[-1]:
            self.transport_linked = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == control.ASTERISK and argv[-1] == "rpt cmd 1999 ilink 3 641890":
            self.transport_linked = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == control.ASTERISK and argv[-1].startswith("rpt show channels "):
            rx, tx = self.channel_ports
            return subprocess.CompletedProcess(
                argv, 0,
                f"RPT channels for node 1999\nrxchannel                : usrp/127.0.0.1:{rx}:{tx}\n",
                "",
            )
        if argv[0] == control.ASTERISK and argv[-1] == "core reload":
            self.reload_count += 1
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == control.ASTERISK or argv[-2:] == ["restart", "asterisk.service"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if (argv[0].startswith("/usr/local/sbin/allscan-reimagined-")
                or argv[0].endswith("asr-container-netns-exec.py")):
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}\n', "")
        if argv[0] == control.SYSTEMCTL and argv[1] == "reset-failed":
            return subprocess.CompletedProcess(
                argv, 1 if self.unloaded_dmr_workers else 0, "",
                "Unit is not loaded.\n" if self.unloaded_dmr_workers else "",
            )
        if "disable" in argv:
            if mode:
                self.active[mode] = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "enable" in argv:
            if mode == self.fail_mode:
                return subprocess.CompletedProcess(argv, 1, "", "injected")
            if mode:
                self.active[mode] = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "is-active --quiet" in joined:
            return subprocess.CompletedProcess(argv, 0 if self.active.get(mode) else 3, "", "")
        raise AssertionError(f"unexpected command: {argv}")


def fixture(root: Path) -> tuple[SimpleNamespace, dict]:
    asr = root / "asr"
    asterisk = root / "asterisk"
    asr.mkdir(); asterisk.mkdir()
    config = {
        "node": "641890", "netBridgeMode": "dmr",
        "bridges": [
            bridge("dmr", "86753"), bridge("ysf", "64189"),
            bridge("p25", "64189"), bridge("nxdn", "15846"),
            bridge("m17", "M17-WIL A"),
        ],
    }
    (asr / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    rpt = "[nodes]\n" + "".join(
        f"{1004 + index} = radio@127.0.0.1:4572/{1004 + index},NONE\n"
        for index in range(5)
    )
    for item in config["bridges"]:
        rpt = control.m17.upsert_node_section(
            rpt, item["id"], int(item["node"]),
            *control.selected_ports(item["mode"], item),
        )
    (asterisk / "rpt.conf").write_text(rpt)
    return SimpleNamespace(
        asr_config=asr, asterisk_config=asterisk,
        asterisk_container="allstarlink3", host_proc=Path("/proc"),
    ), config


def main() -> None:
    if control.PROFILE.is_file():
        assert control.CONTAINERIZED
        assert "asr-container-systemctl.py" in control.SYSTEMCTL
        assert "asr-container-asterisk.py" in control.ASTERISK
    original_refresh = control.backend.refresh_watch_snapshots
    refreshes = []
    control.backend.refresh_watch_snapshots = lambda profile: refreshes.append(profile)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            # Reproduce a native node where stopped DMR worker units are no
            # longer loaded and systemctl reset-failed therefore returns 1.
            runner = FakeRunner(unloaded_dmr_workers=True)
            result = control.switch_mode("ysf", profile, runner)
            assert result["ok"] and result["mode"] == "ysf" and result["node"] == 1999
            if control.CONTAINERIZED:
                assert len(refreshes) >= 3 and refreshes[0] is profile
            assert runner.transport_linked is True
            assert runner.active == {mode: mode == "ysf" for mode in control.MODES}
            assert [
                "/usr/local/sbin/allscan-reimagined-ysf-bridge-control",
                "--connect", "ysf_net", "64189", "--user", "mode-switch",
            ] in runner.commands
            disconnect = [
                command for command in runner.commands
                if command and command[0].endswith("managed-dmr-net-control")
            ]
            assert disconnect == [[
                "/usr/local/sbin/allscan-reimagined-managed-dmr-net-control",
                "--bridge", "dmr_net", "--disconnect",
            ]]
            ysf_runner = FakeRunner(old_mode="ysf")
            control.disconnect_backend("ysf", bridge("ysf", "64189"), profile, ysf_runner)
            assert ysf_runner.commands[-1] == [
                "/usr/local/sbin/allscan-reimagined-ysf-bridge-control",
                "--disconnect", "ysf_net", "--user", "mode-switch",
                "--allow-unconfirmed",
            ]
            dmr_commands = [" ".join(command) for command in runner.commands]
            assert any(command.endswith("disable --now asr-dmr-mmdvm-dmr_net.service")
                       for command in dmr_commands)
            assert sum(" reset-failed asr-dmr-" in command for command in dmr_commands) == 4
            assert not any("urf" in command or "docker compose" in command for command in dmr_commands)
            assert not any("channel request hangup" in " ".join(command)
                           for command in runner.commands)
            updated = json.loads((profile.asr_config / "config.json").read_text())
            assert updated["netBridgeMode"] == "ysf"
            assert {item["node"] for item in updated["bridges"]} == {"1999"}
            assert [item["fixedDestination"] for item in updated["bridges"]] == [
                item["fixedDestination"] for item in original["bridges"]
            ]
            rpt = (profile.asterisk_config / "rpt.conf").read_text()
            assert rpt.count("[1999]") == 1
            assert rpt.count("1999 = radio@127.0.0.1:4569/1999,NONE") == 1
            assert all(f"[{node}]" not in rpt for node in range(1004, 1009))
            assert "controlstates = asr-bridge-controlstates" in rpt
            assert "0 = rptena,lnkena,apdis,totena,ufdis,noicd" in rpt
            # chan_usrp is host:remote:local.  For every backend, Asterisk's
            # remote port must be the backend Rx/bind port and Asterisk's local
            # port must be the backend Tx/destination port.
            for mode in control.MODES:
                rendered = control.unified_rpt("[nodes]\n", {
                    item["mode"]: item for item in original["bridges"]
                }, mode)
                backend_rx, backend_tx = control.selected_ports(
                    mode, next(item for item in original["bridges"] if item["mode"] == mode)
                )
                assert f"rxchannel = USRP/127.0.0.1:{backend_rx}:{backend_tx}" in rendered
            direct_id = next(item["id"] for item in original["bridges"] if item["mode"] == "dmr")
            direct_owned = (
                "; BEGIN ASR BRIDGE " + direct_id + " " + "a" * 32 + "\n"
                "[1999] ; Unified Net Bridge (" + direct_id + ")\n"
                "rxchannel = USRP/127.0.0.1:52000:52001\n"
                "; END ASR BRIDGE " + direct_id + " " + "a" * 32 + "\n"
            )
            rendered = control.unified_rpt(direct_owned + "[nodes]\n", {
                item["mode"]: item for item in original["bridges"]
            }, "ysf")
            assert rendered.count("[1999]") == 1
            assert "BEGIN ASR BRIDGE " + direct_id not in rendered

        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            original["netBridgeMode"] = "ysf"
            next(item for item in original["bridges"] if item["mode"] == "dmr").pop("fixedDestination")
            (profile.asr_config / "config.json").write_text(json.dumps(original, indent=2) + "\n")
            dmr_runner = FakeRunner(old_mode="ysf")
            dmr_result = control.switch_mode("dmr", profile, dmr_runner)
            assert dmr_result["ok"] and [
                "/usr/local/sbin/allscan-reimagined-managed-dmr-net-control",
                "--bridge", "dmr_net", "--connect", "86753",
            ] in dmr_runner.commands

        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            original["netBridgeMode"] = "ysf"
            (profile.asr_config / "config.json").write_text(json.dumps(original, indent=2) + "\n")
            m17_runner = FakeRunner(old_mode="ysf")
            m17_result = control.switch_mode("m17", profile, m17_runner)
            assert m17_result["ok"] and not m17_runner.transport_linked
            assert not any(cmd[0].endswith("m17-bridge-control") and "connect" in cmd
                           for cmd in m17_runner.commands)
            assert m17_runner.active == {mode: mode == "m17" for mode in control.MODES}

        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            original["netBridgeMode"] = "ysf"
            m17 = next(item for item in original["bridges"] if item["mode"] == "m17")
            m17.update(m17AudioQualified=False, m17QualificationState="qualifying")
            (profile.asr_config / "config.json").write_text(json.dumps(original))
            runner = FakeRunner(old_mode="ysf")
            result = control.switch_mode("m17", profile, runner)
            assert result["ok"] and runner.transport_linked
            assert [
                "/usr/local/sbin/allscan-reimagined-m17-bridge-control",
                "--bridge", "m17_net", "--user", "mode-switch", "connect",
                "--reflector", "M17-WIL", "--module", "A",
            ] in runner.commands

        with tempfile.TemporaryDirectory() as temporary:
            profile, _original = fixture(Path(temporary))
            runner = FakeRunner()
            restored = control.restore_transport(profile, runner)
            assert restored["ok"] and restored["mode"] == "dmr"
            assert restored["transportLinked"] is True
            assert runner.transport_linked is True
            assert any(command[-1] == "rpt cmd 1999 ilink 3 641890"
                       for command in runner.commands)

        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            runner = FakeRunner()
            reset = control.reset_runtime(profile, runner)
            assert reset["ok"] and reset["reset"] and reset["mode"] == "dmr"
            assert reset["transportLinked"] is True
            assert runner.active == {mode: mode == "dmr" for mode in control.MODES}
            assert json.loads((profile.asr_config / "config.json").read_text()) == original
            assert any(command and command[0].endswith("managed-dmr-net-control")
                       for command in runner.commands)

        with tempfile.TemporaryDirectory() as temporary:
            profile, _original = fixture(Path(temporary))
            runner = FakeRunner(fail_mode="p25")
            rejected(control.switch_mode, "p25", profile, runner)
            restored = json.loads((profile.asr_config / "config.json").read_text())
            assert restored["netBridgeMode"] == "dmr"
            assert runner.active == {mode: mode == "dmr" for mode in control.MODES}
            assert any(" ".join(command).endswith("enable --now asr-dmr-mmdvm-dmr_net.service")
                       for command in runner.commands)

        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            for item in original["bridges"]:
                if item["mode"] == "m17":
                    item["m17AudioQualified"] = False
            (profile.asr_config / "config.json").write_text(
                json.dumps(original, indent=2) + "\n"
            )
            runner = FakeRunner()
            rejected(control.switch_mode, "m17", profile, runner)
            preserved = json.loads((profile.asr_config / "config.json").read_text())
            assert preserved["netBridgeMode"] == "dmr"
            assert runner.commands == []
            assert runner.active == {mode: mode == "dmr" for mode in control.MODES}

        # Failed mode selection must not reconnect an explicitly disconnected M17 Net.
        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            original["netBridgeMode"] = "m17"
            (profile.asr_config / "config.json").write_text(json.dumps(original))
            runner = FakeRunner(old_mode="m17", fail_mode="dmr")
            rejected(control.switch_mode, "dmr", profile, runner)
            assert not runner.transport_linked
            assert not any(cmd[0].endswith("m17-bridge-control") and "connect" in cmd
                           for cmd in runner.commands)
            assert json.loads((profile.asr_config / "config.json").read_text())["netBridgeMode"] == "m17"

        rejected(control.switch_mode, "shell", SimpleNamespace())
        bad = bridge("ysf", "64189")
        bad["gatewayService"] = "sshd.service"
        rejected(control.service_units, "ysf", bad)
        rejected(control.runtime_argv, "ysf", bridge("ysf", "64189"), "exec")
        legacy = bridge("dmr", "1")
        legacy["setupPorts"]["usrp_rx"] = 39800
        rejected(control.selected_ports, "dmr", legacy)
        rewritten = control.rewrite_usrp_ini(
            "[USRP]\naddress=127.0.0.1\ntxPort=1\nrxPort=2\n\n[DV3000]\n",
            *control.core.unified_net_usrp_ports(), False,
        )
        assert "RxPort=52000" in rewritten and "TxPort=52001" in rewritten
        print("unified Net Bridge mode-control self-test: PASS")
    finally:
        control.backend.refresh_watch_snapshots = original_refresh


if __name__ == "__main__":
    main()
