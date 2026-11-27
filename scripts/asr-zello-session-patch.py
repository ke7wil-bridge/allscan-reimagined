#!/usr/bin/env python3
"""Idempotently add ASR Kick enforcement (a one-minute mute) to the Zello bridge."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


HELPERS = r'''
ZELLO_SESSION_KICKS = os.environ.get(
    "ZELLO_SESSION_KICKS", "/var/lib/allscan-reimagined/admin/asr-zello-kicks.json"
)
# #134: a Zello Kick mutes every transmission from that talker on this bridge
# for a short hold (Zello channels have no remote disconnect).
try:
    ZELLO_KICK_HOLD_SECONDS = max(30, min(3600, int(os.environ.get("ZELLO_KICK_HOLD_SECONDS", "60"))))
except ValueError:
    ZELLO_KICK_HOLD_SECONDS = 60


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
        age = time.time() - int(record.get("createdAt") or 0) if isinstance(record, dict) else -1
        active = 0 <= age < ZELLO_KICK_HOLD_SECONDS
        # At stream end, drop only a hold that has run out: the mute covers
        # every transmission inside the hold, not just the first one.
        if clear and name in sessions and not active:
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

# The one-shot helpers earlier releases installed; upgraded in place.
LEGACY_HELPERS = r'''
ZELLO_SESSION_KICKS = os.environ.get(
    "ZELLO_SESSION_KICKS", "/var/lib/allscan-reimagined/admin/asr-zello-kicks.json"
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


HOLD_BLOCK = '''# #134: a Zello Kick mutes every transmission from that talker on this bridge
# for a short hold (Zello channels have no remote disconnect).
try:
    ZELLO_KICK_HOLD_SECONDS = max(30, min(3600, int(os.environ.get("ZELLO_KICK_HOLD_SECONDS", "60"))))
except ValueError:
    ZELLO_KICK_HOLD_SECONDS = 60
'''
LEGACY_ACTIVE = '''        record = sessions.get(name)
        active = isinstance(record, dict) and time.time() - int(record.get("createdAt") or 0) <= 300
        if clear and name in sessions:'''
TIMED_ACTIVE = '''        record = sessions.get(name)
        age = time.time() - int(record.get("createdAt") or 0) if isinstance(record, dict) else -1
        active = 0 <= age < ZELLO_KICK_HOLD_SECONDS
        # At stream end, drop only a hold that has run out: the mute covers
        # every transmission inside the hold, not just the first one.
        if clear and name in sessions and not active:'''
assert LEGACY_ACTIVE in LEGACY_HELPERS and TIMED_ACTIVE in HELPERS and HOLD_BLOCK in HELPERS

def _patch_kick_text(text: str) -> str:
    if "def _ke7wil_zello_is_kicked(user):" in text:
        if "ZELLO_KICK_HOLD_SECONDS" in text:
            return text
        # Upgrade the earlier one-shot Kick to the timed mute. Installers set
        # their own kicks-file default, so only the two changed spots are matched.
        hold_anchor = "\ndef _ke7wil_zello_kick_state(user, clear=False):\n"
        if text.count(hold_anchor) != 1 or text.count(LEGACY_ACTIVE) != 1:
            raise RuntimeError("unsupported Zello source: earlier Kick helpers changed")
        text = text.replace(hold_anchor, "\n" + HOLD_BLOCK + hold_anchor, 1)
        return text.replace(LEGACY_ACTIVE, TIMED_ACTIVE, 1)
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


EXACT_BAN_HELPER = r'''
# #319: exact Zello-only identity enforcement; radio policy stays separate.
ZELLO_EXACT_BANS = os.path.join(
    os.path.dirname(globals().get("ZELLO_GLOBAL_BANS") or ZELLO_SESSION_KICKS),
    "asr-zello-exact-bans.json",
)


def _ke7wil_zello_is_exact_banned(user):
    name = _ke7wil_clean_talker_name(user).upper()
    if not name:
        return False
    try:
        with open(ZELLO_EXACT_BANS, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            return False
        rules = payload.get("rules")
        record = rules.get(name) if isinstance(rules, dict) else None
        if not isinstance(record, dict) or set(record) != {"createdAt", "expiresAt"}:
            return False
        created, expires = record["createdAt"], record["expiresAt"]
        return (type(created) is int and type(expires) is int and created >= 0
                and (expires == 0 or (expires > created and time.time() < expires)))
    except (OSError, ValueError):
        return False
'''


def patch_text(text: str) -> str:
    text = _patch_kick_text(text)
    if "def _ke7wil_zello_is_exact_banned(user):" in text:
        return text
    anchor = "\n# KE7WIL safe Zello stream debug export\n"
    if text.count(anchor) != 1:
        raise RuntimeError("unsupported Zello source: exact-ban insertion anchor changed")
    for value in ("user", "self._talk_user"):
        old = f"if _ke7wil_zello_is_banned({value}) or _ke7wil_zello_is_kicked({value}):"
        new = old[:-1] + f" or _ke7wil_zello_is_exact_banned({value}):"
        if text.count(old) != 1:
            raise RuntimeError("unsupported Zello source: exact-ban suppression anchor changed")
        text = text.replace(old, new, 1)
    return text.replace(anchor, "\n" + EXACT_BAN_HELPER + anchor, 1)


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
