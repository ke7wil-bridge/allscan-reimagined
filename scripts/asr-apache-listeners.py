#!/usr/bin/env python3
"""Persist live nonstandard Apache listeners across ASR worker restarts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


CONFIG_ROOT = Path(os.environ.get("ASR_APACHE_CONFIG_ROOT", "/etc/apache2"))
STATE_PATH = Path(
    os.environ.get(
        "ASR_APACHE_LISTENER_STATE",
        "/etc/allscan-reimagined/apache-listeners.json",
    )
)
MANAGED_NAME = "allscan-reimagined-listeners.conf"
MANAGED_AVAILABLE = CONFIG_ROOT / "conf-available" / MANAGED_NAME
MANAGED_ENABLED = CONFIG_ROOT / "conf-enabled" / MANAGED_NAME
LISTEN_RE = re.compile(r"^\s*Listen\s+(?:\[[^]]+\]|[^\s:]+:)?(\d+)\s*(?:#.*)?$")
SS_PORT_RE = re.compile(r"(?:\[[^]]+\]|[^\s:]+|\*):(\d+)\s")


def active_config_files() -> list[Path]:
    files: list[Path] = []
    for path in (CONFIG_ROOT / "apache2.conf", CONFIG_ROOT / "ports.conf"):
        if path.is_file():
            files.append(path)
    for directory in (CONFIG_ROOT / "conf-enabled", CONFIG_ROOT / "sites-enabled"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.name == MANAGED_NAME or not path.is_file():
                continue
            files.append(path)
    return files


def configured_ports() -> set[int]:
    ports: set[int] = set()
    for path in active_config_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = LISTEN_RE.match(line)
            if match:
                ports.add(int(match.group(1)))
    return ports


def live_apache_ports(ss_output: str | None = None) -> set[int]:
    if ss_output is None:
        try:
            completed = subprocess.run(
                ["ss", "-H", "-ltnp"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            ss_output = completed.stdout
        except OSError:
            ss_output = ""
    ports: set[int] = set()
    for line in ss_output.splitlines():
        if '(("apache2"' not in line and '(("httpd"' not in line:
            continue
        match = SS_PORT_RE.search(line)
        if match:
            ports.add(int(match.group(1)))
    return ports


def load_state() -> set[int]:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    values = payload.get("ports", []) if isinstance(payload, dict) else []
    return {int(port) for port in values if isinstance(port, int) and 0 < port < 65536}


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def save_state(ports: set[int]) -> None:
    atomic_write(
        STATE_PATH,
        json.dumps({"version": 1, "ports": sorted(ports)}, indent=2) + "\n",
        0o600,
    )


def render(ports: set[int]) -> None:
    persistent_elsewhere = configured_ports()
    managed_ports = sorted(ports - persistent_elsewhere)
    lines = [
        "# Managed by AllScan Reimagined.",
        "# Preserves nonstandard Apache listeners that were active before an ASR update.",
    ]
    lines.extend(f"Listen {port}" for port in managed_ports)
    atomic_write(MANAGED_AVAILABLE, "\n".join(lines) + "\n", 0o644)
    MANAGED_ENABLED.parent.mkdir(parents=True, exist_ok=True)
    if not MANAGED_ENABLED.exists():
        MANAGED_ENABLED.symlink_to(Path("../conf-available") / MANAGED_NAME)


def validate() -> None:
    if os.environ.get("ASR_APACHE_LISTENER_SKIP_CONFIGTEST") == "1":
        return
    subprocess.run(["apache2ctl", "configtest"], check=True)


def render_validated(ports: set[int]) -> None:
    previous_content = MANAGED_AVAILABLE.read_bytes() if MANAGED_AVAILABLE.is_file() else None
    enabled_existed = MANAGED_ENABLED.exists() or MANAGED_ENABLED.is_symlink()
    try:
        render(ports)
        validate()
    except Exception:
        if previous_content is None:
            MANAGED_AVAILABLE.unlink(missing_ok=True)
        else:
            atomic_write(MANAGED_AVAILABLE, previous_content.decode("utf-8"), 0o644)
        if not enabled_existed:
            MANAGED_ENABLED.unlink(missing_ok=True)
        raise


def preserve(extra_ports: set[int] | None = None, ss_output: str | None = None) -> set[int]:
    saved = load_state()
    live_custom = live_apache_ports(ss_output) - configured_ports()
    ports = saved | live_custom | (extra_ports or set())
    render_validated(ports)
    save_state(ports)
    return ports


def self_test() -> None:
    global CONFIG_ROOT, STATE_PATH, MANAGED_AVAILABLE, MANAGED_ENABLED
    with tempfile.TemporaryDirectory(prefix="asr-apache-listeners-") as temporary:
        root = Path(temporary)
        CONFIG_ROOT = root / "apache2"
        STATE_PATH = root / "allscan-reimagined" / "apache-listeners.json"
        MANAGED_AVAILABLE = CONFIG_ROOT / "conf-available" / MANAGED_NAME
        MANAGED_ENABLED = CONFIG_ROOT / "conf-enabled" / MANAGED_NAME
        (CONFIG_ROOT / "conf-enabled").mkdir(parents=True)
        (CONFIG_ROOT / "sites-enabled").mkdir(parents=True)
        (CONFIG_ROOT / "ports.conf").write_text("Listen 80\nListen 443\n", encoding="utf-8")
        os.environ["ASR_APACHE_LISTENER_SKIP_CONFIGTEST"] = "1"
        fake_ss = "\n".join(
            (
                'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("apache2",pid=10,fd=4))',
                'LISTEN 0 511 0.0.0.0:18080 0.0.0.0:* users:(("apache2",pid=10,fd=5))',
                'LISTEN 0 128 0.0.0.0:9999 0.0.0.0:* users:(("other",pid=20,fd=6))',
            )
        )
        assert preserve(ss_output=fake_ss) == {18080}
        assert load_state() == {18080}
        assert "Listen 18080" in MANAGED_AVAILABLE.read_text(encoding="utf-8")
        assert "Listen 80" not in MANAGED_AVAILABLE.read_text(encoding="utf-8")
        assert preserve(ss_output="") == {18080}, "saved custom ports must survive later updates"
    print("Apache listener preservation self-test: PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capture")
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("port", type=int)
    subparsers.add_parser("apply")
    subparsers.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
        return
    if os.geteuid() != 0:
        parser.error("root is required")
    if args.command == "capture":
        preserve()
    elif args.command == "add":
        if not 0 < args.port < 65536:
            parser.error("port must be between 1 and 65535")
        preserve({args.port})
    else:
        render_validated(load_state())


if __name__ == "__main__":
    main()
