#!/usr/bin/env python3
"""Restart only owned bridge units after the Asterisk netns is replaced."""
from __future__ import annotations

import importlib.util
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path("/var/lib/allscan-reimagined/container-asterisk-state.json")
INSTALLATIONS = Path("/var/lib/allscan-reimagined/bridge-setup/installations")
BRIDGE_ID = re.compile(r"[a-z][a-z0-9_-]{1,31}")


def backend():
    spec = importlib.util.spec_from_file_location("asr_backend_recovery", HERE / "asr-provisioning-backend.py")
    if not spec or not spec.loader:
        raise RuntimeError("backend unavailable")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module


def expected_units(record: dict) -> list[str]:
    bridge_id = record.get("bridgeId")
    bridge_type = record.get("bridgeType")
    if not isinstance(bridge_id, str) or not BRIDGE_ID.fullmatch(bridge_id):
        return []
    if bridge_type == "dmr":
        expected = [f"allscan-reimagined-urf-tgif-{bridge_id}.service"]
    elif bridge_type == "m17":
        if record.get("qualificationRequired") is True:
            return []
        expected = [f"allscan-reimagined-m17-bridge@{bridge_id}.service"]
    elif bridge_type in {"p25", "nxdn", "ysf"}:
        expected = [] if bridge_type == "p25" else [f"md380-emu-{bridge_id}.service"]
        if bridge_type in {"p25", "nxdn"}:
            expected.append(f"asr-mqtt-{bridge_id}.service")
        expected += [f"{bridge_type}gateway-{bridge_id}.service",
                     f"mmdvm-bridge-{bridge_id}.service",
                     f"analog-bridge-{bridge_id}.service"]
    else:
        return []
    recorded = record.get("services")
    if recorded is None and len(expected) == 1:
        recorded = [record.get("service")]
    if bridge_type == "m17" and recorded == [None]:
        recorded = expected
    return expected if isinstance(recorded, list) and (
        recorded == expected or set(recorded) == set(expected)
    ) else []


def owned_units() -> list[str]:
    if not INSTALLATIONS.is_dir() or INSTALLATIONS.is_symlink():
        return []
    directory = INSTALLATIONS.stat()
    if directory.st_uid != 0 or directory.st_mode & 0o022:
        return []
    result: list[str] = []
    for path in sorted(INSTALLATIONS.glob("*.json")):
        if path.is_symlink() or not BRIDGE_ID.fullmatch(path.stem):
            continue
        info = path.stat()
        if info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            result.extend(expected_units(record))
    return result


def recovery_units(profile) -> list[str]:
    """Exclude every inactive member of the mutually exclusive Net Bridge."""
    units = owned_units()
    try:
        config = json.loads((profile.asr_config / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    active_mode = str(config.get("netBridgeMode", "")).lower()
    inactive_ids = {
        str(bridge.get("id")) for bridge in config.get("bridges", [])
        if isinstance(bridge, dict)
        and bridge.get("cardType") in {"dmr_net", "ysf_net", "p25_net", "nxdn_net", "m17_net"}
        and str(bridge.get("mode", "")).lower() != active_mode
        and BRIDGE_ID.fullmatch(str(bridge.get("id", "")))
    }
    return [unit for unit in units if not any(
        unit.endswith(f"-{bridge_id}.service") or unit.endswith(f"@{bridge_id}.service")
        for bridge_id in inactive_ids
    )]


def container_identity(name: str) -> dict:
    result = subprocess.run(
        ["/usr/bin/docker", "inspect", "--format", "{{json .State}}", name],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError("Asterisk container is unavailable")
    state = json.loads(result.stdout)
    if state.get("Running") is not True or int(state.get("Pid", 0)) < 2:
        raise RuntimeError("Asterisk container is not running")
    return {"pid": int(state["Pid"]), "startedAt": str(state.get("StartedAt", ""))}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600); os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart-owned", action="store_true",
                        help="restart recorded ASR bridge units during an explicit host reapply")
    args = parser.parse_args()
    profile = backend().load_profile()
    if profile is None:
        raise SystemExit("container profile unavailable")
    backend().validate_active_profile(profile)
    current = container_identity(profile.asterisk_container)
    try:
        previous = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    changed = bool(previous and previous != current)
    if args.restart_owned or changed:
        for unit in recovery_units(profile):
            subprocess.run(["/usr/bin/systemctl", "try-restart", unit], check=True)
    atomic_json(STATE, current)
    print(json.dumps({"ok": True, "changed": changed,
                      "explicitRestart": args.restart_owned,
                      "ownedUnits": owned_units(), "recoveryUnits": recovery_units(profile)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
