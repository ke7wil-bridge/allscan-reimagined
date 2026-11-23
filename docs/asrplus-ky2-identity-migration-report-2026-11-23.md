# ASR Plus KY2 identity migration report

Date: 2026-11-23 (America/Phoenix)

Status: **COMPLETE — OPTIONAL MODE**

Production candidate was prepared from commits `7e54799`, `107aecf`, and
`7bc63ed`. No commit was pushed or merged.

## Installed identity

| Node | Active internal identity | Node number | Mode |
| --- | --- | --- | --- |
| Kentucky MasterHub | `masterhub` (`kentucky` dashboard scope) | 674982 | Central |
| Kentucky KY2 | `ky2` | 674980 | `optional` |
| Phoenix | `phoenix` | 641894 | `optional`, unchanged |

MasterHub control/endpoint/signing configuration, KY2 control configuration,
dashboard status path, active forced-command mappings, queue/result/archive
paths and status paths now use `ky2`. The existing signing-key bytes were
copied locally to `/etc/asrplus/command-keys/ky2.key` on both nodes, owned
`root:root`, mode `0600`; their SHA-256 hashes match. No key was regenerated or
printed.

The legacy key filenames and the held pre-cutover control executable were moved
into the timestamped rollback backup, not deleted. Historical audit and ledger
records retain `georgia` where originally recorded. Current audit records use
`ky2`.

## Backups and rollback

Fresh verified backups exist on MasterHub and KY2 at:

`/root/asrplus-identity-backups/20261123T221227Z/`

The backup manifests pass. State migration is reversible with
`scripts/asrplus-ky2-identity-migration.py state --reverse --apply`; configuration,
entrypoint, forced-command, dashboard and key-path rollback copies are retained.
Queues and ledgers were never deleted or rewritten.

## Production qualification

- Signed read-only KY2 `ban-list` command
  `6c36081d1d6446f99c8b6385e41eb7e7` completed successfully with no bans
  changed.
- Command hub/node/key were `ky2`, `674980`, and `ky2-2026-01`.
- Signed acknowledgment validated and stored under `results/ky2`; queue drained.
- Exact acknowledgment replay was accepted idempotently; a modified replay was
  rejected for an invalid signature.
- Synthetic cross-hub and expired signed commands were rejected in memory and
  never queued or executed.
- The obsolete `georgia` control alias is rejected.
- Unauthenticated session API access remains denied with HTTP 403. Production
  currently has no installed bearer-token helper/store; the reviewed future
  token mapping uses `ky2` and contains no active `georgia` alias.
- Dashboard status refreshed from `remote-status/ky2.json` and reported KY1,
  KY2 and Phoenix fresh.
- KY2 ledger history was preserved and gained the completed qualification
  record. Historical signed content was not rewritten.
- Phoenix executable, module, configuration and key hashes were unchanged.

All ten local ASR Plus regression suites pass, including the reversible KY2
identity migration suite.

## Health and restrictions

Asterisk, ASR Plus timers/watchers, telemetry, dashboard status and existing
radio links remained healthy. Asterisk was not restarted. No radio link,
firewall, bridge, Phoenix configuration, required-mode setting, Git remote, or
legacy credential was deleted or changed beyond the fail-closed forced-command
identity rename documented in the migration plan.

Active ASR Plus code/configuration contains no `georgia` identity. Historical
audit records and timestamped rollback files retain it for traceability.
