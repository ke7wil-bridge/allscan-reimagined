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
            "managedTargetFile": f"/opt/allscan-reimagined-bridges/urf/{bridge_id}/tgif-run/net-target",
            "setupPorts": {"usrp_rx": 31001, "usrp_tx": 31002},
        })
    elif mode == "m17":
        value.update({"m17UsrpRxPort": 35001, "m17UsrpTxPort": 35002})
    else:
        value.update({
            "setupPorts": {"usrp_rx": 32000 + len(mode) * 10,
                           "usrp_tx": 32001 + len(mode) * 10},
            "gatewayService": f"{mode}gateway-{bridge_id}.service",
            "mmdvmService": f"mmdvm-bridge-{bridge_id}.service",
            "analogBridgeService": f"analog-bridge-{bridge_id}.service",
        })
        if mode != "p25":
            value["emulatorService"] = f"md380-emu-{bridge_id}.service"
    return value


class FakeRunner:
    def __init__(self, old_mode: str = "dmr", fail_mode: str = ""):
        self.active = {mode: mode == old_mode for mode in control.MODES}
        self.fail_mode = fail_mode
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], _check: bool) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        joined = " ".join(argv)
        mode = next((item for item in control.MODES if item in joined), "")
        if argv[0] == control.ASTERISK and argv[-1].startswith("rpt stats "):
            node = argv[-1].rsplit(" ", 1)[-1]
            return subprocess.CompletedProcess(argv, 0, f"NODE {node} STATISTICS\n", "")
        if argv[0] == control.ASTERISK or argv[-2:] == ["restart", "asterisk.service"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "docker compose" in joined:
            if " up -d" in joined:
                if self.fail_mode == "dmr":
                    return subprocess.CompletedProcess(argv, 1, "", "injected")
                self.active["dmr"] = True
                return subprocess.CompletedProcess(argv, 0, "", "")
            if joined.endswith(" stop"):
                self.active["dmr"] = False
                return subprocess.CompletedProcess(argv, 0, "", "")
            output = "urfd\ntcd\ntgif\n" if self.active["dmr"] else ""
            return subprocess.CompletedProcess(argv, 0, output, "")
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
    return SimpleNamespace(asr_config=asr, asterisk_config=asterisk), config


def main() -> None:
    original_refresh = control.backend.refresh_watch_snapshots
    control.backend.refresh_watch_snapshots = lambda _profile: None
    try:
        with tempfile.TemporaryDirectory() as temporary:
            profile, original = fixture(Path(temporary))
            runner = FakeRunner()
            result = control.switch_mode("ysf", profile, runner)
            assert result["ok"] and result["mode"] == "ysf" and result["node"] == 1999
            assert runner.active == {mode: mode == "ysf" for mode in control.MODES}
            updated = json.loads((profile.asr_config / "config.json").read_text())
            assert updated["netBridgeMode"] == "ysf"
            assert {item["node"] for item in updated["bridges"]} == {"1999"}
            assert [item["fixedDestination"] for item in updated["bridges"]] == [
                item["fixedDestination"] for item in original["bridges"]
            ]
            rpt = (profile.asterisk_config / "rpt.conf").read_text()
            assert rpt.count("[1999]") == 1
            assert rpt.count("1999 = radio@127.0.0.1:4572/1999,NONE") == 1
            assert all(f"[{node}]" not in rpt for node in range(1004, 1009))

        with tempfile.TemporaryDirectory() as temporary:
            profile, _original = fixture(Path(temporary))
            runner = FakeRunner(fail_mode="p25")
            rejected(control.switch_mode, "p25", profile, runner)
            restored = json.loads((profile.asr_config / "config.json").read_text())
            assert restored["netBridgeMode"] == "dmr"
            assert runner.active == {mode: mode == "dmr" for mode in control.MODES}

        rejected(control.switch_mode, "shell", SimpleNamespace())
        bad = bridge("ysf", "64189")
        bad["gatewayService"] = "sshd.service"
        rejected(control.service_units, "ysf", bad)
        rejected(control.runtime_argv, "ysf", bridge("ysf", "64189"), "exec")
        print("unified Net Bridge mode-control self-test: PASS")
    finally:
        control.backend.refresh_watch_snapshots = original_refresh


if __name__ == "__main__":
    main()
