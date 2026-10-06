#!/usr/bin/env python3
"""Focused regression tests for managed DMR target and AllStar link control."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "asr_managed_dmr_net_control_test", HERE / "asr-managed-dmr-net-control.py"
)
if not spec or not spec.loader:
    raise SystemExit("managed DMR control module unavailable")
control = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = control
spec.loader.exec_module(control)


class FakeRunner:
    def __init__(self, *, linked: bool = False, fail_link: bool = False,
                 channel: str = "usrp/127.0.0.1:52000:52001") -> None:
        self.linked = linked
        self.fail_link = fail_link
        self.channel = channel
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        command = argv[-1]
        if command == "rpt lstats 641890":
            row = ("1999      127.0.0.1           0           OUT        "
                   "00:00:01:000        ESTABLISHED\n") if self.linked else ""
            return subprocess.CompletedProcess(
                argv, 0,
                "NODE      PEER                RECONNECTS  DIRECTION  CONNECT TIME        CONNECT STATE\n"
                "----      ----                ----------  ---------  ------------        -------------\n"
                + row, "",
            )
        if command == "rpt show channels 1999":
            return subprocess.CompletedProcess(
                argv, 0, f"RPT channels for node 1999\nrxchannel : {self.channel}\n", ""
            )
        if " ilink 3 1999" in command:
            if self.fail_link:
                return subprocess.CompletedProcess(argv, 1, "", "link failed")
            self.linked = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if " ilink 11 1999" in command:
            self.linked = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "compose" in argv and "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, "urfd\ntcd\ntgif\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")


def rejected(function, *args) -> None:
    try:
        function(*args)
    except control.ControlError:
        return
    raise AssertionError("invalid managed DMR operation was accepted")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-managed-dmr-") as temporary:
        root = Path(temporary) / "urf"
        target = root / "dmr_tgif_net" / "tgif-run" / "net-target"
        target.parent.mkdir(parents=True)
        (root / "dmr_tgif_net" / "compose.yml").write_text("services: {}\n")
        config_path = Path(temporary) / "config.json"
        config = {
            "node": "641890", "netBridgeMode": "dmr",
            "bridges": [{
                "id": "dmr_tgif_net", "mode": "dmr", "node": "1999",
                "cardType": "dmr_net", "backendMode": "managed",
                "managedNetControl": True,
                "managedTargetFile": "/opt/allscan-reimagined-bridges/urf/dmr_tgif_net/tgif-run/net-target",
                "setupPorts": {"usrp_rx": 52000, "usrp_tx": 52001},
            }],
        }
        config_path.write_text(json.dumps(config))
        loaded, bridge = control.bridge_config("dmr_tgif_net", config_path)
        assert control.target_path(bridge, root) == target
        original_root = control.ROOT
        control.ROOT = root
        try:
            runner = FakeRunner()
            control.write_target(target, 67498)
            status = control.status_payload(loaded, bridge, target, runner)
            assert status["digitalLinked"] is True
            assert status["allstarLinked"] is False
            assert status["linked"] is False

            connected = control.connect(loaded, bridge, target, 67498, runner)
            assert connected["linked"] is True and runner.linked is True
            assert any("ilink 3 1999" in command[-1] for command in runner.commands)

            disconnected = control.disconnect(loaded, bridge, target, runner)
            assert disconnected["linked"] is False and runner.linked is False
            assert not target.exists()
            assert any("ilink 11 1999" in command[-1] for command in runner.commands)

            control.write_target(target, 12345)
            rejected(control.connect, loaded, bridge, target, 67498, FakeRunner(fail_link=True))
            assert control.read_target(target) == 12345

            wrong_mode = dict(loaded, netBridgeMode="m17")
            rejected(control.connect, wrong_mode, bridge, target, 67498, FakeRunner())
            assert control.read_target(target) == 12345
            rejected(
                control.verify_owner, loaded, bridge,
                FakeRunner(channel="usrp/127.0.0.1:35001:35002"),
            )
        finally:
            control.ROOT = original_root
    print("managed DMR Net Bridge control self-test: PASS")


if __name__ == "__main__":
    main()
