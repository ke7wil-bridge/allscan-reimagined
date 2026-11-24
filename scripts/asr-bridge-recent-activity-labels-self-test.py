#!/usr/bin/env python3
"""Bridge card Recent Activity shows CONN, DISC and TX (#134).

Connects, disconnects and transmissions on a bridge card's Recent Activity
are labelled CONN, DISC and TX. Connection events come from the status
reader's client roster changes, never from transmissions.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
live = (ROOT / "src/lib/allscanLive.ts").read_text(encoding="utf-8")
status = (ROOT / "scripts/asr_bridge_status.py").read_text(encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


check("event === 'connect' ? 'CONN' : event === 'disconnect' ? 'DISC' : ''" in live,
      "Recent Activity does not label connects/disconnects CONN/DISC")
check("[entry?.tx_events, 'TX']" in live and ": 'TX'" in live,
      "Recent Activity does not merge TX and connection events")
check("'CON'" not in live and "'DIS'" not in live, "abbreviated CON/DIS labels are back")
check("normalizedMode === 'zello' ? undefined : entry?.client_events" in live,
      "Zello Recent Activity is not TX-only")
check("sourceProtocol !== 'unknown' && sourceProtocol !== normalizedMode" in live,
      "cross-protocol transmission events are not rejected")
check("def reconcile_client_snapshot" in status and '("disconnect", old - new), ("connect", new - old)' in status,
      "status reader no longer records connects/disconnects from roster changes")
for api_path in (ROOT / "asr-api.php", ROOT / "server/asr-api.php"):
    api = api_path.read_text(encoding="utf-8")
    check("'direction'=>'incoming','source_protocol'=>'ZELLO'" in api,
          f"{api_path.name} does not identify authentic inbound Zello TX events")
print("Bridge Recent Activity CONN/DISC/TX self-test: ok")
