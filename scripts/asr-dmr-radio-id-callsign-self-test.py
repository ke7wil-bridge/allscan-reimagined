#!/usr/bin/env python3
"""DMR talkers logged as bare radio IDs must resolve to callsigns (#224).

Managed DMR bridges ship a bridge-only DMRIds.dat that lists just the node
owner, so MMDVM logs every other station as a number. The status reader
resolves those IDs through the host's full, root-owned DMRIds.dat.
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("asr_bridge_status", ROOT / "scripts/asr_bridge_status.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RootOwned(type(Path())):
    """A temp file presented as root-owned, as /var/lib/mmdvm/DMRIds.dat is."""

    def lstat(self):  # type: ignore[override]
        real = super().lstat()
        return os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink, 0, 0,
                               real.st_size, int(real.st_atime), int(real.st_mtime), int(real.st_ctime)))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


with tempfile.TemporaryDirectory() as tmp:
    database = RootOwned(tmp) / "DMRIds.dat"
    database.write_text("3135475 KN4EWT Thomas\n3190684;KD9FSD;Chris\n3205161 N1YKT\n9999999 not-a-call\n",
                        encoding="utf-8")
    os.chmod(database, 0o644)
    check(module.radio_id_callsign("3135475", database) == "KN4EWT", "space-separated ID did not resolve")
    check(module.radio_id_callsign("3190684", database) == "KD9FSD", "semicolon-separated ID did not resolve")
    check(module.radio_id_callsign("1234567", database) == "", "unknown ID resolved to something")
    check(module.radio_id_callsign("9999999", database) == "", "invalid callsign was accepted")
    check(module.radio_id_callsign("KN4EWT", database) == "", "a callsign was treated as an ID")

    module._radio_id_cache.clear()
    os.chmod(database, 0o666)
    check(module.radio_id_callsign("3135475", database) == "", "a world-writable database was trusted")
    os.chmod(database, 0o644)

    # End to end: the MMDVM line carries a bare ID; the card shows the callsign.
    module._radio_id_cache.clear()
    module.RADIO_ID_PATH = database
    state = module.initial_activity_state()
    now = 2_000_000_000
    stamp = "M: 2033-05-18 03:33:20.000 "
    module.apply_activity_line(state, stamp + "DMR Slot 2, received network voice header from 3135475 to TG 42450",
                               "dmr", now)
    check(state.get("current_user") == "KN4EWT", f"DMR talker kept its radio ID: {state.get('current_user')!r}")
    module.apply_activity_line(state, stamp + "DMR Slot 2, received network voice header from N1YKT to TG 42450",
                               "dmr", now + 5)
    check(state.get("current_user") == "N1YKT", "an already-resolved callsign was changed")
print("DMR radio ID callsign self-test: ok")
