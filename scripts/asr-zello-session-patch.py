#!/usr/bin/env python3
"""Idempotently add ASR current-stream Kick enforcement to the Zello bridge."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


HELPERS = r'''
ZELLO_SESSION_KICKS = os.environ.get(
    "ZELLO_SESSION_KICKS", "/run/urf-wil-config/asr-zello-kicks.json"
)


def _ke7wil_zello_kick_state(user, clear=False):
    import fcntl
    import tempfile
    name = _ke7wil_clean_talker_name(user).upper()
    if not name:
        return False
    lock_path = ZELLO_SESSION_KICKS + ".lock"
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(ZELLO_SESSION_KICKS, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = {"schema": 1, "sessions": {}}
        sessions = payload.get("sessions", {}) if isinstance(payload, dict) else {}
        if not isinstance(sessions, dict):
            sessions = {}
        record = sessions.get(name)
        active = isinstance(record, dict) and time.time() - int(record.get("createdAt") or 0) <= 300
        if clear and name in sessions:
            sessions.pop(name, None)
            payload = {"schema": 1, "sessions": sessions}
            directory = os.path.dirname(ZELLO_SESSION_KICKS)
            descriptor, temporary = tempfile.mkstemp(prefix=".asr-zello-kicks.", dir=directory, text=True)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                    handle.write("\n")
                os.chmod(temporary, 0o644)
                os.replace(temporary, ZELLO_SESSION_KICKS)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return active
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def _ke7wil_zello_is_kicked(user):
    return _ke7wil_zello_kick_state(user)


def _ke7wil_zello_clear_kick(user):
    _ke7wil_zello_kick_state(user, clear=True)
'''


def patch_text(text: str) -> str:
    if "def _ke7wil_zello_is_kicked(user):" in text:
        return text
    helper_anchor = "\n# KE7WIL safe Zello stream debug export\n"
    if helper_anchor not in text or "def _ke7wil_zello_is_banned(user):" not in text:
        raise RuntimeError("unsupported Zello source: ASR ban integration is missing")
    text = text.replace(helper_anchor, "\n" + HELPERS + helper_anchor, 1)
    start = "if _ke7wil_zello_is_banned(user):"
    binary = "if _ke7wil_zello_is_banned(self._talk_user):"
    if text.count(start) != 1 or text.count(binary) != 1:
        raise RuntimeError("unsupported Zello source: stream suppression anchors changed")
    text = text.replace(start, "if _ke7wil_zello_is_banned(user) or _ke7wil_zello_is_kicked(user):", 1)
    text = text.replace(binary, "if _ke7wil_zello_is_banned(self._talk_user) or _ke7wil_zello_is_kicked(self._talk_user):", 1)
    stop_anchor = """                                            self._zello_ptt.clear()
                                            # Drop any decoded PCM left from the completed Zello transmission.
"""
    stop_replacement = """                                            _ke7wil_zello_clear_kick(user)
                                            self._zello_ptt.clear()
                                            # Drop any decoded PCM left from the completed Zello transmission.
"""
    if text.count(stop_anchor) != 1:
        raise RuntimeError("unsupported Zello source: stream-stop anchor changed")
    return text.replace(stop_anchor, stop_replacement, 1)


def patch_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    patched = patch_text(source)
    if patched == source:
        return False
    details = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(patched)
            handle.flush()
            os.fchmod(handle.fileno(), details.st_mode & 0o777)
            os.fchown(handle.fileno(), details.st_uid, details.st_gid)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if not args.path.is_absolute() or args.path.is_symlink() or not args.path.is_file():
        raise SystemExit("Zello source must be an absolute regular file")
    changed = patch_file(args.path)
    print("patched" if changed else "already-patched")


if __name__ == "__main__":
    main()
