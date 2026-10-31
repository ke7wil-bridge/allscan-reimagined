#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    core = load("setup_core_test", "asr-bridge-setup-core.py")
    helper = load("setup_helper_test", "asr-bridge-setup-helper.py")
    facts = core.HostFacts("asl3", "/etc/asterisk", "/etc/asterisk/rpt.conf",
                           (641890, 1001), True, False, False, "test")
    plan = core.make_plan(
        facts, "m17", "m17_test",
        [core.PlanAction("create", "/opt/allscan-reimagined-bridges/m17_test/config.json",
                         "create M17 bridge config")],
    )
    payload = json.loads(json.dumps(asdict(plan)))
    parsed = helper.plan_from_payload(core, payload)
    assert parsed == plan
    assert parsed.digest() == plan.digest()

    invalid = dict(payload)
    invalid["unexpected"] = True
    try:
        helper.plan_from_payload(core, invalid)
        raise AssertionError("unsupported plan field accepted")
    except helper.HelperError:
        pass

    m17 = helper.load_m17()
    m17_request = {
        "bridgeId": "m17_test", "callsign": "N0CALL",
        "reflector": "M17-TST", "host": "127.0.0.1",
        "port": 17000, "module": "A",
    }
    settings = helper.m17_settings(m17, m17_request)
    assert settings.bridge_id == "m17_test" and settings.port == 17000
    try:
        helper.m17_settings(m17, {**m17_request, "unexpected": True})
        raise AssertionError("unsupported M17 request field accepted")
    except helper.HelperError:
        pass

    digital = helper.load_module("setup_digital_test", helper.DIGITAL_PLAN_PATH)
    digital_request = {
        "bridgeId": "p25_test", "callsign": "N0CALL", "digitalId": 1234567,
        "destination": 64189, "host": "127.0.0.1", "port": 41000,
    }
    digital_value = helper.digital_settings(digital, "p25", digital_request)
    assert digital_value.mode == "p25" and digital_value.destination == 64189
    try:
        helper.digital_settings(digital, "p25", {**digital_request, "unexpected": True})
        raise AssertionError("unsupported digital request field accepted")
    except helper.HelperError:
        pass

    dmr = helper.load_module("setup_dmr_test", helper.URF_PLAN_PATH)
    dmr_request = {
        "bridgeId": "urf_dmr", "callsign": "KE7WIL", "digitalId": 3224939,
        "destination": 86753,
    }
    dmr_value = helper.dmr_settings(dmr, dmr_request)
    assert dmr_value.reflector == "URFWIL" and dmr_value.tgif_tg == 86753
    assert dmr.tgif_network_id(3224939, 1999) == 322493904
    test_value = helper.dmr_settings(dmr, {**dmr_request, "bridgeNode": 1001,
                                           "hotspotEssid": 50})
    assert test_value.dmr_id == 3224939 and test_value.tgif_essid == 50
    assert dmr.tgif_network_id(test_value.dmr_id, 1001, test_value.tgif_essid) == 322493950
    dmr_ports = dmr.port_block("dmr_tgif_net")
    rendered_urf = dmr.urfd_ini({
        "settings": {"reflector": "URFWIL", "callsign": "KE7WIL", "dmr_id": 3224939},
        "mainNode": 641890,
        "ports": dmr_ports,
    })
    assert f"RxPort = {dmr_ports['usrp_rx']}" in rendered_urf
    assert f"TxPort = {dmr_ports['usrp_tx']}" in rendered_urf
    rendered_tcd = dmr.tcd_ini({"ports": dmr_ports})
    for gain in ("DStarGainIn", "DStarGainOut", "DmrYsfGainIn",
                 "DmrYsfGainOut", "UsrpTxGain", "UsrpRxGain"):
        assert f"{gain} = 0" in rendered_tcd
    for invalid_dmr in ({**dmr_request, "authMode": "website"},
                        {**dmr_request, "network": "custom"}):
        try:
            helper.dmr_settings(dmr, invalid_dmr)
            raise AssertionError("unsupported TGIF authentication or network accepted")
        except helper.HelperError:
            pass
    try:
        helper.dmr_settings(dmr, {**dmr_request, "unexpected": True})
        raise AssertionError("unsupported DMR request field accepted")
    except helper.HelperError:
        pass

    net_request = {
        "callsign": "KE7WIL", "digitalId": 3224939, "mainNode": 641890,
        "modes": {
            "dmr": {"destination": 86753, "authMode": "legacy", "tgifPassword": ""},
            "ysf": {"destination": 64189, "name": "US-KE7WIL-YSF", "host": "127.0.0.1", "port": 42000},
            "p25": {"destination": 64189, "host": "127.0.0.1", "port": 41000},
            "nxdn": {"destination": 6749, "identity": 15846, "host": "127.0.0.1", "port": 41400},
            "m17": {"reflector": "M17-WIL", "host": "127.0.0.1", "port": 17000, "module": "A"},
        },
    }
    net_modes = helper.net_bridge_mode_payloads(net_request)
    assert set(net_modes) == {"dmr", "ysf", "p25", "nxdn", "m17"}
    assert all(value.get("bridgeNode") == "1999" for value in net_modes.values())
    assert net_modes["dmr"]["destination"] == "86753"
    assert net_modes["nxdn"]["destination"] == "6749"
    assert net_modes["nxdn"]["identity"] == "15846"
    dmr_ysf_request = {**net_request, "modes": {
        "dmr": net_request["modes"]["dmr"],
        "ysf": net_request["modes"]["ysf"],
    }}
    dmr_ysf_modes = helper.net_bridge_mode_payloads(dmr_ysf_request)
    assert list(dmr_ysf_modes) == ["dmr", "ysf"]
    try:
        helper.net_bridge_mode_payloads({**net_request, "modes": {}})
        raise AssertionError("empty Net Bridge mode selection accepted")
    except helper.HelperError:
        pass
    try:
        helper.net_bridge_mode_payloads({**net_request, "command": "systemctl restart anything"})
        raise AssertionError("unrestricted Net Bridge command field accepted")
    except helper.HelperError:
        pass
    sync_calls = []
    def sync_runner(argv, **kwargs):
        sync_calls.append((argv, kwargs))
        return helper.subprocess.CompletedProcess(argv, 0, "ok", "")
    helper.sync_net_ysf_custom_hosts(sync_runner)
    assert sync_calls[0][0][-1] == "--sync-custom-hosts"
    try:
        helper.sync_net_ysf_custom_hosts(
            lambda argv, **kwargs: helper.subprocess.CompletedProcess(argv, 1, "", "failed")
        )
        raise AssertionError("failed YSF catalog activation accepted")
    except helper.HelperError:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        controller = Path(tmp) / "allscan-reimagined-net-bridge-mode-control"
        controller.write_text("test\n")
        original_require_root = helper.require_root
        original_audit = helper.append_audit
        audited = []
        helper.require_root = lambda: None
        helper.append_audit = lambda event: audited.append(event)
        try:
            reset = helper.net_bridge_reset(
                {"confirmation": "RESET NET BRIDGE"}, controller,
                lambda argv, **kwargs: helper.subprocess.CompletedProcess(
                    argv, 0, json.dumps({"ok": True, "reset": True, "mode": "dmr"}), ""
                ),
            )
            assert reset["reset"] and reset["mode"] == "dmr"
            assert audited == [{"action": "net-bridge-reset", "bridgeId": "net_bridge", "result": "ok"}]
            try:
                helper.net_bridge_reset({"confirmation": "wrong"}, controller)
                raise AssertionError("unsafe Net Bridge reset confirmation accepted")
            except helper.HelperError:
                pass
        finally:
            helper.require_root = original_require_root
            helper.append_audit = original_audit

    zello = helper.load_module("setup_zello_test", helper.ZELLO_PLAN_PATH)
    zello_request = {
        "bridgeId": "zello", "username": "bridge-user", "channel": "My Channel",
        "issuer": "issuer-1", "wsEndpoint": "wss://zello.io/ws",
    }
    zello_value = helper.zello_settings(zello, zello_request)
    assert zello_value.bridge_id == "zello" and zello_value.channel == "My Channel"
    try:
        helper.zello_settings(zello, {**zello_request, "unexpected": True})
        raise AssertionError("unsupported Zello request field accepted")
    except helper.HelperError:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / "etc/allscan-reimagined/config.json"
        token_path = root / "opt/allscan-reimagined-bridges/urf/dmr_tgif_net/secrets/tgif-static-token"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"bridges": [{
            "id": "dmr_tgif_net", "cardType": "dmr_net", "managedNetControl": True,
            "dmrNetworkId": "322493904",
            "tgifStaticTokenPath": "/opt/allscan-reimagined-bridges/urf/dmr_tgif_net/secrets/tgif-static-token",
        }]}))
        original_map = helper.provisioning_config_path
        original_require_root = helper.require_root
        original_audit = helper.append_audit
        helper.provisioning_config_path = lambda logical: root / str(logical).lstrip("/")
        helper.require_root = lambda: None
        helper.append_audit = lambda event: None
        token = "TGIFSTG1.0123456789ABCDEF.FEDCBA9876543210.abcdefghijklmnop"
        try:
            result = helper.dmr_security_update({"bridgeId": "dmr_tgif_net", "tgifPassword": "", "tgifStaticToken": token})
            assert result["dmrNetworkId"] == "322493904"
            assert token_path.read_text() == token
            assert token_path.stat().st_mode & 0o777 == 0o600

            # Existing bridges created before Static TG maintenance have no
            # saved token path. They must use the same fixed canonical path as
            # the managed DMR controller without trusting an arbitrary path.
            token_path.unlink()
            config.write_text(json.dumps({"bridges": [{
                "id": "dmr_tgif_net", "cardType": "dmr_net", "managedNetControl": True,
                "dmrNetworkId": "322493904",
            }]}))
            legacy = helper.dmr_security_update({
                "bridgeId": "dmr_tgif_net", "tgifPassword": "", "tgifStaticToken": token,
            })
            assert legacy["staticTokenUpdated"] is True
            assert token_path.read_text() == token

            token_path.unlink()
            config.write_text(json.dumps({"bridges": [{
                "id": "dmr_tgif_net", "cardType": "dmr_net", "managedNetControl": True,
                "dmrNetworkId": "322493904",
                "tgifStaticTokenPath": "/tmp/untrusted-token",
            }]}))
            try:
                helper.dmr_security_update({
                    "bridgeId": "dmr_tgif_net", "tgifPassword": "", "tgifStaticToken": token,
                })
                raise AssertionError("untrusted TGIF token path was accepted")
            except helper.HelperError as exc:
                assert "token path is not trusted" in str(exc)
            assert not token_path.exists()
            assert not (root / "tmp/untrusted-token").exists()
        finally:
            helper.provisioning_config_path = original_map
            helper.require_root = original_require_root
            helper.append_audit = original_audit

    with tempfile.TemporaryDirectory() as tmp:
        audit = Path(tmp) / "log" / "audit.jsonl"
        helper.append_audit({"action": "test", "result": "ok"}, audit)
        record = json.loads(audit.read_text().strip())
        assert record["action"] == "test" and record["result"] == "ok"
        assert audit.stat().st_mode & 0o777 == 0o640

        target = Path(tmp) / "existing.conf"
        target.write_text("before\n")
        backup_plan = core.make_plan(
            facts, "m17", "m17_backup",
            [core.PlanAction("modify", str(target), "test backup")],
        )
        backup_dir = helper.backup_existing(backup_plan, Path(tmp) / "state")
        manifest = json.loads((backup_dir / "manifest.json").read_text())
        assert manifest[0]["target"] == str(target)
        assert (backup_dir / manifest[0]["backup"]).read_text() == "before\n"

    with tempfile.TemporaryDirectory() as tmp:
        secret = Path(tmp) / "credentials.json"
        secret.write_text(json.dumps({"bridges": {"p25_test": {
            "username": "asr_p25_test", "password": "existing-secret",
            "passwordHash": "$7$101$existing-hash",
        }}}))
        secret.chmod(0o600)
        original_path = helper.MQTT_SECRETS_PATH
        original_run = helper.subprocess.run
        class RootOwnedSecret:
            def is_file(self): return True
            def is_symlink(self): return False
            def stat(self):
                from types import SimpleNamespace
                return SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100600)
            def read_text(self, **kwargs): return secret.read_text(**kwargs)
        helper.MQTT_SECRETS_PATH = RootOwnedSecret()
        helper.subprocess.run = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing credential must not be rehashed"))
        try:
            credential = helper.digital_credentials("p25", "p25_test")
            assert credential["passwordHash"] == "$7$101$existing-hash"
        finally:
            helper.MQTT_SECRETS_PATH = original_path
            helper.subprocess.run = original_run

    print("bridge setup helper self-test: PASS")


if __name__ == "__main__":
    main()
