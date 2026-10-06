#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "setup_m17", HERE / "asr-bridge-setup-m17.py"
)
assert spec and spec.loader
m17 = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m17
spec.loader.exec_module(m17)


def write(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def fake_root(root: Path) -> None:
    write(root / "etc/os-release", "ID=debian\n")
    write(root / "etc/allscan-reimagined/config.json",
          json.dumps({"node": "641890", "bridges": []}) + "\n")
    write(root / "etc/asterisk/rpt.conf",
          "[general]\nnode_lookup_method=both\n\n[nodes]\n"
          "641890 = radio@127.0.0.1/641890,NONE\n\n"
          "[node-main](!)\nrxchannel = Local/pseudo\n"
          "[641890](node-main)\nrxchannel = Local/pseudo\n")
    write(root / "etc/asterisk/modules.conf", "load = app_rpt.so\n")
    write(root / "usr/local/sbin/allscan-reimagined-m17-bridge-control",
          "#!/bin/sh\nexit 0\n", 0o755)
    write(root / "usr/local/sbin/allscan-reimagined-m17-usrp-connector",
          "#!/bin/sh\nexit 0\n", 0o755)
    write(root / "etc/systemd/system/allscan-reimagined-m17-bridge@.service",
          "[Service]\nExecStart=/usr/local/sbin/allscan-reimagined-m17-usrp-connector\n")
    write(root / "usr/lib/asterisk/modules/chan_usrp.so", "test\n")


def digest_files(root: Path) -> dict[str, str]:
    result = {}
    for logical in (m17.CONFIG_PATH, m17.RPT_PATH, m17.MODULES_PATH):
        path = m17.rooted(root, logical)
        result[logical] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def expect_error(callable_value, phrase: str) -> None:
    try:
        callable_value()
        raise AssertionError(f"expected failure containing {phrase!r}")
    except m17.InstallError as exc:
        assert phrase in str(exc), str(exc)


def main() -> None:
    migrated_states = m17.ensure_bridge_control_states(
        "[asr-bridge-controlstates]\n"
        "0 = rptena,lnkena,apdis,totena,ufdis,noice\n"
    )
    assert "ufdis,noicd" in migrated_states and "ufdis,noice" not in migrated_states
    reapply = (HERE / "asr-reapply.sh").read_text(encoding="utf-8")
    assert "asr-bridge-setup-core.py asr-bridge-setup-m17.py asr-bridge-setup-helper.py" in reapply
    assert reapply.count("asr-bridge-setup-helper.py m17-plan") == 1
    assert reapply.count("asr-bridge-setup-helper.py m17-install") == 1

    settings = m17.M17Settings(
        bridge_id="m17_test", callsign="N0CALL",
        reflector="M17-TST", host="127.0.0.1", port=17000,
        module="A", title="Test M17 Bridge",
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fake_root(root)
        net_settings = m17.M17Settings(**{**settings.__dict__, "bridge_id": "m17_net_test", "card_type": "m17_net"})
        net_plan = m17.plan(root, net_settings)
        net_config = json.loads(net_plan["files"][m17.CONFIG_PATH])
        net_entry = next(item for item in net_config["bridges"] if item["id"] == "m17_net_test")
        assert net_entry["cardType"] == "m17_net"
        assert net_plan["bridgeNode"] == 1999 and net_entry["node"] == "1999"
        assert net_entry["allowTune"] is True
        assert net_entry["m17BindAddress"] == "0.0.0.0"
        assert net_entry["m17UsrpBindAddress"] == "127.0.0.1"
        assert (net_entry["m17UsrpRxPort"], net_entry["m17UsrpTxPort"]) == (52000, 52001)
        assert (
            f"rxchannel = USRP/127.0.0.1:{net_entry['m17UsrpRxPort']}:{net_entry['m17UsrpTxPort']}"
            in net_plan["files"][m17.RPT_PATH]
        )
        assert "controlstates = asr-bridge-controlstates" in net_plan["files"][m17.RPT_PATH]
        assert "0 = rptena,lnkena,apdis,totena,ufdis,noicd" in net_plan["files"][m17.RPT_PATH]
        assert "[1999] ; Unified Net Bridge (m17_net_test)" in net_plan["files"][m17.RPT_PATH]
        assert net_entry["approvedDestinations"] == [{
            "reflector": "M17-TST", "host": "127.0.0.1", "port": 17000,
            "module": "A", "encrypted": False,
        }]
        assert m17.find_existing_bridge(
            m17.read_json(m17.rooted(root, m17.CONFIG_PATH)), "m17_test"
        ) is None

        install_plan = m17.plan(root, settings)
        assert install_plan["bridgeNode"] == 1001
        assert install_plan["changed"] == [
            m17.CONFIG_PATH, m17.RPT_PATH, m17.MODULES_PATH
        ]
        assert install_plan["ports"]["usrpRx"] == install_plan["ports"]["m17"] + 1
        assert "Setup" not in json.dumps(install_plan)

        before = digest_files(root)
        result = m17.install(root, settings, expected_digest=install_plan["digest"])
        assert result["ok"] and result["qualificationRequired"]
        committed = Path(result["committed"])
        assert committed.is_file()
        assert json.loads(committed.read_text())["bridgeId"] == "m17_test"
        m17.verify(root, m17.plan(root, settings))
        installed = digest_files(root)
        assert installed != before
        config = m17.read_json(m17.rooted(root, m17.CONFIG_PATH))
        bridge = m17.find_existing_bridge(config, "m17_test")
        assert bridge and bridge["m17AudioQualified"] is False
        assert bridge["m17QualificationState"] == "not_qualified"

        repeat = m17.install(root, settings)
        assert repeat["changed"] == []
        assert digest_files(root) == installed

        stale = Path(tmp) / "stale"
        fake_root(stale)
        stale_before = digest_files(stale)
        expect_error(
            lambda: m17.install(stale, settings, expected_digest="0" * 64),
            "plan changed",
        )
        assert digest_files(stale) == stale_before

        clean = Path(tmp) / "rollback"
        fake_root(clean)
        rollback_before = digest_files(clean)
        expect_error(lambda: m17.install(clean, settings, fail_after=2),
                     "injected apply failure")
        assert digest_files(clean) == rollback_before
        assert m17.find_existing_bridge(
            m17.read_json(m17.rooted(clean, m17.CONFIG_PATH)), "m17_test"
        ) is None

        missing = Path(tmp) / "missing"
        fake_root(missing)
        (missing / "usr/lib/asterisk/modules/chan_usrp.so").unlink()
        expect_error(lambda: m17.plan(missing, settings), "chan_usrp")

        unsafe = Path(tmp) / "unsafe"
        external = Path(tmp) / "external"
        fake_root(unsafe)
        external.mkdir()
        (unsafe / "etc/allscan-reimagined/config.json").unlink()
        (unsafe / "etc/allscan-reimagined").rmdir()
        (unsafe / "etc/allscan-reimagined").symlink_to(external)
        expect_error(lambda: m17.plan(unsafe, settings), "symbolic link")

        collision = Path(tmp) / "collision"
        fake_root(collision)
        config_path = m17.rooted(collision, m17.CONFIG_PATH)
        config = m17.read_json(config_path)
        config["bridges"] = [{"id": "occupied", "mode": "ysf", "node": "1001"}]
        write(config_path, json.dumps(config) + "\n")
        collision_plan = m17.plan(collision, settings)
        assert collision_plan["bridgeNode"] == 1002

        expect_error(
            lambda: m17.plan(root, m17.M17Settings(
                bridge_id="m17_bad", callsign="bad",
                reflector="M17-TST", host="127.0.0.1", port=17000,
                module="A",
            )),
            "callsign",
        )

    print("M17 scratch installer self-test: PASS")


if __name__ == "__main__":
    main()
