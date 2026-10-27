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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

PROFILE_ENV = "ASR_CONTAINER_PROVISIONING_PROFILE"
DEFAULT_PROVISIONING_PROFILE = Path("/etc/allscan-reimagined/container-provisioning.json")
if (not os.environ.get(PROFILE_ENV) and os.geteuid() == 0
        and DEFAULT_PROVISIONING_PROFILE.is_file()):
    os.environ[PROFILE_ENV] = str(DEFAULT_PROVISIONING_PROFILE)
CONTAINERIZED = bool(os.environ.get(PROFILE_ENV))
ASTERISK = Path("/usr/local/libexec/allscan-reimagined/asr-container-asterisk.py"
                 if CONTAINERIZED else "/usr/sbin/asterisk")
DOCKER = "/usr/bin/docker"
SYSTEMCTL = "/usr/bin/systemctl"
LOCK = Path("/run/lock/allscan-reimagined-managed-dmr-net-control.lock")
UNIFIED_NODE = "1999"
UNIFIED_USRP = (52000, 52001)
TGIF_STATIC_API = "https://api.tgif.network/v1/static-talkgroups"
Runner = Callable


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


STATIC_OPENER = urllib.request.build_opener(NoRedirectHandler())


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


def static_token_path(bridge: dict, root: Path | None = None) -> Path:
    root = ROOT if root is None else root
    bridge_id = str(bridge.get("id") or "")
    logical = Path("/opt/allscan-reimagined-bridges/urf") / bridge_id / "secrets" / "tgif-static-token"
    configured = Path(str(bridge.get("tgifStaticTokenPath") or logical))
    if configured != logical:
        raise ControlError("invalid TGIF Static TG token path")
    path = root / bridge_id / "secrets" / "tgif-static-token"
    for parent in (root, root / bridge_id, path.parent):
        if parent.is_symlink():
            raise ControlError("unsafe TGIF Static TG token path")
    if path.exists():
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o077):
            raise ControlError("unsafe TGIF Static TG token file")
    return path


def read_static_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ControlError(
            "TGIF Static TG API token is not configured for this Net Bridge"
        ) from exc
    if (not 16 <= len(token) <= 4096
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
        raise ControlError("TGIF Static TG API token is invalid")
    return token


def write_static_token(path: Path, token: str) -> None:
    token = token.strip()
    if (not 16 <= len(token) <= 4096
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in token)):
        raise ControlError("TGIF Static TG API token is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tgif-static-token.", dir=path.parent)
    try:
        os.write(descriptor, (token + "\n").encode())
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def dmr_network_id(bridge: dict) -> int:
    raw = str(bridge.get("dmrNetworkId") or "").strip()
    if not raw:
        base = str(bridge.get("dmrId") or "").strip()
        if re.fullmatch(r"[1-9][0-9]{5,6}", base):
            raw = str(int(base) * 100 + 4)
    if not re.fullmatch(r"[1-9][0-9]{5,8}", raw):
        raise ControlError("TGIF device DMR ID/ESSID is unavailable; reinstall the DMR Net Bridge")
    return int(raw)


def tgif_static_request(bridge: dict, token_path: Path, method: str = "GET",
                        talkgroup: int | None = None) -> dict:
    token = read_static_token(token_path)
    device = dmr_network_id(bridge)
    body = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "AllScan-Reimagined Net Bridge",
    }
    if method in {"POST", "DELETE"}:
        body = json.dumps({"slot": 2, "talkgroup": talkgroup}, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{TGIF_STATIC_API}/{device}", data=body, headers=headers, method=method,
    )
    try:
        with STATIC_OPENER.open(request, timeout=12) as response:
            payload = json.loads(response.read(8192).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(4096).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {}
        request_id = str(payload.get("request_id") or "").strip()
        detail = f" (request {request_id})" if request_id else ""
        messages = {
            401: "TGIF rejected the Static TG API token",
            403: "TGIF denied this token for the configured DMR ID/ESSID",
            409: "TGIF reports the DMR bridge device is offline or Static TG is disabled",
            422: "TGIF rejected this talkgroup or timeslot",
            429: "TGIF rate limited the Static TG request; try again later",
            503: "TGIF Static TG service is temporarily unavailable",
        }
        raise ControlError(messages.get(exc.code, f"TGIF Static TG request failed ({exc.code})") + detail) from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ControlError("TGIF Static TG service could not be read") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ControlError("TGIF Static TG service returned an invalid response")
    if int(payload.get("device") or 0) != device:
        raise ControlError("TGIF Static TG response named the wrong DMR device")
    return payload


def static_membership_confirmed(payload: dict, talkgroup: int) -> bool:
    return any(
        isinstance(item, dict)
        and int(item.get("slot") or 0) == 2
        and int(item.get("talkgroup") or 0) == talkgroup
        for item in payload.get("memberships", [])
    )


def set_static_membership(bridge: dict, token_path: Path, talkgroup: int) -> None:
    state = tgif_static_request(bridge, token_path)
    if not bool(state.get("enabled")) or not bool(state.get("supports_ts2")):
        raise ControlError("TGIF Static TG is not enabled for timeslot 2 on this bridge device")
    if not static_membership_confirmed(state, talkgroup):
        tgif_static_request(bridge, token_path, "POST", talkgroup)
        state = tgif_static_request(bridge, token_path)
    if not static_membership_confirmed(state, talkgroup):
        raise ControlError("TGIF did not confirm the requested Static TG membership")


def remove_static_membership(bridge: dict, token_path: Path, talkgroup: int) -> None:
    tgif_static_request(bridge, token_path, "DELETE", talkgroup)
    state = tgif_static_request(bridge, token_path)
    if static_membership_confirmed(state, talkgroup):
        raise ControlError("TGIF did not remove the Static TG membership")


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


def confirmation_path(target: Path) -> Path:
    return target.with_name("static-membership.json")


def read_confirmation(path: Path, bridge: dict, talkgroup: int | None) -> bool:
    if talkgroup is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        info = path.stat()
    except (OSError, ValueError):
        return False
    return (
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not info.st_mode & 0o077
        and isinstance(payload, dict) and payload.get("schema") == 1
        and int(payload.get("device") or 0) == dmr_network_id(bridge)
        and int(payload.get("slot") or 0) == 2
        and int(payload.get("talkgroup") or 0) == talkgroup
        and int(payload.get("confirmedEpoch") or 0) > 0
    )


def write_confirmation(path: Path, bridge: dict, talkgroup: int) -> None:
    payload = {
        "schema": 1, "device": dmr_network_id(bridge), "slot": 2,
        "talkgroup": talkgroup, "confirmedEpoch": int(time.time()),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".static-membership.", dir=path.parent)
    try:
        os.write(descriptor, (json.dumps(payload, separators=(",", ":")) + "\n").encode())
        os.fchmod(descriptor, 0o600)
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
        r"^([A-Za-z0-9_][A-Za-z0-9_-]*)[ \t]+\S+[ \t]+\d+[ \t]+(IN|OUT)"
        r"[ \t]+\S+[ \t]+ESTABLISHED[ \t]*$",
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
    return any(node == UNIFIED_NODE for node, _direction in parse_lstats(output))


def set_direct_link(local_node: str, linked: bool, runner: Runner = run) -> None:
    if direct_linked(local_node, runner) == linked:
        return
    # Establish the private bridge from its own side.  A remote network may
    # advertise the same private node number in its transitive node list; in
    # that case app_rpt rejects local_node -> bridge_node as REMALREADY even
    # though there is no direct link.  The reverse local IAX connection is the
    # same transport and is not blocked by that unrelated advertisement.
    source, target = ((UNIFIED_NODE, local_node) if linked
                      else (local_node, UNIFIED_NODE))
    result = runner([
        str(ASTERISK), "-rx",
        f"rpt cmd {source} ilink {'3' if linked else '11'} {target}",
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
    if not CONTAINERIZED:
        bridge_id = str(bridge["id"])
        units = (
            f"allscan-reimagined-urf-tgif-{bridge_id}.service",
            f"allscan-reimagined-urfd-{bridge_id}.service",
            f"allscan-reimagined-tcd-{bridge_id}.service",
            f"allscan-reimagined-tgif-{bridge_id}.service",
        )
        if any(runner([SYSTEMCTL, "is-active", "--quiet", unit]).returncode for unit in units):
            raise ControlError("managed native DMR runtime is not ready")
        return
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
    token_ready = False
    try:
        read_static_token(static_token_path(bridge))
        token_ready = True
    except ControlError as exc:
        owner_error = owner_error or str(exc)
    static_confirmed = read_confirmation(confirmation_path(path), bridge, talkgroup)
    digital_linked = talkgroup is not None and owner_ready and token_ready and static_confirmed
    linked = digital_linked and allstar_linked
    result = {
        "ok": True, "bridgeId": bridge["id"],
        "currentTg": str(talkgroup or ""), "linked": linked,
        "digitalLinked": digital_linked, "allstarLinked": allstar_linked,
        "ready": owner_ready, "staticControlConfigured": token_ready,
        "staticMembershipConfirmed": static_confirmed,
    }
    if owner_error:
        result["error"] = owner_error
    return result


def connect(config: dict, bridge: dict, path: Path, talkgroup: int,
            runner: Runner = run) -> dict:
    verify_owner(config, bridge, runner)
    token_path = static_token_path(bridge)
    read_static_token(token_path)
    old_talkgroup = read_target(path)
    old_confirmed = read_confirmation(confirmation_path(path), bridge, old_talkgroup)
    set_static_membership(bridge, token_path, talkgroup)
    old_removed = False
    try:
        if old_talkgroup is not None and old_talkgroup != talkgroup:
            remove_static_membership(bridge, token_path, old_talkgroup)
            old_removed = True
        write_target(path, talkgroup)
        write_confirmation(confirmation_path(path), bridge, talkgroup)
        set_direct_link(str(config["node"]), True, runner)
    except Exception:
        try:
            remove_static_membership(bridge, token_path, talkgroup)
            if old_talkgroup is not None and old_removed:
                set_static_membership(bridge, token_path, old_talkgroup)
        except ControlError:
            pass
        if old_talkgroup is None:
            path.unlink(missing_ok=True)
        else:
            write_target(path, old_talkgroup)
            if old_confirmed:
                write_confirmation(confirmation_path(path), bridge, old_talkgroup)
            else:
                confirmation_path(path).unlink(missing_ok=True)
        raise
    result = status_payload(config, bridge, path, runner)
    if not result["linked"]:
        raise ControlError("managed DMR target and AllStar transport were not both confirmed")
    return result


def disconnect(config: dict, bridge: dict, path: Path, runner: Runner = run) -> dict:
    # Always attempt both halves so a damaged backend cannot leave node 1999 linked.
    talkgroup = read_target(path)
    api_error = None
    token_path = static_token_path(bridge)
    if talkgroup is not None and token_path.exists():
        try:
            remove_static_membership(bridge, token_path, talkgroup)
        except ControlError as exc:
            api_error = exc
    path.unlink(missing_ok=True)
    confirmation_path(path).unlink(missing_ok=True)
    set_direct_link(str(config["node"]), False, runner)
    result = status_payload(config, bridge, path, runner)
    if result["allstarLinked"] or result["currentTg"]:
        raise ControlError("managed DMR disconnect was not confirmed")
    result.update({"linked": False, "digitalLinked": False})
    if api_error is not None:
        raise api_error
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--connect")
    operation.add_argument("--disconnect", action="store_true")
    operation.add_argument("--status", action="store_true")
    operation.add_argument("--set-static-token", action="store_true")
    operation.add_argument("--clear-static-token", action="store_true")
    args = parser.parse_args()
    try:
        config, bridge = bridge_config(args.bridge)
        path = target_path(bridge)
        token_path = static_token_path(bridge)
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
            elif args.set_static_token:
                write_static_token(token_path, sys.stdin.readline())
                confirmation_path(path).unlink(missing_ok=True)
                result = {"ok": True, "bridgeId": bridge["id"], "staticControlConfigured": True}
            elif args.clear_static_token:
                if read_target(path) is not None:
                    raise ControlError("disconnect the DMR Net Bridge before removing its Static TG token")
                token_path.unlink(missing_ok=True)
                result = {"ok": True, "bridgeId": bridge["id"], "staticControlConfigured": False}
            else:
                result = status_payload(config, bridge, path)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (ControlError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
