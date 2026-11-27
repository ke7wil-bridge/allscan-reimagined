#!/usr/bin/env python3
"""Zello Kick mutes the talker for one minute, not one transmission (#134).

Zello channels have no remote disconnect, so ASR's Zello Kick is a mute on
this bridge. It must cover every transmission inside the hold (a hot mic keys
up again at once) and lift on its own afterwards. Earlier releases installed
a one-shot mute; the patch upgrades that in place.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("zello_patch", ROOT / "scripts/asr-zello-session-patch.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# The anchors the real asl-zello-bridge source carries after ASR's ban patch.
BRIDGE = '''import json
import os
import time


def _ke7wil_clean_talker_name(user):
    return str(user or "").strip()


def _ke7wil_zello_is_banned(user):
    return False

# KE7WIL safe Zello stream debug export
def on_start(user):
    if _ke7wil_zello_is_banned(user):
        return "suppressed"


def on_audio(self):
    if _ke7wil_zello_is_banned(self._talk_user):
        return "suppressed"


def on_stop(self, user):
                                            self._zello_ptt.clear()
                                            # Drop any decoded PCM left from the completed Zello transmission.
'''

fresh = patch.patch_text(BRIDGE)
check("ZELLO_KICK_HOLD_SECONDS" in fresh and "_ke7wil_zello_clear_kick(user)" in fresh, "fresh patch incomplete")
check(patch.patch_text(fresh) == fresh, "patch is not idempotent")

# A bridge patched by an earlier release (one-shot Kick) is upgraded in place.
legacy = fresh.replace(patch.HELPERS, patch.LEGACY_HELPERS, 1)
check("ZELLO_KICK_HOLD_SECONDS" not in legacy, "legacy fixture still has the hold")
upgraded = patch.patch_text(legacy)
check("ZELLO_KICK_HOLD_SECONDS = 60" in upgraded and patch.TIMED_ACTIVE in upgraded
      and patch.LEGACY_ACTIVE not in upgraded, "earlier one-shot Kick was not upgraded to the one-minute mute")
check(patch.patch_text(upgraded) == upgraded, "upgrade is not idempotent")
# Installers rewrite the kicks-file default; the upgrade must not depend on it.
relocated = legacy.replace("/var/lib/allscan-reimagined/admin/asr-zello-kicks.json", "/run/example/asr-zello-kicks.json")
moved = patch.patch_text(relocated)
check(patch.TIMED_ACTIVE in moved and "/run/example/asr-zello-kicks.json" in moved, "upgrade failed on a relocated kicks file")

with tempfile.TemporaryDirectory() as tmp:
    kicks = Path(tmp) / "asr-zello-kicks.json"
    os.environ["ZELLO_SESSION_KICKS"] = str(kicks)
    os.environ.pop("ZELLO_KICK_HOLD_SECONDS", None)
    namespace: dict = {}
    exec(compile(fresh, "patched-zello-bridge", "exec"), namespace)
    check(namespace["ZELLO_KICK_HOLD_SECONDS"] == 60, "Zello Kick hold is not one minute")
    is_kicked, clear = namespace["_ke7wil_zello_is_kicked"], namespace["_ke7wil_zello_clear_kick"]

    now = time.time()
    kicks.write_text(json.dumps({"schema": 1, "sessions": {"KE7WIL": {"actor": "test", "createdAt": int(now) - 5}}}))
    check(is_kicked("ke7wil"), "kicked talker was not muted")
    clear("KE7WIL")  # the first muted transmission ends
    check(is_kicked("KE7WIL"), "mute ended after one transmission instead of one minute")
    check(not is_kicked("W1AW"), "another talker was muted")

    kicks.write_text(json.dumps({"schema": 1, "sessions": {"KE7WIL": {"actor": "test", "createdAt": int(now) - 61}}}))
    check(not is_kicked("KE7WIL"), "mute outlived the one-minute hold")
    clear("KE7WIL")
    check(json.loads(kicks.read_text())["sessions"] == {}, "an ended mute was not cleaned up")
# #319: encoded identity is used solely by Zello Kick, not global rule parsing.
spec2 = importlib.util.spec_from_file_location("global_ban", ROOT / "scripts/asr-global-ban.py")
policy = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(policy)
token = policy.ZELLO_KICK_TOKEN_PREFIX + "TAURUS 78".encode("ascii").hex().upper()
check(policy.decode_zello_kick_identity(token) == "TAURUS 78", "exact spaced name lost")
for name in ("@NET OP", "TAURUS #78", "USER+ONE:2"):
    encoded = policy.ZELLO_KICK_TOKEN_PREFIX + name.encode("ascii").hex().upper()
    check(policy.decode_zello_kick_identity(encoded) == name, "supported runtime punctuation lost")

for invalid in ("TAURUS 78", policy.ZELLO_KICK_TOKEN_PREFIX + "00", policy.ZELLO_KICK_TOKEN_PREFIX + "2A", policy.ZELLO_KICK_TOKEN_PREFIX + "FF", "TAURUS*"):
    try:
        policy.decode_zello_kick_identity(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe Zello Kick target accepted: " + invalid)
with tempfile.TemporaryDirectory() as tmp:
    policy.ZELLO_TALKERS = str(Path(tmp) / "talkers.json")
    policy.ZELLO_KICKS = str(Path(tmp) / "kicks.json")
    policy.AUDIT_LOG = str(Path(tmp) / "audit.jsonl")
    now = int(time.time())
    Path(policy.ZELLO_TALKERS).write_text(json.dumps({"active": False, "recent_users": [{"name":"TAURUS 78","last_seen_epoch":now},{"name":"TAURUS","last_seen_epoch":now}]}))
    policy.write_zello_kick(policy.decode_zello_kick_identity(token), "fixture")
    state = json.loads(Path(policy.ZELLO_KICKS).read_text())
    check(set(state["sessions"]) == {"TAURUS 78"}, "Kick targeted a different identity")
    os.environ["ZELLO_SESSION_KICKS"] = policy.ZELLO_KICKS
    namespace = {}
    exec(compile(fresh, "patched-zello-bridge", "exec"), namespace)
    check(namespace["_ke7wil_zello_is_kicked"]("Taurus 78"), "spaced target not muted")
    namespace["_ke7wil_zello_clear_kick"]("Taurus 78")
    check(namespace["_ke7wil_zello_is_kicked"]("Taurus 78"), "rekey ended hold")
    check(not namespace["_ke7wil_zello_is_kicked"]("TAURUS"), "prefix neighbor incorrectly muted")
# #319: exact-name ban checks are independent of the Kick hold and radio rules.
with tempfile.TemporaryDirectory() as tmp:
    policy.CONFIG = str(Path(tmp) / "missing-config.json")
    policy.ZELLO_EXACT_BANS = str(Path(tmp) / "asr-zello-exact-bans.json")
    os.environ["ZELLO_SESSION_KICKS"] = str(Path(tmp) / "kicks.json")
    now = int(time.time())
    rules = policy.change_zello_exact_ban({}, "ban", "TAURUS 78", "15m", now)
    policy.write_zello_exact_bans(rules)
    namespace = {}
    exec(compile(fresh, "patched-zello-bridge", "exec"), namespace)
    exact = namespace["_ke7wil_zello_is_exact_banned"]
    check(exact("Taurus 78"), "exact Zello name was not banned")
    check(not exact("TAURUS"), "radio identity was accidentally banned")
    check(not exact("TAURUS 79"), "neighboring name was accidentally banned")
    check(namespace["on_start"]("Taurus 78") == "suppressed", "exact ban failed at stream start")
    from types import SimpleNamespace
    check(namespace["on_audio"](SimpleNamespace(_talk_user="Taurus 78")) == "suppressed", "exact ban failed during stream")
    check(namespace["on_audio"](SimpleNamespace(_talk_user="Taurus 79")) is None, "unrelated stream blocked")
    policy.write_zello_exact_bans({"TAURUS 78": {"createdAt": now-901, "expiresAt": now-1}})
    check(not exact("Taurus 78"), "expired exact-name ban still suppresses audio")
    policy.write_zello_exact_bans(policy.change_zello_exact_ban(rules, "unban", "TAURUS 78"))
    check(not exact("Taurus 78"), "exact-name unban did not take effect")
    # A source with the prior timed Kick patch upgrades without adding duplicate helpers.
    previous = fresh.replace(patch.EXACT_BAN_HELPER, "", 1)
    for target in ("user", "self._talk_user"):
        previous = previous.replace(f" or _ke7wil_zello_is_exact_banned({target})", "")
    upgraded_exact = patch.patch_text(previous)
    check(upgraded_exact.count("def _ke7wil_zello_is_exact_banned(user):") == 1, "exact-ban upgrade failed")
    check(patch.patch_text(upgraded_exact) == upgraded_exact, "exact-ban upgrade not idempotent")
print("Zello Kick one-minute mute self-test: ok")
