#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
ZELLO_PATCH_PATH = ROOT / "scripts/asr-zello-session-patch.py"


def check(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


INSTALLER = (ROOT / "scripts/asr-bridge-setup-zello-install.py").read_text(encoding="utf-8")
check("ExecStartPre=/usr/bin/test -f /var/lib/allscan-reimagined/admin/global-bans.list" in INSTALLER,
      "Zello startup must require the native Global Ban blacklist")
check('ZELLO_GLOBAL_BANS: "/run/asr-global/global-bans.list"' in INSTALLER
      and 'ZELLO_SESSION_KICKS: "/run/asr-global/asr-zello-kicks.json"' in INSTALLER
      and "session_patch.patch_file(zello)" in INSTALLER,
      "managed Zello installer is not the production enforcement path")
check("urfd.blacklist" not in INSTALLER and "/var/lib/allscan-reimagined/global-ban" not in INSTALLER,
      "Zello installer still initializes obsolete Global Ban directory")
check("Recent Zello Talkers" in APP and "managedConnectedCards" in APP,
      "Zello Manage Clients presentation missing")
check("current or next Zello transmission for up to five minutes" in APP
      and "mode.toUpperCase() === 'ZELLO'\n        ? await kickZelloClient(callsign)" in APP,
      "Zello one-shot Kick is not available for recent talkers")

spec = importlib.util.spec_from_file_location("asr_global_ban_zello", ROOT / "scripts/asr-global-ban.py")
global_ban = importlib.util.module_from_spec(spec)
spec.loader.exec_module(global_ban)
with tempfile.TemporaryDirectory(prefix="asr-zello-kick-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    global_ban.ZELLO_TALKERS = str(tmp / "zello-talkers.json")
    global_ban.ZELLO_KICKS = str(tmp / "asr-zello-kicks.json")
    global_ban.append_audit_event = lambda *args, **kwargs: None
    emitted = []
    global_ban.emit = lambda **kwargs: emitted.append(kwargs)
    now = int(time.time())
    Path(global_ban.ZELLO_TALKERS).write_text(json.dumps({
        "active": False,
        "current_user": "",
        "recent_users": [{"name": "N0CALL", "last_seen_epoch": now - 30}],
    }), encoding="utf-8")
    global_ban.write_zello_kick("n0call", "self-test")
    kick = json.loads(Path(global_ban.ZELLO_KICKS).read_text(encoding="utf-8"))
    check("N0CALL" in kick["sessions"], "recent Zello talker Kick was not armed")
    check(emitted[-1]["enforcement"] == "current-or-next-zello-stream"
          and emitted[-1]["expiresSeconds"] == 300,
          "recent Zello talker Kick did not report its one-shot contract")

patch_spec = importlib.util.spec_from_file_location("asr_zello_session_patch_test", ZELLO_PATCH_PATH)
session_patch = importlib.util.module_from_spec(patch_spec)
patch_spec.loader.exec_module(session_patch)

# The release does not vendor the separately maintained Zello application.
# Production stages that upstream tree and this installer-owned
# patch injects enforcement into both decoded-stream entry points.  Exercise
# that exact patch and the resulting runtime behavior instead of silently
# skipping whenever a developer checkout lacks the external source tree.
RUNTIME_FIXTURE = '''import json
import os
import time

def _ke7wil_clean_talker_name(value):
    return str(value or "").strip()

ZELLO_GLOBAL_BANS = ""
def _ke7wil_zello_is_banned(user):
    name = _ke7wil_clean_talker_name(user).upper()
    try:
        with open(ZELLO_GLOBAL_BANS, "r", encoding="utf-8") as handle:
            rules = [line.strip().upper() for line in handle if line.strip() and not line.startswith("#")]
    except OSError:
        return False
    return any(name == rule or (rule.endswith("*") and name.startswith(rule[:-1])) for rule in rules)

# KE7WIL safe Zello stream debug export

def accept_json_stream(user):
    if _ke7wil_zello_is_banned(user):
        return False
    return True

class RuntimeFixture:
    def __init__(self, user):
        self._talk_user = user
        self._zello_ptt = PttFixture()

    def accept_binary_stream(self):
        if _ke7wil_zello_is_banned(self._talk_user):
            return False
        return True

    def stop_stream(self, user):
        if user:
            if True:
                if True:
                    if True:
                        if True:
                            if True:
                                if True:
                                    if True:
                                        if True:
                                            self._zello_ptt.clear()
                                            # Drop any decoded PCM left from the completed Zello transmission.
        return True
'''


class PttFixture:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


with tempfile.TemporaryDirectory(prefix="asr-zello-enforcement-self-test-") as raw_tmp:
    tmp = Path(raw_tmp)
    patched = session_patch.patch_text(RUNTIME_FIXTURE)
    check(session_patch.patch_text(patched) == patched, "Zello enforcement patch is not idempotent")
    namespace = {"PttFixture": PttFixture}
    exec(compile(patched, str(ZELLO_PATCH_PATH), "exec"), namespace)
    bans = tmp / "global-bans.list"
    kicks = tmp / "asr-zello-kicks.json"
    namespace["ZELLO_GLOBAL_BANS"] = str(bans)
    namespace["ZELLO_SESSION_KICKS"] = str(kicks)
    bans.write_text("N0CALL*\nAD7TG\n", encoding="utf-8")

    check(not namespace["accept_json_stream"]("n0call-7"),
          "globally banned Zello JSON stream bypassed enforcement")
    check(not namespace["RuntimeFixture"]("AD7TG").accept_binary_stream(),
          "globally banned Zello binary stream bypassed enforcement")
    check(namespace["accept_json_stream"]("KE7WIL"),
          "legitimate Zello JSON stream was blocked")
    check(namespace["RuntimeFixture"]("KE7WIL").accept_binary_stream(),
          "legitimate Zello binary stream was blocked")

    now = int(time.time())
    kicks.write_text(json.dumps({
        "schema": 1,
        "sessions": {
            "KE7WIL": {"createdAt": now},
            "EXPIRED": {"createdAt": now - 301},
        },
    }), encoding="utf-8")
    check(not namespace["accept_json_stream"]("KE7WIL"),
          "one-shot Zello Kick did not block the selected JSON stream")
    check(not namespace["RuntimeFixture"]("KE7WIL").accept_binary_stream(),
          "one-shot Zello Kick did not block the selected binary stream")
    check(namespace["accept_json_stream"]("K7SAFE"),
          "Zello Kick blocked an unrelated legitimate client")
    check(namespace["accept_json_stream"]("EXPIRED"),
          "expired Zello Kick continued blocking a legitimate client")

    runtime = namespace["RuntimeFixture"]("KE7WIL")
    runtime.stop_stream("KE7WIL")
    check(runtime._zello_ptt.cleared, "Zello stream stop did not clear PTT")
    remaining = json.loads(kicks.read_text(encoding="utf-8"))["sessions"]
    check("KE7WIL" not in remaining and "EXPIRED" in remaining,
          "Zello stream stop did not consume only the selected one-shot Kick")

print("Zello client-management and runtime enforcement self-test: ok")
