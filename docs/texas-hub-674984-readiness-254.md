# Texas hub 674984 readiness and installation checklist — issue #254

Status: design/review package only. No Texas or existing production hub was
deployed or modified.

## Current distributed-hub architecture

Kentucky node 674982 is the ASR Plus aggregation and command-queue authority.
Phoenix 641894 and KY2 674980 (legacy `georgia` identifier) run local agents that collect Asterisk and
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

## Required deployment order

The order is security-critical. Kentucky must not begin issuing signed
commands until both existing remote agents can verify them.

1. **Preflight and backup only after explicit approval.** Record current hashes,
   back up each source/config/unit/queue/result file with owner and mode, verify
   free space and clocks, and run the local security tests. Generate distinct
   Phoenix, KY2, and Texas 256-bit HMAC keys outside the repository and
   chat. Each key file is owner-only `0600`; never reuse a hub key.
2. **Phoenix agent first.** Install the shared integrity module, upgraded agent,
   Phoenix key, optional-mode config, and hardened units. Reload/restart only
   the ASR Plus agent/watcher after an action-time approval. Verify telemetry,
   strict Kentucky host-key pinning, legacy compatibility, ledger permissions,
   and no radio-link change.
3. **KY2 agent second.** KY2 at `44.30.157.22` has a verified host key
   (`SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`). Preserve its
   split control/watcher services, `masterhub-pull` mode and one-second talker
   heartbeat. Use a distinct KY2 signing key. Do not confuse KY2 with the
   unrelated `45.76.255.39` legacy Georgia endpoint.
4. **Kentucky central components.** With both agents in optional mode, install
   the shared module, signed control helper, verifying forced-command endpoint,
   central per-hub key configuration, API/token files, and log rotation. Back
   up queues before replacement. Existing unsigned entries may drain for at
   most five minutes; every newly created command is signed.
5. **Non-mutating protocol qualification.** Use a supervised remote `ban-list`
   request to prove signed delivery and acknowledgment without changing radio
   links. Test tamper/expiry/cross-hub rejection with offline fixture files,
   not production queue injection. Confirm logs and exact duplicate retry.
6. **Close the compatibility window.** After all legacy queue and inflight
   files are resolved, change Phoenix and KY2 agent and Kentucky hub modes
   from `optional` to `required`. Recheck telemetry and a read-only signed
   lifecycle. Optional mode is temporary and must not remain enabled.
7. **Texas telemetry-only installation.** Texas begins in `required` mode with
   a dedicated key, SSH key, forced-command entry, source-IP restriction,
   ledger, queue, status, and result paths. Do not enable command polling or a
   production radio link until its VM/network review and KJ5IRQ agreements are
   complete.
8. **Incremental control approval.** Enable supervised disconnect first, then
   connect, ban, and unban only through separate recorded approval and tests.

## Nodes requiring updates

| Node | Required package changes | Compatibility mode |
| --- | --- | --- |
| Kentucky 674982 | Shared integrity module, signing control helper, verifying SSH endpoint, central API/token files, per-hub key config, audit rotation, Texas forced-command entry when approved | `optional` for Phoenix/KY2 only during drain; then `required` |
| Phoenix 641894 | Shared module, execute-once agent, Phoenix-only key/config, hardened agent and watcher units | Install `optional` first; change to `required` after Kentucky verification |
| KY2 674980 (legacy `georgia` identifier) | Same as Phoenix with a distinct KY2 key | Host key verified; split-service compatibility gate remains; `optional` to `required` |
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
