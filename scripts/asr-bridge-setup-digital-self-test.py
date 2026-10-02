#!/usr/bin/env python3
"""Pure plan tests; installs and audio still require disposable ASL3 testing."""
import importlib.util
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("digital_plan_test", HERE / "asr-bridge-setup-digital.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def put(root, path, text):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def fail(action, text):
    try:
        action()
        raise AssertionError("accepted invalid plan")
    except module.PlanError as error:
        assert text in str(error), str(error)


reapply = (HERE / "asr-reapply.sh").read_text(encoding="utf-8")
for script in ("asr-bridge-setup-digital.py", "asr-bridge-setup-digital-render.py",
               "asr-bridge-setup-digital-install.py", "asr-bridge-runtime-sources.py"):
    assert script in reapply
for mode in module.MODES:
    assert reapply.count(f"asr-bridge-setup-helper.py {mode}-plan") == 1
    assert reapply.count(f"asr-bridge-setup-helper.py {mode}-install") == 1

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    put(root, "/etc/os-release", "ID=debian\n")
    config = put(root, module.CONFIG, json.dumps({"node": "641890", "bridges": []}) + "\n")
    put(root, module.RPT, "[nodes]\n641890 = radio@127.0.0.1/641890,NONE\n[641890]\n")
    put(root, module.MODULES, "load = app_rpt.so\n")
    original = {path: (root / path.lstrip("/")).read_bytes() for path in (module.CONFIG, module.RPT, module.MODULES)}
    for mode in module.MODES:
        settings = module.DigitalSettings(mode, f"{mode}_test", "KE7WIL", 1234567,
                                          15846, "127.0.0.1", 41400)
        result = module.plan(root, settings)
        assert result["bridgeNode"] == 1001 and len(result["digest"]) == 64
        assert result["ports"]["emulator"] != result["ports"]["gateway_rx"]
        assert result == module.plan(root, settings)
        assert len(set(result["services"])) == len(result["services"])
        assert {path: (root / path.lstrip("/")).read_bytes() for path in original} == original
        fail(lambda: module.plan(root, replace(settings, destination=20)), "reserved")
        fail(lambda: module.plan(root, replace(settings, reflector_host="bad\nname")), "host")
        if mode == "ysf":
            fail(lambda: module.plan(root, replace(settings, destination=9999)), "reserved")
    net_p25 = module.DigitalSettings("p25", "p25_net", "KE7WIL", 3224939,
                                     64189, "127.0.0.1", 41000,
                                     bridge_role="net")
    assert module.plan(root, net_p25)["bridgeNode"] == 1999
    config.write_text(json.dumps({"node": "641890", "bridges": [{
        "id": "ysf_net", "mode": "ysf", "node": "1999", "cardType": "ysf_net",
    }]}) + "\n")
    assert module.plan(root, net_p25)["bridgeNode"] == 1999
    config.write_text(json.dumps({"node": "641890", "bridges": []}) + "\n")
    p25 = module.DigitalSettings("p25", "p25_test", "KE7WIL", 1234567,
                                  15846, "127.0.0.1", 41400)
    config.write_text(json.dumps({"node": "641890", "bridges": [{
        "id": "another", "mode": "nxdn", "node": "1001",
        "setupPorts": module.port_block("p25_test"),
    }]}) + "\n")
    fail(lambda: module.plan(root, p25), "port block")
    config.write_text(json.dumps({"node": "641890", "bridges": []}) + "\n")
    original_load = module.load
    def busy_tcp_load(name, filename):
        loaded = original_load(name, filename)
        if filename == "asr-bridge-setup-core.py":
            original_detect = loaded.ASL3Adapter.detect
            def detect_with_busy_tcp(adapter):
                facts = original_detect(adapter)
                return replace(facts, listening_tcp_ports=(module.port_block("p25_test")["mqtt"],))
            loaded.ASL3Adapter.detect = detect_with_busy_tcp
        return loaded
    module.load = busy_tcp_load
    try:
        fail(lambda: module.plan(root, p25), "MQTT port")
    finally:
        module.load = original_load
    gateway = root / module.plan(root, p25)["resources"]["gateway"].lstrip("/")
    gateway.parent.mkdir(parents=True)
    gateway.write_text("unmanaged\n")
    fail(lambda: module.plan(root, p25), "unmanaged")
print("digital provisioning plan self-test: PASS")
