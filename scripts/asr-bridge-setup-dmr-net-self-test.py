#!/usr/bin/env python3
"""Offline tests for the DMR Net Bridge on the direct DVSwitch/TGIF runtime."""
import importlib.util
import json
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("direct_dmr_net_test", HERE / "asr-bridge-setup-dmr-install.py")
assert spec and spec.loader
install = importlib.util.module_from_spec(spec); sys.modules[spec.name] = install; spec.loader.exec_module(install)
core_spec = importlib.util.spec_from_file_location("direct_dmr_net_core", HERE / "asr-bridge-setup-core.py")
assert core_spec and core_spec.loader
core = importlib.util.module_from_spec(core_spec); sys.modules[core_spec.name] = core; core_spec.loader.exec_module(core)
TOKEN = "tgif-static-token-" + "x" * 24


def put(root: Path, logical: str, data: str) -> Path:
    path = root / logical.lstrip("/"); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data); return path


def host(config: dict, rpt: str) -> tuple[Path, Path, tempfile.TemporaryDirectory]:
    holder = tempfile.TemporaryDirectory(prefix="asr-dmr-net-test-")
    root = Path(holder.name)
    put(root, install.planner.CONFIG, json.dumps(config) + "\n")
    put(root, install.planner.RPT, rpt)
    put(root, install.planner.MODULES, "load = app_rpt.so\n")
    stage = root / "stage"; stage.mkdir()
    for name in ("Analog_Bridge", "MMDVM_Bridge", "md380-emu", "qemu-arm-static"):
        (stage / name).write_bytes((name + "\n").encode())
    return root, stage, holder


def net_settings(**overrides):
    values = {"bridge_id": "dmr_tgif_net", "callsign": "KN4EWT", "dmr_id": 3135475,
              "talkgroup": 67498, "title": "DMR Net Bridge", "bridge_role": "net"}
    values.update(overrides)
    return install.planner.DmrSettings(**values)


install.sources.verify = lambda *_args, **_kwargs: {"ok": True}
NET_RX, NET_TX = core.unified_net_usrp_ports()

# 1. First Net backend on a node: owns node 1999 and becomes the selected mode.
root, stage, holder = host({"node": "674982", "bridges": []},
                           "[nodes]\n674982 = radio@127.0.0.1/674982,NONE\n[674982]\n")
with holder:
    settings = net_settings()
    plan = install.planner.plan(root, settings)
    assert plan["bridgeNode"] == 1999 and plan["role"] == "net"
    assert (plan["ports"]["usrp_rx"], plan["ports"]["usrp_tx"]) == (NET_RX, NET_TX)
    assert plan["resources"]["netTarget"].endswith("/dmr_tgif_net/tgif-run/net-target")
    assert "/urf/" not in json.dumps(plan)
    try:
        install.install(root, settings, stage, "hotspot-key", expected_digest=plan["digest"])
        raise AssertionError("DMR Net Bridge installed without a TGIF Static TG token")
    except install.InstallError as error:
        assert "Static TG API token" in str(error)
    result = install.install(root, settings, stage, "hotspot-key", expected_digest=plan["digest"],
                             static_token=TOKEN)
    assert result["bridgeNode"] == 1999
    config = json.loads((root / install.planner.CONFIG.lstrip("/")).read_text())
    assert config["netBridgeMode"] == "dmr"
    bridge = config["bridges"][0]
    assert bridge["cardType"] == "dmr_net" and bridge["node"] == "1999"
    assert bridge["managedNetControl"] is True and bridge["fixedBridgeRecovery"] is False
    assert bridge["managedTargetFile"] == plan["resources"]["netTarget"]
    assert bridge["tgifStaticTokenPath"] == plan["resources"]["staticToken"]
    assert bridge["setupPorts"]["usrp_rx"] == NET_RX and "fixedDestination" not in bridge
    assert not any("urf" in key.lower() for key in bridge), bridge
    token = root / plan["resources"]["staticToken"].lstrip("/")
    assert token.read_text().strip() == TOKEN and stat.S_IMODE(token.stat().st_mode) == 0o600
    run = (root / plan["resources"]["root"].lstrip("/") / "mmdvm/run-mmdvm.sh").read_text()
    assert f"export TGIF_TARGET_FILE={plan['resources']['netTarget']}" in run and "URF" not in run
    rpt = (root / install.planner.RPT.lstrip("/")).read_text()
    assert "[1999]" in rpt and f"usrp/127.0.0.1:{NET_RX}:{NET_TX}" in rpt.lower()
    record = json.loads((root / install.planner.STATE.lstrip("/") / "dmr_tgif_net.json").read_text())
    assert record["role"] == "net"
    # The role is part of the installation and cannot be flipped by an edit.
    try:
        install.planner.plan(root, install.planner.DmrSettings(**{**settings.__dict__, "bridge_role": "standard"}))
        raise AssertionError("installed Net DMR bridge changed role")
    except install.planner.PlanError as error:
        assert "role is immutable" in str(error)
    try:
        install.planner.validate(net_settings(bridge_node=1005))
        raise AssertionError("DMR Net Bridge accepted a node other than 1999")
    except install.planner.PlanError as error:
        assert "node 1999" in str(error)

# 2. Inactive Net backend: another mode owns node 1999, so rpt.conf is untouched.
live_rpt = ("[nodes]\n674982 = radio@127.0.0.1/674982,NONE\n[674982]\n"
            "; ASR MANAGED BEGIN ysf_net\n[1999]\nrxchannel = usrp/127.0.0.1:52000:52001\n"
            "; ASR MANAGED END ysf_net\n")
root, stage, holder = host({"node": "674982", "netBridgeMode": "ysf", "bridges": [
    {"id": "ysf_net", "mode": "ysf", "node": "1999", "cardType": "ysf_net"}]}, live_rpt)
with holder:
    settings = net_settings()
    plan = install.planner.plan(root, settings)
    assert plan["bridgeNode"] == 1999
    result = install.install(root, settings, stage, "hotspot-key", expected_digest=plan["digest"],
                             static_token=TOKEN)
    assert (root / install.planner.RPT.lstrip("/")).read_text() == live_rpt
    config = json.loads((root / install.planner.CONFIG.lstrip("/")).read_text())
    assert config["netBridgeMode"] == "ysf"
    assert {item["id"] for item in config["bridges"]} == {"ysf_net", "dmr_tgif_net"}

print("DMR Net Bridge on the direct runtime: PASS")
