# Texas hub 674984 readiness and installation checklist — issue #254

Status: design/review package only. No Texas or existing production hub was
deployed or modified.

## Current distributed-hub architecture

Kentucky node 674982 is the ASR Plus aggregation and command-queue authority.
Phoenix 641894 and KY2 674980 run local agents that collect Asterisk and
bridge telemetry, initiate outbound SSH to Kentucky, publish status, poll a
hub-specific queue, execute validated local Asterisk link commands, and return
an acknowledgment. Kentucky builds the combined private dashboard snapshot.

Radio routing is still the routing configured in AllStar/Asterisk. ASR Plus
observes it and can request link changes; it is not a routing plane. The
repository and production evidence do **not** establish automatic traffic
balancing, automatic geographic admission, quorum, leader election, or
automatic service failover. Kentucky is currently a single aggregation and
queue-control dependency. Agent timers provide telemetry retry, not hub
failover. Texas must initially be treated as another explicitly linked remote
hub, not as an automatically balanced replacement.

## Trust boundaries and network requirements

| Flow | Direction | Port/protocol | Requirement |
| --- | --- | --- | --- |
| AllStar hub links | Texas to/from approved peers | UDP 4569 by default; confirm actual `iax.conf` | Restrict to required peers where operationally possible; rate limit and log drops |
| ASR Plus agent | Texas outbound to Kentucky | TCP 22 | Dedicated per-hub key, strict pinned host key, forced command, Kentucky `from=` restriction |
| Operator dashboard | Operator to dashboard | TCP 443 | TLS only; MFA/VPN where available; no public token use over HTTP |
| System administration | Approved operators to Texas | TCP 22 or Tailscale | Allowlisted management network/Tailscale only; no password login; no root login from the public Internet |
| Asterisk AMI | Local agent to Asterisk | TCP 5038 loopback only | Never expose off-host; dedicated read/event identity plus only required actions |
| DNS/NTP/updates | Texas outbound | UDP/TCP 53, UDP 123, TCP 443 as required | Constrain to approved resolvers/time sources/repositories |
| Digital bridge services | Mode-specific | Deployment-specific | Keep closed until each bridge plan identifies exact peer IPs and ports |

Default host firewall policy should deny unsolicited inbound traffic. Do not
open a broad range for anticipated future bridges. Add each mode only from its
reviewed runtime plan. Tailscale is optional, but if used it is the preferred
management path; ACLs must name KJ5IRQ and the minimum Netoholics operators,
and subnet routing/exit-node features remain disabled unless separately
approved.

## VM and host isolation

Texas should be a dedicated VM with no co-resident customer workload. Allocate
fixed CPU/RAM/storage, enable provider console access for recovery, and take a
provider snapshot immediately before the first approved ASR installation.
Separate `/var/lib/asrplus`, ASR runtime state, logs, and secrets from the web
document root. Secrets are root-owned `0600`; service configuration is not
writable by Apache. Disable password SSH, agent forwarding, X11 forwarding,
and general shell access for service keys. Do not install AI-agent SSH keys.

Run the remote agent under a dedicated account once Asterisk socket/AMI access
is narrowed enough to avoid root. Apply `NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=strict`, `ProtectHome=true`, explicit `ReadOnlyPaths`, explicit
`ReadWritePaths`, a capability-free bounding set, and network address-family
restrictions consistent with outbound SSH plus loopback AMI.

## Texas agent SSH endpoint and key configuration

The `server` field in `ops/asrplus/texas-agent.json` is the agent's outbound
SSH destination on Kentucky MasterHub. It is **not** an instruction to permit
interactive root login to the Texas host. The current MasterHub endpoint is
`root@44.30.157.21`; this root account is used only with the dedicated
per-hub forced-command key. On MasterHub, that key must be installed with
`restrict`, `from="<approved Texas source IP>"`, and the forced command
`asrplus-remote-endpoint texas`. It must not permit a shell, PTY, forwarding,
agent forwarding, or arbitrary commands. Verify the actual MasterHub address,
source IP, and authorized-key line during host-specific preflight.

The SSH private key (`/etc/asrplus/ssh/texas-to-kentucky`) and HMAC signing
key (`/etc/asrplus/command-keys/texas.key`) are separate credentials. The
agent's HMAC path now matches the central Texas key configuration exactly.
Do not create either key until the host and operator approvals are complete.

The current systemd agent template still runs as root to access Asterisk and
SSH paths. That is a separate local privilege-hardening item: before Texas
installation, validate a dedicated service account and the minimum AMI/socket
permissions needed to remove root from the local agent. The remote forced
command's root account does not justify an interactive root account on Texas.

## Installation checklist

### Operator decisions before credentials

- [ ] KJ5IRQ confirms Texas node ownership, legal/acceptable-use constraints,
  approved control operators, and whether Netoholics may disconnect or ban a
  station without real-time approval.
- [ ] KJ5IRQ and the Netoholics owner sign off on incident contacts, maintenance
  windows, log retention, evidence sharing, outage notification, and who may
  revoke credentials.
- [ ] Define whether permanent links are allowed and who may create them.
- [ ] Define capacity limits, admission rules, abuse thresholds, and the exact
  conditions under which Texas is isolated from the network.
- [ ] Confirm provider console access, backup/snapshot ownership, monthly cost,
  data jurisdiction, and recovery responsibility.

### Build and hardening

- [ ] Record VM image/version, package sources, hostname, node 674984 identity,
  public and management addresses, and time synchronization source.
- [ ] Patch the base OS; enable unattended security notifications without
  automatic radio-service restarts.
- [ ] Create named human administrator accounts with MFA/VPN controls; disable
  public password/root SSH.
- [ ] Apply the deny-by-default firewall and capture its reviewed rule export.
- [ ] Configure Asterisk/AllStar for 674984 without linking it to production.
- [ ] Create a dedicated Texas agent account, AMI identity, runtime directory,
  SSH key, and known-hosts file. Do not reuse any existing hub credential.
- [ ] Independently verify Kentucky's SSH host key, then pin it. Add a Kentucky
  authorized-key entry with `restrict`, `from="TEXAS_SOURCE_IP"`, and forced
  command `asrplus-remote-endpoint texas` only after endpoint support is merged.
- [ ] Extend the endpoint/config hub map to `texas: 674984`; add isolated queue,
  result, and status directories; keep control polling disabled initially.
- [ ] Install telemetry-only services and verify file ownership, sandboxing,
  sudoers syntax, log rotation, and absence of secrets from web/log output.

### Offline and failure qualification

- [x] Run local unit/security tests for invalid hub/node/action,
  stale/replayed/modified commands, cross-hub poll/ack, forged result, expired
  command, queue saturation, malformed JSON boundaries, lost acknowledgments,
  and stuck inflight recovery. See `docs/asrplus-command-integrity-test-report.md`.
- [ ] Repeat deployment-fixture tests for oversized status payload,
  stale timestamp, replayed sequence, cross-hub poll/ack, forged result, expired
  command, queue saturation, and malformed JSON.
- [ ] Prove a Texas key cannot poll or acknowledge another hub and cannot obtain
  an interactive shell, forwarding, PTY, or arbitrary command.
- [ ] Test token read, cross-node denial, each permission boundary, expiration,
  revocation, audit logging, and HTTPS enforcement.
- [ ] Measure status/queue behavior during Kentucky outage, DNS failure, clock
  skew, packet loss, duplicate delivery, agent restart, and a stuck inflight
  command. No stale command may execute after recovery.
- [ ] Load-test only an isolated fixture: documented connection target, normal
  peak, rejection threshold, CPU/RAM/network limits, and recovery. Never load
  test an existing production hub.
- [ ] Prove manual rollback from the pre-install snapshot and configuration
  backup, including credential revocation.

### Staged integration and go-live gates

- [ ] Obtain explicit action-time approval before any Texas installation or
  any Kentucky authorized-key/endpoint/config change. State commands/effects,
  impact, backup, rollback, and verification.
- [ ] Publish Texas telemetry with no command polling and no production radio
  link. Verify identity, freshness, node filtering, and dashboard isolation.
- [ ] Establish one scheduled, supervised test link using approved test nodes;
  verify connect, disconnect, audio direction, loop prevention, and recovery.
- [ ] Enable only `control:disconnect` first for an approved operator; then
  explicitly approve connect, ban, and unban separately if required.
- [ ] Record the deployed commits/hashes and live acceptance evidence. A merge
  or successful local test is not deployment or acceptance.
- [ ] KJ5IRQ signs the final operator agreement and go-live record.

## Failover statement

If Kentucky aggregation is unavailable, remote radio hubs may continue their
existing Asterisk links, but dashboard freshness and queued control cannot be
assumed. Operators must fail closed: show telemetry stale, stop leasing new
commands, expire pending controls, and use the approved human incident path.
No automatic promotion of Texas is designed or approved by this package.

## Isolated capacity and failure test plan

Run these only against a disposable Texas fixture, never a current hub. Record
the VM size, software commit, latency, CPU, RSS, file growth, queue depth, error
rate and recovery time. The acceptance ceiling is 80 queued commands per hub
(below the implementation's hard limit of 100), status payloads at the enforced
size boundary, and the agreed peak telemetry rate for at least 30 minutes with
CPU below 70%, no swap growth, no dropped audit records and status age below
two heartbeat intervals.

Inject Kentucky unreachability, DNS failure, 30-second clock skew, packet loss,
duplicate delivery, lost acknowledgment, agent restart, stale inflight work,
full queue, malformed payloads and disk-full writes. Acceptance requires no
duplicate execution, no cross-hub access, no expired execution after recovery,
bounded retry without a tight loop, explicit stale dashboard state, intact
ledger/audit history, and successful recovery without clearing state. Provider
capacity and final peak assumptions require KJ5IRQ approval before testing.

## Required deployment order

Authenticated command components and distinct Phoenix/KY2 signing keys are
already installed in optional mode. The remaining order is security-critical:

1. **Required-mode preflight after explicit approval.** Back up current
   configs and unit state, verify empty queues, synchronized clocks, healthy
   services, key-hash parity, fresh telemetry and unchanged links.
2. **Harden KY2 control.** Install and qualify the reviewed systemd drop-in
   without changing its split services, `masterhub-pull` topology, one-second
   heartbeat, Asterisk, or radio routing.
3. **Close the compatibility window.** Change Phoenix and KY2 agents to
   `required`, one at a time, then change Kentucky's matching hub definitions.
   Qualify each with a signed read-only `ban-list` lifecycle. Follow
   `docs/asrplus-security-activation-gates.md` for rollback.
4. **Install Kentucky API tokens as a separate transaction.** Install the API,
   root helper, authentication-only sudoers rule, protected store and audit
   rotation together; test TLS, default read-only scope, node isolation,
   permission denial, expiration and revocation. Do not issue a token until
   its owner and scope are approved.
5. **Texas telemetry-only installation after a third approval.** Texas begins
   in `required` with dedicated HMAC and SSH keys, forced endpoint, source-IP
   restriction, ledger and isolated state paths. Keep command polling and all
   production radio links disabled until offline failure/capacity tests and the
   KJ5IRQ agreements are complete.
6. **Incremental control approval.** Enable supervised disconnect first, then
   connect, ban, and unban only through separate recorded approvals and tests.

## Nodes requiring updates

| Node | Required package changes | Compatibility mode |
| --- | --- | --- |
| Kentucky 674982 | Token API package; later Texas mapping/forced endpoint | Existing signed stack is `optional` for Phoenix/KY2; change to `required` only after remote agents |
| Phoenix 641894 | Config-only required-mode cutover | Signed agent installed; `optional` to `required` |
| KY2 674980 | Systemd control sandbox, then config-only required-mode cutover | Signed agent installed; `optional` to `required` |
| Texas 674984 | Full telemetry agent, Texas-only HMAC and SSH keys, required-mode config/ledger/units, central queue/status/result/config and forced-command identity | `required` from first installation; telemetry-only initially |

Dashboard API tokens exist only on Kentucky. Phoenix, KY2, and Texas never
receive the bearer-token store or token-authentication sudo rule.

## Rollback

- During agent-first optional mode, restoring an old Kentucky central stack is
  protocol-compatible. Restore the backed-up agent source/config/unit as one
  set if an agent regression occurs; do not delete its execution ledger.
- During Kentucky cutover, stop issuing new controls, preserve queue/result
  files, restore the central helper/endpoint/API/config backups together, and
  leave agents in optional mode until legacy behavior is verified.
- After required mode is enabled, first return agents to optional mode before
  restoring any unsigned central implementation. Otherwise commands will fail
  closed, as designed.
- Never clear or edit an inflight file to force retry. Preserve the queue,
  result, archive, security audit, and agent ledger for incident review.
- If any HMAC key may have been exposed, disable control for that hub, revoke
  the key by removing its active configuration, generate a new per-hub key,
  and repeat the agent-first rotation. Key material is not restored from the
  repository.
- Rollback does not change Asterisk links. Verify telemetry, queue depth,
  service state, logs, and a read-only signed lifecycle before resuming control.

## Approval required before installation

Josh must separately approve the exact host-by-host deployment plan after
reviewing the final commit. The approval must name the node(s), files and units
to change, per-hub key creation/transfer method, backup locations, expected
service restarts, maintenance impact, validation commands, rollback commands,
and whether the supervised read-only signed lifecycle may be run. A code-review
approval, PR approval, or merge is not deployment authorization. Texas radio
linking and each control permission require later, separate approval.

## Planned UK expansion (not yet validated)

The UK node is a planned future hub, not part of the current three-hub production security qualification. Its node number, deployment configuration, identity, credentials, routing, security posture and operational readiness must be independently confirmed before installation or activation. UK completion is not a prerequisite to KJ5IRQ reviewing the Texas inspection package.
