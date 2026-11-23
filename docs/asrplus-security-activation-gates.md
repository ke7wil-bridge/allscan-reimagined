# ASR Plus coordinated signing activation gates

Status: **BLOCKED / STAGED ONLY**. No production signing or service replacement.

## Gate 1 — compatibility

- [x] All nine offline token, command-integrity, agent-mode, moderation-result,
  import-layout, KY2 watcher, post-command status, staging-config and
  directory-permission self-tests pass at `b1f9e96`.
- [x] Phoenix 641894, KY2 674980 and MasterHub 674982 staged with backups; no activation.
- [x] Verify live-vs-unified configuration and unit contracts for both hubs,
  including Phoenix's existing drop-ins and KY2's split control/watcher configs,
  keyless watcher and `masterhub-pull` telemetry.
- [ ] Validate all current MasterHub forced-command SSH mappings, `asrplus-sync`, `asrplus-poll`, `asrplus-ack`, status and command queues using offline copies. Read-only audit confirms a KY2 `georgia` forced-command entry restricted to `44.30.157.22`, **but also an older `georgia` entry restricted to `45.76.255.39`**. Verify whether the older credential remains in use and plan revocation separately; do not change it during staging.
- [ ] Verify current queued/inflight commands and ledger semantics; do not switch code while commands are pending. Initial read-only queue scan returned no pending `.json` or `.inflight` files; repeat immediately before cutover.
- [ ] Verify current systemd sandbox paths and all required existing Asterisk admin writes.

## Gate 2 — secrets and staged configs

- [ ] Generate separate random HMAC keys for Phoenix and KY2; store root-only, never in repository or logs. Config/module bundles are now staged under `ops/asrplus/staging/`; no keys are included or generated.
- [ ] Configure `phoenix` and legacy `georgia` identities in MasterHub `/etc/asrplus/command-integrity.json`, both initially `optional`.
- [ ] Prepare Phoenix merged config: `eventPublishMode=outbound`, heartbeat 5, signing config and persistent ledger.
- [ ] Prepare KY2 control config: `eventPublishMode=masterhub-pull`, heartbeat 1, signing config and persistent ledger. Keep watcher config separate and watcher-only.
- [x] Validate staged module/config pairing, hub IDs, optional mode, KY2 keyless watcher config, and expected existing SSH/control paths offline.
- [ ] Validate deployed module import paths, root-only key access, ledger persistence, and systemd write allowances on each host.
- [ ] Replace all stale staged entrypoints: Phoenix/KY2 agent (`f4874d...` ->
  `7e4c41...`) and MasterHub control/endpoint (`098144...`/`324630...` ->
  `ba857f...`/`4ef829...`). Verify repository manifest hashes on all hosts.
- [ ] **Import blocker:** all new Python entrypoints import `asrplus_command_integrity`, but the existing deployed entrypoints live in `/usr/local/sbin`; explicitly install the shared module beside each entrypoint (or configure a verified import path) on MasterHub, Phoenix and KY2. Repo scan found no installation instruction for this module. Test imports using the deployed interpreter and service environment before replacement.

## Gate 3 — approved coordinated activation

- [ ] Obtain explicit production activation approval and select quiet maintenance window.
- [ ] Fresh backups and pre-change checks of hub links, telemetry, queues, control API and watcher services.
- [ ] Install compatible agent and central code in coordinated order; never enable central signing until both remote agents can verify it.
- [ ] Exercise signed **read-only** `ban-list` against each hub; verify acknowledgment, replay rejection and audit without changing radio links.
- [ ] Verify controls, last talker, recent activity, status freshness, and no unexpected link changes.
- [ ] Drain queues and obtain separate approval before `optional` -> `required` cutover.

## Rollback

Stop issuing commands; preserve ledger, queue, inflight and audit records. Restore backed-up binaries, configs and units in coordinated order. Do not rotate keys or delete ledger as a shortcut. Validate link and telemetry health after rollback.
