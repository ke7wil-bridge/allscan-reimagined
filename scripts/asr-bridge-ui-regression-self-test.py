#!/usr/bin/env python3
"""Deterministic source checks for bridge UI regression invariants."""
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[1]
app = (root / "src/App.tsx").read_text()
errors = []

recent_starts = [m.start() for m in re.finditer(r"card\.recentRows\.map", app)]
if len(recent_starts) < 2:
    errors.append("expected both Recent Activity rendering paths")
for start in recent_starts:
    chunk = app[start:start + 1400]
    if any(token in chunk for token in ("Kick</button>", "globalBanDurationSelect(", "openBridgeBan(")):
        errors.append("Recent Activity contains a management action")

client_starts = [m.start() for m in re.finditer(r"card\.detailRows\.map", app)]
if not client_starts:
    errors.append("Connected Clients rendering path missing")
elif not any("Kick</button>" in app[start:start + 2200] for start in client_starts):
    errors.append("Connected Clients lost capability-gated Kick")

node_cell = app.find('className="allscan-connection-node-cell"')
more = app.find('className="allscan-connection-more"', node_cell)
if node_cell < 0 or more < 0:
    errors.append("Connection Status action is not in the Node cell")
elif "stopPropagation()" not in app[more:more + 700]:
    errors.append("Connection Status action click can propagate to Node cell")

mapping_chunks = [app[m.start():m.start()+450] for m in re.finditer(r"byNode:\s*new Map", app)]
if len(mapping_chunks) < 2:
    errors.append("bridge friendly-name/state maps missing")
for chunk in mapping_chunks[:2]:
    if "!bridge.urfReflector" in chunk:
        errors.append("URF bridge nodes are excluded from friendly-name/state mapping")
    if "!bridge.linkAlias" not in chunk:
        errors.append("bridge node mapping no longer excludes explicit link aliases")

if errors:
    print("FAIL: bridge UI regression checks")
    for error in errors:
        print(f" - {error}")
    sys.exit(1)
print("PASS: bridge UI regression checks")
