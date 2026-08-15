#!/usr/bin/env python3
"""Optionally announce established Standard digital bridges once at startup."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


CONFIG_PATH = Path("/etc/allscan-reimagined/config.json")
ASTERISK_READ = "/usr/local/sbin/allscan-reimagined-asterisk-read"
ASTERISK = "/usr/sbin/asterisk"
SOUND_DIR = Path("/usr/share/asterisk/sounds/en/custom/allscan-reimagined")
SOUND_MARKER_NAME = ".asr-startup-summary-owner.json"
SOUND_MARKER = {"schema": 1, "createdBy": "allscan-reimagined", "purpose": "startup-bridge-summary"}
FLITE = "/usr/bin/flite"
SOX = "/usr/bin/sox"
MODE_LABELS = {
    "dmr": "DMR",
    "ysf": "YSF",
    "zello": "Zello",
    "p25": "P25",
    "nxdn": "NXDN",
    "m17": "M17",
}


class SummaryError(RuntimeError):
    pass


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=30)


def clean_title(value: Any, mode: str) -> str:
    title = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    title = re.sub(r"[^A-Za-z0-9 .,&+/#'()-]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" .,:;-_")[:60]
    if not title or re.fullmatch(r"New Digital Bridge", title, re.IGNORECASE):
        return MODE_LABELS.get(mode, mode.upper())
    return title


def standard_bridges(config: dict[str, Any]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen_nodes: set[str] = set()
    for bridge in config.get("bridges", []):
        if not isinstance(bridge, dict) or bridge.get("cardType", "standard") != "standard":
            continue
        mode = str(bridge.get("mode", bridge.get("id", ""))).lower()
        mode = next((known for known in MODE_LABELS if mode.startswith(known)), "")
        if not mode:
            continue
        backend_mode = str(bridge.get("backendMode", ""))
        if backend_mode == "display_only":
            continue
        if mode in {"p25", "nxdn", "m17"} and backend_mode != "managed":
            continue
        node = str(bridge.get("node", ""))
        if not re.fullmatch(r"[0-9]{1,10}", node) or int(node) <= 0 or node in seen_nodes:
            continue
        seen_nodes.add(node)
        selected.append({"node": node, "title": clean_title(bridge.get("title"), mode), "mode": mode})
    return selected


def peer_is_loopback(value: str) -> bool:
    peer = value.strip()
    if peer.startswith("[") and "]" in peer:
        peer = peer[1:peer.index("]")]
    elif peer.count(":") == 1 and "." in peer:
        peer = peer.rsplit(":", 1)[0]
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def established_nodes(output: str) -> set[str]:
    result: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].isdigit() and peer_is_loopback(fields[1]) and fields[-1] == "ESTABLISHED":
            result.add(fields[0])
    return result


def summary_text(config: dict[str, Any], links: set[str]) -> str:
    connected = [bridge["title"] for bridge in standard_bridges(config) if bridge["node"] in links]
    if connected:
        if len(connected) == 1:
            names = connected[0]
        elif len(connected) == 2:
            names = f"{connected[0]} and {connected[1]}"
        else:
            names = ", ".join(connected[:-1]) + f", and {connected[-1]}"
        return f"Connected digital bridges: {names}."
    if config.get("announceNoConnectedBridges") is True:
        return "No digital bridges connected."
    return ""


def read_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SummaryError("ASR configuration is invalid.")
    return payload


def read_links(main_node: str, runner: Runner) -> set[str] | None:
    result = runner([ASTERISK_READ, "lstats", main_node])
    if result.returncode != 0:
        return None
    return established_nodes(result.stdout)


def wait_for_stable_links(
    main_node: str, runner: Runner, *, timeout: int = 90, poll: int = 5,
    stable_for: int = 10,
) -> set[str]:
    deadline = time.monotonic() + timeout
    previous: set[str] | None = None
    stable_since = 0.0
    while time.monotonic() <= deadline:
        current = read_links(main_node, runner)
        now = time.monotonic()
        if current is not None:
            if current != previous:
                previous = current
                stable_since = now
            elif now - stable_since >= stable_for:
                return current
        time.sleep(poll)
    return previous or set()


def verify_sound_directory(sound_dir: Path) -> None:
    if sound_dir.is_symlink() or not sound_dir.is_dir():
        raise SummaryError("The ASR startup-summary sound directory is missing, unowned, or unsafe.")
    marker = sound_dir / SOUND_MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise SummaryError("The ASR startup-summary sound ownership marker is missing or unsafe.")
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SummaryError("The ASR startup-summary sound ownership marker is invalid.") from exc
    if payload != SOUND_MARKER:
        raise SummaryError("The ASR startup-summary sound directory is not owned by this ASR feature.")
    if sound_dir == SOUND_DIR:
        directory_details = sound_dir.lstat()
        marker_details = marker.lstat()
        if directory_details.st_uid != os.geteuid() or directory_details.st_mode & 0o027:
            raise SummaryError("The ASR startup-summary sound directory owner or mode is unsafe.")
        if marker_details.st_uid != 0 or marker_details.st_mode & 0o022:
            raise SummaryError("The ASR startup-summary ownership marker owner or mode is unsafe.")


def speak(
    text: str, main_node: str, runner: Runner, *, sound_dir: Path = SOUND_DIR,
    flite: str = FLITE, sox: str = SOX,
) -> None:
    if not text:
        return
    verify_sound_directory(sound_dir)
    for tool in (flite, sox):
        if not os.path.isfile(tool) or not os.access(tool, os.X_OK):
            raise SummaryError(f"Startup bridge summary requires executable {tool}.")
    target = sound_dir / "startup-bridge-summary.gsm"
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SummaryError("The startup-summary sound target is unsafe.")
    wave_fd, wave_name = tempfile.mkstemp(prefix=".startup-summary-", suffix=".wav", dir=sound_dir)
    gsm_fd, gsm_name = tempfile.mkstemp(prefix=".startup-summary-", suffix=".gsm", dir=sound_dir)
    os.close(wave_fd)
    os.close(gsm_fd)
    wave = Path(wave_name)
    gsm_temporary = Path(gsm_name)
    try:
        commands = (
            [flite, "-voice", "slt", "-t", text, "-o", str(wave)],
            [sox, str(wave), "-r", "8000", "-c", "1", str(gsm_temporary)],
        )
        for command in commands:
            result = runner(command)
            if result.returncode != 0:
                raise SummaryError(f"Startup bridge summary command failed: {command[0]}")
        for rendered in (wave, gsm_temporary):
            details = rendered.lstat()
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                raise SummaryError("Startup bridge summary produced an unsafe sound file.")
        os.chmod(gsm_temporary, 0o640)
        os.replace(gsm_temporary, target)
        play = runner([ASTERISK, "-rx", f"rpt localplay {main_node} custom/allscan-reimagined/startup-bridge-summary"])
        if play.returncode != 0:
            raise SummaryError("Asterisk could not play the startup bridge summary.")
    finally:
        wave.unlink(missing_ok=True)
        gsm_temporary.unlink(missing_ok=True)


def execute(config_path: Path = CONFIG_PATH, runner: Runner = run) -> dict[str, Any]:
    config = read_config(config_path)
    if config.get("announceStartupBridgeSummary") is not True:
        return {"ok": True, "enabled": False, "announced": False}
    main_node = str(config.get("node", ""))
    if not re.fullmatch(r"[0-9]{1,10}", main_node) or int(main_node) <= 0:
        raise SummaryError("The main AllStar node is not configured.")
    links = wait_for_stable_links(main_node, runner)
    text = summary_text(config, links)
    if text:
        speak(text, main_node, runner)
    return {
        "ok": True,
        "enabled": True,
        "announced": bool(text),
        "connectedBridgeCount": sum(1 for bridge in standard_bridges(config) if bridge["node"] in links),
    }


def self_test() -> None:
    config = {
        "node": "100000",
        "announceStartupBridgeSummary": True,
        "bridges": [
            {"id": "dmr", "mode": "dmr", "cardType": "standard", "node": "1998", "title": "DMR"},
            {"id": "ysf", "mode": "ysf", "cardType": "standard", "node": "1997", "title": "YSF"},
            {"id": "m17", "mode": "m17", "cardType": "standard", "backendMode": "display_only", "node": "1886", "title": "M17 display"},
            {"id": "nxdn", "mode": "nxdn", "cardType": "standard", "node": "1884", "title": "NXDN default display"},
            {"id": "p25", "mode": "p25", "cardType": "standard", "backendMode": "managed", "node": "1883", "title": "P25 managed"},
            {"id": "ysf_display", "mode": "ysf", "cardType": "standard", "backendMode": "display_only", "node": "1882", "title": "YSF display"},
            {"id": "p25_net", "mode": "p25", "cardType": "p25_net", "node": "1885", "title": "P25 Net"},
            {"id": "zello", "mode": "zello", "cardType": "standard", "node": "1999", "title": "New Digital Bridge"},
        ],
    }
    assert [item["node"] for item in standard_bridges(config)] == ["1998", "1997", "1883", "1999"]
    assert summary_text(config, {"1998", "1997", "1885", "999999"}) == "Connected digital bridges: DMR and YSF."
    assert summary_text(config, {"1999"}) == "Connected digital bridges: Zello."
    assert summary_text(config, set()) == ""
    config["announceNoConnectedBridges"] = True
    assert summary_text(config, set()) == "No digital bridges connected."
    assert "999999" not in summary_text(config, {"999999"})
    assert clean_title("  Evil\x00\n title\t;  ", "dmr") == "Evil title"
    assert clean_title("$(touch /tmp/pwned)`;\x01 DMR", "dmr") == "(touch /tmp/pwned) DMR"
    links = established_nodes(
        "1998 127.0.0.1 0 OUT 00:00:01 ESTABLISHED\n"
        "1997 127.22.1.9:4569 0 OUT 00:00:01 ESTABLISHED\n"
        "1883 ::1 0 OUT 00:00:01 ESTABLISHED\n"
        "1999 192.0.2.44 0 OUT 00:00:01 ESTABLISHED\n"
        "1882 127.0.0.1 0 OUT 00:00:01 CONNECTING\n"
    )
    assert links == {"1998", "1997", "1883"}

    with tempfile.TemporaryDirectory(prefix="asr-startup-summary-test-") as temporary:
        path = Path(temporary) / "config.json"
        disabled = dict(config)
        disabled["announceStartupBridgeSummary"] = False
        path.write_text(json.dumps(disabled), encoding="utf-8")
        assert execute(path)["enabled"] is False
        sound_dir = Path(temporary) / "sounds"
        sound_dir.mkdir()
        marker = sound_dir / SOUND_MARKER_NAME
        marker.write_text(json.dumps(SOUND_MARKER), encoding="utf-8")
        try:
            speak("Test.", "100000", run, sound_dir=sound_dir, flite=str(sound_dir / "missing-flite"), sox=str(sound_dir / "missing-sox"))
        except SummaryError as exc:
            assert "requires executable" in str(exc)
        else:
            raise AssertionError("missing speech tools were accepted")
        # Use existing executables outside the temporary directory so this
        # self-test also works when the system temp directory is mounted
        # noexec. The injected runner below intercepts both commands.
        fake_flite = Path(sys.executable)
        fake_sox = Path("/bin/sh")
        assert fake_flite != fake_sox
        rendered_commands: list[list[str]] = []

        def render_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            rendered_commands.append(command)
            if command[0] == str(fake_flite):
                Path(command[-1]).write_bytes(b"wave")
            elif command[0] == str(fake_sox):
                Path(command[-1]).write_bytes(b"gsm")
            return subprocess.CompletedProcess(command, 0, "", "")

        speak(
            "Connected digital bridges: DMR.", "100000", render_runner,
            sound_dir=sound_dir, flite=str(fake_flite), sox=str(fake_sox),
        )
        target = sound_dir / "startup-bridge-summary.gsm"
        assert target.is_file() and not target.is_symlink() and target.read_bytes() == b"gsm"
        assert rendered_commands[-1][-1] == "rpt localplay 100000 custom/allscan-reimagined/startup-bridge-summary"
        target.unlink()
        marker.write_text('{"schema":0}', encoding="utf-8")
        try:
            verify_sound_directory(sound_dir)
        except SummaryError:
            pass
        else:
            raise AssertionError("unowned sound directory was accepted")
        marker.unlink()
        marker.symlink_to(path)
        try:
            verify_sound_directory(sound_dir)
        except SummaryError:
            pass
        else:
            raise AssertionError("symlinked sound marker was accepted")
    print("ASR startup bridge summary self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        print(json.dumps(execute(), sort_keys=True))
        return 0
    except (OSError, ValueError, SummaryError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
