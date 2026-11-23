# Phoenix 641894 — security cutover checklist (NOT YET AUTHORIZED)

## Current state

- Existing `asrplus-agent` timer/path and talker watcher remain active.
- Unified agent and integrity module staged under `/root/asrplus-security-stage/pending/` on Phoenix, **not activated**.
- Backup under `/root/asrplus-security-stage/20261123T194905Z/`.
- Central MasterHub 674982 has not been upgraded to signed command processing.

## Required coordinated change

1. Verify Phoenix status, timer/path, radio links and current config. Take a fresh backup of the deployed agent, config, service and drop-ins, plus central endpoint/control code and queues. Preserve ownership/modes.
2. Generate a **unique** 256-bit Phoenix HMAC key securely on an authorized machine; distribute matching copies only to Phoenix and MasterHub at `/etc/asrplus/command-keys/phoenix.key` with `0600` root-only permissions. Never print key material or commit it.
3. Install the shared Python module where Python can import it, the unified agent, and a merged `/etc/asrplus-agent.json` that retains **existing live SSH identity, known-hosts and paths** while adding `commandIntegrity.mode=optional`, `keyId=phoenix-2026-01`, `eventPublishMode=outbound`, `talkerHeartbeatSeconds=5`, and a durable `commandLedger` path.
4. Install the additive `command-integrity.conf` drop-in to allow writes to
   `/var/lib/asrplus-agent`. Retain the live `asl-ban.conf` paths
   (`/var/lib/allscan-reimagined/admin`, `/etc/asterisk`, and
   `/var/lib/asterisk`) and `event-rate.conf`; never replace the base unit or
   either existing drop-in. The watcher already has its required
   `/run/asrplus` access. Run `daemon-reload` and restart only approved ASR
   Plus services.
5. Upgrade MasterHub central signer/verifier **only after KY2 compatibility is ready**, per `docs/texas-hub-674984-readiness-254.md`. Never enable signing early.
6. Validate telemetry freshness, talker transitions, link status, legacy command compatibility, and signed read-only `ban-list` acknowledgment; then graduate both ends to `required` mode only after queues drain and explicit approval.

## Stop / rollback

If telemetry or controls regress, stop new controls, preserve queue and ledger, restore the backed-up Phoenix code/config/unit set, and verify radio links unchanged. Do not remove the ledger. No key or production service changes are part of this document.

**KY2 note:** Its `asrplus-ky-agent` currently uses a separate pull/control service layout and must have a dedicated migration plan. Its actual host is `44.30.157.22`, with verified fingerprint `SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. The old `45.76.255.39` reference was an incorrect endpoint for KY2.
