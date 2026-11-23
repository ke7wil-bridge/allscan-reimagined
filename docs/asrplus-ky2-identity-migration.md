# ASR Plus KY2 internal identity migration — preparation only

Status: **OFFLINE PREPARATION — NOT AUTHORIZED FOR PRODUCTION**

This later migration changes node 674980's internal ASR Plus identity from
`georgia` to `ky2`. It is deliberately excluded from the current optional-mode
security deployment. It makes no Asterisk, radio-link, firewall, routing, or
Phoenix change.

## Candidate changes

- MasterHub control routing maps node 674980 and dashboard alias
  `kentucky-hub` to `ky2`; active `georgia` aliases are removed.
- The forced-command endpoint accepts `ky2`, not `georgia`, for node 674980.
- MasterHub and KY2 configs use hub `ky2`, key ID `ky2-2026-01`, and key path
  `/etc/asrplus/command-keys/ky2.key`.
- The existing KY2 signing key bytes are copied—not regenerated—from
  `georgia.key` to the new protected path on MasterHub and KY2. Both paths stay
  available through rollback; key content is never logged.
- The execution ledger path does not change. Historical command digests and
  acknowledgments remain byte-for-byte intact.
- Dashboard/API token aliases expose `ky2`; Phoenix mappings and files are
  unchanged and their hashes are recorded in the generated manifest.
- The live dashboard configuration is transformed with an exact node-674980
  match so only its status path changes from `georgia.json` to `ky2.json`.
- The source-restricted KY2 forced command changes to endpoint identity `ky2`.
  The retained retired-host credential changes to the unsupported identity
  `retired`, so it remains present for separate review but cannot acquire KY2
  authority. Its separate telemetry forced command is likewise retargeted to a
  deliberately unsupported `retired_telemetry_receiver.py` name. No active
  forced-command line retains the old identifier, and neither retired path is
  contacted during this migration.

Run `scripts/asrplus-ky2-identity-migration.py build` into a new empty staging
directory to generate the coordinated MasterHub/KY2 candidate. The builder
fails if reviewed source patterns drift and its test rejects any remaining
`georgia` string in generated active code or configuration.

## State migration

With both command queues empty and controls paused, the state tool atomically
renames these paths:

| Legacy | New |
| --- | --- |
| `queue/georgia` | `queue/ky2` |
| `queue/.georgia.lock` | `queue/.ky2.lock` |
| `results/georgia` | `results/ky2` |
| `queue-archive/georgia` | `queue-archive/ky2` |
| `remote-status/georgia.json` | `remote-status/ky2.json` |

It refuses symlinks, destination conflicts, or a non-empty command queue. It
does not rewrite signed queue/result/archive content because doing so would
invalidate historical signatures. Global JSONL audit files are retained at
their existing paths and left byte-for-byte unchanged; new records use `ky2`.
The tool supports a reverse operation for rollback.

## Future production order

1. Complete and validate optional-mode signing under the legacy identity.
2. Obtain a separate migration approval and fresh backups. Pause new controls;
   prove both KY2 queue and inflight state are empty.
3. Copy the existing key to `ky2.key` on MasterHub and KY2, preserving
   `root:root 0600`, and compare hashes without printing content.
4. Stage and hash-verify the generated candidate. Confirm Phoenix hashes and
   service state are unchanged.
5. Stop only the KY2 control timer long enough to atomically migrate state,
   MasterHub config/entrypoints/active KY2 forced-command mapping, and KY2
   control config as one coordinated change. The watcher remains keyless.
6. Restart/re-enable only the KY2 control timer and invoke one optional-mode
   poll. Qualify a signed read-only `ban-list`, routing, acknowledgment,
   telemetry, audit, Kick/Ban result lookup, and KY1/KY2 separation.
7. On any mismatch, stop controls, reverse state paths, restore the prior
   configs/entrypoints/forced-command mapping, and preserve both key names,
   ledger, queues, results, archives, and audits.

The older source-restricted credential for retired `45.76.255.39` is not used
or contacted. Its eventual revocation is a separate approval. Required-mode
activation is also separate.
