#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("broker_test",HERE/"asr-container-host-broker.py")
assert spec and spec.loader
broker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)

for mode in ("dmr","ysf","p25","nxdn","m17"):
    broker.validate_control("net-mode",["--mode",mode])
for bad in ("dstar","urf","", "DMR"):
    try:
        broker.validate_control("net-mode",["--mode",bad])
    except Exception:
        pass
    else:
        raise AssertionError(f"invalid mode accepted: {bad!r}")

source=(HERE/"asr-net-bridge-mode-control.py").read_text()
assert "NODE = 1999" in source
assert "fcntl.flock" in source
assert "def switch_mode(" in source
assert 'PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")' in source
assert "backend.load_profile(PROFILE)" in source
assert "os.environ[backend.PROFILE_ENV] = str(PROFILE)" in source
assert "for attempt in range(2):" in source
assert 'updated["netBridgeMode"] = mode' in source
assert 'updated["netBridgeMode"] = old_mode' not in source
assert 'subprocess.run(argv' in source and 'shell=True' not in source
connector=(HERE/"asr-m17-usrp-connector.py").read_text()
assert "self.control.saved_destination(self.bridge)" in connector
assert "self.connect(target)" in connector
sudoers=(HERE.parent/"docker/layered-reapply.sh").read_text()
for mode in ("dmr","ysf","p25","nxdn","m17"):
    assert f"allscan-reimagined-net-bridge-mode-control --mode {mode}" in sudoers
print("unified Net Bridge mode self-test: PASS")
