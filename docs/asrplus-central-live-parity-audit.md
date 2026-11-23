# MasterHub central live/repository parity audit — 2026-11-23

Read-only source comparison of live `/usr/local/sbin/asrplus-control` and `/usr/local/sbin/asrplus-remote-endpoint` against this branch. No live changes.

## Confirmed differences

- Control: `main` now signs remote commands and records separate authorization/outcome audits. It also replaces hardcoded two-hub routing with `REMOTE_HUBS` and protects queue mutations with locking.
- Endpoint: `poll` now recovers leased inflight commands, rejects stale or invalid commands, and archives failures. `acknowledge` now checks signed acknowledgments and supports idempotent retries. Endpoint `main` audits rejections.
- Both scripts now preserve permissions on existing parent directories (fix `67d7495`, regression test `5f87168`). Newly created directories request `0700`. **Before activation, still verify ownership and access for all existing readers, writers, and services.**
- The central update is **not a drop-in replacement**. Coordinate with remote agents and signing configuration. Validate old and new queue/result shapes and all SSH forced commands offline before deployment.

## Outstanding gates

- [ ] Execute integration tests against copies of live queue/status/result data, without invoking Asterisk or writing to production paths.
- [ ] Validate `REMOTE_HUBS` mapping, ban-list result retrieval, and dashboard compatibility.
- [ ] Check dependent service identities and access before any mode changes. Read-only production inspection: `/var/lib/asrplus`, `queue`, `results`, `remote-status`, `queue/phoenix`, and `queue/georgia` are currently `0755 root:root`. The earlier `atomic_json` permission regression was fixed and tested; still complete the dependent service access audit before deployment.
- [x] Offline tests cover inflight lease recovery/expiry, duplicate acknowledgment, stale-command rejection and agent ledger deduplication. Live interrupted-execution behavior remains unverified.
- [ ] Verify legacy Georgia forced-command SSH entry is still needed before any revocation.
