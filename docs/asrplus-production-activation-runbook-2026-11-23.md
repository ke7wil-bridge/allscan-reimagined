# ASR Plus authenticated-command production activation runbook

Date: 2026-11-23 (America/Phoenix)

Candidate: `b1f9e96` plus the reviewed runbook/drop-in follow-up commit

State: **PREPARED — PRODUCTION MUTATION NOT YET APPROVED**

## Scope and identities

| Node | Host route | Protocol identity | Runtime mode for this window |
| --- | --- | --- | --- |
| MasterHub 674982 | `root@44.30.157.21` / Mac alias `ky1` | central | Phoenix and KY2 `optional` |
| Phoenix 641894 | Mac alias `az` -> `node64189.ddns.net` | `phoenix` | `optional` |
| KY2 674980 | `root@44.30.157.22` / Mac alias `ky2` | `georgia` | `optional` |

The legacy `georgia` string is an internal KY2 protocol identity. The retired
`45.76.255.39` host is outside this deployment and must not be contacted.
Production hub traffic remains on the existing non-VPN paths. The Mac is only
an operator transport for Phoenix file installation; no runtime VPN dependency
is introduced.

## Read-only preflight evidence

- All nine offline security/regression suites pass at `b1f9e96`.
- Asterisk and existing ASR Plus watcher/timer/path services are active on all
  three nodes. No Asterisk restart is planned.
- MasterHub queues for Phoenix and KY2 contain no `.json` or `.inflight` files.
- Existing MasterHub, Phoenix, and KY2 security entrypoints remain unchanged;
  the integrity module, signing configs, keys, and ledgers are not live.
- Phoenix's existing `asl-ban.conf` and `event-rate.conf` drop-ins, and KY2's
  distinct control/watcher units and keyless watcher config, were inspected and
  must be preserved.
- All three remote staging directories contain older entrypoints. The Phoenix
  and KY2 agent hash is `f4874d...`, while the reviewed candidate is
  `7e4c41...`; the older version lacks the tested post-command status-transition
  fix. MasterHub's staged control (`098144...`) and endpoint (`324630...`) also
  differ from the reviewed `ba857f...` and `4ef829...` files. Every entrypoint
  must be restaged and verified against `SHA256SUMS` before activation. The
  shared integrity module already matches on all three hosts.

## Action-time backup and staging

After explicit approval, create a new root-only timestamped backup directory on
each host. Copy, with metadata, every live entrypoint, config, relevant unit and
drop-in, queue/result/audit directory, and any existing ledger. Do not delete,
move, truncate, or normalize live state. Record hashes and service/link status
in the backup directory without reading private keys.

Restage the reviewed candidate and verify it against the repository manifest:

- MasterHub: control helper, endpoint, integrity module, central optional-mode
  config.
- Phoenix: unified agent, integrity module, merged config, and additive ledger
  write-path drop-in. Preserve both existing drop-ins and the base units.
- KY2: unified agent, integrity module, merged control config, and unchanged
  keyless watcher config. Preserve both distinct units.

Use `/usr/bin/python3` on each host to compile/import the exact staged files.
Do not replace any entrypoint unless its sibling module and config have already
passed on-host validation.

## Key provisioning

Generate two independent 32-byte random files: Phoenix and KY2. Generate them
on an authorized production endpoint with `umask 077`; never emit bytes to a
terminal, argument, log, repository, or persistent operator-machine file.
Install matching copies on MasterHub and the corresponding remote hub beneath
`/etc/asrplus/command-keys/`, owned `root:root`, mode `0600`. The watcher-only
KY2 config remains keyless. Validate only owner, mode, size, and the subsequent
signed exchange; do not report key content.

## Coordinated optional-mode installation

1. Repeat queue-empty, Asterisk-link, telemetry-freshness, service-health, disk,
   clock, and rollback-file checks immediately before the switch.
2. Install the integrity module before each matching entrypoint. Atomically
   install Phoenix and KY2 agents/configs first. Create the ledger directories
   as `0700 root:root`; do not create or clear ledger files manually.
3. Reload systemd only after validating the merged unit definitions. Restart
   the Phoenix and KY2 ASR Plus talker watchers so they load the reviewed
   agent. Let the control timers invoke the new oneshot agent; do not restart
   Asterisk or reset links.
4. Confirm legacy optional-mode telemetry, watcher cadence, link state, and
   command polling on both remotes before modifying MasterHub.
5. Atomically install the MasterHub module, optional-mode config, endpoint, and
   control helper. These are invocation-based components and require no
   Asterisk or general service restart.
6. Submit one signed read-only `ban-list` request per remote. Record its command
   ID; verify the signed acknowledgment, central result identity, ledger entry,
   audit record, empty queue, telemetry freshness, and unchanged radio links.
7. Exercise replay, expiry, invalid-signature, and cross-hub rejection only
   with reviewed non-executing fixtures or exact duplicate acknowledgments.
   Never inject a connect/disconnect/ban mutation as a security test.
8. Re-run all nine local suites and the per-host import/config/permission checks.

## Stop and rollback triggers

Stop immediately on an unexpected link transition, stale or missing telemetry,
watcher/timer failure, import/config error, command for the wrong hub, invalid
acknowledgment, non-empty unexplained queue, changed directory access, or any
Asterisk/bridge degradation. Preserve queues, results, logs, keys, and ledgers.

Rollback in reverse order: stop issuing controls; restore the backed-up central
helper/endpoint/config/module as one set; restore each remote executable,
config, unit/drop-ins, and module as one set; reload/restart only affected ASR
Plus units; verify telemetry and links. Never delete a ledger or queue to make
rollback appear clean. Keys may remain root-only while optional legacy code is
restored, then be handled under a separate approved revocation plan.

## Separate required-mode gate

This window ends with both hubs in `optional` mode. A later request must show
successful signed exchanges on Phoenix and KY2, empty queues/inflight files,
healthy telemetry/watchers, preserved Kick/Ban behavior, and an approved
rollback point. Only then may Josh separately authorize changing all three
matching configurations from `optional` to `required`.

## Approval boundary

One immediate approval may authorize only: fresh backups; restaging and hash
validation; two-key generation/distribution; optional-mode agent-first install;
ASR Plus watcher restarts and systemd reloads; MasterHub helper/endpoint switch;
and two signed read-only `ban-list` qualifications. It does **not** authorize an
Asterisk restart, radio-link change, firewall/bridge/SSH trust change, queue or
ledger deletion, legacy credential revocation, required-mode activation, merge,
or push.
