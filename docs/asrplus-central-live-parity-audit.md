# MasterHub central live/repository parity audit — 2026-11-23

Read-only source comparison of live `/usr/local/sbin/asrplus-control` and `/usr/local/sbin/asrplus-remote-endpoint` against this branch. No live changes.

## Confirmed differences

- Control: `main` now signs remote commands and records separate authorization/outcome audits. It also replaces hardcoded two-hub routing with `REMOTE_HUBS` and protects queue mutations with locking.
- Endpoint: `poll` now recovers leased inflight commands, rejects stale or invalid commands, and archives failures. `acknowledge` now checks signed acknowledgments and supports idempotent retries. Endpoint `main` audits rejections.
- Both scripts' `atomic_json` now force parent directory mode `0700`. **Before activation, verify ownership and access for all existing readers, writers, and services; chmod on an existing shared directory may break them.**
- The central update is **not a drop-in replacement**. Coordinate with remote agents and signing configuration. Validate old and new queue/result shapes and all SSH forced commands offline before deployment.

## Outstanding gates

- [ ] Execute integration tests against copies of live queue/status/result data, without invoking Asterisk or writing to production paths.
- [ ] Validate `REMOTE_HUBS` mapping, ban-list result retrieval, and dashboard compatibility.
- [ ] Check actual ownership/mode of affected queue, result, and status directories and dependent service identities.
- [ ] Test inflight recovery, duplicate acknowledgment, stale-command rejection and interrupted execution.
- [ ] Verify legacy Georgia forced-command SSH entry is still needed before any revocation.
