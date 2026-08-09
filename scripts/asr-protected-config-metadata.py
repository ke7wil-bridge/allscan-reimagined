#!/usr/bin/env python3
"""Safely restore privileged metadata after atomic ASR Settings saves."""

from __future__ import annotations

import argparse
import grp
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable


CONFIG_DIR = Path("/etc/allscan-reimagined")
TARGETS = (("config.json", 0o664, True), ("secrets.json", 0o640, False))


class RepairError(RuntimeError):
    pass


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_config_dir(path: Path, expected_uid: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RepairError(f"ASR configuration directory is not a safe directory: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid:
            raise RepairError(f"ASR configuration directory has unsafe ownership or type: {path}")
        if stat.S_IMODE(info.st_mode) & 0o002:
            raise RepairError(f"ASR configuration directory is world-writable: {path}")
        path_info = os.stat(path, follow_symlinks=False)
        if not _same_inode(info, path_info):
            raise RepairError(f"ASR configuration directory changed while it was opened: {path}")
        return descriptor, info
    except Exception:
        os.close(descriptor)
        raise


def _repair_one(
    directory_fd: int,
    name: str,
    expected_uid: int,
    expected_gid: int,
    expected_mode: int,
    before_path_verify: Callable[[], None] | None = None,
) -> bool:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RepairError(f"Protected ASR file is not a safe regular file: {name}") from exc

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise RepairError(f"Protected ASR file must be a single-link regular file: {name}")

        os.fchown(descriptor, expected_uid, expected_gid)
        os.fchmod(descriptor, expected_mode)

        repaired = os.fstat(descriptor)
        if not _same_inode(opened, repaired) or repaired.st_nlink != 1:
            raise RepairError(f"Protected ASR file changed while metadata was repaired: {name}")
        if before_path_verify is not None:
            before_path_verify()

        try:
            installed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise RepairError(f"Protected ASR file disappeared after metadata repair: {name}") from exc
        if not stat.S_ISREG(installed.st_mode) or not _same_inode(repaired, installed):
            raise RepairError(f"Protected ASR file was replaced during metadata repair: {name}")
        if (
            installed.st_uid != expected_uid
            or installed.st_gid != expected_gid
            or stat.S_IMODE(installed.st_mode) != expected_mode
            or installed.st_nlink != 1
        ):
            raise RepairError(f"Protected ASR file metadata did not verify: {name}")
        return True
    finally:
        os.close(descriptor)


def repair_protected_config_metadata(
    config_dir: Path,
    expected_uid: int,
    expected_gid: int,
) -> list[str]:
    directory_fd, directory_info = _open_config_dir(config_dir, expected_uid)
    repaired: list[str] = []
    try:
        for name, mode, required in TARGETS:
            present = _repair_one(directory_fd, name, expected_uid, expected_gid, mode)
            if required and not present:
                raise RepairError(f"Required protected ASR file is missing: {name}")
            if present:
                repaired.append(name)
        current_directory = os.stat(config_dir, follow_symlinks=False)
        if not _same_inode(directory_info, current_directory):
            raise RepairError("ASR configuration directory was replaced during metadata repair.")
    finally:
        os.close(directory_fd)
    return repaired


def _atomic_replace(path: Path, content: str, mode: int) -> tuple[int, int]:
    previous = path.stat() if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    current = path.stat()
    if previous is not None and _same_inode(previous, current):
        raise AssertionError("atomic replacement self-test did not replace the inode")
    return current.st_dev, current.st_ino


def _expect_repair_error(action: Callable[[], object], label: str) -> None:
    try:
        action()
    except RepairError:
        return
    raise AssertionError(f"unsafe {label} was accepted")


def self_test() -> None:
    uid = os.getuid()
    gid = os.getgid()
    with tempfile.TemporaryDirectory(prefix="asr-protected-config-metadata-") as raw_tmp:
        config_dir = Path(raw_tmp) / "config"
        config_dir.mkdir(mode=0o770)
        config = config_dir / "config.json"
        secrets = config_dir / "secrets.json"

        config.write_text('{"bridges":[]}\n', encoding="utf-8")
        original_inode = config.stat().st_ino
        _atomic_replace(config, '{"bridges":[{"id":"test"}]}\n', 0o600)
        assert config.stat().st_ino != original_inode
        _atomic_replace(secrets, '{"bridgeClientPasswords":{}}\n', 0o666)
        expected_contents = {
            config.name: config.read_bytes(),
            secrets.name: secrets.read_bytes(),
        }

        assert repair_protected_config_metadata(config_dir, uid, gid) == [
            "config.json",
            "secrets.json",
        ]
        for path, mode in ((config, 0o664), (secrets, 0o640)):
            info = path.stat()
            assert info.st_uid == uid and info.st_gid == gid
            assert stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1
            assert path.read_bytes() == expected_contents[path.name]

        first_stats = {path.name: path.stat() for path in (config, secrets)}
        repair_protected_config_metadata(config_dir, uid, gid)
        for path in (config, secrets):
            assert _same_inode(first_stats[path.name], path.stat())
            assert path.read_bytes() == expected_contents[path.name]

        secrets.unlink()
        assert repair_protected_config_metadata(config_dir, uid, gid) == ["config.json"]

        missing_config = config_dir / ".missing-config"
        config.rename(missing_config)
        _expect_repair_error(
            lambda: repair_protected_config_metadata(config_dir, uid, gid),
            "missing required config",
        )
        missing_config.rename(config)

        target = config_dir / "outside-target"
        target.write_text("do not modify\n", encoding="utf-8")
        target.chmod(0o600)
        target_before = target.stat()
        config.unlink()
        config.symlink_to(target)
        directory_fd, _ = _open_config_dir(config_dir, uid)
        try:
            _expect_repair_error(
                lambda: _repair_one(directory_fd, config.name, uid, gid, 0o664),
                "symbolic link",
            )
        finally:
            os.close(directory_fd)
        target_after = target.stat()
        assert target.read_text(encoding="utf-8") == "do not modify\n"
        assert stat.S_IMODE(target_after.st_mode) == stat.S_IMODE(target_before.st_mode)
        assert (target_after.st_uid, target_after.st_gid) == (target_before.st_uid, target_before.st_gid)

        config.unlink()
        config.mkdir()
        directory_fd, _ = _open_config_dir(config_dir, uid)
        try:
            _expect_repair_error(
                lambda: _repair_one(directory_fd, config.name, uid, gid, 0o664),
                "directory",
            )
        finally:
            os.close(directory_fd)

        config.rmdir()
        hardlink_target = config_dir / ".config-hardlink-target"
        hardlink_target.write_text('{"hardlink":true}\n', encoding="utf-8")
        os.link(hardlink_target, config)
        directory_fd, _ = _open_config_dir(config_dir, uid)
        try:
            _expect_repair_error(
                lambda: _repair_one(directory_fd, config.name, uid, gid, 0o664),
                "multiple-link file",
            )
        finally:
            os.close(directory_fd)
        config.unlink()
        hardlink_target.unlink()

        os.mkfifo(config, 0o600)
        directory_fd, _ = _open_config_dir(config_dir, uid)
        try:
            _expect_repair_error(
                lambda: _repair_one(directory_fd, config.name, uid, gid, 0o664),
                "FIFO",
            )
        finally:
            os.close(directory_fd)

        config.unlink()
        config.write_text('{"race":"original"}\n', encoding="utf-8")
        replacement = config_dir / ".config-race-replacement"

        def replace_during_repair() -> None:
            replacement.write_text('{"race":"replacement"}\n', encoding="utf-8")
            replacement.chmod(0o600)
            os.replace(replacement, config)

        directory_fd, _ = _open_config_dir(config_dir, uid)
        try:
            _expect_repair_error(
                lambda: _repair_one(
                    directory_fd,
                    config.name,
                    uid,
                    gid,
                    0o664,
                    before_path_verify=replace_during_repair,
                ),
                "replacement race",
            )
        finally:
            os.close(directory_fd)
        assert config.read_text(encoding="utf-8") == '{"race":"replacement"}\n'
        assert stat.S_IMODE(config.stat().st_mode) == 0o600

    print("ASR protected-config metadata self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-group", help="Web-server group to apply in production")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if os.geteuid() != 0:
        parser.error("production metadata repair must run as root")
    if not args.web_group:
        parser.error("--web-group is required for production metadata repair")
    try:
        web_gid = grp.getgrnam(args.web_group).gr_gid
        repair_protected_config_metadata(CONFIG_DIR, 0, web_gid)
    except (KeyError, RepairError, OSError) as exc:
        parser.exit(1, f"Protected ASR configuration metadata repair failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
