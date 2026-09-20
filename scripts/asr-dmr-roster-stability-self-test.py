#!/usr/bin/env python3
"""Regression: DMR connection roster must not be mutated by TX state."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tgif = load("asr_tgif_user_session", ROOT / "asr-tgif-user-session.py")
status = load("asr_bridge_status", ROOT / "asr_bridge_status.py")

tg = "12345"
sessions: dict[str, dict] = {}
for index, call in enumerate(["CLIENT-A", "CLIENT-B", "CLIENT-C", "CLIENT-D", "CLIENT-E"], 1):
    tgif.update_session_roster(
        sessions,
        {"uuid": str(index), "callsign": call, "state": "1", "ts2_talkgroup": tg},
        tg,
        100,
    )
assert len(sessions) == 5
assert {row["callsign"] for row in sessions.values()} == {
    "CLIENT-A", "CLIENT-B", "CLIENT-C", "CLIENT-D", "CLIENT-E"
}

activity = status.initial_activity_state()
start = "M: 2026-09-14 12:00:00.000 DMR Slot 2, received network voice header from CLIENT-C to TG 86753"
end = "M: 2026-09-14 12:00:05.000 DMR Slot 2, received network end of voice transmission"
status.apply_activity_line(activity, start, "dmr", 1_789_387_200)
assert activity["current_user"] == "CLIENT-C"
assert len(sessions) == 5
status.apply_activity_line(activity, end, "dmr", 1_789_387_205)
assert activity["current_user"] == ""
assert activity["last_source_user"] == "CLIENT-C"
assert activity["tx_events"][0]["callsign"] == "CLIENT-C"
assert activity["tx_events"][0]["event"] == "transmit"
assert len(sessions) == 5

# A TX-end-shaped session update is not connection-disconnect evidence.
tgif.update_session_roster(
    sessions,
    {"uuid": "3", "callsign": "CLIENT-C", "state": "0", "ts2_talkgroup": tg, "tx": False},
    tg,
    106,
)
assert len(sessions) == 5

# Only explicit independent connection evidence removes CLIENT-C.
tgif.update_session_roster(sessions, {"uuid": "3", "event": "disconnect"}, tg, 107)
assert len(sessions) == 4
assert all(row["callsign"] != "CLIENT-C" for row in sessions.values())

start_b = "M: 2026-09-14 12:01:00.000 DMR Slot 2, received network voice header from CLIENT-B to TG 86753"
end_b = "M: 2026-09-14 12:01:03.000 DMR Slot 2, received network end of voice transmission"
status.apply_activity_line(activity, start_b, "dmr", 1_789_387_260)
assert activity["current_user"] == "CLIENT-B" and len(sessions) == 4
status.apply_activity_line(activity, end_b, "dmr", 1_789_387_263)
assert activity["current_user"] == ""
assert activity["last_source_user"] == "CLIENT-B"
assert activity["tx_events"][0]["callsign"] == "CLIENT-B"
assert len(sessions) == 4

print("DMR roster stability self-test: ok")
