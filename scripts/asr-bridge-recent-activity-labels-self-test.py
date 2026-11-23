#!/usr/bin/env python3
"""Bridge card Recent Activity shows CON, DIS and TX (#134).

Connects, disconnects and transmissions on a bridge card's Recent Activity
are labelled CON, DIS and TX. Connection events come from the status
reader's client roster changes, never from transmissions.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
live = (ROOT / "src/lib/allscanLive.ts").read_text(encoding="utf-8")
status = (ROOT / "scripts/asr_bridge_status.py").read_text(encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


check("event === 'connect' ? 'CON' : event === 'disconnect' ? 'DIS' : ''" in live,
      "Recent Activity does not label connects/disconnects CON/DIS")
check("[entry?.tx_events, 'TX'], [entry?.client_events, 'CLIENT']" in live and ": 'TX'" in live,
      "Recent Activity does not merge TX and connection events")
check("'CONN'" not in live and "'DISC'" not in live, "old CONN/DISC labels are back")
check("def reconcile_client_snapshot" in status and '("disconnect", old - new), ("connect", new - old)' in status,
      "status reader no longer records connects/disconnects from roster changes")
print("Bridge Recent Activity CON/DIS/TX self-test: ok")
