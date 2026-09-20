# Beta 8 Bridge Client Management Handoff

Status: implemented and deployed for live acceptance; uncommitted by design.

## Safety boundary

- Worktree: `/home/josh/worktrees/asr-client-disconnect`
- ASR source: `allscan-reimagined`
- Branch: `feature/bridge-client-disconnect`
- Base HEAD: `7a5e302`
- Do not commit, push, merge, reset, clean, stash, or discard without Josh's approval.
- Do not disturb bridge nodes 1002 (D-STAR) or 1003 (Zello).

## Implemented behavior

- Talking, Last Talker, Connected Clients, and Recent Activity have independent semantics.
- Session-scoped Kick exists for DMR, YSF, P25, NXDN, and M17.
- Global Ban supports exact callsigns, prefixes, 15m, 1h, 1w, 30d, and Permanent.
- Expiration removes synchronized backend enforcement without changing unrelated bans.
- Hidden DMR numeric enforcement is removed during Unban.
- Service/probe identities RFCKRD0, YSF-LIVE, and KF0WSS are protected from exact or prefix Ban.
- KF0WSS-2 remains a distinct, bannable identity.
- Administrator Kick/Ban/Unban and automatic expiration are persisted in `asr-admin-audit.jsonl`.
- Corrupt or structurally invalid timed-ban state fails closed.
- Timed-ban replacement is flushed and fsynced before becoming authoritative.

## Manage Clients

- Opaque themed modal, X button, Escape/backdrop close, internal scrolling, and safe-area padding.
- Compact inline Ban selector retains its hidden placeholder.
- Timed bans are sorted by earliest expiration.
- Permanent bans are grouped separately.
- Timed bans show both countdown and exact local expiration.
- Administration History shows action, target, duration/protocol, actor, and timestamp.
- Responsive layout audit passed with no horizontal overflow for desktop, phone portrait,
  phone landscape, tablet portrait, and tablet landscape.
- Bright Side, Dark Side, Matrix, and ST:ASL theme layouts passed.

## Runtime state verified

- `asr-timed-bans.json`: empty after the completed live expiration test.
- `urfd.blacklist`: no active rules.
- DMR `banned_ids`, `banned_clients`, and `kicked_ids`: empty.
- Live protected-identity Ban attempt was rejected without changing backend state.
- Layered ASR container rebuilt and restarted; URFD was not rebuilt because its source did not change.
- DMR, URFD, transcoder, and dashboard remained available.

## Validation

```sh
python3 scripts/asr-bridge-admin-controls-self-test.py
python3 scripts/asr-ysf-client-management-self-test.py
python3 scripts/asr-dmr-roster-stability-self-test.py
python3 -m py_compile scripts/asr-urf-admin.py \
  scripts/asr-bridge-admin-controls-self-test.py \
  scripts/asr-ysf-client-management-self-test.py
npm run build
git diff --check
```

## Feature-only rollback

The reverse patch check below passed on the current worktree. Perform rollback only after
saving a recoverable copy and confirming that the listed files contain no later mixed work.

```sh
cd /home/josh/worktrees/asr-client-disconnect
cp -a allscan-reimagined/scripts/asr-ysf-client-management-self-test.py \
  /tmp/asr-ysf-client-management-self-test.py.rollback-copy
cp -a allscan-reimagined/docs/beta8-bridge-client-management-handoff.md \
  /tmp/beta8-bridge-client-management-handoff.md.rollback-copy

git diff --binary HEAD -- \
  allscan-reimagined/asr-api.php allscan-reimagined/server/asr-api.php \
  allscan-reimagined/scripts/asr-urf-admin.py \
  allscan-reimagined/scripts/asr-bridge-admin-controls-self-test.py \
  allscan-reimagined/src/App.tsx allscan-reimagined/src/index.css \
  allscan-reimagined/src/lib/allscanLive.ts allscan-reimagined/docker-compose.yml \
  allscan-reimagined/docker/layered-entrypoint.sh \
  allscan-reimagined/docker/layered-reapply.sh \
  > /tmp/asr-client-management-rollback.patch

git apply --reverse --check /tmp/asr-client-management-rollback.patch
git apply --reverse /tmp/asr-client-management-rollback.patch
mv allscan-reimagined/scripts/asr-ysf-client-management-self-test.py \
  /tmp/asr-ysf-client-management-self-test.py.rolled-back
mv allscan-reimagined/docs/beta8-bridge-client-management-handoff.md \
  /tmp/beta8-bridge-client-management-handoff.md.rolled-back
```

Do not delete `urfd.blacklist`, `asr-timed-bans.json`, `asr-admin-audit.jsonl`,
or DMR administrative state during source rollback. Review and preserve active policy
state separately. Rebuild only the layered ASR service after the source rollback.
Do not use `git reset`, `git clean`, `git stash`, or checkout over the worktree.
