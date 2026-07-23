#!/usr/bin/env python3
"""Perform locked, atomic ASR Favorites updates as root."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile


ALLOWED_DIRECTORY = Path("/etc/allscan")
LOCK_FILE = Path("/run/allscan-reimagined/favorites.lock")
FAVORITES_NAME = re.compile(r"favorites[\w.\-\[\]]*\.ini\Z")
NODE_VALUE = re.compile(r"[A-Za-z0-9*#]{3,8}\Z")
LABEL_LINE = re.compile(r"^\s*label\s*\[\]\s*=")
MAX_FAVORITES_BYTES = 2 * 1024 * 1024


def validate_file(requested: str, allowed_directory: Path = ALLOWED_DIRECTORY) -> Path:
    path = Path(requested).resolve(strict=True)
    allowed = allowed_directory.resolve(strict=True)
    if path.parent != allowed or not path.is_file() or not FAVORITES_NAME.fullmatch(path.name):
        raise ValueError("Favorites path is not an allowed /etc/allscan Favorites file.")
    if path.stat().st_size > MAX_FAVORITES_BYTES:
        raise ValueError("Favorites file is unexpectedly large.")
    return path


def command_node(line: str) -> str:
    match = re.search(r"\bilink\s+\d+\s+([A-Za-z0-9*#]{3,8})\b", line, re.IGNORECASE)
    return match.group(1) if match else ""


def favorite_text(current: str, action: str, node: str, label: str) -> str:
    lines = current.splitlines()
    if action == "delete":
        updated: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            if LABEL_LINE.match(line) and command_node(next_line) == node:
                index += 2
                continue
            updated.append(line)
            index += 1
        return "\n".join(updated).rstrip() + "\n"

    if re.search(r"\bilink\s+\d+\s+" + re.escape(node) + r"\b", current, re.IGNORECASE):
        return current
    if not label or len(label) > 500 or any(ord(char) < 32 for char in label):
        raise ValueError("Favorite label is invalid.")
    escaped = label.replace("\\", "\\\\").replace('"', '\\"')
    entry = (
        f'label[] = "{escaped}"\n'
        f'cmd[] = "rpt cmd %node% ilink 3 {node}"\n'
    )
    base = current.rstrip()
    return (base + "\n\n" if base else "") + entry


def atomic_replace(path: Path, data: str, metadata: os.stat_result) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".asr-favorites.",
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IMODE(metadata.st_mode))
        os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(data)
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


def update_favorites(
    path: Path,
    action: str,
    node: str,
    label: str,
    lock_file: Path = LOCK_FILE,
) -> bool:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        metadata = path.stat()
        current = path.read_text(encoding="utf-8")
        updated = favorite_text(current, action, node, label)
        if updated == current:
            return False
        atomic_replace(Path(str(path) + ".bak"), current, metadata)
        atomic_replace(path, updated, metadata)
        return True


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-favorites-update-") as directory:
        root = Path(directory)
        favorites_dir = root / "etc-allscan"
        favorites_dir.mkdir()
        favorites = favorites_dir / "favorites.ini"
        favorites.write_text(
            'label[] = "Existing 2300"\n'
            'cmd[] = "rpt cmd %node% ilink 3 2300"\n',
            encoding="utf-8",
        )
        favorites.chmod(0o664)
        validated = validate_file(str(favorites), favorites_dir)
        lock_file = root / "run" / "favorites.lock"

        assert update_favorites(validated, "add", "29332", "WL7LP 29332", lock_file)
        assert not update_favorites(validated, "add", "29332", "WL7LP 29332", lock_file)
        assert "29332" in favorites.read_text(encoding="utf-8")
        assert update_favorites(validated, "delete", "29332", "", lock_file)
        assert "29332" not in favorites.read_text(encoding="utf-8")
        assert "29332" in Path(str(favorites) + ".bak").read_text(encoding="utf-8")
        assert stat.S_IMODE(favorites.stat().st_mode) == 0o664

        outside = root / "favorites.ini"
        outside.write_text("", encoding="utf-8")
        try:
            validate_file(str(outside), favorites_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("Out-of-directory Favorites path was accepted.")
    print("locked atomic Favorites update self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("action", nargs="?", choices=("add", "delete"))
    parser.add_argument("--file", default="")
    parser.add_argument("--node", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if os.geteuid() != 0:
        raise PermissionError("Favorites update helper must run as root.")
    if not args.action or not NODE_VALUE.fullmatch(args.node):
        raise ValueError("Favorites action or node is invalid.")

    path = validate_file(args.file)
    changed = update_favorites(path, args.action, args.node, args.label)
    print(json.dumps({"ok": True, "changed": changed}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        raise SystemExit(1)
