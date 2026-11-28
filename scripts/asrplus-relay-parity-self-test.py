#!/usr/bin/env python3
"""Offline regression fixtures for legacy collector parity; no network access."""
import runpy
from pathlib import Path
m = runpy.run_path(str(Path(__file__).with_name("asrplus-status.py")))
assert m["iax_connection"]("N0TEST", "AllStar node N0TEST")
assert not m["iax_connection"]("900005", "Test station")
assert not m["iax_connection"]("1002", "YSF bridge")
assert m["talker_hub_label"]({"id": "remote", "name": "Example Hub", "location": "Example City"}) == "Example"
def active(node, start, current=True, end=0, source="AllStar"):
    return {"node": node, "callsign": "N0TEST", "current": current,
            "startedEpoch": start, "eventEpoch": end or start,
            "duration": "00:05", "originatedLocally": False, "source": source}
def hub(key, node, rows):
    return {"id": key, "node": node, "name": key, "location": key,
            "stale": False, "talkers": rows, "connections": []}
hubs = [
    hub("primary", "900001", [active("900002", 200), active("900009", 90, False, 100)]),
    hub("source", "900002", [active("900005", 200)]),
    hub("downstream-a", "900003", [active("900001", 90)]),
    hub("downstream-b", "900004", [active("900001", 90)]),
]
cards = m["aggregate_talkers"](hubs, 300)
assert cards[0]["node"] == "900005"
assert [h["txState"] for h in hubs] == ["relay", "origin", "relay", "relay"]
assert all(h["relayOrigin"]["station"] == "900005" for h in (hubs[0], hubs[2], hubs[3]))
hubs = [hub("primary", "900001", [active("1002", 400, source="YSF")]),
        hub("downstream", "900003", [active("900001", 350)])]
cards = m["aggregate_talkers"](hubs, 450)
assert cards[0]["node"] == "1002" and cards[0]["source"] == "YSF"
assert [h["txState"] for h in hubs] == ["origin", "relay"]
print("Synthetic relay parity PASS")
