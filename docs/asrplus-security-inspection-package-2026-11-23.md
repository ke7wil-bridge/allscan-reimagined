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

Existing browser-session authentication and dashboard control code paths were preserved. PR #272 adds regression assertions for common.php initialization, UserModel::validate(), valid user detection, login-required fallback, and the existing modifyOk()/control-header gate; all 12 ASR Plus self-test entrypoints pass. A live authenticated browser-session regression test remains outstanding because the connected Mac Chrome profile has no ASR login cookies. No credentials or cookie values were accessed.

The audit log is root-owned mode 0640, but no logrotate rule currently exists. The log is small today (3.4 KB); retention/rotation should be agreed by operators before configuring it. Suggested starting point: daily compressed rotation, 90-day retention, root:root 0640.

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

1. Authenticated browser-session compatibility needs a live browser regression test; source-level regression guards now pass.
2. Audit-log rotation/retention policy is not configured; agree retention with operators before installing logrotate.
3. No permanent API bearer token has been issued; create one only when a named integration and owner require it.
4. Remote agents still run as root; Texas requires a dedicated-account feasibility decision; existing hubs need a later compatibility migration.
5. Kentucky is a single aggregation/queue dependency. Hub radio links can continue during a Kentucky outage, but dashboard and remote control fail stale/closed; there is no automatic regional failover.
6. Texas provider capacity, source address, administrator model, retention, incident authority and radio/control policy still require KJ5IRQ agreement.

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

Follow docs/asrplus-https-readiness.md after DNS propagates. Validate trusted
TLS and certificate renewal before installing tokens. Verify session compatibility,
scope isolation, expiry and revocation on the live API. Texas contact/install
requires separate operator approval. Beta 8.6.1 release qualification remains
separate under #129; no publication authorized.

## Supporting technical files

- `docs/asrplus-security-audit-253.md`
- `docs/asrplus-security-activation-gates.md`
- `docs/asrplus-live-deployment-inventory-2026-11-23.md (November 24 update appended)`
- `docs/asrplus-command-integrity-test-report.md`
- `docs/texas-hub-674984-readiness-254.md`
- `docs/asrplus-security-inspection-evidence-2026-11-23.json`
