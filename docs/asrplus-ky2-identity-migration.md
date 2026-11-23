# ASR Plus KY2 internal identity — canonical repository state

Status: **DEPLOYED IN OPTIONAL MODE — 2026-11-23**

See `docs/asrplus-ky2-identity-migration-report-2026-11-23.md` for production
hashes, qualification evidence, backups, and rollback readiness.

Node 674980 is canonically identified as `ky2` throughout active ASR Plus
runtime source, examples, and staged deployment configuration. The old
`georgia` identifier is retained only in historical records, migration-state
paths, and rollback documentation; it is not an accepted runtime alias.

## Canonical runtime contract

- MasterHub control routing maps `ky2` and the dashboard compatibility alias
  `kentucky-hub` to node 674980. The old `georgia` alias is rejected.
- The remote forced-command endpoint accepts `ky2` for node 674980 only.
- Scoped API tokens use `ky2`; token scope `georgia` is invalid.
- MasterHub and KY2 staged signing configuration use hub `ky2`, key ID
  `ky2-2026-01`, and `/etc/asrplus/command-keys/ky2.key`.
- The KY2 agent retains its existing SSH identity, known-hosts path,
  `/run/asrplus-ky` control path, split watcher service, and command ledger.
  The watcher remains keyless.
- Phoenix remains `phoenix` / node 641894 and its agent/configuration hashes
  are recorded in the generated manifest.

The canonical source and staging files are the build inputs. The builder copies
them without performing a `georgia`-to-`ky2` source rewrite. Its regression
tests inspect both the committed runtime source/configuration and the generated
bundle, preventing a future package or redeploy from restoring the obsolete
identity.

## Historical state mapping

The production migration preserved historical signed records and audit data.
The offline state tool retains reversible path mappings for rollback fixtures:

| Historical path | Canonical path |
| --- | --- |
| `queue/georgia` | `queue/ky2` |
| `queue/.georgia.lock` | `queue/.ky2.lock` |
| `results/georgia` | `results/ky2` |
| `queue-archive/georgia` | `queue-archive/ky2` |
| `remote-status/georgia.json` | `remote-status/ky2.json` |

The state tool refuses symlinks, destination conflicts, or a non-empty command
queue. It does not rewrite signed queue/result/archive content or global JSONL
audit files. Historical records may therefore legitimately retain the original
identity string.

## Production boundaries

The identity migration does not change Asterisk, radio links, firewall rules,
routing, or Phoenix. Production command authentication remains in optional
compatibility mode. Required-mode activation, credential revocation, and any
additional production deployment require separate approval.
