#!/usr/bin/env python3
"""Deterministic source checks for bridge UI regression invariants."""
from pathlib import Path
import re, sys

root = Path(__file__).resolve().parents[1]
app = (root / "src/App.tsx").read_text()
live = (root / "src/lib/allscanLive.ts").read_text()
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

talker_enrichment = app[app.find("const enrichBridgeTalker"):app.find("const talkerCards")]
if "info: 'Identifying…'" not in talker_enrichment or "node: 'Net Bridge'" not in talker_enrichment:
    errors.append("node 1999 can leak its generic AllStar identity before mode enrichment")
if "if (!isCurrentTalker) return null" not in talker_enrichment:
    errors.append("unmatched Net Bridge history can remain stuck on Identifying while idle")
if "fastNextModeActivityRef.current" not in talker_enrichment:
    errors.append("P25/NXDN fast identity is not used by Talker Card enrichment")
if "fast?.recentTalkers" not in talker_enrichment or "bridgeTalkerIdentityRef.current[cacheKey]" not in talker_enrichment:
    errors.append("completed P25/NXDN talkers no longer retain event-scoped fast identity")
if "liveMatchesTransmission" not in talker_enrichment or "Math.abs(fast.eventEpoch - talker.startedEpoch)" not in talker_enrichment:
    errors.append("live P25/NXDN identity can leak across transmissions")
if "isCurrentTalker && sourceCard && sourceCard.cardType !== 'p25_net' && sourceCard.cardType !== 'nxdn_net'" not in talker_enrichment:
    errors.append("generic node metadata can overwrite authoritative P25/NXDN identity")
if "persistedUrfIdentity" not in talker_enrichment or "talker.source" not in talker_enrichment or "node: 'URFWIL'" not in talker_enrichment:
    errors.append("persisted URFWIL caller identity/provenance is not rendered after refresh")
if "String(entry.description || '').trim() || parts.description" not in live or "String(entry.location || '').trim() || parts.location" not in live:
    errors.append("persisted Talker Card description/location metadata is discarded")
if "if (!isCurrentTalker) return null" not in talker_enrichment or "info: 'Identifying…'" not in talker_enrichment:
    errors.append("generic URFWIL transport identity can masquerade as the caller")
if "modeCard?.cardType === 'm17_net'" not in talker_enrichment or "modeCard.recentTalkers" not in talker_enrichment:
    errors.append("completed M17 talkers do not retain event-scoped identity")
if "selected Talkgroup ${canonicalId}." not in app:
    errors.append("P25 success message is not concise or lacks Talkgroup terminology")
if "TOUCH_MODULE_DRAG_DELAY_MS = 350" not in app or "TOUCH_MODULE_DRAG_CANCEL_DISTANCE_PX = 8" not in app:
    errors.append("responsive module dragging lacks a touch hold delay and scroll-cancel threshold")
if "event.pointerType === 'touch' && window.innerWidth < 1200" not in app or "clearPendingDashboardDrag()" not in app:
    errors.append("touch module drag delay is not limited to the responsive range or cannot be cancelled")
if "confirmed its AllStar transport" in app:
    errors.append("P25 success message exposes internal qualification language")
if ".filter((row) => !row.configuredTransport)" not in app:
    errors.append("configured node 1999 presentation row can leak into live activity arbitration")

if errors:
    print("FAIL: bridge UI regression checks")
    for error in errors:
        print(f" - {error}")
    sys.exit(1)
print("PASS: bridge UI regression checks")
