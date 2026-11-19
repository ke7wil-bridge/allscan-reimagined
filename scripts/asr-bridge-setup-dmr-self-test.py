#!/usr/bin/env python3
"""Offline direct-DMR planning, rendering, rollback, and edit tests."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("direct_dmr_test", HERE / "asr-bridge-setup-dmr-install.py")
assert spec and spec.loader
install = importlib.util.module_from_spec(spec); sys.modules[spec.name] = install; spec.loader.exec_module(install)
admin_spec = importlib.util.spec_from_file_location("direct_dmr_admin_test", HERE / "asr-dmr-standalone-admin-adapter.py")
assert admin_spec and admin_spec.loader
admin = importlib.util.module_from_spec(admin_spec); sys.modules[admin_spec.name] = admin; admin_spec.loader.exec_module(admin)


def put(root: Path, logical: str, data: str) -> Path:
    path = root / logical.lstrip("/"); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(data); return path


def snapshot(root: Path):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and "/backups/" not in str(path)}


with tempfile.TemporaryDirectory(prefix="asr-direct-dmr-test-") as temporary:
    root = Path(temporary)
    put(root, install.planner.CONFIG, json.dumps({"node": "623123", "bridges": []}) + "\n")
    put(root, install.planner.RPT, "[nodes]\n623123 = radio@127.0.0.1/623123,NONE\n[623123]\n")
    put(root, install.planner.MODULES, "load = app_rpt.so\n")
    stage = root / "stage"; stage.mkdir()
    for name in ("Analog_Bridge", "MMDVM_Bridge", "md380-emu", "qemu-arm-static"):
        (stage / name).write_bytes((name + "\n").encode())
    install.sources.verify = lambda *_args, **_kwargs: {"ok": True}
    settings = install.planner.DmrSettings(
        "dmr_johnny2", "N1YKT", 3205161, 42450, bridge_node=1001,
        tgif_essid=3, timeslot=2, color_code=1, dmr_to_asl_gain_db=5,
        asl_to_dmr_gain_db=-4,
        title="TGIF DMR 42450",
    )
    # Collision with an inactive managed DMR bridge must fail before apply.
    # Deterministic CRC port allocation can collide for different bridge IDs.
    collision_id = next(name for i in range(10000)
                        if (name := f"dmr_collision_{i}") != settings.bridge_id
                        and install.planner.port_block(name) == install.planner.port_block(settings.bridge_id))
    record_dir = root / install.planner.STATE.lstrip("/")
    record_dir.mkdir(parents=True, exist_ok=True)
    conflict_record = record_dir / f"{collision_id}.json"
    conflict_record.write_text(json.dumps({"bridgeType": "dmr", "ports": install.planner.port_block(collision_id)}))
    try:
        install.planner.plan(root, settings)
        raise AssertionError("inactive managed DMR port collision was accepted")
    except install.planner.PlanError as error:
        assert "conflicts with managed bridge" in str(error)
    conflict_record.unlink()
    plan = install.planner.plan(root, settings)
    assert plan["dmrNetworkId"] == 320516103 and plan["ports"]["usrp_rx"] == 23160
    assert plan["ports"]["mmdvm_local"] == 23164
    assert plan["ports"]["mmdvm_remote"] == 23165
    assert plan["ports"]["emulator"] == 23170
    assert len(set(plan["ports"].values())) == len(plan["ports"])
    assert abs(plan["gains"]["dmrToAsl"] - 1.7783) < 0.0001
    assert abs(plan["gains"]["aslToDmr"] - 10 ** (-4 / 20)) < 0.0001
    for invalid in (True, False, float('nan'), float('inf'), -20.01, 12.01):
        for key in ('dmr_to_asl_gain_db', 'asl_to_dmr_gain_db'):
            try:
                install.planner.validate(install.planner.DmrSettings(**{
                    **settings.__dict__, key: invalid}))
                raise AssertionError(f'invalid DMR gain {key}={invalid!r} accepted')
            except install.planner.PlanError:
                pass
    # Validate 25 independent asymmetrical settings against the actual
    # Analog_Bridge renderer while keeping Johnny2's +5 dB fixture intact.
    for i in range(25):
        incoming, outgoing = -20 + i * 1.25, 12 - i * 1.25
        gain_settings = install.planner.DmrSettings(**{
            **settings.__dict__, 'dmr_to_asl_gain_db': incoming,
            'asl_to_dmr_gain_db': outgoing})
        gain_plan = install.planner.plan(root, gain_settings)
        gain_ini = install.renderer.analog_ini(gain_plan)
        assert f'usrpGain={10 ** (incoming / 20):.4f}\n' in gain_ini
        assert f'tlvGain={10 ** (outgoing / 20):.4f}\n' in gain_ini
        assert 'usrpAudio=AUDIO_USE_GAIN' in gain_ini
        assert 'tlvAudio=AUDIO_USE_GAIN' in gain_ini
    rendered = install.renderer.mmdvm_template(plan)
    assert "Address=127.0.0.1" in rendered and "Slot2=1" in rendered
    assert "Port=23164" in rendered and "Local=23165" in rendered
    original = snapshot(root)
    try:
        install.install(root, settings, stage, "test-hotspot-key", expected_digest=plan["digest"], fail_after=4)
        raise AssertionError("injected apply failure was accepted")
    except install.InstallError as error:
        assert "injected" in str(error)
    assert {k: v for k, v in snapshot(root).items() if not k.startswith("var/lib/")} == original
    plan = install.planner.plan(root, settings)
    result = install.install(root, settings, stage, "test-hotspot-key", expected_digest=plan["digest"])
    assert result["dmrNetworkId"] == "320516103"
    config = json.loads((root / install.planner.CONFIG.lstrip("/")).read_text())
    bridge = config["bridges"][0]
    assert bridge["backendMode"] == "managed" and bridge["architecture"] == "direct-dvswitch"
    assert bridge["dmrTimeslot"] == 2 and bridge["dmrToAslGainDb"] == 5.0
    assert bridge["aslToDmrGainDb"] == -4.0
    assert "test-hotspot-key" not in json.dumps(config)
    installed_proxy = root / "opt/allscan-reimagined-bridges/dmr_johnny2/mmdvm/asr-tgif-dmr-proxy.py"
    assert "URF_ENABLED" in installed_proxy.read_text() and "banned_callsigns" in installed_proxy.read_text()
    changed = install.planner.DmrSettings(**{**settings.__dict__, "dmr_to_asl_gain_db": 3.0})
    edit_plan = install.planner.plan(root, changed)
    edited = install.install(root, changed, stage, "replacement-key", expected_digest=edit_plan["digest"])
    assert edited["changedFiles"] > 0
    updated_config = json.loads((root / install.planner.CONFIG.lstrip("/")).read_text())
    assert updated_config["bridges"][0]["dmrToAslGainDb"] == 3.0
    assert updated_config["bridges"][0]["aslToDmrGainDb"] == -4.0
    secret = root / "opt/allscan-reimagined-bridges/dmr_johnny2/secrets/master-password"
    assert secret.read_text() == "replacement-key\n"
    assert secret.stat().st_mode & 0o777 == 0o640
    try:
        install.planner.validate(install.planner.DmrSettings("bad", "N1YKT", 314133, 42450, tgif_essid=3))
        raise AssertionError("repeater ID accepted an ESSID")
    except install.planner.PlanError:
        pass

    admin_runtime = root / "admin-runtime"; admin_runtime.mkdir()
    roster = admin_runtime / "dmr-clients.json"
    control = admin_runtime / "dmr-admin.json"
    roster.write_text(json.dumps({"urf_dmr": [
        {"dmrid": "3205161", "callsign": "N1YKT"},
        {"dmrid": "314133", "callsign": "W7ABC"},
    ]}) + "\n")
    capabilities = admin.capability("dmr_johnny2", "0" * 64)
    assert capabilities["listClients"] is True
    assert capabilities["kickClient"] is True
    assert capabilities["kickContract"] == "disconnect-until-reconnect"
    assert capabilities["banClient"] is True and capabilities["unbanClient"] is True
    assert capabilities["banContract"] == "global-timed-v1"
    assert admin.kick("N1YKT", roster, control) == 1
    control_payload = json.loads(control.read_text())
    assert "3205161" in control_payload["kicked_ids"]
    assert [row["callsign"] for row in admin.current_rows(roster)] == ["W7ABC"]
    policy_digest = "1" * 64
    assert admin.apply_policy(control, {"digest": policy_digest,
        "rules": ["314133", "BADGUY", "SPAM*"]}) == policy_digest
    control_payload = json.loads(control.read_text())
    assert control_payload["banned_ids"] == [314133]
    assert control_payload["banned_callsigns"] == ["BADGUY"]
    assert control_payload["banned_prefixes"] == ["SPAM"]

print("direct DMR managed install/edit/rollback/Kick/Ban: PASS")
