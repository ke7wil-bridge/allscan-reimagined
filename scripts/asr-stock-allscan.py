#!/usr/bin/env python3
"""Prepare a pinned, verified stock AllScan tree for a browser update."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import urllib.request


MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBERS = 10000
VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class StockUpdateError(RuntimeError):
    pass


def load_manifest(path: Path) -> dict[str, str | int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StockUpdateError("Stock AllScan manifest is unavailable") from exc
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise StockUpdateError("Stock AllScan manifest schema is unsupported")
    version = data.get("version")
    commit = data.get("commit")
    url = data.get("url")
    sha256 = data.get("sha256")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise StockUpdateError("Stock AllScan manifest version is invalid")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise StockUpdateError("Stock AllScan manifest commit is invalid")
    expected_url = f"https://codeload.github.com/davidgsd/AllScan/tar.gz/{commit}"
    if url != expected_url:
        raise StockUpdateError("Stock AllScan manifest URL is not pinned")
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise StockUpdateError("Stock AllScan manifest checksum is invalid")
    return data


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "AllScan-Reimagined/stock-updater"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("xb") as output:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_ARCHIVE:
                raise StockUpdateError("Stock AllScan archive exceeds size limit")
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE:
                    raise StockUpdateError("Stock AllScan archive exceeds size limit")
                output.write(chunk)
    except (OSError, ValueError) as exc:
        raise StockUpdateError("Stock AllScan archive download failed") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(name: str, root: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise StockUpdateError("Stock AllScan archive contains an unsafe path")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or parts[0] != root:
        raise StockUpdateError("Stock AllScan archive has an unexpected root")
    if len(parts) == 1:
        return None
    return PurePosixPath(*parts[1:])


def extract_verified(archive_path: Path, destination: Path, manifest: dict[str, str | int]) -> None:
    if destination.exists():
        raise StockUpdateError("Stock AllScan staging destination already exists")
    root = f"AllScan-{manifest['commit']}"
    destination.mkdir(mode=0o755, parents=False)
    total = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_MEMBERS:
                raise StockUpdateError("Stock AllScan archive member count is invalid")
            for member in members:
                relative = safe_relative(member.name, root)
                if relative is None:
                    continue
                normalized = str(relative)
                if normalized in seen:
                    raise StockUpdateError("Stock AllScan archive contains a duplicate path")
                seen.add(normalized)
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise StockUpdateError("Stock AllScan archive contains a link or special file")
                if member.size < 0 or member.size > MAX_ARCHIVE:
                    raise StockUpdateError("Stock AllScan archive contains an oversized file")
                total += member.size
                if total > MAX_EXPANDED:
                    raise StockUpdateError("Stock AllScan archive expands beyond its limit")
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise StockUpdateError("Stock AllScan archive member cannot be read")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
        common = destination / "include/common.php"
        try:
            text = common.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise StockUpdateError("Stock AllScan archive has no version metadata") from exc
        match = re.search(r'^\$AllScanVersion\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match or match.group(1) != manifest["version"]:
            raise StockUpdateError("Stock AllScan archive version does not match its manifest")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def prepare(manifest_path: Path, destination: Path) -> None:
    if os.geteuid() != 0:
        raise StockUpdateError("Root is required")
    manifest = load_manifest(manifest_path)
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".allscan-download-", dir=destination.parent)
    os.close(fd)
    archive_path = Path(name)
    archive_path.unlink()
    try:
        download(str(manifest["url"]), archive_path)
        if sha256_file(archive_path) != manifest["sha256"]:
            raise StockUpdateError("Stock AllScan archive checksum does not match")
        extract_verified(archive_path, destination, manifest)
    finally:
        archive_path.unlink(missing_ok=True)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest_path = root / "stock.json"
        manifest = {
            "schema": 1,
            "version": "v1.05",
            "commit": "a" * 40,
            "url": f"https://codeload.github.com/davidgsd/AllScan/tar.gz/{'a' * 40}",
            "sha256": "b" * 64,
        }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert load_manifest(manifest_path)["version"] == "v1.05"
        archive_path = root / "stock.tar.gz"
        root_name = f"AllScan-{manifest['commit']}"
        with tarfile.open(archive_path, "w:gz") as archive:
            payload = b'<?php\n$AllScanVersion = "v1.05";\n'
            info = tarfile.TarInfo(f"{root_name}/include/common.php")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        destination = root / "allscan"
        extract_verified(archive_path, destination, manifest)
        assert (destination / "include/common.php").is_file()
        bad_archive = root / "bad.tar.gz"
        with tarfile.open(bad_archive, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("../escape"))
        try:
            extract_verified(bad_archive, root / "bad", manifest)
        except StockUpdateError:
            pass
        else:
            raise AssertionError("unsafe archive path was accepted")
    print("ASR stock AllScan updater self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "manifest-version", "self-test"))
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("destination", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
        return 0
    if args.command == "manifest-version":
        if args.manifest is None:
            parser.error("manifest-version requires MANIFEST")
        try:
            print(load_manifest(args.manifest)["version"])
        except StockUpdateError as exc:
            print(f"ERROR: {exc}", file=os.sys.stderr)
            return 1
        return 0
    if args.manifest is None or args.destination is None:
        parser.error("prepare requires MANIFEST and DESTINATION")
    try:
        prepare(args.manifest, args.destination)
    except StockUpdateError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
