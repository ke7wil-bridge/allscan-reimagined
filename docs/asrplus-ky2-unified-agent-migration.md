# KY2 674980 — unified agent migration (preparation only)

**No production deployment authorized by this plan.** Verified SSH alias `ky2` resolves to `44.30.157.22`, hostname `NTH-SERV-P02`, ED25519 fingerprint `SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. The legacy `45.76.255.39` endpoint is NOT KY2 and must not be used for this rollout.

## Preserve live behavior

- Keep hub ID `georgia` (internal protocol identity) and node `674980`; display label KY2.
- Current program: `/usr/local/sbin/asrplus-ky-agent` (Nov 18 build), with **two distinct configs and units**.
- Control timer: `asrplus-ky-control.timer` runs `asrplus-ky-control.service`, config `/etc/asrplus-ky-control.json`, with outbound SSH to Kentucky MasterHub `44.30.157.21` and socket path `/run/asrplus-ky/ssh-%C`.
- Talker watcher: `asrplus-ky-watcher.service`, config `/etc/asrplus-ky.json`, writing `/run/asrplus-ky/talkers.json` and `/run/asrplus-ky/talker-event`. MasterHub pulls these; **no background outbound event publisher**.
- Unified agent mode: `eventPublishMode=masterhub-pull`, `talkerHeartbeatSeconds=1`; existing talker paths must remain unchanged.

## Pre-activation work

1. Back up executable, both configs, unit files and drop-ins, talker state, central pull configuration, queue/ack files and their metadata; inventory live Asterisk links and fresh telemetry.
2. Install the common Python module and unified agent to a **versioned path**; preserve the legacy executable and both units for rollback. No service switches yet.
3. Prepare separate merged control and watcher configs, retaining existing paths and credentials; add distinct KY2 HMAC key reference, `commandIntegrity.mode=optional`, `keyId=georgia-2026-01`, and persistent `commandLedger` for the control service. The watcher config must not need to read the HMAC key unless it executes commands.
4. Validate watcher-only behavior without outgoing event publication, and validate status/command handling with offline fixtures. Confirm correct sandbox write paths (`/run/asrplus-ky`, `/var/lib/asrplus-agent`) and preserve existing Asterisk control access.
5. In an explicitly approved window, switch the two unit ExecStart commands to the versioned unified agent, reload and restart **only** the ASR Plus units; verify status, talker freshness, controls and no unexpected radio link changes. Keep old binaries and configs for immediate rollback.
6. Only after both KY2 and Phoenix agents can verify signatures, upgrade MasterHub. Change from `optional` to `required` only after signed read-only qualification and queue drain.

## Hard stop

Do not replace KY2's current agent with Phoenix's service file: KY2 has a different watcher, paths, and pull topology. Do not alter SSH host trust or key material in source control. Any unexpected talker delay, queue error, or control failure requires rollback to backed-up KY2 unit/config/executable set while preserving the execution ledger.

## Verified live unit layout (read-only)

`asrplus-ky-control.service` runs `/usr/local/sbin/asrplus-ky-agent --config /etc/asrplus-ky-control.json`. `asrplus-ky-watcher.service` runs the same executable with `/etc/asrplus-ky.json --watch-talkers`, with `ProtectSystem=strict`, `ReadOnlyPaths` including `/etc/asrplus-ky.json`, and `ReadWritePaths=/run/asrplus-ky`. Preserve both distinct unit/config contracts during migration.
