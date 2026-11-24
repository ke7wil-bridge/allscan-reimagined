# ASR Plus security completion and Texas readiness

Inspection package - 2026-11-23 (America/Phoenix)

**Internal draft — not approved for external delivery.**

Scope: issues #252, #253 and #254; related #134. PR #268 merged into main.
Production signing activation and KY2 hardening completed November 23 Arizona time.
API token deployment awaits public DNS and trusted HTTPS.

## Executive decision

Mandatory command integrity is active on all three existing hubs. Signed
read-only ban-list checks passed independently for Phoenix and KY2 after
central enforcement. KY2 sandbox hardening passed. No Asterisk restart or
radio-link changes were made during cutover. All 12 local security suites
passed after token storage and status-scope corrections.

## Production inspection result

| Node | Identity | Installed state | Readiness |
| --- | --- | --- | --- |
| Kentucky 674982 | kentucky | Required signing for both remote hubs | HTTPS and token deployment pending |
| KY2 674980 | ky2 | Required signing; sandbox verified | Passed |
| Phoenix 641894 | phoenix | Required signing; hardened unit | Passed |
| Texas 674984 | texas | Required-mode configuration prepared | Operator and isolated qualification pending |
| UK 674985 | Planned | ASR Plus not independently validated | Separate qualification |

KY2 and Phoenix independently pin the Kentucky ED25519 host fingerprint
`SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. Key contents were never
read or logged. The unrelated retired endpoint was not contacted. Current
Asterisk services and dashboard telemetry remained healthy throughout the
read-only inspection.

## Security changes completed locally

- Incorporated the draft PR #266 Texas correction: the Texas agent and central
  config use `/etc/asrplus/command-keys/texas.key`, separate from its SSH key.
- Corrected the same latent path drift in the generic Phoenix config to
  `/etc/asrplus/command-keys/phoenix.key`.
- Extended config parity tests across Phoenix, KY2 and Texas.
- Added negative tests proving missing, short, group-readable and symlinked
  command keys fail closed.
- Added an offline required-mode cutover/rollback contract test.
- Prepared a KY2 control-service hardening drop-in matching Phoenix's privilege,
  filesystem, umask, address-family and state-directory baseline without
  altering its timer, watcher, topology, Asterisk or radio links.
- Updated active security, activation, deployment-inventory and Texas guidance
  to the canonical `ky2` identity and actual installed state.

## Vulnerabilities addressed

| Threat | Protection |
| --- | --- |
| Forged or modified command | Per-hub HMAC, canonical digest, key ID and hub/node binding |
| Replayed or duplicate command | 128-bit nonce, expiry, one inflight lease and durable execute-once ledger |
| Forged/conflicting acknowledgment | Signed result bound to exact command digest; exact duplicate is idempotent, conflict rejected |
| Cross-hub execution | Forced SSH identity, isolated path, signed hub/node and per-hub key |
| Stale work after outage | Maximum validity window, archive/quarantine and no execution on failed sync |
| Queue tampering/saturation | Verification before lease, invalid quarantine, atomic files and bounded depth |
| Over-broad automation | Kentucky-only scoped tokens, read-only default, explicit node/operation, expiry, revocation and audit |
| Key-path drift or unsafe key file | Three-hub parity assertions and fail-closed type/mode/symlink checks |
| KY2 root service filesystem access | Installed systemd sandbox; production qualification passed |

## Test result

All repository ASR Plus security and compatibility self-tests pass, including
command/ack lifecycle, queue recovery, replay/expiry/cross-hub rejection,
Kentucky outage behavior, token authorization, agent operating modes, KY2
identity/watcher behavior, directory permissions, staging parity and Texas
config parity. Python compilation and PHP syntax checks pass. The exact command
list is in `docs/asrplus-command-integrity-test-report.md`.

No load or failure injection was run against production. Texas capacity and
failure cases remain an isolated-fixture gate.

## Remaining risks and blockers

1. Public DNS and trusted HTTPS are pending.
2. Token corrections are locally tested but not deployed.
3. Kentucky still has the session-only API. Token code is not an installed
   control until the API, helper, sudoers, protected store and audit path are
   deployed and tested together.
4. Remote agents still run as root. Texas requires a dedicated-account
   feasibility decision; existing hubs need a later compatibility migration.
5. Kentucky is a single aggregation/queue dependency. Hub radio links can
   continue during a Kentucky outage, but dashboard and remote control fail
   stale/closed; there is no automatic regional failover.
6. Texas provider capacity, source address, administrator model, retention,
   incident authority and radio/control policy still require KJ5IRQ agreement.

## Recommended sequence and rollback

Signing cutover is complete. If rollback is needed, stop new controls,
return Kentucky then the affected agent to optional, and preserve queues,
results, archives, audits and ledgers. Restore KY2's backed-up unit only
if necessary.

Install the Kentucky token package only in a separate window. Back up the API
and sudoers state, install all components as one set, validate syntax and TLS,
exercise a short-lived read-only test token, test cross-node/permission denial,
expiry and revocation, then remove the test token. Roll back the whole set to
the session-only API; do not leave a partial token stack.

Texas is last and starts telemetry-only in required mode. It receives new HMAC
and SSH keys, a source-restricted forced endpoint, isolated state and no
production radio link. Snapshot rollback and credential revocation must be
demonstrated before any control permission.

## Remaining activation gates

Follow docs/asrplus-https-readiness.md after DNS propagates. Validate trusted
TLS and certificate renewal before installing tokens. Verify session compatibility,
scope isolation, expiry and revocation on the live API. Texas contact/install
requires separate operator approval. Beta 8.6.1 release qualification remains
separate under #129; no publication authorized.

## Supporting technical files

- `docs/asrplus-security-audit-253.md`
- `docs/asrplus-security-activation-gates.md`
- `docs/asrplus-live-deployment-inventory-2026-11-23.md`
- `docs/asrplus-command-integrity-test-report.md`
- `docs/texas-hub-674984-readiness-254.md`
- `docs/asrplus-security-inspection-evidence-2026-11-23.json`
