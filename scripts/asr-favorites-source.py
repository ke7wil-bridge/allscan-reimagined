#!/usr/bin/env python3
"""Safely make stock AllScan and ASR use the canonical Favorites source."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time


CFG_ID_FAVORITES = 2
CANONICAL_ENTRY = "/etc/allscan/favorites.ini"
DEFAULT_ENTRIES = [
    "favorites.ini",
    "../supermon/favorites.ini",
    CANONICAL_ENTRY,
]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_preserved(source: Path, destination: Path, mode: int = 0o600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=".asr-favorites-source.",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def ordered_sources(raw: str | None) -> list[str]:
    entries = [
        item.strip()
        for item in (raw.split(",") if raw is not None else DEFAULT_ENTRIES)
        if item.strip()
    ]
    result = [CANONICAL_ENTRY]
    for entry in entries:
        if entry != CANONICAL_ENTRY and entry not in result:
            result.append(entry)
    return result


def source_policy_ready(database: Path, canonical: Path) -> bool:
    if not database.is_file() or not canonical.is_file() or canonical.stat().st_size == 0:
        return False
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT val FROM cfg WHERE cfg_id=?",
            (CFG_ID_FAVORITES,),
        ).fetchone()
        if row is None:
            return False
        entries = [item.strip() for item in str(row[0]).split(",") if item.strip()]
        return bool(entries) and entries[0] == CANONICAL_ENTRY
    finally:
        connection.close()


def apply_source_policy(
    database: Path,
    canonical: Path,
    stock_favorites: Path,
    migration_dir: Path,
) -> dict[str, object]:
    if not database.is_file():
        raise ValueError("Shared AllScan database was not found.")
    if not canonical.is_file() or canonical.stat().st_size == 0:
        raise ValueError("Canonical Favorites file is missing or empty.")

    migration_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stock_backup = ""
    if (
        stock_favorites.is_file()
        and stock_favorites.resolve() != canonical.resolve()
        and digest(stock_favorites) != digest(canonical)
    ):
        destination = migration_dir / (
            f"favorites-stock-before-canonical-{timestamp}-{os.getpid()}.ini"
        )
        copy_preserved(stock_favorites, destination)
        stock_backup = str(destination)

    connection = sqlite3.connect(database, timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout=10000")
        row = connection.execute(
            "SELECT val FROM cfg WHERE cfg_id=?",
            (CFG_ID_FAVORITES,),
        ).fetchone()
        old_value = str(row[0]) if row is not None else None
        new_value = ",".join(ordered_sources(old_value))
        changed = old_value != new_value
        database_backup = ""
        if changed:
            backup_path = migration_dir / (
                f"allscan-db-before-favorites-source-{timestamp}-{os.getpid()}.db"
            )
            backup_connection = sqlite3.connect(backup_path)
            try:
                connection.backup(backup_connection)
            finally:
                backup_connection.close()
            backup_path.chmod(0o600)
            database_backup = str(backup_path)

            connection.execute("BEGIN IMMEDIATE")
            if row is None:
                connection.execute(
                    "INSERT INTO cfg (cfg_id, val, updated) VALUES (?, ?, ?)",
                    (CFG_ID_FAVORITES, new_value, int(time.time())),
                )
            else:
                connection.execute(
                    "UPDATE cfg SET val=?, updated=? WHERE cfg_id=?",
                    (new_value, int(time.time()), CFG_ID_FAVORITES),
                )
            connection.commit()
        return {
            "ok": True,
            "changed": changed,
            "sourceOrder": ordered_sources(new_value),
            "stockBackup": stock_backup,
            "databaseBackup": database_backup,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="asr-favorites-source-") as directory:
        root = Path(directory)
        database = root / "allscan.db"
        canonical = root / "etc" / "favorites.ini"
        stock = root / "www" / "allscan" / "favorites.ini"
        migrations = root / "migrations"
        canonical.parent.mkdir(parents=True)
        stock.parent.mkdir(parents=True)
        canonical.write_text('label[] = "Canonical 29332"\n', encoding="utf-8")
        stock.write_text('label[] = "Stock 2300"\n', encoding="utf-8")
        canonical_before = digest(canonical)
        stock_before = digest(stock)

        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE cfg (cfg_id INTEGER PRIMARY KEY, val TEXT NOT NULL, updated INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO cfg VALUES (?, ?, ?)",
            (
                CFG_ID_FAVORITES,
                "favorites.ini,../supermon/favorites.ini,/etc/allscan/favorites.ini",
                int(time.time()),
            ),
        )
        connection.commit()
        connection.close()

        result = apply_source_policy(database, canonical, stock, migrations)
        assert result["changed"] is True
        assert result["sourceOrder"][0] == CANONICAL_ENTRY
        assert result["stockBackup"]
        assert result["databaseBackup"]
        assert digest(canonical) == canonical_before
        assert digest(stock) == stock_before
        assert Path(str(result["stockBackup"])).read_bytes() == stock.read_bytes()
        assert stat.S_IMODE(Path(str(result["stockBackup"])).stat().st_mode) == 0o600

        connection = sqlite3.connect(database)
        value = connection.execute(
            "SELECT val FROM cfg WHERE cfg_id=?",
            (CFG_ID_FAVORITES,),
        ).fetchone()[0]
        connection.close()
        assert value.startswith(CANONICAL_ENTRY + ",")
        assert source_policy_ready(database, canonical)

        second = apply_source_policy(database, canonical, stock, migrations)
        assert second["changed"] is False
        assert second["databaseBackup"] == ""
    print("canonical Favorites source self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--database", default="/etc/allscan/allscan.db")
    parser.add_argument("--canonical", default=CANONICAL_ENTRY)
    parser.add_argument(
        "--stock-favorites",
        default="/var/www/html/allscan/favorites.ini",
    )
    parser.add_argument(
        "--migration-dir",
        default="/var/lib/allscan-reimagined/migrations",
    )
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.check:
        ready = source_policy_ready(Path(args.database), Path(args.canonical))
        print(json.dumps({"ok": True, "ready": ready}, separators=(",", ":")))
        return 0 if ready else 3
    if not args.apply or os.geteuid() != 0:
        raise PermissionError("Run this helper as root with --apply.")
    result = apply_source_policy(
        Path(args.database),
        Path(args.canonical),
        Path(args.stock_favorites),
        Path(args.migration_dir),
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")))
        raise SystemExit(1)
