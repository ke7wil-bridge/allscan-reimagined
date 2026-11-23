#!/usr/bin/env python3
"""Build and offline-test the future ASR Plus georgia-to-ky2 identity cutover."""

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


def replace_exact(text: str, replacements: tuple[tuple[str, str], ...], source: Path) -> str:
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{source}: expected one occurrence of {old!r}, found {count}")
        text = text.replace(old, new)
    return text


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
    replacements = {
        "masterhub/asrplus-control.py": (
            ('        "georgia": "674980", "texas": "674984"}',
             '        "ky2": "674980", "texas": "674984"}'),
            ('REMOTE_HUBS = {"641894": "phoenix", "674980": "georgia", "674984": "texas"}',
             'REMOTE_HUBS = {"641894": "phoenix", "674980": "ky2", "674984": "texas"}'),
            ('if remote_hub == "georgia":', 'if remote_hub == "ky2":'),
        ),
        "masterhub/asrplus-remote-endpoint.py": (
            ('HUB_NODES = {"phoenix": "641894", "georgia": "674980", "texas": "674984"}',
             'HUB_NODES = {"phoenix": "641894", "ky2": "674980", "texas": "674984"}'),
        ),
        "masterhub/api.php": (
            ("'kentucky-hub' => 'georgia', 'georgia' => 'georgia',",
             "'kentucky-hub' => 'ky2', 'ky2' => 'ky2',"),
        ),
        "masterhub/asrplus-token.py": (
            ('    "georgia": "674980",', '    "ky2": "674980",'),
        ),
    }
    manifest = {}
    for relative, source in sources.items():
        text = replace_exact(source.read_text(encoding="utf-8"), replacements[relative], source)
        destination = output / relative
        write_text(destination, text, 0o755 if source.suffix in {".py", ".php"} else 0o644)
        manifest[relative] = sha256(destination)

    unchanged = {
        "masterhub/asrplus_command_integrity.py": repo / "scripts/asrplus_command_integrity.py",
        "ky2/asrplus-ky-agent": repo / "ops/asrplus/staging/ky2/asrplus-ky-agent",
        "ky2/asrplus_command_integrity.py": repo / "scripts/asrplus_command_integrity.py",
        "ky2/asrplus-ky.json": repo / "ops/asrplus/staging/ky2/asrplus-ky.json",
    }
    for relative, source in unchanged.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest[relative] = sha256(destination)

    central_source = repo / "ops/asrplus/staging/masterhub/command-integrity.json"
    central = json.loads(central_source.read_text(encoding="utf-8"))
    definition = central["hubs"].pop("georgia")
    definition.update({"keyId": "ky2-2026-01", "keyFile": "/etc/asrplus/command-keys/ky2.key"})
    central["hubs"]["ky2"] = definition
    central_path = output / "masterhub/command-integrity.json"
    write_text(central_path, json.dumps(central, indent=2) + "\n", 0o644)
    manifest["masterhub/command-integrity.json"] = sha256(central_path)

    agent_source = repo / "ops/asrplus/staging/ky2/asrplus-ky-control.json"
    agent = json.loads(agent_source.read_text(encoding="utf-8"))
    agent["hub"] = "ky2"
    agent["commandIntegrity"].update(
        {"keyId": "ky2-2026-01", "keyFile": "/etc/asrplus/command-keys/ky2.key"})
    agent_path = output / "ky2/asrplus-ky-control.json"
    write_text(agent_path, json.dumps(agent, indent=2) + "\n", 0o644)
    manifest["ky2/asrplus-ky-control.json"] = sha256(agent_path)

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
    args = parser.parse_args()
    try:
        if args.command == "build":
            build(args.repo.resolve(), args.output.resolve())
            print(json.dumps({"ok": True, "output": str(args.output.resolve())}))
        else:
            print(json.dumps({"ok": True, "applied": args.apply,
                              "operations": state_plan(args.root.resolve(), args.reverse, args.apply)},
                             sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:300]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
