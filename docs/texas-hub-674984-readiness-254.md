# Texas hub 674984 readiness and installation checklist — issue #254

Status: design/review package only. No Texas or existing production hub was
deployed or modified.

## Current distributed-hub architecture

Kentucky node 674982 is the ASR Plus aggregation and command-queue authority.
Phoenix 641894 and Georgia 674980 run local agents that collect Asterisk and
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

- [ ] Run unit/security tests for invalid hub/node/action, oversized payload,
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
