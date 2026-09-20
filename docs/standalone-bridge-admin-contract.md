# Standalone bridge administration contract

This contract applies to independently installed standard DMR, YSF, P25,
NXDN, and M17 cards. URF cards keep their existing administration path.

An installer must not mark a standard bridge creation complete until it has
created:

- a root-owned adapter for that exact bridge;
- `/run/asr-standalone-admin/<bridge-id>/capabilities.json`;
- `/run/asr-standalone-admin/<bridge-id>/.asr-bridge-owner.json`;
- a supervised service that refreshes the manifest heartbeat and processes
  administration requests.

The lifecycle preflight and ownership manifest must include the capability
manifest runtime path. Missing administration plumbing is therefore an
installation failure, rather than a partially functional card.

## Capability manifest

The file must be root-owned, a regular non-symlink, no larger than 64 KiB,
and not group/world writable. It is stale after 30 seconds.
Required base fields:

```json
{
  "schema": 1,
  "bridgeId": "ysf-local",
  "mode": "ysf",
  "adapter": "ysf-reflector-admin-v1",
  "healthy": true,
  "heartbeatEpoch": 1789776000,
  "listClients": true
}
```

Kick may be advertised only with every field below:

```json
{
  "kickClient": true,
  "kickContract": "disconnect-until-reconnect",
  "kickRequiresCurrentSession": true,
  "kickAllowsImmediateReconnect": true
}
```

The adapter must remove only the selected current bridge session. It must
reject a recent talker that is no longer connected. Audio suppression or
dropping one transmission is not Kick.
Global Ban may be advertised only with every field below:

```json
{
  "banClient": true,
  "unbanClient": true,
  "listBans": true,
  "banContract": "global-timed-v1",
  "appliedPolicyDigest": "<64 lowercase hexadecimal characters>"
}
```

The digest is SHA-256 over these exact bytes:

```text
asr-global-ban-v1 NUL urfd.blacklist-bytes NUL asr-timed-bans.json-bytes
```

The API hides Ban whenever the applied digest differs from the authoritative
policy. The adapter must enforce exact and prefix identities for current
traffic, new sessions, restarts, timed expiry, Ban, and Unban.

## Request and acknowledgment

The helper atomically creates
`request-<32-hex-nonce>.json` in the bridge runtime directory. The adapter
must atomically create the matching `result-<nonce>.json` within 12 seconds.
Both files are schema 1 and bound to nonce, bridge ID, mode, and action.
A successful Kick acknowledgment also contains:

```json
{
  "identity": "N7TEST",
  "removed": 1,
  "verified": true,
  "enforcement": "disconnect-until-reconnect"
}
```

A successful reconciliation acknowledgment contains:

```json
{
  "verified": true,
  "appliedPolicyDigest": "<the request policy digest>"
}
```

The adapter must update its capability manifest with that same applied digest
only after backend enforcement is active. An error result uses `"ok": false`
and a safe human-readable `error` string.

## Backend requirements

- DMR identity mapping must distinguish subscriber and hotspot IDs.
- YSF must distinguish the connected gateway from the transmitted source.
- P25 and NXDN numeric source identities must be resolved without guessing.
- M17 must use the reflector's real client removal and blacklist primitives.
- Protected ASR service and health identities must never be targeted.
- A backend without a verified session primitive must set `kickClient: false`.
- Adapter failure must leave capabilities unavailable and must never be
  reported as successful global enforcement.
