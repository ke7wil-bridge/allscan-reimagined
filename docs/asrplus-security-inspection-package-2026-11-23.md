# ASR Plus security completion and Texas readiness

Inspection package - updated 2026-11-24 (America/Phoenix)

**Internal draft — not approved for external delivery.**

Scope: issues #252, #253 and #254; related #134. PR #268 merged into main.
Production signing activation and KY2 hardening completed November 23 Arizona time.
API token stack is installed on Kentucky after DNS and trusted HTTPS activation; live authorization tests passed. No production bearer token retained.

## Executive decision

Mandatory command integrity is active on all three existing hubs. Signed
read-only ban-list checks passed independently for Phoenix and KY2 after
central enforcement. KY2 sandbox hardening passed. No Asterisk restart or
radio-link changes were made during cutover. All 12 local security suites passed. Live API tests verified HTTPS enforcement, single-hub scope, cross-hub denial, read-only permission denial, expiration and revocation.
Final browser regression testing also passed against Josh's existing authenticated
Chrome session on Kentucky. The ordinary dashboard API returned current three-hub
status with no bearer token, an omitted-cookie request was denied with `403`, the
control-header probe was denied without executing a control, and logout invalidated
the session and redirected both Kentucky tabs to `/asr/user/`.

## Production inspection result

| Node | Identity | Installed state | Readiness |
| --- | --- | --- | --- |
| Kentucky 674982 | kentucky | Required signing for both remote hubs | HTTPS and token activation passed |
| KY2 674980 | ky2 | Required signing; sandbox verified | Passed |
| Phoenix 641894 | phoenix | Required signing; hardened unit | Passed |
| Texas 674984 | texas | Required-mode configuration prepared | Operator and isolated qualification pending |
| UK 674985 | Planned | ASR Plus not independently validated | Separate qualification |

KY2 and Phoenix independently pin the Kentucky ED25519 host fingerprint
`SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. Key contents were never
read or logged. The unrelated retired endpoint was not contacted. Current
Asterisk services and dashboard telemetry remained healthy throughout the
read-only inspection.

## Production HTTPS and token validation — November 24

- Public DNS verified; Let's Encrypt certificate issued for asrplus.netoholicsnetwork.net, valid through 2027-02-22.
- Apache TLS vhost active; TCP 443 allowed in UFW; external HTTPS certificate validation passed. HTTP remains available for existing dashboard compatibility; bearer tokens are rejected over HTTP.
- Certbot automatic renewal enabled; renewal dry-run succeeded.
- Token-enabled API installed in authoritative /opt/asrplus/current/web/api.php source and reapplied to the web root. Periodic asrplus-reapply integrity job rerun; source/web hashes match.
- Helper /usr/local/sbin/asrplus-token and authentication-only sudoers rule installed. Token store is 0700 root:root, token database 0600 root:root, audit log 0640 root:root.
- Live tests passed: anonymous and invalid token denial; HTTP bearer rejection; HTTPS status read; one-hub-only response; cross-hub denial; read-only token control denial; expiry and revocation. All test tokens revoked; no permanent production token issued.
- Apache and Asterisk remained active; no Asterisk restart or radio-link changes.

Existing browser-session authentication and dashboard control code paths were preserved. PR #272 adds regression assertions for common.php initialization, UserModel::validate(), valid user detection, login-required fallback, and the existing modifyOk()/control-header gate; all 12 ASR Plus self-test entrypoints pass. On November 24, Desktop Commander used Josh's existing authenticated Mac Chrome tab without reading cookies or credentials. The session-authenticated API returned `200`, `ok: true`, response freshness metadata, and Kentucky 674982 plus Phoenix and KY2 status. The dashboard showed all three hubs and all six bridges online with no authentication error. A request with credentials omitted returned `403` (`Login or bearer token required.`), proving unauthorized access remains closed. A non-operational `probe` POST without the required control header returned `403` (`Invalid control request.`); no connect, disconnect, ban, unban, Asterisk, or radio action ran. The live account reported `canModify: true`. Kentucky has no read-only accounts, so the deployed `modifyOk()` boundary was additionally executed read-only in PHP with synthetic Read Only and Read/Modify roles; denial and allowance both passed. The deployed API, `CfgModel.php`, and `UserModel.php` hashes exactly match the tested repository sources. Logout then redirected both tabs to `/asr/user/`, and the same API returned `403`.

Kentucky audit-log rotation is installed: daily, 90 archives, compression, root:root 0640 recreation. Timer active; configuration debug validation passed. No forced rotation performed.

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

1. Audit rotation is configured for 90 daily archives; operator review of retention remains possible.
2. No permanent API bearer token has been issued; create one only when a named integration and owner require it.
3. Remote agents still run as root; Texas requires a dedicated-account feasibility decision; existing hubs need a later compatibility migration.
4. Kentucky is a single aggregation/queue dependency. Hub radio links can continue during a Kentucky outage, but dashboard and remote control fail stale/closed; there is no automatic regional failover.
5. Texas provider capacity, source address, administrator model, retention, incident authority and radio/control policy still require KJ5IRQ agreement.

## Recommended sequence and rollback

Signing cutover is complete. If rollback is needed, stop new controls,
return Kentucky then the affected agent to optional, and preserve queues,
results, archives, audits and ledgers. Restore KY2's backed-up unit only
if necessary.

The Kentucky token stack is installed and live authorization checks passed. Roll back the API source, helper and sudoers as one set to the backed-up session-only API if a regression is found. All test tokens were revoked; no permanent bearer token was issued.

Texas is last and starts telemetry-only in required mode. It receives new HMAC
and SSH keys, a source-restricted forced endpoint, isolated state and no
production radio link. Snapshot rollback and credential revocation must be
demonstrated before any control permission.

## Remaining activation gates

Kentucky's scoped-token implementation is ready for final security acceptance.
Release publication remains separately gated by Beta 8.6.1 qualification under
#129 and any decision to issue a permanent token.
Texas contact/install requires separate operator approval and isolated
qualification. No publication is authorized by this inspection.

## Supporting technical files

- `docs/asrplus-security-audit-253.md`
- `docs/asrplus-security-activation-gates.md`
- `docs/asrplus-live-deployment-inventory-2026-11-23.md (November 24 update appended)`
- `docs/asrplus-command-integrity-test-report.md`
- `docs/texas-hub-674984-readiness-254.md`
- `docs/asrplus-security-inspection-evidence-2026-11-24.json`
