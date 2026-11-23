#!/usr/bin/env python3
"""Offline qualification for the future KY2 identity migration."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


repo = Path(__file__).resolve().parents[1]
tool = Path(__file__).with_name("asrplus-ky2-identity-migration.py")
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    output = root / "candidate"
    subprocess.run([sys.executable, str(tool), "build", "--repo", str(repo),
                    "--output", str(output)], check=True, capture_output=True, text=True)
    generated = [path for path in output.rglob("*") if path.is_file()]
    for path in generated:
        if path.name != "manifest.json":
            assert "georgia" not in path.read_text(encoding="utf-8").lower(), path
    central = json.loads((output / "masterhub/command-integrity.json").read_text())
    agent = json.loads((output / "ky2/asrplus-ky-control.json").read_text())
    assert set(central["hubs"]) == {"phoenix", "ky2"}
    assert central["hubs"]["ky2"]["keyId"] == agent["commandIntegrity"]["keyId"] == "ky2-2026-01"
    assert central["hubs"]["ky2"]["keyFile"] == agent["commandIntegrity"]["keyFile"] == "/etc/asrplus/command-keys/ky2.key"
    assert agent["hub"] == "ky2" and agent["node"] == "674980"
    assert not (output / "phoenix").exists()
    assert json.loads((output / "ky2/asrplus-ky.json").read_text()) == json.loads(
        (repo / "ops/asrplus/staging/ky2/asrplus-ky.json").read_text())
    subprocess.run([sys.executable, "-m", "py_compile",
                    str(output / "masterhub/asrplus-control.py"),
                    str(output / "masterhub/asrplus-remote-endpoint.py"),
                    str(output / "masterhub/asrplus-token.py"),
                    str(output / "masterhub/asrplus_command_integrity.py"),
                    str(output / "ky2/asrplus-ky-agent"),
                    str(output / "ky2/asrplus_command_integrity.py")], check=True)
    subprocess.run(["php", "-l", str(output / "masterhub/api.php")],
                   check=True, capture_output=True, text=True)
    sys.path.insert(0, str(output / "masterhub"))
    try:
        for name in ("asrplus-control", "asrplus-remote-endpoint", "asrplus-token"):
            path = output / "masterhub" / f"{name}.py"
            spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if name == "asrplus-control":
                assert module.HUBS["masterhub"] == "674982"
                assert module.HUBS["ky2"] == "674980"
                assert module.REMOTE_HUBS["674980"] == "ky2"
                assert "georgia" not in module.HUBS.values() and "georgia" not in module.HUBS
            elif name == "asrplus-remote-endpoint":
                assert module.HUB_NODES["ky2"] == "674980" and "georgia" not in module.HUB_NODES
            else:
                assert module.KNOWN_NODES["ky2"] == "674980" and "georgia" not in module.KNOWN_NODES
    finally:
        sys.path.pop(0)
    manifest = json.loads((output / "manifest.json").read_text())
    import hashlib
    assert manifest["phoenixSourceAgentSha256"] == hashlib.sha256(
        (repo / "ops/asrplus/staging/phoenix/asrplus-agent").read_bytes()).hexdigest()
    assert manifest["phoenixSourceConfigSha256"] == hashlib.sha256(
        (repo / "ops/asrplus/staging/phoenix/asrplus-agent.json").read_bytes()).hexdigest()

    state = root / "state"
    fixtures = {
        "var/lib/asrplus/queue/georgia": None,
        "var/lib/asrplus/queue/.georgia.lock": "lock",
        "var/lib/asrplus/results/georgia/result.json": '{"hub":"georgia"}',
        "var/lib/asrplus/queue-archive/georgia/history.json": '{"hub":"georgia"}',
        "var/lib/asrplus/remote-status/georgia.json": '{"hub":"georgia"}',
        "var/log/asrplus-command-integrity.jsonl": '{"hub":"georgia"}\n',
        "var/lib/asrplus-agent/executed-commands.json": '{"version":1,"commands":{}}',
    }
    for relative, content in fixtures.items():
        path = state / relative
        if content is None:
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    audit_before = (state / "var/log/asrplus-command-integrity.jsonl").read_bytes()
    ledger_before = (state / "var/lib/asrplus-agent/executed-commands.json").read_bytes()
    subprocess.run([sys.executable, str(tool), "state", "--root", str(state), "--apply"],
                   check=True, capture_output=True, text=True)
    assert (state / "var/lib/asrplus/queue/ky2").is_dir()
    assert (state / "var/lib/asrplus/results/ky2/result.json").is_file()
    assert (state / "var/lib/asrplus/queue-archive/ky2/history.json").is_file()
    assert (state / "var/lib/asrplus/remote-status/ky2.json").is_file()
    assert (state / "var/log/asrplus-command-integrity.jsonl").read_bytes() == audit_before
    assert (state / "var/lib/asrplus-agent/executed-commands.json").read_bytes() == ledger_before
    subprocess.run([sys.executable, str(tool), "state", "--root", str(state), "--reverse", "--apply"],
                   check=True, capture_output=True, text=True)
    assert (state / "var/lib/asrplus/results/georgia/result.json").is_file()

    conflict = root / "conflict/var/lib/asrplus/queue/georgia"
    conflict.mkdir(parents=True)
    (conflict / ("a" * 32 + ".json")).write_text("{}")
    failed = subprocess.run([sys.executable, str(tool), "state", "--root", str(root / "conflict"), "--apply"],
                            capture_output=True, text=True)
    assert failed.returncode != 0 and "queue must be empty" in failed.stdout

    dashboard = root / "asrplus.json"
    dashboard.write_text(json.dumps({"hubs": [
        {"id": "masterhub", "node": "674982", "statusFile": "/local/master.json"},
        {"id": "arizona-hub", "node": "641894", "statusFile": "/remote/phoenix.json"},
        {"id": "kentucky-hub", "node": "674980", "statusFile": "/remote/georgia.json"},
    ]}))
    dashboard_new = root / "asrplus-new.json"
    subprocess.run([sys.executable, str(tool), "dashboard-config", "--input", str(dashboard),
                    "--output", str(dashboard_new)], check=True, capture_output=True, text=True)
    hubs = json.loads(dashboard_new.read_text())["hubs"]
    assert next(row for row in hubs if row["node"] == "674982")["statusFile"] == "/local/master.json"
    assert next(row for row in hubs if row["node"] == "641894")["statusFile"] == "/remote/phoenix.json"
    assert next(row for row in hubs if row["node"] == "674980")["statusFile"] == "/remote/ky2.json"

    keys = root / "authorized_keys"
    keys.write_text(
        'restrict,from="44.30.157.22",command="/usr/local/sbin/asrplus-remote-endpoint georgia" ssh-ed25519 KY2\n'
        'restrict,from="45.76.255.39",command="/usr/local/sbin/asrplus-remote-endpoint georgia" ssh-ed25519 RETIRED\n'
        'restrict,from="45.76.255.39",command="/opt/asl3-geo-monitor/georgia_telemetry_receiver.py" ssh-ed25519 OLD asl3-georgia-telemetry\n'
        'restrict,from="72.222.219.41",command="/usr/local/sbin/asrplus-remote-endpoint phoenix" ssh-ed25519 PHX\n')
    os.chmod(keys, 0o600)
    keys_new = root / "authorized_keys.new"
    subprocess.run([sys.executable, str(tool), "authorized-keys", "--input", str(keys),
                    "--output", str(keys_new)], check=True, capture_output=True, text=True)
    changed = keys_new.read_text()
    assert '44.30.157.22' in changed and 'endpoint ky2' in changed
    assert '45.76.255.39' in changed and 'endpoint retired' in changed
    assert 'retired_telemetry_receiver.py' in changed
    assert 'endpoint phoenix' in changed and 'georgia' not in changed.lower()
    keys_restored = root / "authorized_keys.restored"
    subprocess.run([sys.executable, str(tool), "authorized-keys", "--input", str(keys_new),
                    "--output", str(keys_restored), "--reverse"],
                   check=True, capture_output=True, text=True)
    assert keys_restored.read_text() == keys.read_text()

print("ASR Plus KY2 identity migration: PASS")
