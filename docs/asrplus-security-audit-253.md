# ASR Plus security audit - issue #253

Audit date: 2026-11-23 (America/Phoenix)

Scope: Kentucky MasterHub 674982, KY2 674980, Phoenix 641894, and the planned
Texas 674984 trust boundary. Production inspection was read-only. No service,
queue, radio link, credential, configuration, or firewall was changed.

## Current conclusion

Authenticated command and acknowledgment code from merged commit `4b43851` is
installed on the three current hubs and matches the repository hashes. Phoenix
and KY2 remain in `optional` compatibility mode. Required-mode activation is
not yet approved and is blocked by the KY2 control-unit sandbox gap described
below.

The scoped token implementation is complete in source but is not installed on
MasterHub. Production still serves the session-only API; the token helper,
token sudoers rule, token store, and token audit log are absent. Tokens must be
installed centrally on Kentucky only. Remote hubs never receive bearer-token
stores or token authentication rules.

## Read-only production evidence

| Node | Identity and role | Signing state | Evidence |
| --- | --- | --- | --- |
| Kentucky 674982 | `kentucky`, central API, queue and signing authority | Phoenix and KY2 `optional` | Control, endpoint and integrity-module hashes match merged source; per-hub keys are root-owned `0600`; queues empty; forced SSH mappings are source-restricted |
| KY2 674980 | `ky2`, remote agent | `optional`, key ID `ky2-2026-01` | Agent/module hashes match; durable `0700` ledger has complete records; strict pinned MasterHub host key; control and watcher healthy |
| Phoenix 641894 | `phoenix`, remote agent | `optional`, key ID `phoenix-2026-01` | Agent/module hashes match; durable `0700` ledger; strict pinned MasterHub host key; control and watcher sandboxed and healthy |

KY2 and Phoenix independently pin the Kentucky ED25519 host fingerprint
`SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. No host-key bypass was
used. The unrelated retired endpoint at `45.76.255.39` was not contacted and
must not be confused with KY2.

## Authentication and authorization

| Boundary | Current control | Remaining requirement |
| --- | --- | --- |
| Human dashboard | AllScan session; control requires `modifyOk()` and control header | Keep session controls; review broad role separately |
| API automation | Source supports scoped bearer tokens; production has none installed | Install on Kentucky only after separate approval; TLS, expiry, revocation and audit required |
| Apache to control | `www-data` may run only root-owned `asrplus-control` through the ASR Plus rule | Preserve closed action vocabulary and root ownership |
| Apache to token auth | Source sudo rule permits only `asrplus-token authenticate` | Do not permit create/list/revoke to Apache |
| Remote hub to Kentucky | Per-hub SSH key, `restrict`, forced endpoint, source address and hub binding | Preserve strict host-key verification and one identity per key |
| Command and result | Per-hub HMAC, digest, nonce, issue/expiry, signed acknowledgment and execute-once ledger | Complete approved optional-to-required cutover |
| AI or automation | Dashboard bearer token design only | Never issue SSH keys to AI agents |

## Available control operations

| Operation | Effect | Token permission |
| --- | --- | --- |
| Status | Read the authorized hub snapshot | `status:read` |
| Connect or permanent connect | Enumerated Asterisk link action | `control:connect` |
| Disconnect | Enumerated Asterisk unlink action | `control:disconnect` |
| Ban | Validated local moderation helper call | `control:ban` |
| Unban | Validated local moderation helper call | `control:unban` |
| Ban list/result | Read-only moderation qualification and result retrieval | Session/control path only; not exposed to tokens |

Tokens cannot edit configuration, access queues or credentials, run arbitrary
commands, open a shell, restart services, or update software.

## Findings and required remediation

### High

1. Required-mode enforcement is not active. Optional mode deliberately accepts
   bounded unsigned legacy commands and therefore remains a downgrade window.
   Drain queues, qualify signed read-only commands, harden KY2, then obtain a
   separate approval for config-only activation.
2. Production API-token controls are absent. Installing only the repository
   API without its root-owned helper and narrowly scoped sudoers rule would
   fail, while installing the helper without TLS and audit verification would
   be incomplete. Treat these as one Kentucky deployment transaction.

### Medium

1. KY2's control oneshot currently runs as root without `ProtectSystem` or
   `NoNewPrivileges`; its watcher is hardened. Phoenix already has the expected
   restrictions. The prepared KY2 drop-in adds filesystem, privilege, umask,
   address-family and state-directory controls without changing Asterisk or
   radio routing. Install and verify it only in an approved window.
2. Remote control agents still run as root because they use local Asterisk and
   moderation paths. Texas must validate a dedicated service account and
   minimum AMI/socket permissions before installation. Existing hubs should be
   migrated only after functional parity is demonstrated.
3. MasterHub queue, result, archive and status directories remain `0755` for
   existing dependent readers; individual sensitive files are `0600` or
   `0640`. Changing shared directory modes requires a dependency-specific
   rollout rather than an untested blanket restriction.
4. The broader AllScan web application has additional root helpers outside ASR
   Plus. Their authorization surface is out of this issue's scope and should be
   audited separately.

## Positive controls verified

- Commands and acknowledgments use per-hub HMAC-SHA-256 authentication and are
  bound to hub, node, command ID, digest, key ID, nonce, and validity window.
- Queue leases, saturation bounds, stale quarantine, exact duplicate handling,
  conflicting-ack rejection, and durable execute-once ledgers are implemented.
- Missing, short, group-readable, or symbolic-link signing keys fail closed.
- Remote SSH uses batch mode, dedicated identities, strict known-hosts files,
  forwarding disabled, and no TTY.
- Scoped tokens default to read-only, store only secret hashes, require an
  explicit hub, permission and expiry, support revocation, require HTTPS, and
  audit allowed and denied authentication without logging bearer values.
- Root-owned helpers are not writable by Apache; token administration is never
  authorized through the web-server sudo rule.

## Least-privilege target

Each hub has one canonical identity, one node mapping, one HMAC key, one SSH
identity, one forced endpoint, isolated queue/result/archive/status paths, and
one durable ledger. A hub key cannot poll, execute, or acknowledge another
hub's command. Texas starts in required and telemetry-only mode. Dashboard
automation receives the shortest practical Kentucky-issued token, defaults to
`status:read`, and receives control permissions only by explicit operation and
hub approval.
