# ASR Plus security audit — issue #253

Audit date: 2026-11-23 (America/Phoenix)

Target design: Texas hub 674984

Deployment status: development only; no live configuration was changed

## Evidence and limits

This audit compared `origin/main`, historical deployment commit `75421da`, the
`fix/asrplus-tx-relay-reliability` worktree, and a read-only inventory of the
Kentucky production host `nth-serv-p01` (`44.30.157.21`). The Kentucky check
read file metadata/hashes, sudo authorization, systemd unit definitions,
redacted authorized-key options, and non-secret program source. It did not
invoke a control, read a private key, change a queue, or restart a service.

Kentucky 2 (KY2, node 674980; legacy internal identifier `georgia`) is at
`44.30.157.22`, hostname `NTH-SERV-P02`. Its ED25519 SSH host key was
independently checked by the operator on KY2 and matches the pinned key:
`SHA256:a5I8wvynj6X5DMOCsXUVnM0MbdpZpJhoBXfyuC17fJs`. Strict host-key
checking succeeds. The previously inspected `45.76.255.39` is **not KY2**;
its host-key mismatch must not be attributed to KY2 or used to block KY2.
The MasterHub still contains a separate legacy `georgia` forced-command SSH
entry restricted to `45.76.255.39`; its use and revocation need review.

`origin/main` is the development baseline. It contained only the ASR Plus
browser JavaScript before this work. The deployed API, root control helper,
remote endpoint, sudoers rule, services, and agent source existed only in
history/other worktrees and on production. Production therefore could not be
reproduced or security-reviewed from `main`; this change restores the API and
security components needed for review, but does not deploy them.

## Authentication and authorization

| Boundary | Production behavior | Risk | Least-privilege requirement |
| --- | --- | --- | --- |
| Browser/API read | Valid AllScan PHP session required | All authenticated users can see every hub; no machine credential | Session or scoped bearer; bearer must be HTTPS-only and node-bound |
| Browser/API control | `modifyOk()` plus `X-ASRPlus-Requested-With: control` | Coarse AllScan role grants every ASR Plus hub operation available to the helper | Separate permission per operation and explicit hub scope |
| Apache to helper | `www-data` may run `/usr/local/sbin/asrplus-control` as root without a password | Web compromise reaches a root command broker | Root-owned non-writable helper, closed action vocabulary, strict validation and complete audit |
| Remote agent | Root service polls over SSH and executes fixed Asterisk commands | Agent/key compromise can control its hub and falsify telemetry/acknowledgments | Dedicated service identity where feasible; one hub identity; no general shell; queue TTL and signed/bound acknowledgments |
| Kentucky endpoint | Root forced command with `restrict` and source-IP `from=` constraints | Key compromise can submit status, poll commands, acknowledge results, and read admission state for that hub | Preserve forced command, source restriction, strict host key checking, per-hub key, bounded payloads |
| AI/automation | No scoped API credential existed | Pressure to distribute SSH/root keys | Use scoped API tokens only; never issue SSH keys to AI agents |

The new token implementation stores only SHA-256 digests of 256-bit random
secrets, defaults to `status:read`, requires an explicit node list and expiry,
supports immediate revocation, and logs allowed and denied authentication.
Apache is authorized to run exactly `asrplus-token authenticate`; token create,
list, and revoke remain root-operator commands.

## Available control operations

| Interface operation | Target/effect | Production authorization | Token permission |
| --- | --- | --- | --- |
| Status | Read one or all hub snapshots, connections, talkers and bridge summaries | Authenticated session | `status:read`, exactly one authorized hub per request |
| Connect | Asterisk `rpt cmd <local> ilink 3 <remote>` | `modifyOk()` | `control:connect` |
| Permanent connect | Asterisk `ilink 13` | `modifyOk()` | `control:connect` |
| Disconnect | Asterisk `ilink 11` | `modifyOk()` | `control:disconnect` |
| Ban list/result | Read local or queued remote moderation result | `modifyOk()` in the current POST API | Not exposed to tokens |
| Ban | Queue or execute AllStar node/callsign or EchoLink callsign ban | `modifyOk()` | `control:ban` |
| Unban | Queue or execute removal of the selected ban | `modifyOk()` | `control:unban` |
| Remote status | Agent publishes bounded connection/talker/bridge data | Per-hub forced SSH key | Not exposed to dashboard tokens |
| Remote sync/poll | Publish status and lease the next queued command | Per-hub forced SSH key | Not exposed to dashboard tokens |
| Remote acknowledge | Store outcome for one inflight command ID | Per-hub forced SSH key | Not exposed to dashboard tokens |
| Admission-state read | Empty forced command returns the central admission JSON | Per-hub forced SSH key | Not exposed to dashboard tokens |

Dashboard tokens deliberately do not grant configuration edits, software
updates, service restarts, arbitrary commands, shell access, queue access, or
credential access.

## Findings

### High

1. **Production implementation was absent from `main`.** Deployed behavior had
   drifted beyond historical source: the production control helper includes
   aliases, moderation actions, queue prioritization, and a second Kentucky
   address not represented by the canonical branch. Security fixes could not
   be reliably built from or reviewed on `main`.
2. **Authorization was role-wide, not operation- or node-scoped.** Any session
   satisfying `modifyOk()` could request every supported hub action. There was
   no expiration or independent revocation mechanism for automation.
3. **Remote command integrity depends entirely on the root SSH key and local
   filesystem.** Queue records are unsigned and have no execution deadline.
   A compromised per-hub key can lease commands and provide a false result for
   an inflight ID. The SSH forced command limits this to the assigned hub but
   does not cryptographically bind the acknowledgment to command content.

   **Development remediation:** PR #255 now signs each remote command with a
   per-hub 256-bit HMAC key, enforces a maximum five-minute validity window
   (two minutes by default), verifies the envelope at Kentucky and the agent,
   binds signed acknowledgments to the exact command digest, rejects conflicting
   duplicates, and uses a durable execute-once ledger. This finding remains a
   deployment blocker until both existing agents and Kentucky complete the
   staged required-mode cutover.

### Medium

1. Remote agents run as root, read an AMI secret from `manager.conf`, invoke
   Asterisk, and hold a root-owned SSH private key. `NoNewPrivileges`,
   `ProtectSystem=strict`, strict host-key checking, and forced commands reduce
   exposure, but a dedicated agent account and narrowly permissioned Asterisk
   control socket would be safer.
2. Production queue entries do not expire and inflight recovery is not
   explicit. PR #255 adds expiry, one-command leases, safe redelivery, invalid
   and stale quarantine, bounded legacy draining, and signed idempotent
   acknowledgment. Production remains unchanged until separately approved.
3. The production sudo inventory for `www-data` is broad because the full ASR
   application exposes many separate root helpers. This audit found the ASR
   Plus helper itself root-owned and non-writable, but the overall web-to-root
   attack surface must be reviewed as a separate hardening task.
4. The custom request header blocks simple cross-site forms but is not a CSRF
   token. Cookie controls depend on the AllScan session configuration. Bearer
   requests avoid cookie CSRF and are independently authenticated.

### Positive controls observed

- Production helper/API files are owned by root and not writable by Apache.
- The ASR Plus sudoers file is mode `0440`; the helper uses an argument-vector
  subprocess for Asterisk rather than a shell.
- Remote SSH uses `BatchMode`, `IdentitiesOnly`, `StrictHostKeyChecking`, and a
  dedicated known-hosts file.
- Kentucky authorized keys use `restrict`, a forced command, and source-IP
  restrictions for Phoenix and KY2.
- Endpoint payloads are size bounded, validate hub/node/timestamps, sanitize
  connection/talker fields, and atomically write root-controlled state.
- Systemd services use `NoNewPrivileges`, `PrivateTmp`, and read-only system
  views. The status builder has an explicit narrow writable path.

## Texas agent outbound SSH destination

The Texas agent connects outbound to the Kentucky MasterHub forced-command
endpoint. The `root@44.30.157.21` destination in the example is the remote
MasterHub endpoint, not a proposed interactive root login on Texas. Its
per-hub key must be restricted with `restrict`, a Texas source-IP `from=`
constraint, and forced command `asrplus-remote-endpoint texas`; no shell, PTY,
forwarding, or arbitrary command is permitted. Texas's local service identity
is a separate concern and must be minimized independently before installation.

## Texas least-privilege baseline

1. Texas gets its own OS service identity, SSH key, forced-command entry,
   source address restriction, known-hosts file, queue/status/result directory,
   and node mapping for 674984. Never reuse Phoenix or KY2 keys.
2. The agent may read only the Texas Asterisk status/AMI data it needs, write
   only its runtime directory, connect outbound only to the Kentucky endpoint,
   and execute only the enumerated Asterisk link commands.
3. Commands include an issue-time, expiry, immutable signed content, nonce,
   key ID, and digest. The agent rejects expired, modified, or mismatched
   commands; acknowledgments bind to the complete command digest. Texas uses
   required mode from first installation. Existing hubs use optional mode only
   during the documented migration window, then switch to required mode.
4. Start Texas in telemetry-only mode. Enable connect, disconnect, ban, and
   unban individually only after their positive and negative tests pass and
   KJ5IRQ approves the operator agreement.
5. Human or automation access uses short-lived dashboard tokens. Default scope
   is `status:read` on `texas`; no SSH credentials are issued to AI agents.
