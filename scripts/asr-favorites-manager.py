#!/usr/bin/env python3
"""Persistent Favorites metadata and safe Supermon import management."""

from __future__ import annotations

import argparse
import base64
import configparser
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

ALLOWED_DIRECTORY = Path("/etc/allscan")
STATE_FILE = ALLOWED_DIRECTORY / "favorites-user.json"
LOCK_FILE = Path("/run/allscan-reimagined/favorites.lock")
SUPERM0N_CANDIDATES = (
    Path("/var/www/html/supermon/favorites.ini"),
    Path("/var/www/html/supermon2/favorites.ini"),
)
FAVORITES_NAME = re.compile(r"favorites[\w.\-\[\]]*\.ini\Z", re.I)
NODE_VALUE = re.compile(r"[A-Za-z0-9*#]{3,8}\Z")
ASSIGNMENT = re.compile(r"^\s*(label|cmd)\s*\[\]\s*=\s*(.*?)\s*$", re.I)
MAX_BYTES = 2 * 1024 * 1024


def decode_ini_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        quote = raw[0]
        body = raw[1:-1]
        body = body.replace("\\" + quote, quote).replace("\\\\", "\\")
        return body.strip()
    return raw.strip()


def encode_ini_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def command_node(command: str) -> str:
    match = re.search(r"\bilink\s+\d+\s+([A-Za-z0-9*#]{3,8})\b", command, re.I)
    if match:
        return match.group(1)
    match = re.search(r"\b([0-9]{3,8})\b(?!.*\b[0-9]{3,8}\b)", command)
    return match.group(1) if match else ""


def label_node(label: str) -> str:
    match = re.search(r"\b([0-9]{3,8})\b\s*$", label)
    return match.group(1) if match else ""


def parse_allscan(text: str) -> list[dict[str, str]]:
    labels: list[str] = []
    commands: list[str] = []
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        value = decode_ini_value(match.group(2))
        (labels if match.group(1).lower() == "label" else commands).append(value)
    rows: list[dict[str, str]] = []
    for index, label in enumerate(labels):
        command = commands[index] if index < len(commands) else ""
        node = command_node(command) or label_node(label)
        if NODE_VALUE.fullmatch(node):
            rows.append({"node": node, "label": label, "command": command})
    return rows


def parse_supermon(text: str) -> list[dict[str, str]]:
    rows = parse_allscan(text)
    if rows:
        return rows
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error:
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
    candidates: list[tuple[str, str]] = []
    for section in parser.sections():
        for key, value in parser.items(section):
            candidates.append((key.strip(), decode_ini_value(value)))
    if not candidates:
        for line in text.splitlines():
            match = re.match(r"^\s*([0-9]{3,8})\s*(?:=|,|\|)\s*(.*?)\s*$", line)
            if match:
                candidates.append((match.group(1), decode_ini_value(match.group(2))))
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, value in candidates:
        node = key if NODE_VALUE.fullmatch(key) else command_node(value) or label_node(value)
        if not NODE_VALUE.fullmatch(node) or node in seen:
            continue
        description = re.sub(r"\s+" + re.escape(node) + r"\s*$", "", value).strip(" \t,|")
        label = (description + " " + node).strip()
        result.append({"node": node, "label": label, "command": f"rpt cmd %node% ilink 3 {node}"})
        seen.add(node)
    return result
def validate_favorites(requested: str, allowed: Path = ALLOWED_DIRECTORY) -> Path:
    path = Path(requested).resolve(strict=True)
    root = allowed.resolve(strict=True)
    if path.parent != root or not path.is_file() or not FAVORITES_NAME.fullmatch(path.name):
        raise ValueError("Favorites path is not allowed.")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Favorites file is unexpectedly large.")
    return path


def read_state(path: Path = STATE_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("files", {}), dict):
        raise ValueError("Favorites user state is invalid.")
    data.setdefault("version", 1)
    data.setdefault("files", {})
    return data


def atomic_write(path: Path, content: str, metadata: os.stat_result | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".asr-favorites-")
    temporary = Path(temporary_name)
    try:
        if metadata:
            os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
            os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
        else:
            os.fchmod(descriptor, 0o664)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def write_state(data: dict[str, Any], path: Path = STATE_FILE) -> None:
    metadata = path.stat() if path.exists() else None
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", metadata)


def file_state(state: dict[str, Any], favorites: Path) -> dict[str, Any]:
    files = state.setdefault("files", {})
    entry = files.setdefault(favorites.name, {})
    entry.setdefault("nodes", {})
    return entry


def render(rows: list[dict[str, str]]) -> str:
    blocks = []
    for row in rows:
        command = row["command"] or f"rpt cmd %node% ilink 3 {row['node']}"
        blocks.append(
            f"label[] = {encode_ini_value(row['label'])}\n"
            f"cmd[] = {encode_ini_value(command)}"
        )
    return "\n\n".join(blocks).rstrip() + "\n"


def discover_supermon(explicit: str = "") -> Path | None:
    paths = (Path(explicit),) if explicit else SUPERM0N_CANDIDATES
    for path in paths:
        if path.is_file() and path.stat().st_size <= MAX_BYTES:
            return path
    return None
def preview_import(favorites: Path, source: Path) -> dict[str, Any]:
    incoming = parse_supermon(source.read_text(encoding="utf-8", errors="replace"))
    existing = parse_allscan(favorites.read_text(encoding="utf-8", errors="replace"))
    existing_nodes = {row["node"] for row in existing}
    additions = [row for row in incoming if row["node"] not in existing_nodes]
    conflicts = [row for row in incoming if row["node"] in existing_nodes]
    return {
        "ok": True,
        "source": str(source),
        "detected": len(incoming),
        "additions": additions,
        "existing": conflicts,
        "malformed": len(incoming) == 0,
    }


def upstream_order_preview(favorites: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Preview a conflict-safe import of physical favorites.ini ordering."""
    raw = favorites.read_bytes()
    upstream = [row["node"] for row in parse_allscan(raw.decode("utf-8"))]
    if not upstream or len(upstream) != len(set(upstream)):
        raise ValueError("Upstream Favorites order is empty or contains duplicate nodes.")
    # A raw parser can skip malformed/unpaired entries. Refuse to import
    # ambiguous physical ordering rather than silently dropping a Favorite.
    label_count = len(re.findall(r"(?im)^\s*label\[\]\s*=", raw.decode("utf-8")))
    cmd_count = len(re.findall(r"(?im)^\s*cmd\[\]\s*=", raw.decode("utf-8")))
    if label_count != len(upstream) or cmd_count != len(upstream):
        raise ValueError("Favorites file contains unmatched or unrecognized records; review manually.")
    entry = state.get("files", {}).get(favorites.name, {})
    custom = entry.get("order")
    if custom is not None and (not isinstance(custom, list) or not all(isinstance(node, str) for node in custom)
                               or len(custom) != len(set(custom)) or sorted(custom) != sorted(upstream)):
        raise ValueError("Custom and upstream Favorites memberships differ; reconcile before importing order.")
    token = hashlib.sha256(raw + b"\0" + json.dumps(entry, sort_keys=True).encode()).hexdigest()
    return {"ok": True, "upstreamOrder": upstream, "customOrder": custom,
            "different": custom is not None and custom != upstream, "token": token}


def upstream_export_preview(favorites: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Only support plain, adjacent label/cmd records; refuse ambiguous INI layouts."""
    raw = favorites.read_bytes()
    content = raw.decode("utf-8")
    if "\r" in content:
        raise ValueError("Nonstandard line endings: export requires manual review.")
    lines = content.splitlines(keepends=True)
    records: list[tuple[str, str]] = []
    starts: list[int] = []
    ends: list[int] = []
    index = 0
    while index < len(lines):
        label = ASSIGNMENT.match(lines[index].rstrip("\n"))
        if label and label.group(1).lower() == "label":
            if index + 1 >= len(lines):
                raise ValueError("Unpaired Favorites label; export aborted.")
            cmd = ASSIGNMENT.match(lines[index + 1].rstrip("\n"))
            if not cmd or cmd.group(1).lower() != "cmd":
                raise ValueError("Nonadjacent label/cmd entries; export aborted to preserve file layout.")
            node = command_node(decode_ini_value(cmd.group(2))) or label_node(decode_ini_value(label.group(2)))
            if not NODE_VALUE.fullmatch(node):
                raise ValueError("Unrecognized Favorites node; export aborted.")
            records.append((node, "".join(lines[index:index + 2])))
            starts.append(index)
            ends.append(index + 2)
            index += 2
        else:
            if label and label.group(1).lower() == "cmd":
                raise ValueError("Unpaired Favorites command; export aborted.")
            index += 1
    upstream = [node for node, _ in records]
    if not records:
        raise ValueError("Favorites file has no recognized label/cmd records to reorder.")
    desired = state.get("files", {}).get(favorites.name, {}).get("order")
    if not isinstance(desired, list) or not desired:
        raise ValueError("No custom ASR order to export.")
    if (not all(isinstance(node, str) for node in desired) or len(set(upstream)) != len(upstream)
            or len(desired) != len(upstream) or sorted(desired) != sorted(upstream)):
        raise ValueError("Favorites membership differs or contains duplicates; reconcile before exporting.")
    # Only whitespace may separate records: otherwise comments/sections could be displaced.
    for left, right in zip(ends, starts[1:]):
        if any(line.strip() for line in lines[left:right]):
            raise ValueError("Comments or sections between Favorites entries; export requires manual review.")
    by_node = dict(records)
    spans = ["".join(lines[left:right]) for left, right in zip(ends, starts[1:])]
    replacement = "".join(by_node[node] + (spans[i] if i < len(spans) else "") for i, node in enumerate(desired))
    rewritten = "".join(lines[:starts[0]]) + replacement + "".join(lines[ends[-1]:])
    token = hashlib.sha256(raw + b"\0" + json.dumps(state.get("files", {}).get(favorites.name, {}), sort_keys=True).encode()).hexdigest()
    return {"ok": True, "token": token, "upstreamOrder": upstream, "customOrder": desired,
            "different": upstream != desired, "content": rewritten}


def manage(args: argparse.Namespace) -> dict[str, Any]:
    favorites = Path(args.file) if args.action in ("preview-recovery", "restore-backup") else validate_favorites(args.file)
    state = read_state()
    entry = file_state(state, favorites)
    nodes = entry["nodes"]
    rows = parse_allscan(favorites.read_text(encoding="utf-8", errors="replace")) if favorites.exists() else []
    row_nodes = [row["node"] for row in rows]

    if args.action in ("preview-recovery", "restore-backup"):
        # Only restore from known migration/backup directories. Never accept
        # arbitrary source paths even when called through sudo.
        source = Path(args.source)
        allowed = [ALLOWED_DIRECTORY, Path("/var/lib/allscan-reimagined/migrations"),
                   Path("/var/www/html/allscan"), Path("/var/www/html/asr")]
        if source.is_symlink() or not source.is_file() or source.parent not in allowed:
            raise ValueError("Recovery source is outside approved Favorites backup locations.")
        if not re.fullmatch(r"favorites[\w.\-\[\]]*\.ini(?:\.bak|\.asr-backup-[\w-]+)?|favorites[\w.-]*before[\w.-]*\.ini", source.name, re.I):
            raise ValueError("Recovery source must be a recognized Favorites backup.")
        if not any(marker in source.name for marker in (".bak", "before", ".asr-backup-")):
            raise ValueError("Recovery requires a preserved backup, not a live Favorites file.")
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("Recovery backup exceeds the file size limit.")
        recovered = source.read_bytes()
        recovered.decode("utf-8")
        if not recovered.strip():
            raise ValueError("Recovery backup is empty; refusing to replace Favorites.")
        current = favorites.read_bytes() if favorites.exists() else b""
        token = hashlib.sha256(current + b"\0" + recovered + b"\0" + str(source).encode()).hexdigest()
        if args.action == "preview-recovery":
            return {"ok": True, "source": str(source), "target": str(favorites),
                    "bytes": len(recovered), "existingBytes": len(current), "token": token}
        if args.value != token:
            raise ValueError("Recovery source or target changed; preview again.")
        if current == recovered:
            return {"ok": True, "changed": False}
        import datetime
        backup = favorites.with_name(favorites.name + ".asr-backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(current)
            handle.flush()
            os.fsync(handle.fileno())
        if (favorites.read_bytes() if favorites.exists() else b"") != current or source.read_bytes() != recovered:
            raise ValueError("Recovery files changed; original backup retained, restore cancelled.")
        atomic_write(favorites, recovered.decode("utf-8"), favorites.stat() if favorites.exists() else None)
        return {"ok": True, "changed": True, "backup": str(backup)}

    if args.action == "edit-file":
        # The web API sends a base64 JSON envelope; the helper holds the shared
        # Favorites lock and never writes before preserving the exact original.
        request = json.loads(base64.b64decode(args.value, validate=True))
        expected = request.get("expectedHash", "")
        original = favorites.read_bytes()
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(original).hexdigest() != expected:
            raise ValueError("Favorites file changed; reload before editing.")
        new = base64.b64decode(request.get("contentBase64", ""), validate=True)
        if len(new) > MAX_BYTES or b"\0" in new:
            raise ValueError("Invalid or oversized Favorites content.")
        new.decode("utf-8")
        if not new.strip() and original.strip():
            raise ValueError("Refusing to replace a populated Favorites file with empty content.")
        if new == original:
            return {"ok": True, "changed": False}
        import datetime
        backup = favorites.with_name(favorites.name + ".asr-backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        if favorites.read_bytes() != original:
            raise ValueError("Favorites changed during edit; backup preserved and write cancelled.")
        atomic_write(favorites, new.decode("utf-8"), favorites.stat())
        return {"ok": True, "changed": True, "backup": str(backup)}

    if args.action == "export-upstream-order":
        preview = upstream_export_preview(favorites, state)
        if args.value != preview["token"]:
            raise ValueError("Favorites changed since preview; review again before applying.")
        if not preview["different"]:
            return {"ok": True, "changed": False}
        metadata = favorites.stat()
        import datetime
        backup = favorites.with_name(favorites.name + ".asr-backup-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(favorites.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        if upstream_export_preview(favorites, read_state())["token"] != preview["token"]:
            raise ValueError("Favorites changed before write; backup retained, export aborted.")
        atomic_write(favorites, preview["content"], metadata)
        return {"ok": True, "changed": True, "backup": str(backup)}
    if args.action == "import-upstream-order":
        preview = upstream_order_preview(favorites, state)
        if args.value != preview["token"]:
            raise ValueError("Favorites changed since preview; review again before applying.")
        entry["order"] = preview["upstreamOrder"]
        write_state(state)
        return {"ok": True, "changed": bool(preview["different"]), "count": len(preview["upstreamOrder"])}
    if args.action == "update-description":
        if args.node not in row_nodes:
            raise ValueError("Favorite was not found.")
        value = args.value.strip()
        if not value or len(value) > 200 or any(ord(char) < 32 for char in value):
            raise ValueError("Description is invalid.")
        nodes.setdefault(args.node, {})["description"] = value
    elif args.action == "reset-description":
        nodes.setdefault(args.node, {}).pop("description", None)
    elif args.action == "set-color":
        value = args.value.strip()
        if value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError("Color must use #RRGGBB.")
        if value:
            nodes.setdefault(args.node, {})["color"] = value
        else:
            nodes.setdefault(args.node, {}).pop("color", None)
    elif args.action == "reset-appearance":
        for data in nodes.values():
            if isinstance(data, dict):
                data.pop("color", None)
    elif args.action == "reorder":
        order = json.loads(args.value)
        if not isinstance(order, list) or sorted(map(str, order)) != sorted(row_nodes):
            raise ValueError("Reorder list must contain every current Favorite exactly once.")
        entry["order"] = list(map(str, order))
    elif args.action == "reset-order":
        entry.pop("order", None)
    elif args.action == "reset-favorite":
        nodes.pop(args.node, None)
    elif args.action == "import":
        source = discover_supermon(args.source)
        if not source:
            raise ValueError("No compatible Supermon Favorites file was found.")
        preview = preview_import(favorites, source)
        if preview["malformed"]:
            raise ValueError("Supermon Favorites file contains no compatible entries.")
        additions = preview["additions"]
        if additions:
            metadata = favorites.stat()
            atomic_write(Path(str(favorites) + ".bak"), favorites.read_text(encoding="utf-8"), metadata)
            atomic_write(favorites, render(rows + additions), metadata)
        entry["lastImport"] = {"source": str(source), "added": len(additions)}
        write_state(state)
        return {**preview, "changed": bool(additions), "added": len(additions)}

    write_state(state)
    return {"ok": True, "changed": True}


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-favorites-manager-") as directory:
        root = Path(directory)
        sample = (
            'label[] = "W7XYZ Phoenix, Arizona 12345"\n'
            'cmd[] = "rpt cmd %node% ilink 3 12345"\n'
        )
        rows = parse_allscan(sample)
        assert rows[0]["node"] == "12345"
        assert decode_ini_value('"Phoenix, \\"Valley\\""') == 'Phoenix, "Valley"'
        legacy = '[favorites]\n54321 = "Phoenix Club, Main Site"\n'
        imported = parse_supermon(legacy)
        assert imported == [{
            "node": "54321",
            "label": "Phoenix Club, Main Site 54321",
            "command": "rpt cmd %node% ilink 3 54321",
        }]
        favorites_dir = root / "etc"
        favorites_dir.mkdir()
        favorites = favorites_dir / "favorites.ini"
        favorites.write_text(sample, encoding="utf-8")
        source = root / "supermon.ini"
        source.write_text(legacy + '67890 = "Quoted, \\"Description\\""\n', encoding="utf-8")
        preview = preview_import(favorites, source)
        assert preview["detected"] == 2
        assert preview["additions"][1]["label"] == 'Quoted, "Description" 67890'
        malformed = root / "bad.ini"
        malformed.write_text("not favorites", encoding="utf-8")
        assert preview_import(favorites, malformed)["malformed"] is True

        state_path = root / "favorites-user.json"
        expected_state = {
            "version": 1,
            "files": {
                "favorites.ini": {
                    "order": ["12345"],
                    "nodes": {
                        "12345": {
                            "description": "Phoenix Club Repeater",
                            "color": "#123456",
                        },
                    },
                },
            },
        }
        write_state(expected_state, state_path)
        assert read_state(state_path) == expected_state
        merged = rows + preview["additions"]
        rendered = render(merged)
        assert "W7XYZ Phoenix, Arizona 12345" in rendered
        assert 'Quoted, \\"Description\\" 67890' in rendered
    print("Favorites metadata and Supermon import self-test: ok")
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("action", nargs="?", choices=(
        "preview-import", "import", "update-description", "reset-description",
        "set-color", "reset-appearance", "reorder", "reset-order", "reset-favorite",
        "preview-upstream-order", "import-upstream-order",
        "preview-export-upstream-order", "export-upstream-order", "edit-file", "preview-recovery", "restore-backup",
    ))
    parser.add_argument("--file", default="")
    parser.add_argument("--node", default="")
    parser.add_argument("--value", default="")
    parser.add_argument("--source", default="")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if os.geteuid() != 0:
        raise PermissionError("Favorites manager must run as root.")
    if args.action in ("preview-recovery", "restore-backup") and not Path(args.file).exists():
        favorites = Path(args.file)
        if favorites.parent != ALLOWED_DIRECTORY or not FAVORITES_NAME.fullmatch(favorites.name) or favorites.is_symlink():
            raise ValueError("Missing recovery target must be a canonical Favorites filename.")
    else:
        favorites = validate_favorites(args.file)
    source = discover_supermon(args.source)
    if args.action == "preview-export-upstream-order":
        preview = upstream_export_preview(favorites, read_state())
        preview.pop("content")
        print(json.dumps(preview))
        return 0
    if args.action == "preview-upstream-order":
        print(json.dumps(upstream_order_preview(favorites, read_state())))
        return 0
    if args.action == "preview-import":
        if not source:
            print(json.dumps({"ok": True, "available": False}))
        else:
            print(json.dumps({"available": True, **preview_import(favorites, source)}))
        return 0
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        print(json.dumps(manage(args)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)
