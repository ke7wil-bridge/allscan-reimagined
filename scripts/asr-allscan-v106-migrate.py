#!/usr/bin/env python3
"""Safely migrate the ASR cfg key collision introduced by AllScan v1.06."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


ASR_SHOW_COMMAND_BUTTONS = 15
LEGACY_COLLIDING_KEY = 14
VERSION_RE = re.compile(r"^v([0-9]+)\.([0-9]+)$")


class MigrationError(RuntimeError):
    pass


def is_v106_or_newer(version: str) -> bool:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise MigrationError(f"Unrecognized installed AllScan version: {version}")
    return (int(match.group(1)), int(match.group(2))) >= (1, 6)


def migrate(database: Path, from_version: str) -> str:
    if is_v106_or_newer(from_version):
        return "native-v1.06-preserved"
    if not database.is_file() or database.is_symlink():
        raise MigrationError("Shared AllScan database is missing or unsafe")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cfg)").fetchall()
        }
        if columns != {"cfg_id", "val", "updated"}:
            raise MigrationError("Shared AllScan cfg table has an unexpected schema")

        migrated = connection.execute(
            "SELECT val FROM cfg WHERE cfg_id = ?", (ASR_SHOW_COMMAND_BUTTONS,)
        ).fetchone()
        if migrated is None:
            legacy = connection.execute(
                "SELECT val, updated FROM cfg WHERE cfg_id = ?", (LEGACY_COLLIDING_KEY,)
            ).fetchone()
            show_buttons = legacy[0] if legacy is not None else "1"
            updated = int(legacy[1]) if legacy is not None else 0
            if show_buttons not in {"0", "1"}:
                raise MigrationError("Legacy ASR command-button setting is invalid")
            connection.execute(
                "INSERT INTO cfg(cfg_id, val, updated) VALUES(?, ?, ?)",
                (ASR_SHOW_COMMAND_BUTTONS, show_buttons, updated),
            )

        connection.execute(
            "INSERT INTO cfg(cfg_id, val, updated) VALUES(?, '0', strftime('%s','now')) "
            "ON CONFLICT(cfg_id) DO UPDATE SET val = '0', updated = excluded.updated",
            (LEGACY_COLLIDING_KEY,),
        )
        connection.commit()
        return "migrated" if migrated is None else "already-migrated"
    except (sqlite3.Error, ValueError) as exc:
        connection.rollback()
        raise MigrationError(f"Shared AllScan database migration failed: {exc}") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_fixture(path: Path, value14: str | None, value15: str | None = None) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE cfg (cfg_id INTEGER PRIMARY KEY, val TEXT NOT NULL, updated INTEGER NOT NULL)"
    )
    if value14 is not None:
        connection.execute("INSERT INTO cfg VALUES(14, ?, 1700000014)", (value14,))
    if value15 is not None:
        connection.execute("INSERT INTO cfg VALUES(15, ?, 1700000015)", (value15,))
    connection.commit()
    connection.close()


def values(path: Path) -> dict[int, str]:
    connection = sqlite3.connect(path)
    try:
        return dict(connection.execute("SELECT cfg_id, val FROM cfg ORDER BY cfg_id"))
    finally:
        connection.close()


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for original in (None, "0", "1"):
            database = root / f"legacy-{original}.db"
            create_fixture(database, original)
            assert migrate(database, "v1.05") == "migrated"
            expected = "1" if original is None else original
            assert values(database) == {14: "0", 15: expected}
            assert migrate(database, "v1.05") == "already-migrated"
            assert values(database) == {14: "0", 15: expected}

        native = root / "native.db"
        create_fixture(native, "1")
        before = native.read_bytes()
        assert migrate(native, "v1.06") == "native-v1.06-preserved"
        assert native.read_bytes() == before
        assert values(native) == {14: "1"}

        rollback = root / "rollback.db"
        create_fixture(rollback, "1")
        backup = rollback.read_bytes()
        migrate(rollback, "v1.05")
        rollback.write_bytes(backup)
        assert values(rollback) == {14: "1"}
    print("ASR AllScan v1.06 cfg migration self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("migrate", "self-test"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--from-version")
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.database is None or args.from_version is None:
        parser.error("migrate requires --database and --from-version")
    try:
        print(migrate(args.database, args.from_version))
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
