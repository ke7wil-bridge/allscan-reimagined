#!/usr/bin/env python3
"""Recover opted-in fixed bridges and required AllStar links safely."""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import stat
from contextlib import contextmanager
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_CONFIG = Path("/etc/allscan-reimagined/config.json")
DEFAULT_RPT_CONFIG = Path("/etc/asterisk/rpt.conf")
MIGRATION_QUIESCE_STATE = Path("/var/lib/allscan-reimagined/bridge-setup/migration-quiesced.json")
DEFAULT_STATE = Path("/run/allscan-reimagined-fixed-bridge-recovery/state.json")
RECOVERY_LOCK = Path("/run/lock/allscan-reimagined-fixed-bridge/recovery.lock")
ASTERISK = Path("/usr/sbin/asterisk")
RUNUSER = Path("/usr/sbin/runuser")
NODE_RE = re.compile(r"^[0-9]{3,10}$")
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")


class RecoveryError(RuntimeError):
    pass


def read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"Could not read ASR configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecoveryError("ASR configuration must be a JSON object.")
    return payload


def uncommented(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("#"):
        return ""
    return stripped.split(";", 1)[0].strip()


def section_values(text: str, section_name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    active = False
    for raw_line in text.splitlines():
        section = SECTION_RE.match(raw_line)
        if section:
            active = section.group(1).strip() == section_name
            continue
        if not active:
            continue
        line = uncommented(raw_line)
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().lower()] = value.strip()
    return values


def named_macro_value(text: str, name: str) -> str:
    wanted = name.strip().lower()
    if not wanted:
        return ""
    for raw_line in text.splitlines():
        line = uncommented(raw_line)
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == wanted:
            return value.strip()
    return ""


def native_permanent_nodes(text: str, main_node: str) -> set[str]:
    startup = section_values(text, main_node).get("startup_macro", "").strip()
    if not startup:
        return set()
    expanded = startup
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", startup):
        expanded = named_macro_value(text, startup)
    nodes = set(
        re.findall(
            r"(?i)(?:^|[\s*])(?:COP\s*,\s*6\s*,\s*[0-9]+\s*,\s*)?"
            r"ilink\s*,\s*13\s*,\s*([0-9]{3,10})(?=$|[\s*])",
            expanded,
        )
    )
    # app_rpt DTMF function 813 is the native permanent-connect command.
    nodes.update(re.findall(r"\*813([0-9]{3,10})(?=$|[\s*])", expanded))
    return nodes


def read_startup_config(path: Path) -> str:
    """Expand bounded literal and final-component wildcard includes inside the Asterisk config tree.

    Never execute #exec, follow symlinks, or read arbitrary host paths. An
    uninspectable configuration cannot justify a durable disconnect promise.
    """
    base = path.parent.resolve(strict=True)
    remaining = 2 * 1024 * 1024
    visited = 0

    def expand(candidate: Path, stack: tuple[Path, ...]) -> str:
        nonlocal remaining, visited
        if len(stack) >= 12 or visited >= 64 or candidate in stack:
            raise RecoveryError("Asterisk startup include depth or cycle cannot be verified.")
        try:
            relative = candidate.relative_to(base)
        except ValueError as exc:
            raise RecoveryError("Asterisk startup include is outside the config directory.") from exc
        if ".." in relative.parts or not relative.parts:
            raise RecoveryError("Asterisk startup include path is unsafe.")
        directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = child
            fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o002:
                raise RecoveryError("Asterisk startup config is not a safe regular file.")
            content = handle.read(remaining + 1)
        remaining -= len(content); visited += 1
        if remaining < 0:
            raise RecoveryError("Asterisk startup configuration exceeds inspection limits.")
        lines = []
        for line in content.decode("utf-8").splitlines():
            if ";--" in line or "--;" in line or line.rstrip().endswith("\\"):
                raise RecoveryError("Asterisk startup config uses unsupported multiline syntax.")
            directive = line.strip().split(";", 1)[0].strip()
            if not directive.startswith("#"):
                lines.append(line); continue
            match = re.fullmatch(r'#(include|tryinclude)\s+(?:"([^"\n]+)"|([^\s]+))', directive, re.I)
            if not match:
                raise RecoveryError("Unsupported Asterisk startup configuration directive.")
            name = match.group(2) or match.group(3)
            if any(char in name for char in "?[]${}\\"):
                raise RecoveryError("Dynamic Asterisk startup includes cannot be verified.")
            include = Path(name)
            if include.is_absolute():
                try:
                    include = include.relative_to("/etc/asterisk")
                except ValueError as exc:
                    raise RecoveryError("Asterisk startup include is outside the config directory.") from exc
            target = base / include
            if ".." in include.parts or not include.parts or target.is_symlink():
                raise RecoveryError("Asterisk startup include path is unsafe.")
            optional = match.group(1).lower() == "tryinclude"
            if "*" in name:
                # Stock ASL3 includes custom/rpt/*.conf. Limit globbing to
                # the final filename component of a literal subdirectory.
                if (len(include.parts) < 2 or any("*" in part for part in include.parts[:-1])
                        or not re.fullmatch(r"[A-Za-z0-9_.-]*\*[A-Za-z0-9_.-]*", include.name)):
                    raise RecoveryError("Dynamic Asterisk startup includes cannot be verified.")
                directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for part in include.parts[:-1]:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        os.close(directory); directory = child
                    names = sorted(entry for entry in os.listdir(directory) if not entry.startswith(".")
                                   and fnmatch.fnmatchcase(entry, include.name))
                except FileNotFoundError:
                    if not optional: raise
                    names = []
                finally:
                    os.close(directory)
                if len(names) > 64 or (not names and not optional):
                    raise RecoveryError("Asterisk startup include matches cannot be verified.")
                for entry in names:
                    lines.append(expand(target.parent / entry, (*stack, candidate)))
            else:
                try:
                    lines.append(expand(target, (*stack, candidate)))
                except FileNotFoundError:
                    if not optional or target.exists():
                        raise
        return "\n".join(lines) + "\n"

    return expand(base / path.name, ())


def verified_startup_nodes(text: str, node: str) -> set[str]:
    """Recognize a conservative subset of app_rpt startup macro syntax."""
    sections: dict[str, list[tuple[list[str], dict[str, list[str]]]]] = {}
    current = None
    for raw in text.splitlines():
        line = uncommented(raw)
        if not line:
            continue
        if line.startswith("["):
            header = re.fullmatch(r"\[([^\]]+)\]\s*(?:\(([^)]*)\))?", line)
            if not header:
                raise RecoveryError("Asterisk startup section syntax cannot be verified.")
            name = header.group(1).strip()
            parents = [part.strip() for part in (header.group(2) or "").split(",") if part.strip()]
            current = (parents, {})
            sections.setdefault(name, []).append(current)
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[1].setdefault(key.strip().lower(), []).append(value.strip())

    def effective(name: str, key: str, chain: tuple[str, ...] = ()) -> str:
        if name in chain or len(chain) >= 12:
            raise RecoveryError("Asterisk startup template inheritance cannot be verified.")
        rows = sections.get(name, [])
        if not rows:
            raise RecoveryError("Asterisk startup section or template is missing.")
        if len(rows) != 1:
            raise RecoveryError("Multiple Asterisk startup section definitions cannot be verified.")
        parents, fields = rows[0]
        values = fields.get(key, [])
        if len(values) > 1 or "+" in parents:
            raise RecoveryError("Asterisk startup override is ambiguous.")
        if values:
            return values[0]
        inherited = {effective(parent, key, (*chain, name)) for parent in parents if parent != "!"}
        inherited.discard("")
        if len(inherited) > 1:
            raise RecoveryError("Asterisk startup template values conflict.")
        return next(iter(inherited), "")

    startup = effective(node, "startup_macro")
    if not startup:
        return set()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", startup):
        macro_section = effective(node, "macro") or "macro"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", macro_section):
            raise RecoveryError("Asterisk startup macro table cannot be verified.")
        startup = effective(macro_section, startup.lower())
        if not startup:
            raise RecoveryError("Named Asterisk startup macro cannot be verified.")
    pattern = re.compile(r"(?:\*?COP\s*,\s*6\s*,\s*[0-9]+\s*,\s*)?\*?ilink\s*,\s*(?:3|13)\s*,\s*([0-9]{3,10})(?=$|[\s*])|\*(?:813|3)([0-9]{3,10})(?=$|[\s*])", re.I)
    nodes = set(); position = 0
    while position < len(startup):
        if startup[position].isspace():
            position += 1; continue
        match = pattern.match(startup, position)
        if not match:
            raise RecoveryError("Asterisk startup macro contains unsupported commands.")
        nodes.add(match.group(1) or match.group(2)); position = match.end()
    return nodes


def inspect_native_holds(config: dict, rpt_path: Path) -> dict:
    held = manually_held_nodes(config)
    main = str(config.get("node", ""))
    conflicts, unknown = [], []
    try:
        text = read_startup_config(rpt_path) if held else ""
    except (RecoveryError, OSError, ValueError):
        return {"nativeManualHoldConflicts": [], "nativeManualHoldUnknown": sorted(held)}
    for node in sorted(held):
        try:
            if node in verified_startup_nodes(text, main) or main in verified_startup_nodes(text, node):
                conflicts.append(node)
        except RecoveryError:
            unknown.append(node)
    return {"nativeManualHoldConflicts": conflicts, "nativeManualHoldUnknown": unknown}


def manually_held_nodes(config: dict) -> set[str]:
    main_node = str(config.get("node", "")).strip()
    return {
        node for bridge in config.get("bridges", [])
        if isinstance(bridge, dict)
        and bridge.get("cardType", "standard") == "standard"
        and bridge.get("manualDisconnect") is True
        and NODE_RE.fullmatch(node := str(bridge.get("node", "")).strip())
        and node != main_node
    }


def configured_targets(config: dict, quiesced_nodes: set[str] | None = None) -> tuple[str, list[dict], list[dict]]:
    quiesced_nodes = quiesced_nodes or set()
    main_node = str(config.get("node", "")).strip()
    if not NODE_RE.fullmatch(main_node):
        raise RecoveryError("The configured main AllStar node is invalid.")
    held = manually_held_nodes(config)
    targets: list[dict] = []
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict):
            continue
        bridge_id = str(bridge.get("id", "")).strip().lower()
        if bridge.get("cardType", "standard") != "standard":
            continue
        if bridge.get("fixedBridgeRecovery") is not True:
            continue
        # Explicit operator hold takes precedence over periodic fallback recovery.
        if bridge.get("manualDisconnect") is True:
            continue
        node = str(bridge.get("node", "")).strip()
        if not NODE_RE.fullmatch(node) or node == main_node or node in quiesced_nodes or node in held:
            continue
        targets.append({
            "id": bridge_id or "bridge",
            "node": node,
        })
    required_links: list[dict] = []
    seen = {main_node, *(target["node"] for target in targets)}
    rows = config.get("requiredAllStarLinks", [])
    if not isinstance(rows, list):
        raise RecoveryError("requiredAllStarLinks must be a list.")
    if len(rows) > 4:
        raise RecoveryError("requiredAllStarLinks supports at most four nodes.")
    for row in rows:
        node = str(row.get("node", "") if isinstance(row, dict) else row).strip()
        if not NODE_RE.fullmatch(node) or node in seen:
            raise RecoveryError("requiredAllStarLinks contains an invalid or duplicate node.")
        seen.add(node)
        # A second recovery policy must not bypass the explicit operator hold
        # or a migration quiesce. Validate duplicates even for skipped nodes.
        if node not in held and node not in quiesced_nodes:
            required_links.append({"id": "allstar", "node": node})
    return main_node, targets, required_links


def recovery_plan(config: dict, rpt_text: str, quiesced_nodes: set[str] | None = None) -> dict:
    main_node, targets, required_links = configured_targets(config, quiesced_nodes)
    native = native_permanent_nodes(rpt_text, main_node)
    # A native Asterisk permanent link can defeat a manual hold even when
    # ASR's fallback timer is suppressed. Report it; never rewrite rpt.conf.
    held_nodes = manually_held_nodes(config)
    native_conflicts = sorted(node for node in held_nodes if node in native
                              or main_node in native_permanent_nodes(rpt_text, node))
    fallback = [target for target in targets if target["node"] not in native]
    required_fallback = [target for target in required_links if target["node"] not in native]
    return {
        "mainNode": main_node,
        "configured": targets,
        "nativePermanent": [target for target in targets if target["node"] in native],
        "nativeManualHoldConflicts": native_conflicts,
        "manualHeldNodes": sorted(held_nodes),
        "fallback": fallback,
        "requiredLinks": required_fallback,
        "nativeRequiredLinks": [target for target in required_links if target["node"] in native],
    }


def asterisk_command(arguments: Iterable[str]) -> list[str]:
    command = [str(ASTERISK), "-rx", " ".join(arguments)]
    if os.geteuid() == 0:
        try:
            pwd.getpwnam("asterisk")
        except KeyError:
            pass
        else:
            if RUNUSER.is_file():
                command = [str(RUNUSER), "-u", "asterisk", "--", *command]
    return command


def run_asterisk(arguments: Iterable[str]) -> str:
    result = subprocess.run(
        asterisk_command(arguments),
        cwd="/",
        text=True,
        capture_output=True,
        timeout=12,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RecoveryError(detail or "The Asterisk command failed.")
    return result.stdout


def listed_local_nodes(output: str) -> set[str]:
    return set(re.findall(r"(?<![0-9])([0-9]{3,10})(?![0-9])", output))


def established_nodes(output: str) -> set[str]:
    established: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and NODE_RE.fullmatch(fields[0]) and fields[-1].upper() == "ESTABLISHED":
            established.add(fields[0])
    return established


def verified_link_state(output: str, node: str) -> str:
    """Distinguish absent, established, and transitional lstats rows.

    A reconnecting IAX channel is not an established connection, but it is
    also not a verified disconnect. Never accept an unknown response as proof.
    """
    if not re.search(r"(?im)^\s*NODE\s+PEER\b", output):
        raise RecoveryError("Asterisk link status could not be verified.")
    states = []
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] == node:
            if len(fields) < 3:
                raise RecoveryError("Asterisk link row could not be verified.")
            states.append(fields[-1].upper())
    if not states:
        return "absent"
    return "established" if all(state == "ESTABLISHED" for state in states) else "pending"


def autopatch_up(output: str) -> bool:
    return re.search(r"(?im)^\s*RPT_AUTOPATCHUP\s*=\s*1\s*$", output) is not None


def clear_bridge_autopatch(node: str, runner: Callable[[Iterable[str]], str]) -> bool:
    """Clear impossible autopatch state only on an opted-in transport bridge."""
    if not autopatch_up(runner(["rpt", "show", "variables", node])):
        return False
    runner(["rpt", "cmd", node, "cop", "9"])
    try:
        runner(["rpt", "cmd", node, "autopatchdn"])
    finally:
        runner(["rpt", "cmd", node, "cop", "10"])
    if autopatch_up(runner(["rpt", "show", "variables", node])):
        raise RecoveryError(f"Bridge node {node} remained in autopatch state after guarded cleanup.")
    return True


def read_retry_state(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_retry_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".state.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def retry_due(state: dict, node: str, now: int) -> bool:
    entry = state.get(node)
    try:
        next_attempt = int(entry.get("next", 0) or 0) if isinstance(entry, dict) else 0
    except (TypeError, ValueError):
        next_attempt = 0
    return next_attempt <= now


def record_retry(state: dict, node: str, now: int) -> None:
    prior = state.get(node) if isinstance(state.get(node), dict) else {}
    try:
        failures = max(0, int(prior.get("failures", 0) or 0)) + 1
    except (TypeError, ValueError):
        failures = 1
    delays = (30, 60, 300)
    state[node] = {"failures": failures, "next": now + delays[min(failures - 1, len(delays) - 1)]}


def wait_for_link(main_node: str, node: str, runner: Callable[[Iterable[str]], str],
                  sleeper: Callable[[float], None], attempts: int = 6,
                  interval: float = 0.25) -> bool:
    for attempt in range(attempts):
        if node in established_nodes(runner(["rpt", "lstats", main_node])):
            return True
        if attempt + 1 < attempts:
            sleeper(interval)
    return False


def recover_once(plan: dict, runner: Callable[[Iterable[str]], str] = run_asterisk,
                 sleeper: Callable[[float], None] = time.sleep,
                 retry_state: dict | None = None, now: int | None = None) -> dict:
    fallback = plan.get("fallback", [])
    required_links = plan.get("requiredLinks", [])
    retry_state = retry_state if retry_state is not None else {}
    now = int(time.time()) if now is None else now
    if not fallback and not required_links:
        return {"checked": 0, "reconnected": [], "reverseFallback": [], "failed": [],
                "unavailable": [], "autopatchCleared": [], "backingOff": []}
    main_node = str(plan["mainNode"])
    required_nodes = {str(target["node"]) for target in required_links}
    for stale_node in set(retry_state) - required_nodes:
        retry_state.pop(stale_node, None)
    local_nodes = listed_local_nodes(runner(["rpt", "localnodes"]))
    if main_node not in local_nodes:
        raise RecoveryError(f"Main node {main_node} is not ready locally.")
    established = established_nodes(runner(["rpt", "lstats", main_node]))
    reconnected: list[str] = []
    reverse_fallback: list[str] = []
    failed: list[str] = []
    unavailable: list[str] = []
    autopatch_cleared: list[str] = []
    backing_off: list[str] = []
    for target in fallback:
        node = str(target["node"])
        if node not in local_nodes:
            unavailable.append(node)
            continue
        if clear_bridge_autopatch(node, runner):
            autopatch_cleared.append(node)
        if node in established:
            continue
        runner(["rpt", "cmd", main_node, "ilink", "3", node])
        if wait_for_link(main_node, node, runner, sleeper):
            reconnected.append(node)
            continue
        # Some local app_rpt doorway nodes accept the main-node command but do
        # not originate its IAX call. The equivalent doorway-side command is a
        # valid permanent link and must be verified before reporting success.
        runner(["rpt", "cmd", node, "ilink", "3", main_node])
        if wait_for_link(main_node, node, runner, sleeper):
            reconnected.append(node)
            reverse_fallback.append(node)
        else:
            failed.append(node)
    for target in required_links:
        node = str(target["node"])
        if node in established:
            retry_state.pop(node, None)
            continue
        if not retry_due(retry_state, node, now):
            backing_off.append(node)
            continue
        runner(["rpt", "cmd", main_node, "ilink", "3", node])
        if wait_for_link(main_node, node, runner, sleeper, attempts=10, interval=0.5):
            reconnected.append(node)
            retry_state.pop(node, None)
            established.add(node)
        else:
            failed.append(node)
            record_retry(retry_state, node, now)
    return {
        "checked": len(fallback) + len(required_links),
        "reconnected": reconnected,
        "reverseFallback": reverse_fallback,
        "failed": failed,
        "unavailable": unavailable,
        "autopatchCleared": autopatch_cleared,
        "backingOff": backing_off,
    }


@contextmanager
def recovery_lock(path: Path = RECOVERY_LOCK):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o022:
            raise RecoveryError("Unsafe recovery lock.")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def read_control_config(path: Path) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o002:
            raise RecoveryError("Unsafe ASR configuration file.")
        content = handle.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise RecoveryError("ASR configuration exceeds size limit.")
        return content, info


def save_manual_hold(path: Path, original: bytes, config: dict, bridge: dict, held: bool) -> None:
    # The config directory is shared with Settings. Never follow a substituted
    # symlink or overwrite a configuration that changed during the operation.
    content, info = read_control_config(path)
    if content != original:
        raise RecoveryError("Settings changed during bridge control; retry.")
    bridge["manualDisconnect"] = held
    descriptor, temporary = tempfile.mkstemp(prefix=".manual-hold.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(config, indent=2, sort_keys=True) + "\n").encode())
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), info.st_uid, info.st_gid)
            handle.flush(); os.fsync(handle.fileno())
        content, current = read_control_config(path)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or content != original:
            raise RecoveryError("Settings changed during bridge control; retry.")
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


# Existing root operation locks: provisioning, lifecycle, Net Bridge mode,
# qualification, and install/rollback. Do not change their acquisition order
# or take them in their nested subprocesses; only explicit manual control
# tries the whole set, non-blocking, and releases everything on contention.
WRITER_LOCKS = (
    "allscan-reimagined-rollback.lock",
    "allscan-reimagined-bridge-setup.lock",
    "allscan-reimagined-bridge-lifecycle.lock",
    "allscan-reimagined-m17-qualification.lock",
    "allscan-reimagined-net-bridge-mode.lock",
    "allscan-reimagined-container-updater.lock",
)


@contextmanager
def config_writer_guard(directory: Path = Path("/run/lock"), *, expected_uid: int = 0):
    descriptors = []
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in WRITER_LOCKS:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=dir_fd)
            descriptors.append(fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_uid
                    or info.st_nlink != 1 or info.st_mode & 0o022):
                raise RecoveryError("ASR configuration operation lock is unsafe.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecoveryError("ASR provisioning, qualification, or maintenance is active; retry bridge control.") from exc
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
        os.close(dir_fd)


@contextmanager
def config_directory_lock(directory: Path):
    # Linux flock on the directory inode is shared by the native and Docker
    # bind-mounted Settings directory. No replaceable lock file in a writable
    # config directory is needed. Settings takes the same short-lived lock.
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryError("ASR Settings are being saved; retry bridge control.") from exc
        yield
    finally:
        os.close(fd)


def manual_link(config_path: Path, rpt_path: Path, bridge_id: str, verb: str,
                runner=run_asterisk, sleeper=time.sleep) -> dict:
    with config_directory_lock(config_path.parent):
        return manual_link_locked(config_path, rpt_path, bridge_id, verb, runner, sleeper)


def manual_link_locked(config_path: Path, rpt_path: Path, bridge_id: str, verb: str,
                       runner, sleeper) -> dict:
    if verb not in {"connect", "disconnect"} or not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", bridge_id):
        raise RecoveryError("Invalid Standard Bridge control request.")
    original, _ = read_control_config(config_path)
    config = json.loads(original)
    if not isinstance(config, dict) or not isinstance(config.get("bridges"), list):
        raise RecoveryError("Invalid ASR configuration.")
    main = str(config.get("node", ""))
    bridges = config.get("bridges", [])
    matches = [row for row in bridges if isinstance(row, dict) and row.get("id") == bridge_id]
    if len(matches) != 1 or matches[0].get("cardType", "standard") != "standard":
        raise RecoveryError("Configured Standard Bridge was not found.")
    bridge = matches[0]; node = str(bridge.get("node", ""))
    if not NODE_RE.fullmatch(main) or not NODE_RE.fullmatch(node) or node in {main, "1999"}:
        raise RecoveryError("Invalid or reserved bridge doorway.")
    if sum(isinstance(row, dict) and str(row.get("node", "")) == node for row in bridges) != 1:
        raise RecoveryError("Bridge doorway is shared or ambiguous.")
    local = listed_local_nodes(runner(["rpt", "localnodes"]))
    if not {main, node} <= local:
        raise RecoveryError("Both configured nodes must be local and available.")
    if verb == "disconnect":
        # Do not promise a persistent hold when the effective startup config
        # is uninspectable. Leave both the current link and settings alone.
        inspection = inspect_native_holds({"node": main, "bridges": [{**bridge, "manualDisconnect": True}]}, rpt_path)
        if node in inspection["nativeManualHoldConflicts"]:
            raise RecoveryError("Native Asterisk permanent startup link conflicts with manual disconnect. Remove or migrate that startup link first; no settings changed.")
        if node in inspection["nativeManualHoldUnknown"]:
            raise RecoveryError("Native Asterisk startup links could not be verified. Review startup macros and includes; no settings changed.")
    def link_state():
        return verified_link_state(runner(["rpt", "lstats", main]), node)
    current = link_state()
    desired = verb == "connect"
    expected = "established" if desired else "absent"
    if not desired:
        # Durable hold must precede unlink, even if Asterisk refuses the command.
        save_manual_hold(config_path, original, config, bridge, True)
    if current != expected:
        runner(["rpt", "cmd", main, "ilink", "3" if desired else "11", node])
        for _ in range(12):
            if link_state() == expected:
                break
            sleeper(0.25)
        else:
            runner(["rpt", "cmd", node, "ilink", "3" if desired else "11", main])
            for _ in range(12):
                if link_state() == expected:
                    break
                sleeper(0.25)
            else:
                raise RecoveryError("Asterisk did not confirm the requested link state. " + ("Manual hold is retained." if not desired else "Prior recovery policy is unchanged."))
    if desired:
        save_manual_hold(config_path, original, config, bridge, False)
    # A configuration writer outside the coordinated Settings/control path
    # may still have replaced the file during Asterisk command execution.
    # Never claim a durable hold when the stored identity or flag changed.
    latest_raw, _ = read_control_config(config_path)
    latest = json.loads(latest_raw)
    rows = [row for row in latest.get("bridges", []) if isinstance(row, dict)
            and row.get("id") == bridge_id] if isinstance(latest, dict) else []
    if (len(rows) != 1 or str(latest.get("node", "")) != main
            or str(rows[0].get("node", "")) != node
            or rows[0].get("mode") != bridge.get("mode")
            or rows[0].get("cardType", "standard") != "standard"
            or rows[0].get("manualDisconnect") is not (not desired)):
        raise RecoveryError("Bridge settings changed during link control; saved recovery policy could not be verified.")
    return {"ok": True, "bridgeId": bridge_id, "manualDisconnect": not desired,
            "linked": desired, "message": "Bridge reconnected; normal recovery policy restored." if desired else "Bridge disconnected; ASR automatic recovery paused."}


def load_quiesced_nodes(path: Path = MIGRATION_QUIESCE_STATE) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"Could not read bridge migration quiesce state: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes", []), list):
        raise RecoveryError("Bridge migration quiesce state is invalid; recovery is stopped safely.")
    nodes = {str(value).strip() for value in payload.get("nodes", [])}
    if any(not NODE_RE.fullmatch(node) for node in nodes):
        raise RecoveryError("Bridge migration quiesce state contains an invalid node; recovery is stopped safely.")
    return nodes


def load_plan(config_path: Path, rpt_path: Path,
              quiesce_path: Path = MIGRATION_QUIESCE_STATE) -> dict:
    try:
        rpt_text = rpt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RecoveryError(f"Could not read Asterisk configuration: {exc}") from exc
    return recovery_plan(read_json_object(config_path), rpt_text, load_quiesced_nodes(quiesce_path))


def self_test() -> None:
    config = {
        "node": "600000",
        "bridges": [
            {"id": "dmr", "node": "1201", "cardType": "standard", "fixedBridgeRecovery": True},
            {"id": "ysf", "node": "1202", "cardType": "standard", "fixedBridgeRecovery": True},
            {"id": "m17", "node": "1205", "cardType": "standard", "fixedBridgeRecovery": True},
            {"id": "zello", "node": "1203", "cardType": "standard", "fixedBridgeRecovery": False},
            {"id": "ysf_net", "node": "1204", "cardType": "ysf_net", "fixedBridgeRecovery": True},
        ],
        "requiredAllStarLinks": ["578990"],
    }
    rpt_text = """
[600000](node-main)
startup_macro = A1

[macro]
A1 = *COP,6,12345,ilink,13,1201 *COP,6,12345,ilink,13,1203
[node-main](!)
[1201]
[1202]
[1203]
[1205]
"""
    plan = recovery_plan(config, rpt_text)
    assert [item["node"] for item in plan["nativePermanent"]] == ["1201"]
    assert [item["node"] for item in plan["fallback"]] == ["1202", "1205"]
    native_required = recovery_plan(
        config, rpt_text.replace("*COP,6,12345,ilink,13,1203", "*813578990*COP,6,12345,ilink,13,1203")
    )
    assert native_required["requiredLinks"] == []
    assert [item["node"] for item in native_required["nativeRequiredLinks"]] == ["578990"]
    # Manual disconnect is scoped to one bridge; other configured links recover.
    held_config = {**config, "bridges": [
        {**bridge, "manualDisconnect": bridge["id"] == "ysf"}
        for bridge in config["bridges"]
    ]}
    held_plan = recovery_plan(held_config, rpt_text)
    assert [item["node"] for item in held_plan["fallback"]] == ["1205"]
    assert [item["node"] for item in held_plan["nativePermanent"]] == ["1201"]
    native_held_config = {**config, "bridges": [
        {**bridge, "manualDisconnect": bridge["id"] == "dmr"}
        for bridge in config["bridges"]
    ]}
    assert recovery_plan(native_held_config, rpt_text)["nativeManualHoldConflicts"] == ["1201"]
    assert held_plan["nativeManualHoldConflicts"] == []
    assert held_plan["manualHeldNodes"] == ["1202"]
    required_held = recovery_plan({**held_config, "requiredAllStarLinks": ["1202", "578990"]}, rpt_text)
    assert [item["node"] for item in required_held["requiredLinks"]] == ["578990"]
    assert [item["node"] for item in required_held["fallback"]] == ["1205"]
    native_required_held = recovery_plan({**native_held_config, "requiredAllStarLinks": ["1201", "578990"]}, rpt_text)
    assert native_required_held["nativeRequiredLinks"] == []
    assert native_required_held["nativeManualHoldConflicts"] == ["1201"]
    quiesced_required = recovery_plan({**config, "requiredAllStarLinks": ["1202", "578990"]}, rpt_text, {"1202"})
    assert [item["node"] for item in quiesced_required["requiredLinks"]] == ["578990"]
    resumed_config = {**held_config, "bridges": [
        {**bridge, "manualDisconnect": False} if bridge["id"] == "ysf" else bridge
        for bridge in held_config["bridges"]
    ]}
    assert [item["node"] for item in recovery_plan(resumed_config, rpt_text)["fallback"]] == ["1202", "1205"]
    quiesced_plan = recovery_plan(config, rpt_text, {"1202"})
    assert [item["node"] for item in quiesced_plan["fallback"]] == ["1205"]
    assert all(item["node"] != "1202" for item in quiesced_plan["configured"])

    calls: list[tuple[str, ...]] = []

    connected = {"1201"}
    patch_up = {"1202"}

    def runner(arguments: Iterable[str]) -> str:
        command = tuple(arguments)
        calls.append(command)
        if command == ("rpt", "localnodes"):
            return "Local nodes: 1201 1202 1203 1205 600000\n"
        if command == ("rpt", "lstats", "600000"):
            rows = "".join(f"{node} 127.0.0.1 ESTABLISHED\n" for node in sorted(connected))
            return "NODE PEER STATE\n" + rows
        if command[:4] == ("rpt", "show", "variables", "1202"):
            return "   RPT_AUTOPATCHUP=1\n" if "1202" in patch_up else "   RPT_AUTOPATCHUP=0\n"
        if command[:4] == ("rpt", "show", "variables", "1205"):
            return "RPT_AUTOPATCHUP=0\n"
        if command == ("rpt", "cmd", "1202", "cop", "9"):
            return ""
        if command == ("rpt", "cmd", "1202", "autopatchdn"):
            patch_up.discard("1202")
            return ""
        if command == ("rpt", "cmd", "1202", "cop", "10"):
            return ""
        if command == ("rpt", "cmd", "600000", "ilink", "3", "1202"):
            connected.add("1202")
            return ""
        if command == ("rpt", "cmd", "600000", "ilink", "3", "1205"):
            return ""
        if command == ("rpt", "cmd", "1205", "ilink", "3", "600000"):
            connected.add("1205")
            return ""
        if command == ("rpt", "cmd", "600000", "ilink", "3", "578990"):
            return ""
        raise AssertionError(command)

    retry: dict = {}
    result = recover_once(plan, runner, lambda _: None, retry, 1000)
    assert result == {"checked": 3, "reconnected": ["1202", "1205"],
                      "reverseFallback": ["1205"], "failed": ["578990"], "unavailable": [],
                      "autopatchCleared": ["1202"], "backingOff": []}
    assert retry == {"578990": {"failures": 1, "next": 1030}}
    assert ("rpt", "cmd", "600000", "ilink", "3", "1202") in calls
    assert ("rpt", "cmd", "600000", "ilink", "3", "1205") in calls
    assert ("rpt", "cmd", "1205", "ilink", "3", "600000") in calls
    backed_off = recover_once(plan, runner, lambda _: None, retry, 1015)
    assert backed_off["backingOff"] == ["578990"] and "578990" not in backed_off["failed"]

    unavailable_plan = {**plan, "fallback": [{"id": "p25", "node": "1299"}],
                        "requiredLinks": []}
    result = recover_once(unavailable_plan, runner, lambda _: None)
    assert result == {"checked": 1, "reconnected": [], "reverseFallback": [],
                      "failed": [], "unavailable": ["1299"], "autopatchCleared": [],
                      "backingOff": []}

    direct = native_permanent_nodes(
        "[600001]\nstartup_macro = ilink,13,1301 *3 1302 *8131006*813578990\n",
        "600001",
    )
    assert direct == {"1301", "1006", "578990"}

    with tempfile.TemporaryDirectory(prefix="asr-fixed-bridge-recovery-test-") as temporary:
        root = Path(temporary)
        config_path = root / "config.json"
        rpt_path = root / "rpt.conf"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        rpt_path.write_text(rpt_text, encoding="utf-8")
        assert load_plan(config_path, rpt_path) == plan
        quiesce_path = root / "quiesced.json"
        quiesce_path.write_text(json.dumps({"nodes": ["1202"]}), encoding="utf-8")
        assert [item["node"] for item in load_plan(config_path, rpt_path, quiesce_path)["fallback"]] == ["1205"]
        quiesce_path.write_text(json.dumps({"nodes": ["bad-node"]}), encoding="utf-8")
        try:
            load_plan(config_path, rpt_path, quiesce_path)
            raise AssertionError("invalid migration quiesce state did not fail closed")
        except RecoveryError:
            pass
    # Inspect effective literal includes and inherited templates, without ever
    # executing directives or editing Asterisk configuration.
    with tempfile.TemporaryDirectory(prefix="asr-startup-inspection-") as temporary:
        root = Path(temporary); rpt = root / "rpt.conf"
        (root / "sub").mkdir()
        (root / "sub" / "nodes.conf").write_text("[600000](template)\n[1202]\n")
        (root / "template.conf").write_text("[template](!)\nstartup_macro = A1\nmacro = custom-macros\n[custom-macros]\nA1 = *8131202\n")
        rpt.write_text('#include "sub/nodes.conf"\n#include "/etc/asterisk/template.conf"\n#tryinclude optional.conf\n')
        assert inspect_native_holds(held_config, rpt) == {"nativeManualHoldConflicts": ["1202"], "nativeManualHoldUnknown": []}
        (root / "template.conf").write_text("[template](!)\nstartup_macro = *8131201\n")
        assert inspect_native_holds(held_config, rpt) == {"nativeManualHoldConflicts": [], "nativeManualHoldUnknown": []}
        (root / "sub" / "conflict.conf").write_text("[1202](+)\nstartup_macro = *813600000\n")
        rpt.write_text('#include "sub/*.conf"\n#include "template.conf"\n')
        assert inspect_native_holds(held_config, rpt)["nativeManualHoldUnknown"] == ["1202"]
        (root / "sub" / "conflict.conf").unlink()
        assert inspect_native_holds(held_config, rpt)["nativeManualHoldUnknown"] == []
        for bad in ('#exec "false"', '#include "missing.conf"', '#include "../outside.conf"',
                    '#include "*.conf"', '#include "rpt.conf"', '#include "alias.conf"',
                    '[1202](+)\nstartup_macro = *813600000', '[unrelated]\n;-- comment'):
            (root / "alias.conf").unlink(missing_ok=True)
            (root / "alias.conf").symlink_to(root / "template.conf")
            rpt.write_text('#include "sub/nodes.conf"\n#include "template.conf"\n' + bad + "\n")
            assert inspect_native_holds(held_config, rpt)["nativeManualHoldUnknown"] == ["1202"], bad
        rpt.write_text('#include "sub/nodes.conf"\n#include "template.conf"\n')
        for unsupported in ("unknown_macro", "*999", "*8131201 *999"):
            (root / "template.conf").write_text(f"[template](!)\nstartup_macro = {unsupported}\n")
            assert inspect_native_holds(held_config, rpt)["nativeManualHoldUnknown"] == ["1202"]
    # Explicit controls persist the exact bridge hold before unlink and clear
    # it only after observed reconnect. No production commands are executed.
    with tempfile.TemporaryDirectory(prefix="asr-manual-link-test-") as temporary:
        root = Path(temporary); config_path = root / "config.json"; rpt_path = root / "rpt.conf"
        config_path.write_text(json.dumps(config)); rpt_path.write_text(rpt_text)
        linked = {"1201", "1202"}; commands = []; reject_unlink = False
        transitional = set()
        def control_runner(arguments):
            command = tuple(arguments); commands.append(command)
            if command == ("rpt", "localnodes"):
                return "Local nodes: 600000 1201 1202 1203 1204 1205\n"
            if command == ("rpt", "lstats", "600000"):
                return "NODE PEER STATE\n" + "".join(f"{n} 127.0.0.1 {'CONNECTING' if n in transitional else 'ESTABLISHED'}\n" for n in sorted(linked))
            if command[:3] == ("rpt", "cmd", "600000") and command[3:] == ("ilink", "11", "1202"):
                assert read_json_object(config_path)["bridges"][1]["manualDisconnect"] is True
                if not reject_unlink: linked.discard("1202")
                return ""
            if command == ("rpt", "cmd", "1202", "ilink", "11", "600000"):
                if not reject_unlink: linked.discard("1202")
                return ""
            if command == ("rpt", "cmd", "600000", "ilink", "3", "1202"):
                assert read_json_object(config_path)["bridges"][1]["manualDisconnect"] is True
                linked.add("1202"); return ""
            raise AssertionError(command)
        def rejected(bridge, phrase):
            try:
                manual_link(config_path, rpt_path, bridge, "disconnect", control_runner, lambda _: None)
            except RecoveryError as exc:
                assert phrase in str(exc), str(exc)
            else:
                raise AssertionError("invalid manual control succeeded")
        before = config_path.read_bytes()
        with config_directory_lock(root):
            rejected("ysf", "Settings are being saved")
        assert config_path.read_bytes() == before and not commands
        rejected("dmr", "permanent startup link")
        rejected("ysf_net", "Standard Bridge")
        assert config_path.read_bytes() == before
        rpt_path.write_text(rpt_text.replace("[1202]\n", "[1202]\nstartup_macro = *813600000\n"))
        rejected("ysf", "permanent startup link")
        assert config_path.read_bytes() == before
        rpt_path.write_text(rpt_text + '\n#include "missing.conf"\n')
        rejected("ysf", "startup links could not be verified")
        assert config_path.read_bytes() == before
        rpt_path.write_text(rpt_text)
        assert manual_link(config_path, rpt_path, "ysf", "disconnect", control_runner, lambda _: None)["linked"] is False
        assert "1202" not in linked and "1201" in linked
        assert load_plan(config_path, rpt_path)["manualHeldNodes"] == ["1202"]
        assert manual_link(config_path, rpt_path, "ysf", "connect", control_runner, lambda _: None)["linked"] is True
        assert load_plan(config_path, rpt_path)["manualHeldNodes"] == []
        # A pending IAX row must be actively removed, not mistaken for an
        # already disconnected bridge. A failed pending unlink retains hold.
        transitional.add("1202")
        assert verified_link_state(control_runner(["rpt", "lstats", "600000"]), "1202") == "pending"
        reject_unlink = True
        rejected("ysf", "Manual hold is retained")
        assert "1202" in linked and load_plan(config_path, rpt_path)["manualHeldNodes"] == ["1202"]
        reject_unlink = False
        assert manual_link(config_path, rpt_path, "ysf", "disconnect", control_runner, lambda _: None)["linked"] is False
        assert "1202" not in linked
        transitional.clear()
        assert manual_link(config_path, rpt_path, "ysf", "connect", control_runner, lambda _: None)["linked"] is True
        def concurrent_writer(arguments):
            result = control_runner(arguments)
            if tuple(arguments) == ("rpt", "cmd", "600000", "ilink", "11", "1202"):
                config_path.write_text(json.dumps(config))
            return result
        try:
            manual_link(config_path, rpt_path, "ysf", "disconnect", concurrent_writer, lambda _: None)
            raise AssertionError("concurrent uncoordinated writer falsely reported a durable hold")
        except RecoveryError as exc:
            assert "saved recovery policy could not be verified" in str(exc)
        linked.add("1202")
        reject_unlink = True
        rejected("ysf", "Manual hold is retained")
        assert load_plan(config_path, rpt_path)["manualHeldNodes"] == ["1202"]
        assert rpt_path.read_text() == rpt_text
        assert all(cmd[2] in {"600000", "1202"} for cmd in commands if cmd[:2] == ("rpt", "cmd"))
    # Contention on every existing writer lock must fail before any control
    # and release the locks already acquired. Test entirely in a temp tree.
    with tempfile.TemporaryDirectory(prefix="asr-writer-guard-") as tmp:
        directory = Path(tmp)
        with config_writer_guard(directory, expected_uid=os.getuid()):
            pass
        for name in WRITER_LOCKS:
            with (directory / name).open("r+") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    with config_writer_guard(directory, expected_uid=os.getuid()):
                        raise AssertionError("writer lock contention ignored")
                except RecoveryError as exc:
                    assert "maintenance is active" in str(exc)
            with config_writer_guard(directory, expected_uid=os.getuid()):
                pass
        target = directory / WRITER_LOCKS[-1]
        target.unlink()
        target.symlink_to(directory / WRITER_LOCKS[0])
        try:
            with config_writer_guard(directory, expected_uid=os.getuid()):
                raise AssertionError("symlink lock accepted")
        except OSError:
            pass
    print("ASR fixed-bridge recovery self-test: ok")


def main() -> int:
    if sys.argv[1:2] == ["--manual-link"]:
        try:
            if len(sys.argv) != 4 or os.geteuid() != 0 or Path("/.dockerenv").exists():
                raise RecoveryError("Native Standard Bridge control is unavailable.")
            with config_writer_guard(), recovery_lock():
                result = manual_link(DEFAULT_CONFIG, DEFAULT_RPT_CONFIG, sys.argv[3], sys.argv[2])
            print(json.dumps(result)); return 0
        except (RecoveryError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:240]})); return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--rpt-config", type=Path, default=DEFAULT_RPT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--once", action="store_true", help="Recover missing opted-in fixed bridge links")
    action.add_argument("--has-fallback-targets", action="store_true", help="Exit successfully when link recovery is required")
    action.add_argument("--plan-json", action="store_true", help="Print the sanitized recovery plan as JSON")
    action.add_argument("--self-test", action="store_true", help="Run temp-only tests")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        plan = load_plan(args.config, args.rpt_config)
        if args.has_fallback_targets:
            return 0 if plan["fallback"] or plan["requiredLinks"] or plan["manualHeldNodes"] else 1
        if args.plan_json:
            plan.update(inspect_native_holds(read_json_object(args.config), args.rpt_config))
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if plan["nativeManualHoldConflicts"]:
            print("Manual bridge hold conflicts with native Asterisk permanent startup links: "
                  + ", ".join(plan["nativeManualHoldConflicts"])
                  + ". ASR will not rewrite unmanaged Asterisk configuration.", file=sys.stderr)
        with recovery_lock():
            # Re-read under the same lock used by explicit link controls.
            plan = load_plan(args.config, args.rpt_config)
            retry_state = read_retry_state(args.state)
            result = recover_once(plan, retry_state=retry_state)
            write_retry_state(args.state, retry_state)
        if result["reconnected"]:
            print("Restored configured fixed bridge link(s): " + ", ".join(result["reconnected"]))
        if result["reverseFallback"]:
            print("Used doorway-side recovery for bridge link(s): " + ", ".join(result["reverseFallback"]))
        if result["autopatchCleared"]:
            print("Cleared invalid autopatch state on bridge link(s): " + ", ".join(result["autopatchCleared"]))
        required_nodes = {str(target["node"]) for target in plan["requiredLinks"]}
        for node in result["failed"]:
            if node in required_nodes:
                print(f"Required AllStar node {node} did not establish; bounded retry backoff is active.", file=sys.stderr)
            else:
                print(f"Configured fixed bridge node {node} did not establish after both recovery directions.", file=sys.stderr)
        for node in result["unavailable"]:
            print(f"Configured fixed bridge node {node} is not available locally; not reconnecting.", file=sys.stderr)
        return 1 if result["failed"] else 0
    except (RecoveryError, OSError) as exc:
        print(f"Fixed-bridge recovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
