# Johnny2 ASR bridge-status handoff

Date: 2026-11-06
Node: Johnny2, AllStar 623123
ASR: 1.0.0 Beta 8.3.5

## User-visible failure

- Idle YSF, P25, and NXDN cards displayed `Relay` continuously.
- DMR 1001 and M17 1006 could disappear from Connection Status.
- The local pseudo-hub row stayed red/PTT-keyed even with every bridge and public link disconnected.

## Confirmed cause and evidence

The false Relay display was an ASR frontend bug. `src/App.tsx` used the node-wide local `RPT_TXKEYED` state as a fallback for an individual bridge card:

```ts
return withStatus(card, localIsTransmitting ? 'Relay' : 'Idle')
```

On Johnny2, node 623123 uses `rxchannel=Local/pseudo` and reports `RPT_TXKEYED=1` continuously. The bit remained set after disconnecting all five bridge nodes, both public links, and Broadcastify. At the same time:

- every bridge's own input/keyed state was false;
- P25 `inboundTalkerActive` was false;
- NXDN `inboundTalkerActive` was false;
- no bridge logs showed active traffic.

Therefore the hub TX bit is not valid bridge-specific Relay evidence.

There are two related recovery/status defects:

1. `/run/allscan-reimagined-standard-bridge-status/bridge-live.json` remained `{"bridges":{}}` with configured DMR/YSF services active, so the UI lacked authoritative idle samples.
2. M17 control reported `allstarLinked: true` while node 1006 was absent from `rpt lstats 623123`. Restarting `allscan-reimagined-m17-bridge@m17_johnny2.service` restored it. Fixed bridge recovery automatically restored 1001/1003/1004/1005 but did not reconcile this stale M17 state.

## Applied production fix

`src/App.tsx` now leaves an otherwise-idle bridge `Idle` when only the node-wide pseudo-hub TX bit is set. Bridge-specific producers and transport rows still drive `Source/TX` and `Relay`.

Build verification:

```text
npm run build: passed
node scripts/asr-bridge-dashboard-self-test.mjs: passed
```

Deployed bundle on Johnny2: `assets/index-DbsZafOj.js`
Production backup: `/var/backups/allscan-reimagined/asr-ui-relay-20261106T134253Z`

After recovery, `rpt lstats 623123` showed 1001, 1003, 1004, 1005, 1006, BCSTFY, 53430, and 578990 established.

## Master fixes recommended

1. Keep the frontend change: never infer an individual bridge's Relay state solely from the local pseudo-hub `RPT_TXKEYED` bit.
2. Make the standard bridge-status producer always publish configured DMR and YSF entries, including explicit idle state, and recognize the installed direct-service unit names.
3. For P25/NXDN, use their bridge-specific `inboundTalkerActive`/gateway evidence; false must render Idle.
4. Reconcile M17 `allstarLinked` against live `rpt lstats`, and include M17 in fixed-link recovery.
5. Add regression coverage: pseudo hub TX keyed + all bridge evidence idle must render every bridge Idle; stale M17 linked state must be repaired.

Do not change hub duplex or audio routing merely to clear the red local row; testing showed that row is independent of actual bridge traffic and was not caused by BCSTFY.
