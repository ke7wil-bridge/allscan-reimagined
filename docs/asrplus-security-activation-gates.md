# ASR Plus required-mode activation and rollback gates

Status: prepared locally; production activation is not approved.

## Current baseline

- Authenticated command components are installed and source-parity verified on
  Kentucky 674982, KY2 674980, and Phoenix 641894.
- Phoenix and KY2 are healthy in `optional`; Texas is not installed.
- Current remote queues were empty during the read-only inspection. Repeat this
  check at the action window.
- Signing keys are separate, root-owned `0600`, and were never displayed.
- KY2's control service requires the staged sandbox drop-in before activation.
- Kentucky's token package is a separate deployment and is not a prerequisite
  for signing activation.

## Preflight gate

- [ ] Josh approves the exact hosts, window, service effects and rollback.
- [ ] Capture root-only backups of each config and unit/drop-in with owner,
  mode and SHA-256; record a rollback directory on each node.
- [ ] Confirm NTP synchronized, all three nodes healthy, dashboards fresh, and
  Asterisk links unchanged.
- [ ] Confirm no queued or inflight commands and preserve all ledgers/audits.
- [ ] Confirm per-hub key paths, IDs and hashes match their central counterpart
  without printing key contents.
- [ ] Run all local security suites at the approved commit.

## Stage 1 - KY2 sandbox remediation

Install only the reviewed `security-hardening.conf` drop-in for
`asrplus-ky-control.service`, run `systemd-analyze verify`, reload systemd, and
allow the existing timer to invoke it. Do not restart Asterisk. Verify KY2
telemetry, ledger persistence, strict SSH, queue polling, watcher freshness,
and unchanged links. On failure, restore/remove the drop-in, reload systemd,
and preserve ledger/audit state.

## Stage 2 - remote agents require signatures

With queues empty, change Phoenix and KY2 agent configs from `optional` to
`required`, one hub at a time. Do not change key IDs, key paths, SSH settings,
node numbers, timers, or routing. Let each existing timer run and verify fresh
telemetry plus a signed read-only `ban-list` lifecycle before continuing.

Order: Phoenix first, then KY2. A failure affects only that hub; restore its
backed-up config to `optional`, allow the timer to run, and stop the cutover.
Kentucky remains compatible because it already signs all newly created work.

## Stage 3 - central rejection of unsigned work

After both remote agents are verified required and queues remain empty, change
only Phoenix and KY2 modes in Kentucky
`/etc/asrplus/command-integrity.json` to `required`. Verify a signed read-only
command and acknowledgment independently for Phoenix and KY2, then confirm
dashboard freshness, empty queues, audit events, ledgers, and radio-link health.

## Rollback order

1. Stop issuing new controls; preserve queue, inflight, result, archive, ledger
   and audit files.
2. Return Kentucky's affected hub mode to `optional` if central required mode
   was reached.
3. Return the affected remote agent to its backed-up optional config.
4. If the KY2 sandbox caused the regression, restore its prior unit state and
   reload systemd. Do not clear the execute-once ledger.
5. Verify telemetry, queue depth, signed read-only lifecycle, audit continuity,
   and unchanged Asterisk links before resuming control.

Required-mode activation needs a fresh, explicit production approval naming
Kentucky, Phoenix and KY2. A merge, issue comment, or approval of the token or
Texas work does not authorize it.
