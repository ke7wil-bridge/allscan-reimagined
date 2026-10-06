#!/usr/bin/env python3
"""Atomically select the one ASR Net Bridge backend using node 1999."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
MODES = ("dmr", "ysf", "p25", "nxdn", "m17")
NET_CARD_TYPES = {f"{mode}_net" for mode in MODES}
NODE = 1999
LOCK = Path("/run/lock/allscan-reimagined-net-bridge-mode.lock")
PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")
SYSTEMCTL = "/usr/local/libexec/allscan-reimagined/asr-container-systemctl.py"
ASTERISK = "/usr/local/libexec/allscan-reimagined/asr-container-asterisk.py"
Runner = Callable[[list[str], bool], subprocess.CompletedProcess[str]]


class ModeError(RuntimeError):
    pass


def load(name: str, filename: str):
    path = HERE / filename
    if not path.is_file():
        path = Path("/usr/local/libexec/allscan-reimagined") / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise ModeError(f"missing {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backend = load("asr_net_mode_backend", "asr-provisioning-backend.py")
m17 = load("asr_net_mode_m17", "asr-bridge-setup-m17.py")
core = load("asr_net_mode_core", "asr-bridge-setup-core.py")


def atomic(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fchmod(handle.fileno(), path.stat().st_mode & 0o777)
            os.fchown(handle.fileno(), path.stat().st_uid, path.stat().st_gid)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=check, capture_output=True, text=True,
                          timeout=120)


def checked(result: subprocess.CompletedProcess[str], operation: str) -> None:
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise ModeError(f"{operation} failed{': ' + detail if detail else ''}")


def net_bridges(config: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    bridges = config.get("bridges")
    if not isinstance(bridges, list):
        raise ModeError("configured bridges are invalid")
    for bridge in bridges:
        if not isinstance(bridge, dict) or bridge.get("cardType") not in NET_CARD_TYPES:
            continue
        mode = str(bridge.get("mode", "")).lower()
        bridge_id = str(bridge.get("id", ""))
        if mode not in MODES or bridge.get("cardType") != f"{mode}_net":
            raise ModeError("Net Bridge mode metadata is invalid")
        if mode in result:
            raise ModeError(f"multiple {mode.upper()} Net Bridges are configured")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
            raise ModeError("Net Bridge ID is invalid")
        result[mode] = bridge
    return result


def service_units(mode: str, bridge: dict) -> list[str]:
    bridge_id = re.escape(str(bridge["id"]))
    if mode == "m17":
        return [f"allscan-reimagined-m17-bridge@{bridge['id']}.service"]
    expected = {
        "gatewayService": rf"{mode}gateway-{bridge_id}\.service",
        "mmdvmService": rf"mmdvm-bridge-{bridge_id}\.service",
        "analogBridgeService": rf"analog-bridge-{bridge_id}\.service",
    }
    if mode != "p25":
        expected["emulatorService"] = rf"md380-emu-{bridge_id}\.service"
    validated: dict[str, str] = {}
    for key, pattern in expected.items():
        value = str(bridge.get(key, ""))
        if not re.fullmatch(pattern, value):
            raise ModeError(f"{mode.upper()} Net Bridge service allowlist is invalid")
        validated[key] = value
    units = ([validated["emulatorService"]] if "emulatorService" in validated else [])
    if mode in {"p25", "nxdn"}:
        units.append(f"asr-mqtt-{bridge['id']}.service")
    units.extend([validated["gatewayService"], validated["mmdvmService"],
                  validated["analogBridgeService"]])
    return units


def dmr_compose(bridge: dict) -> Path:
    bridge_id = str(bridge["id"])
    expected = Path(f"/opt/allscan-reimagined-bridges/urf/{bridge_id}/tgif-run/net-target")
    target = Path(str(bridge.get("managedTargetFile", "")))
    if (bridge.get("managedNetControl") is not True
            or bridge.get("backendMode") != "managed" or target != expected):
        raise ModeError("DMR Net Bridge runtime allowlist is invalid")
    return target.parent.parent / "compose.yml"


def runtime_argv(mode: str, bridge: dict, verb: str) -> list[list[str]]:
    if verb not in {"start", "stop", "status"}:
        raise ModeError("invalid runtime operation")
    if mode == "dmr":
        compose = dmr_compose(bridge)
        project = str(bridge["id"])
        unit = f"allscan-reimagined-urf-tgif-{bridge['id']}.service"
        if verb == "start":
            return [[SYSTEMCTL, "enable", unit],
                    ["docker", "compose", "-f", str(compose), "-p", project, "up", "-d"]]
        if verb == "stop":
            return [["docker", "compose", "-f", str(compose), "-p", project, "stop"],
                    [SYSTEMCTL, "disable", unit]]
        return [["docker", "compose", "-f", str(compose), "-p", project,
                 "ps", "--services", "--filter", "status=running"]]
    units = service_units(mode, bridge)
    if verb == "start":
        return [[SYSTEMCTL, "enable", "--now", unit] for unit in units]
    if verb == "stop":
        return [[SYSTEMCTL, "disable", "--now", unit] for unit in reversed(units)]
    return [[SYSTEMCTL, "is-active", "--quiet", unit] for unit in units]


def stop_backend(mode: str, bridge: dict, runner: Runner = run) -> None:
    for argv in runtime_argv(mode, bridge, "stop"):
        checked(runner(argv, False), f"stopping {mode.upper()} runtime")
    for argv in runtime_argv(mode, bridge, "status"):
        result = runner(argv, False)
        if mode == "dmr":
            if result.returncode or result.stdout.strip():
                raise ModeError("DMR runtime did not stop cleanly")
        elif result.returncode == 0:
            raise ModeError(f"inactive {mode.upper()} service is still running")


def start_backend(mode: str, bridge: dict, runner: Runner = run) -> None:
    for argv in runtime_argv(mode, bridge, "start"):
        checked(runner(argv, False), f"starting {mode.upper()} runtime")
    for attempt in range(20):
        ready = True
        for argv in runtime_argv(mode, bridge, "status"):
            result = runner(argv, False)
            if mode == "dmr":
                if result.returncode or len(result.stdout.splitlines()) < 3:
                    ready = False
            elif result.returncode:
                ready = False
        if ready:
            return
        if attempt < 19:
            time.sleep(0.5)
    raise ModeError(f"{mode.upper()} runtime readiness was not confirmed")


def configured_ports(mode: str, bridge: dict) -> tuple[int, int]:
    try:
        if mode == "m17":
            rx, tx = int(bridge["m17UsrpRxPort"]), int(bridge["m17UsrpTxPort"])
        else:
            ports = bridge["setupPorts"]
            rx, tx = int(ports["usrp_rx"]), int(ports["usrp_tx"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModeError(f"{mode.upper()} USRP configuration is invalid") from exc
    if not (1024 <= rx <= 65535 and 1024 <= tx <= 65535 and rx != tx):
        raise ModeError(f"{mode.upper()} USRP ports are invalid")
    return rx, tx


def selected_ports(mode: str, bridge: dict) -> tuple[int, int]:
    rx, tx = configured_ports(mode, bridge)
    expected = core.unified_net_usrp_ports()
    if (rx, tx) != expected:
        raise ModeError(
            f"{mode.upper()} Net Bridge requires shared USRP ports "
            f"{expected[0]}:{expected[1]}; reprovision the backend"
        )
    return rx, tx


def rewrite_usrp_ini(text: str, rx: int, tx: int, spaced: bool) -> str:
    section = re.search(r"(?ms)^\[USRP\]\s*$.*?(?=^\[[^]]+\]\s*$|\Z)", text)
    if not section:
        raise ModeError("backend configuration has no [USRP] section")
    block = section.group(0)
    separator = " = " if spaced else "="
    for key, value in (("RxPort", rx), ("TxPort", tx)):
        pattern = re.compile(rf"(?mi)^{key}\s*=\s*[0-9]+\s*$")
        if len(pattern.findall(block)) != 1:
            raise ModeError(f"backend configuration has invalid {key}")
        block = pattern.sub(f"{key}{separator}{value}", block)
    return text[:section.start()] + block + text[section.end():]


def migrate_transport(profile, runner: Runner = run) -> dict:
    """Normalize legacy per-mode USRP ports without touching live app_rpt."""
    config_path = profile.asr_config / "config.json"
    rpt_path = profile.asterisk_config / "rpt.conf"
    original_config = config_path.read_text(encoding="utf-8")
    original_rpt = rpt_path.read_text(encoding="utf-8")
    config = json.loads(original_config)
    bridges = net_bridges(config)
    if set(bridges) != set(MODES):
        raise ModeError("all five Net Bridge modes must be provisioned before migration")
    mode = str(config.get("netBridgeMode", "")).lower()
    if mode not in bridges:
        raise ModeError("selected Net Bridge mode is invalid")
    for candidate, bridge in bridges.items():
        runtime_argv(candidate, bridge, "status")
        configured_ports(candidate, bridge)

    rx, tx = core.unified_net_usrp_ports()
    files: dict[Path, bool] = {}
    dmr_root = dmr_compose(bridges["dmr"]).parent
    files[dmr_root / "config" / "urfd.ini"] = True
    for candidate in ("ysf", "p25", "nxdn"):
        bridge_id = str(bridges[candidate]["id"])
        files[Path(f"/opt/Analog_Bridge_{bridge_id}/Analog_Bridge.ini")] = False
    originals: dict[Path, str] = {}
    rendered: dict[Path, str] = {}
    for path, spaced in files.items():
        if path.is_symlink() or not path.is_file():
            raise ModeError(f"unsafe or missing backend configuration: {path}")
        originals[path] = path.read_text(encoding="utf-8")
        rendered[path] = rewrite_usrp_ini(originals[path], rx, tx, spaced)

    updated = json.loads(original_config)
    for bridge in updated["bridges"]:
        if not isinstance(bridge, dict) or bridge.get("cardType") not in NET_CARD_TYPES:
            continue
        bridge["node"] = str(NODE)
        bridge.pop("linkAlias", None)
        if bridge.get("mode") == "m17":
            bridge["m17UsrpRxPort"], bridge["m17UsrpTxPort"] = rx, tx
        else:
            bridge["setupPorts"]["usrp_rx"] = rx
            bridge["setupPorts"]["usrp_tx"] = tx
    updated_bridges = net_bridges(updated)
    new_rpt = unified_rpt(original_rpt, updated_bridges, mode)
    stopped = False
    try:
        for candidate, bridge in bridges.items():
            stop_backend(candidate, bridge, runner)
        stopped = True
        for path, value in rendered.items():
            atomic(path, value)
        atomic(rpt_path, new_rpt)
        atomic(config_path, json.dumps(updated, sort_keys=True, indent=2) + "\n")
        backend.refresh_watch_snapshots(profile)
    except Exception:
        if stopped:
            for path, value in originals.items():
                try:
                    atomic(path, value)
                except Exception:
                    pass
            try:
                atomic(rpt_path, original_rpt)
                atomic(config_path, original_config)
                backend.refresh_watch_snapshots(profile)
            except Exception:
                pass
        raise
    return {"ok": True, "migrated": True, "mode": mode, "node": NODE,
            "usrpRx": rx, "usrpTx": tx, "appRptRestartRequired": True}


def strip_managed(text: str, bridge_ids: list[str]) -> str:
    for bridge_id in bridge_ids:
        for prefix in ("; ASR BRIDGE SETUP ", "; ASR MANAGED BRIDGE "):
            begin = f"{prefix}BEGIN {bridge_id}"
            end = f"{prefix}END {bridge_id}"
            text = re.sub(
                rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}[ \t]*$\n?",
                "", text,
            )
    return text


def unified_rpt(text: str, bridges: dict[str, dict], selected_mode: str) -> str:
    selected = bridges[selected_mode]
    rx, tx = selected_ports(selected_mode, selected)
    old_nodes = {str(bridge.get("node", "")) for bridge in bridges.values()}
    text = strip_managed(text, [str(bridge["id"]) for bridge in bridges.values()])
    for node in old_nodes | {str(NODE)}:
        if node.isdigit():
            text = re.sub(
                rf"(?m)^\s*{re.escape(node)}\s*=\s*radio@127\.0\.0\.1(?::4572)?/{re.escape(node)},NONE\s*$\n?",
                "", text,
            )
    text = m17.insert_nodes_mapping(text, NODE)
    text = m17.upsert_node_section(text, str(selected["id"]), NODE, rx, tx)
    label = f"{selected_mode.upper()} Net Bridge ({selected['id']})"
    return text.replace(f"[{NODE}] ; M17 Bridge ({selected['id']})", f"[{NODE}] ; {label}")


def disconnect_transport(config: dict, bridge_node: str, runner: Runner = run) -> None:
    main_node = str(config.get("node", ""))
    if not re.fullmatch(r"[0-9]{3,10}", main_node):
        raise ModeError("main AllStar node is invalid")
    nodes = {str(NODE), bridge_node}
    if not all(re.fullmatch(r"[0-9]{3,10}", node) for node in nodes):
        raise ModeError("configured Net Bridge node is invalid")
    for node in sorted(nodes):
        runner([ASTERISK, "-rx", f"rpt cmd {main_node} ilink 11 {node}"], False)


def reload_and_verify(
    runner: Runner = run, node: int = NODE,
    expected_ports: tuple[int, int] | None = None,
) -> None:
    # Every Net Bridge backend shares one permanent chan_usrp endpoint. app_rpt
    # does not recreate a soft-hung rxchannel, and `rpt restart` disrupts every
    # node, so a routine mode switch must never replace this channel.
    checked(runner([ASTERISK, "-rx", "core reload"], False),
            "reloading unified Asterisk configuration")
    if expected_ports is not None:
        expected = f"usrp/127.0.0.1:{expected_ports[0]}:{expected_ports[1]}"
        channels = runner([ASTERISK, "-rx", f"rpt show channels {node}"], False)
        checked(channels, "reading unified Asterisk channels")
        match = re.search(r"(?mi)^rxchannel\s*:\s*(usrp/127\.0\.0\.1:[0-9]{4,5}:[0-9]{4,5})\s*$",
                          channels.stdout)
        if not match:
            raise ModeError("Asterisk did not report the unified USRP receive channel")
        if match.group(1).lower() != expected:
            raise ModeError(
                f"Asterisk node {node} is using {match.group(1)}, not permanent "
                f"USRP {expected}; one app_rpt recovery restart is required"
            )
    last = ""
    for _ in range(20):
        result = runner([ASTERISK, "-rx", f"rpt stats {node}"], False)
        last = (result.stderr or result.stdout).strip()
        if result.returncode == 0 and f"NODE {node} STATISTICS" in result.stdout:
            return
        time.sleep(0.25)
    raise ModeError(f"Asterisk node {node} readiness was not confirmed: {last[-300:]}")


def switch_mode(mode: str, profile, runner: Runner = run) -> dict:
    if mode not in MODES:
        raise ModeError("invalid Net Bridge mode")
    config_path = profile.asr_config / "config.json"
    rpt_path = profile.asterisk_config / "rpt.conf"
    original_config = config_path.read_text(encoding="utf-8")
    original_rpt = rpt_path.read_text(encoding="utf-8")
    config = json.loads(original_config)
    bridges = net_bridges(config)
    if mode not in bridges:
        raise ModeError(f"Net Bridge mode {mode} is not provisioned")
    for candidate, bridge in bridges.items():
        runtime_argv(candidate, bridge, "status")
        selected_ports(candidate, bridge)
    old_mode = str(config.get("netBridgeMode", "")).lower()
    if old_mode not in bridges:
        old_mode = ""
    new_rpt = unified_rpt(original_rpt, bridges, mode)
    updated = json.loads(original_config)
    for bridge in updated["bridges"]:
        if isinstance(bridge, dict) and bridge.get("cardType") in NET_CARD_TYPES:
            bridge["node"] = str(NODE)
    # Publish the selected mode in the staged configuration before starting its
    # runtime.  Backends such as M17 fail closed unless they can verify that
    # they are the selected unified mode.  The original configuration remains
    # available for the rollback path until readiness is confirmed.
    updated["netBridgeMode"] = mode
    staged_config = json.dumps(updated, sort_keys=True, indent=2) + "\n"
    try:
        old_node = str(bridges[old_mode].get("node", NODE)) if old_mode else str(NODE)
        disconnect_transport(config, old_node, runner)
        for candidate, bridge in bridges.items():
            stop_backend(candidate, bridge, runner)
        atomic(rpt_path, new_rpt)
        atomic(config_path, staged_config)
        backend.refresh_watch_snapshots(profile)
        reload_and_verify(runner, expected_ports=selected_ports(mode, bridges[mode]))
        start_backend(mode, bridges[mode], runner)
        updated["netBridgeMode"] = mode
        atomic(config_path, json.dumps(updated, sort_keys=True, indent=2) + "\n")
        backend.refresh_watch_snapshots(profile)
    except Exception as exc:
        for candidate, bridge in bridges.items():
            try:
                stop_backend(candidate, bridge, runner)
            except Exception:
                pass
        rollback_error = ""
        try:
            atomic(rpt_path, original_rpt)
            atomic(config_path, original_config)
            backend.refresh_watch_snapshots(profile)
            rollback_node = int(bridges[old_mode]["node"]) if old_mode else NODE
            rollback_ports = selected_ports(old_mode, bridges[old_mode]) if old_mode else None
            reload_and_verify(runner, rollback_node, rollback_ports)
            if old_mode:
                start_backend(old_mode, bridges[old_mode], runner)
        except Exception as rollback_exc:
            rollback_error = f"; rollback left all Net Bridge runtimes stopped: {rollback_exc}"
        raise ModeError(f"Net Bridge mode switch failed: {exc}{rollback_error}") from exc
    return {"ok": True, "mode": mode, "previousMode": old_mode or None,
            "node": NODE, "bridgeId": bridges[mode]["id"], "ready": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--mode", choices=MODES)
    operation.add_argument("--migrate-transport", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # Unlike broker-launched requests, the root-only administrative entry point
    # does not inherit the broker's private environment.  Use the fixed host
    # profile path; load_profile still enforces ownership, mode, symlink, shape,
    # and provisioned-path validation.
    profile = backend.load_profile(PROFILE)
    if profile is None:
        raise SystemExit("container profile unavailable")
    backend.validate_active_profile(profile)
    # Child helpers intentionally require the same validated profile marker.
    # Broker launches already provide it; direct root administration does not.
    os.environ[backend.PROFILE_ENV] = str(PROFILE)
    config = json.loads((profile.asr_config / "config.json").read_text())
    bridges = net_bridges(config)
    if args.mode is not None and args.mode not in bridges:
        raise SystemExit(f"Net Bridge mode {args.mode} is not provisioned")
    if args.dry_run:
        if args.migrate_transport:
            rx, tx = core.unified_net_usrp_ports()
            print(json.dumps({"ok": True, "migrate": True, "usrpRx": rx,
                              "usrpTx": tx}))
            return 0
        rx, tx = selected_ports(args.mode, bridges[args.mode])
        print(json.dumps({"ok": True, "mode": args.mode, "node": NODE,
                          "bridgeId": bridges[args.mode]["id"],
                          "usrpRx": rx, "usrpTx": tx}))
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
        os.close(descriptor)
        raise SystemExit("Net Bridge mode lock is unsafe")
    with os.fdopen(descriptor, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            result = (migrate_transport(profile) if args.migrate_transport
                      else switch_mode(args.mode, profile))
        except (ModeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
