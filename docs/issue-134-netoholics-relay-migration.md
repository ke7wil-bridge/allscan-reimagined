# Issue #134 / #147 — Netoholics standalone DMR migration gate

Status: **not authorized for production deployment**. The managed direct-DMR
installer stages a relay copy but deliberately does not activate it or take
ownership of the existing Netoholics service. The legacy production relay is
still externally managed. Passing offline tests does not establish live safety.

## Required read-only inventory before cutover

1. Record the current systemd unit names, enabled state, ExecStart, restart
   policy, environment files, runtime directories, and service owner.
2. Record the active UDP bind/peer/upstream ports and all listening PIDs;
   verify only one relay owns each endpoint.
3. Record checksums and ownership of the active relay and ASR-installed proxy.
4. Record current control/evidence/roster paths, ownership, access controls,
   and the adapter's effective environment. Confirm ASR and the relay resolve
   the **same** control and evidence files.
5. Back up the unit, relay script, configuration, and service environment;
   preserve credentials securely without printing them into logs.
6. Verify no active net or uncontrolled subscriber test will be interrupted.

## Cutover design required before approval

- Add an explicit, opt-in adoption path for the existing Netoholics unit,
  including an ownership marker and collision preflight. Never enable a second
  relay on the same ports; do not silently replace the legacy service.
- Keep existing TGIF credentials, talkgroup, network identity, port mapping,
  and MMDVM peer unchanged. Ensure rollback restores the original service
  binary, unit, permissions, and enabled state.
- Confirm service runtime permissions permit the relay to read the control
  file and atomically write the evidence file; keep the adapter as sole writer
  of the control file.
- Test a fresh policy digest, a dropped DMR media packet, nonce-matched Kick
  receipt, repeated Kick, quiet-gap reconnection, timed Ban, Unban, restart,
  and stale evidence. Confirm no evidence is produced by a control update alone.
- Reconfirm Standard bridge operation and outbound/inbound TGIF audio using
  dedicated test identities only. Thomas and Doug may coordinate testing.

**Rollback trigger:** authentication outage, missing inbound/outbound audio,
endpoint collision, stale or false enforcement receipt, or unexpected impact
on active clients. Do not merge/publish until these gates pass and approval is
explicitly granted.

## Read-only production findings (2026-11-10)

- Active relay: `netoholics-native-dmr-relay.service`, Python script under
  `/root/netoholics-native-bridge-migration/dmr/`; MMDVM peer is a separate unit.
- Relay bind `127.0.0.1:62041`; MMDVM bind `0.0.0.0:62042`; TG `67498`.
  Both services are active. **Do not launch a second relay on these ports.**
- Active adapter: `allscan-reimagined-native-dmr-admin.service`, running as root;
  its roster argument is `/run/allscan-reimagined/tgif-users/snapshots/node.json`.
- The snapshot exists and contains `clients` with `dmrid`, `callsign`,
  `last_seen_epoch`; it also carries `updatedEpoch`. The older assumed
  `/run/netoholics-native-dmr/dmr-clients.json` does **not** exist.
- The legacy relay has no enforcement evidence output; expected
  `/run/netoholics-native-dmr/dmr-admin-enforcement.json` does **not** exist.
- The active adapter is the legacy version which updates roster state during
  Kick, rather than waiting for a relay-observed dropped packet. This does not
  meet the new evidence contract.
- The new standalone relay now defaults to the existing TGIF snapshot path;
  deployment must explicitly verify service environment, snapshot refresh
  cadence, and permissions. Stale or missing identity mappings fail closed.

These observations are read-only; no production changes were made.

## Cross-mode administration acceptance requirement (2026-11-10)
- An administrator Kick or Ban targeting an identity must apply globally across DMR, YSF, P25, NXDN, M17, and Zello, not only the mode where the identity was observed.
- Kick must terminate matching active sessions across every supported mode; reconnect is permitted unless an active Ban also applies.
- Ban must persist in the shared native ASR policy store, prevent matching reconnects on every mode, and be reconciled to each verified enforcement adapter. Unban must remove the global restriction across all modes.
- Do not report global success if any enabled mode lacks verified enforcement; return explicit per-mode applied/skipped/failed evidence. Do not advertise unsupported controls.
- Current gap: standalone-admin kick() targets one bridge; reconcile_all() skips missing adapters. Neither alone proves global Kick or Ban across all six modes.
- Test with authorized synthetic identities and per-mode active-session/reconnect checks before production deployment.
