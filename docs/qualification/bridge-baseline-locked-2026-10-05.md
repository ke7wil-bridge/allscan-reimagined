# Beta 8 Bridge Baseline Lock — 2026-10-05

This document locks the bridge behavior qualified before M17 and NXDN testing. Do not alter these invariants to accommodate either unqualified mode; make those modes conform to this model and repeat qualification if a locked path must change.

## Repository checkpoint

- Repository: `/home/josh/repos/allscan-reimagined`
- Branch and base commit: `main` at `8d4a29d646cd8fae5ea4296d20aa5953e751a42a`
- Checkpoint type: documented dirty-worktree lock; no Git commit was created.
- Commit safety finding: a focused code commit is unsafe. The qualified frontend and API changes share large modified files with unrelated Beta 8 work, including D-Star removal, administration/setup changes, and unqualified M17/NXDN work. The tree contains 68 tracked modified/deleted files plus untracked files. Staging whole files would swallow unrelated work, while reconstructing selected hunks would risk changing the live-qualified state.

## Locked behavior

### Talker Cards

- Node 1999 is Unified Net Bridge transport provenance, never caller identity.
- The active digital mode owns node 1999 exclusively and supplies Talker Card provenance.
- P25 displays `P25 · Net Bridge` and `Talkgroup 707`.
- `Identifying…` is permitted only transiently while the active transmission lacks authoritative identity.
- Identity quality is monotonic per mode and transmission: unknown → identifying → real identity. A resolved identity cannot regress to identifying or generic Unified/AllStar metadata.
- Completed talkers retain the identity learned for that transmission through unkey, history, and browser refresh. Identity must not leak between transmissions or modes.
- Talker timing and identity are independent of connection destination and live direction.

### P25 Unified Net Bridge — live qualified

- Connect/disconnect/reconnect and repeated lifecycle pass with Talkgroup 707.
- Destination remains 707 during incoming and outbound traffic. Local identity 64189 cannot replace it.
- Incoming direction is Idle → Source/TX → Idle, without false trailing Relay.
- Outbound AllStar direction is Idle → Relay → Idle, while destination remains 707.
- Actual P25 source identity is used; generic node-1999 identity is rejected.
- Successful connect messaging is concise: `P25 Net Bridge selected Talkgroup 707.` Internal transport/reachability qualification text is not shown as normal success.

The controller's persisted fast activity reconstructed four qualified P25 transmissions from actual source ID 3224939 as `KE7WIL`, with durations 4, 2, 3, and 2 seconds. Their start/EOT epochs align with the four persisted AllStar history events, so refresh recovery is locked without manufacturing identity from local configuration.

### Node 1999 and connection model

- Node 1999 represents the configured Unified Net Bridge transport.
- Connection Status retains a presentation-only node-1999 row when the transport is configured but not actually linked. This row is excluded from connection totals, activity arbitration, and node-control selection.
- The presentation row must never fabricate an AllStar link.
- Transport, active mode, selected destination, remote reachability, live direction, caller identity, and traffic metadata are independent fields.
- A selected destination is not proof of remote reachability. Where independent verification is unavailable, keep reachability unverified without turning normal success into a warning paragraph.

### DMR and YSF Unified Net Bridge

- DMR remains locked at its previously qualified connection lifecycle, exclusive node-1999 ownership, source/relay direction, PTT/unkey, destination identity, Connected Clients, Talker Card integration, and absence of stale TX/loops.
- YSF direct MMDVM source/TX/EOT evidence remains authoritative: incoming Idle → Source/TX → Idle and outbound Idle → Relay → Idle.
- Generic node-1999 state cannot override fresh YSF direction. YSF catalog and custom-reflector behavior remain locked.

### Zello and URFWIL/standard timing

- Zello's measured false USRP unkey requires its mode-specific 150 ms confirmation. Do not remove it or generalize it to other bridges.
- Fresh direct protocol/key/unkey/source edges are authoritative for URFWIL and standard bridges.
- Slow collectors provide metadata, history, and fallback only while direct state is fresh.
- Do not reintroduce the generic `Reflector.cpp` latch or treat a generic USRP false final frame as universal protocol authority.

### Live-state architecture

1. Fresh direct protocol/source/key/unkey evidence owns live direction.
2. Incoming source EOT returns directly to Idle; trailing transport state cannot manufacture Relay.
3. Genuine outbound evidence produces Relay immediately.
4. Stale responses cannot overwrite newer state; blind frontend debounce is not an accepted race fix.
5. Talker metadata cannot determine direction.
6. Traffic identity cannot overwrite selected destination.
7. Transport identity cannot masquerade as caller identity.

## Qualified implementation surfaces

The locked behavior currently spans these working-tree surfaces. Function/feature anchors identify the qualified portions of mixed files.

- `src/App.tsx`: `enrichBridgeTalker`, mode/event identity cache, fast-feed arbitration, connection-row presentation, P25 success message, and bridge control UI.
- `src/lib/allscanLive.ts`: Talker history construction, selected-mode node-1999 identity, configured transport presentation row, destination labeling, and fast activity parsing.
- `asr-api.php` and `server/asr-api.php`: runtime bridge normalization, selected-mode node ownership, fast P25/NXDN activity, connection-state dimensions, and bridge status/control payloads.
- `scripts/asr-p25-bridge-control.py`: destination control, direct MMDVM direction/EOT, source-ID resolution, persisted recent talkers, and stale-event rejection.
- `scripts/asr-ysf-bridge-control.py`: authoritative direct YSF timing.
- `scripts/asr-managed-dmr-net-control.py` and `scripts/asr-net-bridge-mode-control.py`: qualified DMR lifecycle and exclusive Unified Net Bridge mode ownership.
- `runtime/urf/patches/AsrPttState.h`, `DMRMMDVMProtocol.cpp`, `YSFProtocol.cpp`, `P25Protocol.cpp`, `NXDNProtocol.cpp`, `M17Protocol.cpp`, and `USRPProtocol.cpp`: direct protocol lifecycle producer architecture. P25/DMR/YSF/standard behavior is locked; this document does not qualify M17 or NXDN.
- `scripts/asr-bridge-dashboard-self-test.mjs` and `scripts/asr-bridge-ui-regression-self-test.py`: regression guards for the accepted dashboard model.
- Existing evidence remains in `docs/qualification/bridge-live-timing-2026-10-04.md` and `docs/qualification/talker-cards-bridge-identity-2026-10-04.md`.

## Source and deployed hashes

All hashes are SHA256.

| Artifact | Hash | Verification |
|---|---|---|
| `src/App.tsx` | `1850e61cac8bbe5a41c689b62a6b89ce995c81489f25dc04f6c4e536cae0b9ba` | checkpoint source |
| `src/lib/allscanLive.ts` | `bf25fdb7b39ef0d970f77e7751c69fb3cdef9306f341a64113abb98e7d550e3d` | checkpoint source |
| `dist/assets/index-DS2z00jT.js` | `aff1e147a6bb4c25df5889e4165951e340f70262aa5eeba9152f150aafb75b91` | exactly matches served bundle |
| `dist/assets/index-W_1O0otb.css` | `73bcb58bff5e372720abdbd1021c88d8cb0e181bb524d2d934493661dd146b8a` | exactly matches served bundle |
| `dist/index.html` | `ae057b1cc05a6c48d23999ff0eb0633d994d764494f65920bc1c79d64eae8158` | exactly matches served index |
| `asr-api.php` and `server/asr-api.php` | `75f5e834267b963811a5449f56cc80a94e1e4053fd819a83121c20a867ecac64` | copies match each other and live web volume |
| `scripts/asr-p25-bridge-control.py` | `9202c0c287dd21d1dc9cffa41395f2da1d6206d954b7481cbeb6b2e6fab81d48` | exactly matches `/usr/local/sbin/allscan-reimagined-p25-bridge-control` |
| `scripts/asr-ysf-bridge-control.py` | `46ae1a843f58468cc4c87b3879762e9704e92a5ddb3ba4d43524bdfe05bca14c` | exactly matches `/usr/local/sbin/allscan-reimagined-ysf-bridge-control` |
| `scripts/asr-managed-dmr-net-control.py` | `faafcdcbaebf98233b05ce3416391039aa0470a4cb1f049a788a65af66bc4aa0` | exactly matches `/usr/local/sbin/allscan-reimagined-managed-dmr-net-control` |
| Zello `usrp.py` qualified source/deployment | `4fae84a8458d89b12a46b43c5bb75d6f20e685c268bb2496df083cff5508b3fc` | preserved 2026-10-04 qualification evidence |
| running `urf-wil-reflector` image | `sha256:38a3c17a4e56265e2c2d95f25f97563f6ca74efba8db213c9001d3920793fa28` | live container image ID |
| running `/usr/local/bin/urfd` | `781e94bc84816c765d95c63e8fecd282d3dd44113e4b7e6b1ebc957e059c6906` | live binary fingerprint |

## Live state at lock

- Configured active Unified Net Bridge mode: P25.
- P25 destination: disconnected/cleared; controller reports `digitalSelected=false`, `allstarLinked=false`, and `connectionState=disconnected`.
- P25 traffic state: Idle, with persisted qualified recent-talker history intact.
- Asterisk has no actual node-1999 link. The configured Unified Net Bridge row remains represented by the presentation model without changing counts or fabricating connectivity.
- P25 and YSF live watcher services are active. The DMR mode-specific watcher is inactive while P25 owns the Unified Net Bridge; its installed controller matches source.
- No service was restarted and no RF transmission was made for this checkpoint.

## Recovery

- P25 controller pre-deployment backup: `/usr/local/sbin/allscan-reimagined-p25-bridge-control.bak-20261005-145649`
- API pre-deployment backup: `/var/lib/docker/volumes/allscan-reimagined_layered-web/_data/asr/asr-api.php.bak-20261005-145649`
- Frontend index pre-deployment backup: `/var/lib/docker/volumes/allscan-reimagined_layered-web/_data/asr/index.html.bak-20261005-145649`
- Hashed source files plus the served artifact hashes above identify the exact accepted working-tree/deployment state. Restore only a specifically required artifact; do not reset, clean, stash, rebase, or overwrite the dirty tree.

## Next phase

The system is ready for M17 qualification followed by NXDN qualification. Neither mode is qualified by this checkpoint.
