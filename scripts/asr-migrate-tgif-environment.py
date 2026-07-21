#!/usr/bin/env python3
"""Move TGIF daemon credentials out of a systemd drop-in into a protected file."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import tempfile
from pathlib import Path


DEFAULT_DROPIN = Path(
    "/etc/systemd/system/connected-clients-daemon.service.d/tgif-token.conf"
)
DEFAULT_ENV = Path("/etc/allscan-reimagined/connected-clients-daemon.env")
CANONICAL_DROPIN = "[Service]\nEnvironmentFile=-/etc/allscan-reimagined/connected-clients-daemon.env\n"


def environment_assignments(path: Path, prefix: str | None = None) -> dict[str, str]:
    assignments: dict[str, str] = {}
    if not path.is_file():
        return assignments
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Environment="):
            values = shlex.split(line.split("=", 1)[1], posix=True)
        else:
            values = shlex.split(line, comments=True, posix=True)
        for value in values:
            if "=" not in value:
                continue
            name, contents = value.split("=", 1)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and (
                prefix is None or name.startswith(prefix)
            ):
                assignments[name] = contents
    return assignments


def quote_environment_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("TGIF environment values must be single-line text")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_atomic(path: Path, contents: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        if os.geteuid() == 0:
            os.chown(temporary_name, 0, 0)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def migrate(dropin: Path, env_file: Path) -> bool:
    legacy = environment_assignments(dropin, prefix="TGIF_")
    existing = environment_assignments(env_file)
    configured = bool(legacy or existing)
    if not configured:
        return False

    combined = dict(existing)
    combined.update(legacy)
    env_contents = "".join(
        f"{name}={quote_environment_value(combined[name])}\n" for name in sorted(combined)
    )
    expected_uid = 0 if os.geteuid() == 0 else os.geteuid()
    expected_gid = 0 if os.geteuid() == 0 else os.getegid()
    env_needs_write = (
        not env_file.is_file()
        or env_file.read_text(encoding="utf-8") != env_contents
        or stat.S_IMODE(env_file.stat().st_mode) != 0o600
        or env_file.stat().st_uid != expected_uid
        or env_file.stat().st_gid != expected_gid
    )
    dropin_contents = CANONICAL_DROPIN.replace(str(DEFAULT_ENV), str(env_file))
    dropin_needs_write = (
        not dropin.is_file()
        or dropin.read_text(encoding="utf-8") != dropin_contents
        or stat.S_IMODE(dropin.stat().st_mode) != 0o644
        or dropin.stat().st_uid != expected_uid
        or dropin.stat().st_gid != expected_gid
    )
    if env_needs_write:
        write_atomic(env_file, env_contents, 0o600)
    if dropin_needs_write:
        write_atomic(dropin, dropin_contents, 0o644)
    return env_needs_write or dropin_needs_write


def self_test() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        dropin = root / "systemd/tgif-token.conf"
        env_file = root / "etc/connected-clients-daemon.env"
        dropin.parent.mkdir(parents=True)
        dropin.write_text(
            '[Service]\nEnvironment="TGIF_API_TOKEN=secret value" "TGIF_TG=86753"\n',
            encoding="utf-8",
        )
        env_file.parent.mkdir(parents=True)
        env_file.write_text('LOCAL_SETTING="preserved"\n', encoding="utf-8")
        assert migrate(dropin, env_file)
        assert "secret value" not in dropin.read_text(encoding="utf-8")
        assert f"EnvironmentFile=-{env_file}" in dropin.read_text(encoding="utf-8")
        assert environment_assignments(env_file) == {
            "LOCAL_SETTING": "preserved",
            "TGIF_API_TOKEN": "secret value",
            "TGIF_TG": "86753",
        }
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(dropin.stat().st_mode) == 0o644
        assert not migrate(dropin, env_file)
    print("TGIF protected-environment migration self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dropin", type=Path, default=DEFAULT_DROPIN)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if os.geteuid() != 0:
        parser.error("run as root")
    if migrate(args.dropin, args.env_file):
        print("migrated TGIF daemon credentials to protected environment storage")
        return 0
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
