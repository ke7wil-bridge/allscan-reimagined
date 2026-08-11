#!/usr/bin/env python3
"""Recover opted-in fixed ASR bridge links without managing Net Bridges."""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_CONFIG = Path("/etc/allscan-reimagined/config.json")
DEFAULT_RPT_CONFIG = Path("/etc/asterisk/rpt.conf")
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
    return set(
        re.findall(
            r"(?i)(?:^|[\s*])(?:COP\s*,\s*6\s*,\s*[0-9]+\s*,\s*)?"
            r"ilink\s*,\s*13\s*,\s*([0-9]{3,10})(?=$|[\s*])",
            expanded,
        )
    )


def configured_targets(config: dict) -> tuple[str, list[dict]]:
    main_node = str(config.get("node", "")).strip()
    if not NODE_RE.fullmatch(main_node):
        raise RecoveryError("The configured main AllStar node is invalid.")
    targets: list[dict] = []
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict):
            continue
        bridge_id = str(bridge.get("id", "")).strip().lower()
        if re.match(r"^d[-_]?star(?:[_-]|$)", bridge_id):
            continue
        if bridge.get("cardType", "standard") != "standard":
            continue
        if bridge.get("fixedBridgeRecovery") is not True:
            continue
        node = str(bridge.get("node", "")).strip()
        if not NODE_RE.fullmatch(node) or node == main_node:
            continue
        targets.append({
            "id": bridge_id or "bridge",
            "node": node,
        })
    return main_node, targets


def recovery_plan(config: dict, rpt_text: str) -> dict:
    main_node, targets = configured_targets(config)
    native = native_permanent_nodes(rpt_text, main_node)
    fallback = [target for target in targets if target["node"] not in native]
    return {
        "mainNode": main_node,
        "configured": targets,
        "nativePermanent": [target for target in targets if target["node"] in native],
        "fallback": fallback,
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


def recover_once(plan: dict, runner: Callable[[Iterable[str]], str] = run_asterisk) -> dict:
    fallback = plan.get("fallback", [])
    if not fallback:
        return {"checked": 0, "reconnected": [], "unavailable": []}
    main_node = str(plan["mainNode"])
    local_nodes = listed_local_nodes(runner(["rpt", "localnodes"]))
    established = established_nodes(runner(["rpt", "lstats", main_node]))
    reconnected: list[str] = []
    unavailable: list[str] = []
    for target in fallback:
        node = str(target["node"])
        if node not in local_nodes:
            unavailable.append(node)
            continue
        if node in established:
            continue
        runner(["rpt", "fun", main_node, f"*3{node}"])
        reconnected.append(node)
    return {
        "checked": len(fallback),
        "reconnected": reconnected,
        "unavailable": unavailable,
    }


def load_plan(config_path: Path, rpt_path: Path) -> dict:
    try:
        rpt_text = rpt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RecoveryError(f"Could not read Asterisk configuration: {exc}") from exc
    return recovery_plan(read_json_object(config_path), rpt_text)


def self_test() -> None:
    config = {
        "node": "600000",
        "bridges": [
            {"id": "dmr", "node": "1201", "cardType": "standard", "fixedBridgeRecovery": True},
            {"id": "ysf", "node": "1202", "cardType": "standard", "fixedBridgeRecovery": True},
            {"id": "zello", "node": "1203", "cardType": "standard", "fixedBridgeRecovery": False},
            {"id": "ysf_net", "node": "1204", "cardType": "ysf_net", "fixedBridgeRecovery": True},
            {"id": "dstar", "node": "1205", "cardType": "standard", "fixedBridgeRecovery": True},
        ],
    }
    rpt_text = """
[600000](node-main)
startup_macro = A1

[macro]
A1 = *COP,6,12345,ilink,13,1201 *COP,6,12345,ilink,13,1203
"""
    plan = recovery_plan(config, rpt_text)
    assert [item["node"] for item in plan["nativePermanent"]] == ["1201"]
    assert [item["node"] for item in plan["fallback"]] == ["1202"]

    calls: list[tuple[str, ...]] = []

    def runner(arguments: Iterable[str]) -> str:
        command = tuple(arguments)
        calls.append(command)
        if command == ("rpt", "localnodes"):
            return "Local nodes: 1201 1202 1203 600000\n"
        if command == ("rpt", "lstats", "600000"):
            return "NODE PEER STATE\n1201 127.0.0.1 ESTABLISHED\n"
        if command == ("rpt", "fun", "600000", "*31202"):
            return ""
        raise AssertionError(command)

    result = recover_once(plan, runner)
    assert result == {"checked": 1, "reconnected": ["1202"], "unavailable": []}
    assert ("rpt", "fun", "600000", "*31202") in calls

    unavailable_plan = {**plan, "fallback": [{"id": "p25", "node": "1299"}]}
    result = recover_once(unavailable_plan, runner)
    assert result == {"checked": 1, "reconnected": [], "unavailable": ["1299"]}

    direct = native_permanent_nodes(
        "[600001]\nstartup_macro = ilink,13,1301 *3 1302\n",
        "600001",
    )
    assert direct == {"1301"}

    with tempfile.TemporaryDirectory(prefix="asr-fixed-bridge-recovery-test-") as temporary:
        root = Path(temporary)
        config_path = root / "config.json"
        rpt_path = root / "rpt.conf"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        rpt_path.write_text(rpt_text, encoding="utf-8")
        assert load_plan(config_path, rpt_path) == plan
    print("ASR fixed-bridge recovery self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--rpt-config", type=Path, default=DEFAULT_RPT_CONFIG)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--once", action="store_true", help="Recover missing opted-in fixed bridge links")
    action.add_argument("--has-fallback-targets", action="store_true", help="Exit successfully when fallback recovery is required")
    action.add_argument("--plan-json", action="store_true", help="Print the sanitized recovery plan as JSON")
    action.add_argument("--self-test", action="store_true", help="Run temp-only tests")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        plan = load_plan(args.config, args.rpt_config)
        if args.has_fallback_targets:
            return 0 if plan["fallback"] else 1
        if args.plan_json:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        result = recover_once(plan)
        if result["reconnected"]:
            print("Restored configured fixed bridge link(s): " + ", ".join(result["reconnected"]))
        for node in result["unavailable"]:
            print(f"Configured fixed bridge node {node} is not available locally; not reconnecting.", file=sys.stderr)
        return 0
    except RecoveryError as exc:
        print(f"Fixed-bridge recovery failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
