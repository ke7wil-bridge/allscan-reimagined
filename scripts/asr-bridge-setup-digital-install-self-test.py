#!/usr/bin/env python3
"""Run offline install/second install/rollback with real verified runtime stage."""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("digital_install_test", HERE / "asr-bridge-setup-digital-install.py")
assert spec and spec.loader
install = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = install
spec.loader.exec_module(install)

def put(root, logical, data):
    path = root / logical.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path

def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "/backups/" not in str(p)}

def exercise(stage_root):
    for mode in install.planner.MODES:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            put(root, "/etc/os-release", "ID=debian\n")
            put(root, install.planner.CONFIG, json.dumps({"node": "641890", "bridges": []}) + "\n")
            put(root, install.planner.RPT, "[nodes]\n641890 = radio@127.0.0.1/641890,NONE\n[641890]\n")
            put(root, install.planner.MODULES, "load = app_rpt.so\n")
            settings = install.planner.DigitalSettings(mode, f"{mode}_test", "KE7WIL",
                      1234567, 15846, "127.0.0.1", 41400)
            stage = stage_root / mode
            original = snapshot(root)
            plan = install.planner.plan(root, settings)
            kwargs = {
                "username": "test-user", "password": "test-password",
                "passwordHash": "$7$101$offline-test-hash",
            }
            try:
                install.install(root, settings, stage, kwargs,
                                expected_digest=plan["digest"], fail_after=4)
                raise AssertionError("accepted injected apply failure")
            except install.InstallError as error:
                assert "injected" in str(error), error
            after_failure = snapshot(root)
            assert {k: v for k, v in after_failure.items() if not k.startswith("var/lib/")} == original
            plan = install.planner.plan(root, settings)
            installed = install.install(root, settings, stage, kwargs, expected_digest=plan["digest"])
            assert installed["changedFiles"] > 0
            if mode in ("p25", "nxdn"):
                prefix = root / f"etc/mosquitto/asr-{mode}_test"
                assert all((Path(str(prefix) + suffix).stat().st_mode & 0o777) == 0o640
                           for suffix in (".conf", ".acl", ".passwords"))
                broker_unit = root / f"etc/systemd/system/asr-mqtt-{mode}_test.service"
                assert "User=mosquitto\nGroup=mosquitto" in broker_unit.read_text()
            first = snapshot(root)
            plan = install.planner.plan(root, settings)
            repeated = install.install(root, settings, stage, kwargs, expected_digest=plan["digest"])
            assert repeated["changedFiles"] == 0
            assert snapshot(root) == first
            print(f"offline {mode.upper()} apply/rollback/idempotency: PASS")

    calls = []
    real_run = install.subprocess.run
    def fake_run(argv, **kwargs):
        calls.append(tuple(argv))
        command = argv[-1]
        output = ("chan_usrp.so Running" if command == "module show like chan_usrp.so"
                  else f"NODE {plan['bridgeNode']} STATISTICS" if command.startswith("rpt stats ")
                  else "")
        return type("Completed", (), {"returncode": 0, "stdout": output, "stderr": ""})()
    install.subprocess.run = fake_run
    try:
        install.apply_services(plan, True)
        order = install.service_order(plan)
        assert [call[2] for call in calls if call[:2] == ("systemctl", "restart")] == ["asterisk.service", *order]
        assert [call[3] for call in calls if call[:2] == ("systemctl", "is-active")] == order
        calls.clear()
        install.restore_services(plan, False, True)
        assert len([call for call in calls if call[:3] == ("systemctl", "disable", "--now")]) == len(order)
        assert ("systemctl", "restart", "asterisk.service") in calls
    finally:
        install.subprocess.run = real_run
    print("digital service activation/rollback sequencing: PASS")

if __name__ == "__main__":
    exercise(Path(sys.argv[1]))
