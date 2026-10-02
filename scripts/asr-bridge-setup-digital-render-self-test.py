#!/usr/bin/env python3
"""Exercise render output against each verified upstream staging bundle."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

planner = load("digital_test_planner", "asr-bridge-setup-digital.py")
renderer = load("digital_test_renderer", "asr-bridge-setup-digital-render.py")
source = load("digital_test_source", "asr-bridge-runtime-sources.py")
assert source.describe("ysf")["gateway"]["patches"] == [
    "Log.cpp: bound vsnprintf to the remaining destination buffer"
]


def put(root, path, content):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def main() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        put(root, "/etc/os-release", "ID=debian\n")
        put(root, planner.CONFIG, json.dumps({"node": "641890", "bridges": []}))
        put(root, planner.RPT, "[nodes]\n641890 = radio@127.0.0.1/641890,NONE\n[641890]\n")
        put(root, planner.MODULES, "load=app_rpt.so\n")
        for mode in planner.MODES:
            stage = Path(sys.argv[1]) / mode
            source.verify(stage, mode)
            settings = planner.DigitalSettings(mode, f"{mode}_test", "KE7WIL", 1234567,
                                                15846, "127.0.0.1", 41400)
            plan = planner.plan(root, settings)
            gateway = renderer.gateway_ini(plan, stage, "test-user", "test-secret")
            assert f"Callsign=KE7WIL" in gateway
            assert f"Port={plan['ports']['remote']}" in gateway
            assert f"FileRoot={planner.MODES[mode]}_{mode}_test" in gateway
            if mode in ("p25", "nxdn"):
                assert "Auth=1" in gateway and "Password=test-secret" in gateway
            else:
                assert "Hosts=/var/lib/mmdvm/ASR-ysf_test-YSFHosts.txt" in gateway
            analog = renderer.analog_ini(plan)
            assert f"useEmulator={'false' if mode == 'p25' else 'true'}" in analog
            assert f"emulatorAddress=127.0.0.1:{plan['ports']['emulator']}" in analog
            assert f"rxPort={plan['ports']['usrp_rx']}" in analog
            mmdvm = renderer.mmdvm_ini(plan, stage)
            assert f"FileRoot=MMDVM_Bridge_{mode}_test" in mmdvm
            assert f"LocalPort={plan['ports']['gateway_tx']}" in mmdvm
            dvswitch = renderer.dvswitch_ini(plan, stage)
            assert f"RemotePort={plan['ports']['remote']}" in dvswitch
            hosts = renderer.hosts_files(plan)
            assert len(hosts) in (1, 2)
            assert all("127.0.0.1" in text for text in hosts.values() if not text.startswith("#"))
            units = renderer.service_units(plan)
            assert len(units) == (4 if mode in ("p25", "ysf") else 5)
            assert all("WantedBy=multi-user.target" in text for text in units.values())
            assert any(f"-S {plan['ports']['emulator']}" in text for text in units.values()) == (mode != "p25")
            assert any(f"/opt/{planner.MODES[mode]}_{mode}_test/{planner.MODES[mode]}.ini"
                       in text for text in units.values())
            if mode in ("p25", "nxdn"):
                assert any("/usr/sbin/mosquitto -c " in text for text in units.values())
    print("digital render source integration: PASS")

if __name__ == "__main__":
    main()
