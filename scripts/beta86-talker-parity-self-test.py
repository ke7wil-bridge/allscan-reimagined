#!/usr/bin/env python3
"""Regression checks for Netoholics-approved Beta 8.6 M17 talker behavior."""
from pathlib import Path

scope = {}
source = Path(__file__).with_name("asr_bridge_status.py")
exec(compile(source.read_text(), str(source), "exec"), scope)
state = scope["initial_activity_state"]()
open_line = "1795198635.277340 nth-serv-p01 mrefd[981772]: Opening stream on module A from client KE7WIL-02 with id 0x3fec by user KE7WIL-02"
close_line = "1795198639.316378 nth-serv-p01 mrefd[981772]: Closing stream on module A"
scope["apply_m17_journal"](state, open_line, 1795198636)
assert state["role"] == "source", state
assert state["current_user"] == "KE7WIL", state
scope["apply_m17_activity"](state, "M: 2026-11-20 18:17:16.000 USRP voice received as first frame", 1795198636)
assert state["role"] == "source" and state["current_user"] == "KE7WIL"
scope["apply_m17_journal"](state, close_line, 1795198640)
assert state["role"] == "idle" and not state["current_user"]
assert state["last_source_user"] == "KE7WIL"
assert any(row.get("callsign") == "KE7WIL" and row.get("direction") == "incoming" for row in state["tx_events"])
module_state = scope["initial_activity_state"]()
module_line = "1795198635.277340 nth-serv-p01 mrefd[981772]: Opening stream on module A from client KN4EWT N with id 0x7fb0 by user KN4EWT N"
scope["apply_m17_journal"](module_state, module_line, 1795198636)
assert module_state["current_user"] == "KN4EWT", module_state
state2 = scope["initial_activity_state"]()
scope["apply_m17_journal"](state2, "1795198635.277340 nth-serv-p01 mrefd[981772]: Opening stream on module A from client NOTVALID with id 0x3fec by user NOTVALID", 1795198636)
assert state2["role"] == "idle"
print("PASS: M17 callsign normalization, source priority, close/idle, history, invalid identity")
