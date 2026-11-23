# ASR Plus security completion and Texas readiness

Inspection package - 2026-11-23 (America/Phoenix)

Authoritative scope: issues #252, #253 and #254; related #134; merged command
security foundation `4b43851`; local completion branch
`security/asrplus-security-completion` based on `e9641b7` with PR #266 fix
`918a995`. The final local commit is recorded in the delivery report after the
package is committed. No production mutation, restart, radio-link change,
firewall change, token installation, push or merge was performed.

## Executive decision

The installed signed-command stack is healthy and source-parity verified on
Kentucky, KY2 and Phoenix, but required mode is not ready for approval until
KY2's control service receives and passes the prepared sandbox drop-in.
Kentucky API tokens are implemented and tested in source but are not installed.
Texas configuration is internally consistent and remains a design-only package;
Texas has not been contacted.

Three production approvals remain deliberately separate:

1. KY2 systemd hardening followed by Phoenix/KY2/Kentucky required-mode
   activation.
2. Kentucky-only API token package installation and qualification.
3. Texas telemetry-only installation after capacity tests and operator
   agreements. Radio linking and each control permission are later approvals.

## Production inspection result

| Node | Identity | Installed state | Readiness |
| --- | --- | --- | --- |
| Kentucky 674982 | `kentucky` | Signed control/endpoint/module and per-hub optional config match reviewed source; queues empty; token package absent | Ready to remain optional; token and required cutovers need approval |
| KY2 674980 | `ky2` | Signed agent/module, separate key and durable ledger healthy in optional mode | Blocked on control-unit sandbox qualification |
| Phoenix 641894 | `phoenix` | Signed agent/module, separate key, hardened unit and durable ledger healthy in optional mode | Ready for approved config-only required cutover after KY2 gate |
| Texas 674984 | `texas` | Repository config only; required from first installation | Not install-ready until VM/account/network agreements and isolated tests pass |

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
| KY2 root service filesystem access | Prepared systemd sandbox; production qualification still required |

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

1. Optional mode accepts bounded legacy unsigned work; it must not be treated
   as the final security posture.
2. KY2's live control service lacks the prepared sandbox. Required mode should
   not be activated before this is installed and qualified.
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

First approve and qualify the KY2 drop-in. Then, with empty queues, change
Phoenix and KY2 agents to required one at a time, followed by Kentucky's two
hub definitions. Roll back by stopping new controls, returning Kentucky then
the affected agent to optional, restoring the KY2 unit state if necessary, and
preserving all queues, results, archives, audits and ledgers.

Install the Kentucky token package only in a separate window. Back up the API
and sudoers state, install all components as one set, validate syntax and TLS,
exercise a short-lived read-only test token, test cross-node/permission denial,
expiry and revocation, then remove the test token. Roll back the whole set to
the session-only API; do not leave a partial token stack.

Texas is last and starts telemetry-only in required mode. It receives new HMAC
and SSH keys, a source-restricted forced endpoint, isolated state and no
production radio link. Snapshot rollback and credential revocation must be
demonstrated before any control permission.

## Exact approval required from Josh

No approval is needed for the completed local work. Before the next live step,
Josh must explicitly approve: (a) KY2 674980 installation of the named systemd
drop-in and systemd reload, then (b) the config-only `optional` to `required`
changes on Phoenix 641894, KY2 674980 and Kentucky 674982, including the
supervised signed read-only `ban-list` checks. The approval must name the
maintenance window, backup locations, affected files, expected timer/service
effects, verification and rollback. It must explicitly confirm no Asterisk
restart or radio-link change.

Kentucky token installation and any Texas contact/install remain unapproved
and require their own later action-time approvals.

## Supporting technical files

- `docs/asrplus-security-audit-253.md`
- `docs/asrplus-security-activation-gates.md`
- `docs/asrplus-live-deployment-inventory-2026-11-23.md`
- `docs/asrplus-command-integrity-test-report.md`
- `docs/texas-hub-674984-readiness-254.md`
- `docs/asrplus-security-inspection-evidence-2026-11-23.json`
