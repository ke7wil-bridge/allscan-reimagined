# Texas 674984 isolated preapproval qualification report

Date: 2026-11-24 (America/Phoenix)
Issue: #254
Branch: `test/254-texas-isolated-qualification`
Baseline: merged `main` commit `63a32f8` (PR #277). Final implementation commit:
`890ef91898c0b63eb9ffddef28cba20683638c61`.

## Scope and conclusion

The repository implementation is qualified in disposable local fixtures for
security, admission policy, queue bounds, telemetry boundaries, recovery and
installation rollback. No Texas host was contacted. No production credential,
radio/Asterisk control, live queue, service, firewall or configuration was
used or changed. This report is not Texas VM or provider hardware certification.

The proposed Texas policy admits 60 non-loopback inbound radio channels and
rejects the 61st. Local IPv4 loopback bridges and outbound links are excluded. Inbound non-loopback inter-hub links are counted by the proposed dialplan; verify actual link direction and admission on the host. ASR Plus displays 60. This is separate from the operational command
queue ceiling of 80 and implementation hard stop at 100.

## Test host and fixture

- Host: `josh-Latitude-5420`, Linux 7.0.0-43-generic, x86_64.
- CPU: 11th Gen Intel Core i5-1145G7, 8 logical CPUs.
- Memory observed before soak: 6.4 GiB total, 2.6 GiB available.
- Filesystem observed before soak: 233 GiB, 98 GiB available, 57% used.
- Fixture: Python standard-library temporary directories and mocks; no VM,
  container, real SSH connection, Asterisk process or systemd action.
- Software: repository source at the baseline above plus the branch diff;
  Python/platform and final metrics are recorded in the JSON evidence.

## Results

The final short qualification run passed 35 checks. It
covered:

- admission 1–60, rejection 61, slot recovery, simultaneous attempts, local
  bridge exclusion and outbound/inter-hub exclusion;
- distinct Texas HMAC identity, wrong-key/cross-hub rejection, signed command
  and acknowledgment, tamper rejection, exact duplicate behavior, execution
  ledger and interrupted-execution fail-closed behavior;
- telemetry-only operation without control polling, identity/freshness,
  monotonic sequence, 200-row sanitation, exact 4 MiB input boundary and
  ±120-second clock tolerance with ±121 seconds rejected;
- lost acknowledgment, stale inflight/duplicate recovery, malformed input,
  queue behavior at 80 and hard rejection at 100, Kentucky/DNS/SSH-style
  transport failure before execution, disk-full/atomic-write preservation;
- scoped-token read/control boundaries, cross-node denial, HTTPS enforcement,
  expiration, revocation, replacement-token rotation and audit logs without
  bearer secrets;
- guarded installation, repeated/idempotent installation, backup integrity,
  full rollback, injected partial-failure rollback and emergency telemetry-only
  isolation;
- dedicated agent and talker-watcher service account, source-restricted forced
  SSH endpoint, strict systemd/filesystem sandbox and validated narrow local-
  control helper.

The final-code quick-gate evidence is
`docs/texas-674984-isolated-qualification-supplemental-2026-11-24.json`.

All 12 pre-existing ASR Plus security self-test entrypoints also passed after
the implementation changes. Python compilation, PHP syntax, JSON validation,
systemd unit composition, secret scanning, `pnpm lint`, `pnpm build`, the full
Beta 8 bridge/Talker gate and the exact `build-release.sh` repository release
gate passed. The local Beta 8.6.1 validation archive SHA-256 was
`4f3c7d85b6bfae66e4a52050af6b3803724ff6a21a1544daf234ac9783c7ab47`;
it was not published or deployed.

## Sustained synthetic telemetry

The required 30-minute run uses 200 sanitized connection records every five
seconds and checks every stored sequence/freshness value. Final elapsed time,
sample count, process CPU, maximum RSS, swap start/end, disk bytes, median and
maximum write latency are recorded in
`docs/texas-674984-isolated-qualification-evidence-2026-11-24.json`.

Acceptance requires at least 1,800 elapsed seconds, no sequence/freshness
failure, process CPU below 70%, and no swap growth attributable to the fixture.
These are development-host measurements only. They do not establish Texas
provider capacity or authorize a 60-client production load test.

An initial 1,800.45-second attempt completed the telemetry workload but failed
its final assertion because the first harness version measured whole-host swap;
other concurrent VS Code jobs changed that global value even though the
qualification process showed `VmSwap: 0 kB`. That attempt is recorded as a
failed measurement, not a pass. Commit `890ef91` changes the acceptance metric
to the qualification process's own `VmSwap`; the full-duration rerun supplies
the authoritative evidence below.

**Corrected full-duration result: PASS.** 1,800.002 seconds, 361 samples
of 200 records, 0.051% process CPU, 44,008 KiB maximum RSS, zero process
swap at start and finish, 25,952 bytes final fixture disk usage, median
write latency 2.143 ms and maximum 8.868 ms. All 35 checks passed.
These measurements are from the disposable development fixture only.

## Failure and recovery interpretation

Transport failures stop before command execution and retry only on the normal
timer cadence. Exact acknowledgments and ledger entries are reusable; changed
duplicates are rejected. Fresh inflight work blocks concurrency, expired work
is archived and receives a failure result, and an interrupted `executing`
ledger entry becomes an explicit uncertain failure rather than executing twice.
Atomic replacement leaves the previous file intact on simulated write failure.
Stale telemetry is unavailable/offline and retains the configured capacity.

The fixture models DNS failure, timeout, packet loss and network interruption
as failed bounded SSH transactions because the security property is identical:
no response means no lease/execution. It does not emulate kernel packet timing
or provider networking. A supervised Texas preflight must still verify actual
DNS, MTU, latency and source-address behavior.

## Least privilege conclusion

Texas can run telemetry as a non-login `asrplus-agent` account with no
capabilities, strict filesystem protection, owner-only state and read-only
configuration. A distinct AMI file and Asterisk socket group are required. The
narrow helper accepts only validated connect/disconnect arguments for local node
674984; telemetry-only mode never invokes it. No sudo rule is installed. If
Texas cannot expose the control socket safely to the service group, control
activation remains blocked pending an explicit alternative; root service or a
shell-capable key is not an acceptable fallback.

## Remaining external qualifications

Only operator/access-dependent work remains: the ten approval gates, actual
Texas hardware/VM measurements, host-specific source IP and firewall, unique
credential creation/distribution, provider snapshot/rollback, telemetry-only
installation, supervised radio/audio and 60/61 admission, and separately
approved disconnect/connect/ban/unban tests. No automatic load balancing,
geographic routing or failover is implemented or claimed.
