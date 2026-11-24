#!/usr/bin/env python3
"""Focused regression coverage for persistent TGIF DMR transition capture (#134)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


tgif = load("asr_tgif_persistent_test", ROOT / "scripts/asr-tgif-user-session.py")
status = load("asr_bridge_status_tgif_test", ROOT / "scripts/asr_bridge_status.py")
NOW = 2_000_000_000
TG = "67498"


def message(dmrid: str, callsign: str, state: str, talkgroup: str = TG) -> dict:
    return {"uuid": dmrid, "callsign": callsign, "state": state,
            "ts1_talkgroup": "4000", "ts2_talkgroup": talkgroup}


with tempfile.TemporaryDirectory(prefix="asr-tgif-persistent-") as temp:
    root = Path(temp)
    tgif.STATE = root / "state"
    tgif.RUNTIME = root / "run"
    tgif.TOKENS = tgif.STATE / "tokens"
    tgif.JOURNALS = tgif.STATE / "journals"
    tgif.SNAPSHOTS = tgif.RUNTIME / "snapshots"
    tgif.CHALLENGES = tgif.RUNTIME / "challenges"
    tgif.ensure_storage()

    tracker = tgif.SessionEventTracker("node", TG, "NODECALL")
    assert tracker.apply(message("322493902", "KE7WIL", "1"), NOW) == "connect"
    tracker.finish_sync()
    assert tracker.events == [], "initial roster generated a false connection"
    assert tracker.apply(message("322493902", "KE7WIL", "1"), NOW) == "duplicate"

    # Four genuine transitions in the same second remain distinct and ordered.
    for state_value, expected in (("0", "disconnect"), ("1", "connect"),
                                  ("0", "disconnect"), ("1", "connect")):
        assert tracker.apply(message("322493902", "KE7WIL", state_value), NOW + 1) == expected
    assert [row["event"] for row in tracker.events[:4]] == ["connect", "disconnect", "connect", "disconnect"]
    assert [row["sequence"] for row in tracker.events[:4]] == [4, 3, 2, 1]
    assert len({row["event_id"] for row in tracker.events}) == 4
    assert all(row["dmrid"] == "322493902" and row["callsign"] == "KE7WIL" for row in tracker.events)

    # Simultaneous stations and duplicate messages are captured exactly once.
    assert tracker.apply(message("310000101", "N1ABC", "1"), NOW + 2) == "connect"
    assert tracker.apply(message("310000202", "N2ABC", "1"), NOW + 2) == "connect"
    before = list(tracker.events)
    assert tracker.apply(message("310000202", "N2ABC", "1"), NOW + 2) == "duplicate"
    assert tracker.events == before
    assert tgif.session_membership(message("9999999", "TXONLY", "1", "91"), TG) is False

    # Listener restart/reconnect silently rebuilds state and replays no events.
    replay = tgif.SessionEventTracker("node", TG, "NODECALL")
    prior_events = list(replay.events)
    for row in list(tracker.sessions.values()):
        replay.apply(row, NOW + 3)
    replay.finish_sync()
    replay.persist(False, "temporary network interruption")
    assert replay.events == prior_events, "restart or network loss fabricated a transition"
    persisted = json.loads(tgif.snapshot_path("node").read_text())
    assert persisted["stale"] and len(persisted["clients"]) == len(tracker.sessions)

    # Authentication renewal is obtained for every connection attempt, not cached globally.
    source = (ROOT / "scripts/asr-tgif-user-session.py").read_text()
    monitor_body = source[source.index("def monitor_user"):source.index("def monitor_all")]
    assert "token = current_api_token(data, talkgroup)" in monitor_body
    assert "token_path(user_id).stat().st_mtime_ns != token_mtime" in monitor_body

    # Journal ingestion preserves exact IDs and replaces roster-derived duplicates.
    activity = status.tgif_session_activity(NOW + 3, tgif.snapshot_path("node"))
    assert activity is not None and len(activity["events"]) == len(prior_events)
    state = status.initial_activity_state()
    state["client_events"] = [{"callsign": "N1ABC", "event": "connect", "epoch": NOW + 2}]
    status.merge_tgif_session_activity(state, activity)
    assert len(state["client_events"]) == len(prior_events)
    assert all(row["source"] == "tgif_event_journal" for row in state["client_events"])
    assert len({row["event_id"] for row in state["client_events"]}) == len(state["client_events"])

    # Non-DMR snapshot fallback also establishes its first roster silently.
    fallback = status.initial_activity_state()
    status.reconcile_client_snapshot(fallback, ["W1AW"], NOW)
    assert fallback["client_events"] == [] and fallback["client_snapshot"] == ["W1AW"]

reapply = (ROOT / "scripts/asr-reapply.sh").read_text()
integrity = (ROOT / "scripts/asr-integrity-check.sh").read_text()
assert "ExecStart=/usr/local/sbin/allscan-reimagined-tgif-user-session monitor-all" in reapply
assert "Restart=always" in reapply
assert "WantedBy=multi-user.target" in reapply
assert "systemctl restart allscan-reimagined-tgif-user-sessions.service" in reapply
assert "systemctl is-active --quiet allscan-reimagined-tgif-user-sessions.service" in integrity
assert "[ ! -e /etc/systemd/system/allscan-reimagined-tgif-user-sessions.timer ]" in integrity

print("persistent TGIF DMR transition monitor self-test: ok")
