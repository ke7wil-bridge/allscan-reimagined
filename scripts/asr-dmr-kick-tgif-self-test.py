#!/usr/bin/env python3
"""DMR Kick must also disconnect the station from TGIF (#134).

The relay hold only refuses the station's audio on this node; TGIF keeps the
session (and lists it) until the talkgroup owner removes it. Kick now asks the
TGIF session helper for a short access-list hold and reports the outcome.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("asr_standalone_admin", ROOT / "scripts/asr-standalone-admin.py")
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


with tempfile.TemporaryDirectory() as tmp:
    helper = Path(tmp) / "tgif-helper"
    calls = Path(tmp) / "calls"

    def fake(stdout: str, code: int = 0) -> None:
        helper.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\ncat <<'JSON'\n{stdout}\nJSON\nexit {code}\n")
        os.chmod(helper, 0o755)

    admin.TGIF_SESSION_HELPER = str(helper)
    fake(json.dumps({"ok": True, "talkgroup": "67498", "entryId": 7, "ids": ["3228850"], "disconnected": True, "released": True,
                     "seconds": 0, "expiresAt": 0, "cookies": "must-not-leak"}))
    done = admin.tgif_kick("3228850", "KF8DRJ")
    check(done == {"ok": True, "talkgroup": "67498", "disconnected": True, "released": True, "seconds": 0, "expiresAt": 0}, f"unexpected result {done}")
    check(calls.read_text().split() == ["kick", "node", "3228850", "KF8DRJ"], calls.read_text())

    fake(json.dumps({"ok": False, "error": "TGIF session was rejected; sign in to TGIF again in ASR Settings"}), 1)
    failed = admin.tgif_kick("3228850", "KF8DRJ")
    check(not failed["ok"] and "sign in to TGIF again" in failed["error"], f"failure not reported: {failed}")

    fake("not json")
    check(admin.tgif_kick("3228850", "KF8DRJ") == {"ok": False, "error": "TGIF Kick returned no result."}, "garbage accepted")

    admin.TGIF_SESSION_HELPER = str(Path(tmp) / "missing")
    check(not admin.tgif_kick("3228850", "KF8DRJ")["ok"], "missing helper not reported")

# Only DMR Kick reaches TGIF; other modes are unchanged.
source = (ROOT / "scripts/asr-standalone-admin.py").read_text(encoding="utf-8")
check('tgif_kick(client_id, identity) if bridge["_mode"] == "dmr" else None' in source, "DMR Kick does not call TGIF")
ui = (ROOT / "src/App.tsx").read_text(encoding="utf-8")
check("TGIF did not disconnect the station" in ui and "was disconnected from TGIF" in ui, "dashboard does not report the TGIF result")
print("DMR Kick TGIF self-test: ok")
