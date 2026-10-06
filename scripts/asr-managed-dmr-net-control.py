#!/usr/bin/env python3
"""Control the managed DMR Net Bridge and its private AllStar transport."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

ASTERISK = Path("/usr/local/libexec/allscan-reimagined/asr-container-asterisk.py")
DOCKER = "/usr/bin/docker"
LOCK = Path("/run/lock/allscan-reimagined-managed-dmr-net-control.lock")
UNIFIED_NODE = "1999"
UNIFIED_USRP = (52000, 52001)
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class ControlError(RuntimeError):
    pass


def provisioning_backend(name: str):
    source = Path(__file__).resolve().parent / "asr-provisioning-backend.py"
    if not source.is_file():
        source = Path("/usr/local/libexec/allscan-reimagined/asr-provisioning-backend.py")
    spec = importlib.util.spec_from_file_location(name, source)
    if not spec or not spec.loader:
        raise ControlError("container provisioning backend unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config_path() -> Path:
    if not os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE"):
        return Path("/etc/allscan-reimagined/config.json")
    return provisioning_backend("asr_backend_dmr_control").map_path(
        Path("/"), "/etc/allscan-reimagined/config.json"
    )


def runtime_root() -> Path:
    if not os.environ.get("ASR_CONTAINER_PROVISIONING_PROFILE"):
        return Path("/opt/allscan-reimagined-bridges/urf")
    return provisioning_backend("asr_backend_dmr_runtime").map_path(
        Path("/"), "/opt/allscan-reimagined-bridges/urf"
    )


CONFIG = config_path()
ROOT = runtime_root()


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)


def load_config(path: Path = CONFIG) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ControlError("managed DMR configuration is unavailable") from exc
    if not isinstance(value, dict):
        raise ControlError("managed DMR configuration is invalid")
    return value


def bridge_config(bridge_id: str, path: Path = CONFIG) -> tuple[dict, dict]:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise ControlError("invalid bridge ID")
    config = load_config(path)
    for bridge in config.get("bridges", []):
        if (isinstance(bridge, dict) and bridge.get("id") == bridge_id
                and bridge.get("cardType") == "dmr_net"
                and bridge.get("mode") == "dmr"
                and bridge.get("backendMode") == "managed"
                and bridge.get("managedNetControl") is True):
            break
    else:
        raise ControlError("managed DMR Net Bridge not found")
    local_node = str(config.get("node", ""))
    if not re.fullmatch(r"[0-9]{3,10}", local_node):
        raise ControlError("main AllStar node is invalid")
    if str(bridge.get("node", "")) != UNIFIED_NODE:
        raise ControlError("managed DMR Net Bridge is not assigned to node 1999")
    try:
        ports = bridge["setupPorts"]
        usrp = int(ports["usrp_rx"]), int(ports["usrp_tx"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ControlError("managed DMR USRP configuration is invalid") from exc
    if usrp != UNIFIED_USRP:
        raise ControlError("managed DMR transport is not on the unified USRP endpoint")
    return config, bridge


def target_path(bridge: dict, root: Path = ROOT) -> Path:
    bridge_id = str(bridge.get("id") or "")
    logical = Path("/opt/allscan-reimagined-bridges/urf") / bridge_id / "tgif-run" / "net-target"
    if Path(str(bridge.get("managedTargetFile") or logical)) != logical:
        raise ControlError("invalid managed target path")
    path = root / bridge_id / "tgif-run" / "net-target"
    for parent in (root, root / bridge_id, path.parent):
        if parent.is_symlink():
            raise ControlError("unsafe managed target path")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ControlError("unsafe managed target file")
    return path


def read_target(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        talkgroup = int(value)
    except ValueError:
        return None
    return talkgroup if 1 <= talkgroup <= 16777215 and talkgroup != 4000 else None


def write_target(path: Path, talkgroup: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".net-target.", dir=path.parent)
    try:
        os.write(descriptor, f"{talkgroup}\n".encode())
        os.fchmod(descriptor, 0o644)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def parse_lstats(output: str) -> set[tuple[str, str]]:
    if not re.search(
        r"^NODE\s+PEER\s+RECONNECTS\s+DIRECTION\s+CONNECT TIME\s+CONNECT STATE\s*$",
        output, re.MULTILINE,
    ):
        raise ControlError("Asterisk direct-link status was not recognized")
    return set(re.findall(
        r"^([A-Za-z0-9_][A-Za-z0-9_-]*)[ \t]+\S+[ \t]+\d+[ \t]+(IN|OUT)[ \t]+",
        output, re.MULTILINE,
    ))


def checked_output(argv: list[str], label: str, runner: Runner = run) -> str:
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"{label} could not be read") from exc
    if result.returncode or not result.stdout.strip():
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise ControlError(f"{label} returned an error{': ' + detail if detail else ''}")
    return result.stdout


def direct_linked(local_node: str, runner: Runner = run) -> bool:
    output = checked_output(
        [str(ASTERISK), "-rx", f"rpt lstats {local_node}"],
        "Asterisk direct-link status", runner,
    )
    return (UNIFIED_NODE, "OUT") in parse_lstats(output)


def set_direct_link(local_node: str, linked: bool, runner: Runner = run) -> None:
    if direct_linked(local_node, runner) == linked:
        return
    result = runner([
        str(ASTERISK), "-rx",
        f"rpt cmd {local_node} ilink {'3' if linked else '11'} {UNIFIED_NODE}",
    ])
    if result.returncode:
        raise ControlError("Asterisk bridge-link command returned an error")
    for _ in range(40):
        if direct_linked(local_node, runner) == linked:
            return
        time.sleep(0.25)
    raise ControlError(
        f"Asterisk did not confirm the bridge-node {'link' if linked else 'unlink'}"
    )


def verify_owner(config: dict, bridge: dict, runner: Runner = run) -> None:
    if str(config.get("netBridgeMode", "")).lower() != "dmr":
        raise ControlError("DMR is not the selected Unified Net Bridge owner")
    channels = checked_output(
        [str(ASTERISK), "-rx", f"rpt show channels {UNIFIED_NODE}"],
        "Asterisk unified channel status", runner,
    )
    expected = f"usrp/127.0.0.1:{UNIFIED_USRP[0]}:{UNIFIED_USRP[1]}"
    match = re.search(r"(?mi)^rxchannel\s*:\s*(usrp/\S+)\s*$", channels)
    if not match or match.group(1).lower() != expected:
        raise ControlError("Asterisk node 1999 does not own the unified DMR USRP endpoint")
    compose = ROOT / str(bridge["id"]) / "compose.yml"
    output = checked_output(
        [DOCKER, "compose", "-f", str(compose), "-p", str(bridge["id"]),
         "ps", "--services", "--filter", "status=running"],
        "managed DMR runtime status", runner,
    )
    if set(output.split()) != {"urfd", "tcd", "tgif"}:
        raise ControlError("managed DMR runtime is not exclusively ready")


def status_payload(config: dict, bridge: dict, path: Path, runner: Runner = run) -> dict:
    talkgroup = read_target(path)
    owner_ready = False
    owner_error = ""
    try:
        verify_owner(config, bridge, runner)
        owner_ready = True
    except ControlError as exc:
        owner_error = str(exc)
    try:
        allstar_linked = direct_linked(str(config["node"]), runner)
    except ControlError as exc:
        allstar_linked = False
        owner_error = owner_error or str(exc)
    digital_linked = talkgroup is not None and owner_ready
    linked = digital_linked and allstar_linked
    result = {
        "ok": True, "bridgeId": bridge["id"],
        "currentTg": str(talkgroup or ""), "linked": linked,
        "digitalLinked": digital_linked, "allstarLinked": allstar_linked,
        "ready": owner_ready,
    }
    if owner_error:
        result["error"] = owner_error
    return result


def connect(config: dict, bridge: dict, path: Path, talkgroup: int,
            runner: Runner = run) -> dict:
    verify_owner(config, bridge, runner)
    old_talkgroup = read_target(path)
    write_target(path, talkgroup)
    try:
        set_direct_link(str(config["node"]), True, runner)
    except Exception:
        if old_talkgroup is None:
            path.unlink(missing_ok=True)
        else:
            write_target(path, old_talkgroup)
        raise
    result = status_payload(config, bridge, path, runner)
    if not result["linked"]:
        raise ControlError("managed DMR target and AllStar transport were not both confirmed")
    return result


def disconnect(config: dict, bridge: dict, path: Path, runner: Runner = run) -> dict:
    # Always attempt both halves so a damaged backend cannot leave node 1999 linked.
    path.unlink(missing_ok=True)
    set_direct_link(str(config["node"]), False, runner)
    result = status_payload(config, bridge, path, runner)
    if result["allstarLinked"] or result["currentTg"]:
        raise ControlError("managed DMR disconnect was not confirmed")
    result.update({"linked": False, "digitalLinked": False})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--connect")
    operation.add_argument("--disconnect", action="store_true")
    operation.add_argument("--status", action="store_true")
    args = parser.parse_args()
    try:
        config, bridge = bridge_config(args.bridge)
        path = target_path(bridge)
        if args.connect is not None:
            if not re.fullmatch(r"\d{1,8}", args.connect):
                raise ControlError("invalid talkgroup")
            talkgroup = int(args.connect)
            if not 1 <= talkgroup <= 16777215 or talkgroup == 4000:
                raise ControlError("invalid talkgroup")
        with LOCK.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if args.connect is not None:
                result = connect(config, bridge, path, talkgroup)
            elif args.disconnect:
                result = disconnect(config, bridge, path)
            else:
                result = status_payload(config, bridge, path)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (ControlError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
