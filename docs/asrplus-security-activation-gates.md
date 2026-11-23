# ASR Plus coordinated signing activation gates

Status: **BLOCKED / STAGED ONLY**. No production signing or service replacement.

## Gate 1 — compatibility

- [x] Offline token, command-integrity and agent-mode self-tests pass.
- [x] Phoenix 641894, KY2 674980 and MasterHub 674982 staged with backups; no activation.
- [ ] Verify live-vs-unified agent parity for both hubs, including KY2's split control/watcher configs and `masterhub-pull` telemetry.
- [ ] Validate all current MasterHub forced-command SSH mappings, `asrplus-sync`, `asrplus-poll`, `asrplus-ack`, status and command queues using offline copies.
- [ ] Verify current queued/inflight commands and ledger semantics; do not switch code while commands are pending.
- [ ] Verify current systemd sandbox paths and all required existing Asterisk admin writes.

## Gate 2 — secrets and staged configs

- [ ] Generate separate random HMAC keys for Phoenix and KY2; store root-only, never in repository or logs.
- [ ] Configure `phoenix` and legacy `georgia` identities in MasterHub `/etc/asrplus/command-integrity.json`, both initially `optional`.
- [ ] Prepare Phoenix merged config: `eventPublishMode=outbound`, heartbeat 5, signing config and persistent ledger.
- [ ] Prepare KY2 control config: `eventPublishMode=masterhub-pull`, heartbeat 1, signing config and persistent ledger. Keep watcher config separate and watcher-only.
- [ ] Validate module import paths, root-only key access, ledger persistence, and systemd write allowances.

## Gate 3 — approved coordinated activation

- [ ] Obtain explicit production activation approval and select quiet maintenance window.
- [ ] Fresh backups and pre-change checks of hub links, telemetry, queues, control API and watcher services.
- [ ] Install compatible agent and central code in coordinated order; never enable central signing until both remote agents can verify it.
- [ ] Exercise signed **read-only** `ban-list` against each hub; verify acknowledgment, replay rejection and audit without changing radio links.
- [ ] Verify controls, last talker, recent activity, status freshness, and no unexpected link changes.
- [ ] Drain queues and obtain separate approval before `optional` -> `required` cutover.

## Rollback

Stop issuing commands; preserve ledger, queue, inflight and audit records. Restore backed-up binaries, configs and units in coordinated order. Do not rotate keys or delete ledger as a shortcut. Validate link and telemetry health after rollback.
