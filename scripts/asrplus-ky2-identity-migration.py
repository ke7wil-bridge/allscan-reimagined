#!/usr/bin/env python3
"""Build and offline-test the already-canonical ASR Plus KY2 identity bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


STATE_MOVES = (
    ("var/lib/asrplus/queue/georgia", "var/lib/asrplus/queue/ky2", True),
    ("var/lib/asrplus/queue/.georgia.lock", "var/lib/asrplus/queue/.ky2.lock", False),
    ("var/lib/asrplus/results/georgia", "var/lib/asrplus/results/ky2", False),
    ("var/lib/asrplus/queue-archive/georgia", "var/lib/asrplus/queue-archive/ky2", False),
    ("var/lib/asrplus/remote-status/georgia.json", "var/lib/asrplus/remote-status/ky2.json", False),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_text(destination: Path, text: str, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.new")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, destination)


def build(repo: Path, output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("migration output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    sources = {
        "masterhub/asrplus-control.py": repo / "scripts/asrplus-control.py",
        "masterhub/asrplus-remote-endpoint.py": repo / "scripts/asrplus-remote-endpoint.py",
        "masterhub/api.php": repo / "ops/asrplus/api.php",
        "masterhub/asrplus-token.py": repo / "scripts/asrplus-token.py",
    }
    manifest = {}
    for relative, source in sources.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest[relative] = sha256(destination)

    unchanged = {
        "masterhub/asrplus_command_integrity.py": repo / "scripts/asrplus_command_integrity.py",
        "ky2/asrplus-ky-agent": repo / "ops/asrplus/staging/ky2/asrplus-ky-agent",
        "ky2/asrplus_command_integrity.py": repo / "scripts/asrplus_command_integrity.py",
        "ky2/asrplus-ky.json": repo / "ops/asrplus/staging/ky2/asrplus-ky.json",
        "masterhub/command-integrity.json": repo / "ops/asrplus/staging/masterhub/command-integrity.json",
        "ky2/asrplus-ky-control.json": repo / "ops/asrplus/staging/ky2/asrplus-ky-control.json",
    }
    for relative, source in unchanged.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest[relative] = sha256(destination)

    manifest["phoenixSourceAgentSha256"] = sha256(repo / "ops/asrplus/staging/phoenix/asrplus-agent")
    manifest["phoenixSourceConfigSha256"] = sha256(repo / "ops/asrplus/staging/phoenix/asrplus-agent.json")
    write_text(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n", 0o644)


def state_plan(root: Path, reverse: bool, apply: bool) -> list[dict[str, str]]:
    operations = []
    for legacy, current, require_empty in STATE_MOVES:
        source_rel, destination_rel = (current, legacy) if reverse else (legacy, current)
        source, destination = root / source_rel, root / destination_rel
        if source.is_symlink() or destination.is_symlink():
            raise RuntimeError(f"symbolic-link state path refused: {source_rel}")
        if not source.exists():
            operations.append({"source": source_rel, "destination": destination_rel, "status": "absent"})
            continue
        if destination.exists():
            raise RuntimeError(f"destination already exists: {destination_rel}")
        if require_empty and source.is_dir() and any(source.glob("*.json")) or (
                require_empty and source.is_dir() and any(source.glob("*.inflight"))):
            raise RuntimeError("KY2 command queue must be empty before identity migration")
        operations.append({"source": source_rel, "destination": destination_rel,
                           "status": "moved" if apply else "planned"})
        if apply:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
    return operations


def transform_dashboard(source: Path, destination: Path, reverse: bool) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    matches = [hub for hub in payload.get("hubs", [])
               if isinstance(hub, dict) and str(hub.get("node")) == "674980"]
    if len(matches) != 1:
        raise RuntimeError("dashboard config must contain exactly one KY2 node")
    hub = matches[0]
    old, new = (("ky2.json", "georgia.json") if reverse else
                ("georgia.json", "ky2.json"))
    current = Path(str(hub.get("statusFile", ""))).name
    if current != old:
        raise RuntimeError(f"unexpected KY2 status path: {current}")
    hub["statusFile"] = str(Path(str(hub["statusFile"])).with_name(new))
    write_text(destination, json.dumps(payload, indent=2) + "\n", 0o644)


def transform_authorized_keys(source: Path, destination: Path, reverse: bool) -> None:
    text = source.read_text(encoding="utf-8")
    if reverse:
        changes = (
            ('from="44.30.157.22"', 'asrplus-remote-endpoint ky2', 'asrplus-remote-endpoint georgia'),
            ('from="45.76.255.39"', 'asrplus-remote-endpoint retired', 'asrplus-remote-endpoint georgia'),
            ('from="45.76.255.39"', 'retired_telemetry_receiver.py', 'georgia_telemetry_receiver.py'),
        )
    else:
        changes = (
            ('from="44.30.157.22"', 'asrplus-remote-endpoint georgia', 'asrplus-remote-endpoint ky2'),
            ('from="45.76.255.39"', 'asrplus-remote-endpoint georgia', 'asrplus-remote-endpoint retired'),
            ('from="45.76.255.39"', 'georgia_telemetry_receiver.py', 'retired_telemetry_receiver.py'),
        )
    lines = text.splitlines(keepends=True)
    for source_option, old_command, new_command in changes:
        indexes = [index for index, line in enumerate(lines)
                   if source_option in line and old_command in line]
        if len(indexes) != 1:
            raise RuntimeError(f"expected one forced-command mapping for {source_option}")
        index = indexes[0]
        if lines[index].count(old_command) != 1:
            raise RuntimeError("forced-command mapping is ambiguous")
        lines[index] = lines[index].replace(old_command, new_command)
        if source_option == 'from="45.76.255.39"':
            lines[index] = lines[index].replace(
                "retired" if reverse else "georgia",
                "georgia" if reverse else "retired")
    write_text(destination, "".join(lines), source.stat().st_mode & 0o777)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--repo", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    state = sub.add_parser("state")
    state.add_argument("--root", type=Path, required=True)
    state.add_argument("--reverse", action="store_true")
    state.add_argument("--apply", action="store_true")
    dashboard = sub.add_parser("dashboard-config")
    dashboard.add_argument("--input", type=Path, required=True)
    dashboard.add_argument("--output", type=Path, required=True)
    dashboard.add_argument("--reverse", action="store_true")
    authorized = sub.add_parser("authorized-keys")
    authorized.add_argument("--input", type=Path, required=True)
    authorized.add_argument("--output", type=Path, required=True)
    authorized.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.repo.resolve(), args.output.resolve())
            print(json.dumps({"ok": True, "output": str(args.output.resolve())}))
        elif args.command == "state":
            print(json.dumps({"ok": True, "applied": args.apply,
                              "operations": state_plan(args.root.resolve(), args.reverse, args.apply)},
                             sort_keys=True))
        elif args.command == "dashboard-config":
            transform_dashboard(args.input.resolve(), args.output.resolve(), args.reverse)
            print(json.dumps({"ok": True, "output": str(args.output.resolve())}))
        else:
            transform_authorized_keys(args.input.resolve(), args.output.resolve(), args.reverse)
            print(json.dumps({"ok": True, "output": str(args.output.resolve())}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:300]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
