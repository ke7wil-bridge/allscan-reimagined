# Issue #331: provisioning response transport

## Failure and scope

The installed broker matched main at ae322b2 (SHA-256
e05e77e4fa1027f83869e5add9ce0d4d99036fb33b93b00d5f6011b4b6ddf7c0).
On 2026-11-27 the first socket-activated instance started at 06:20:20 MST
and failed at 06:20:23 with Python shutdown exit 120 (broken stdout pipe).
Docker finished initialization at 06:20:22. Apache started at 06:20:20
and successful fast activity GET requests were logged around the incident.
This supports a startup race as a possibility, not a proven caller or timeout. The setup/control/update
socket clients have 1200/120/30-second timeouts respectively; higher-level
callers can abandon requests earlier (for example, the PHP diagnostic
command wrapper enforces a four-second timeout). The socket remained active with no
refusals (3879 accepted at inspection). Do not increase timeouts or retry
operations without evidence.

The unchanged broker contract reports operation outcome in the JSON
exitCode field, not the one-shot process's exit status. A disconnected
peer receives no complete response; the fixed diagnostic includes only
the numeric operation result, with no payload, command arguments or
helper output. Unexpected execution errors, timeouts and non-peer write
errors still propagate; delivered helper errors retain their exact
response. The broker does not retry any request. Clients must reconcile
an uncertain mutating outcome before considering another operation.

The broker now flushes stdout within the protected write path and uses
a byte-counted write loop (including short writes in unbuffered mode).
EPIPE/ECONNRESET redirects only stdout to /dev/null to prevent another
flush against the closed socket during interpreter shutdown. All
authorization, profile validation, helper routing, request/response
limits and execution timeouts are unchanged.

## Regression

Run the existing provisioning and updater suites from the candidate
worktree. The provisioning suite now covers actual Unix socketpairs,
process exit, normal/error responses, authorization, abandoned clients
before/during responses, buffered/unbuffered stdout, helper failure,
timeout, unrelated I/O failure and a successful subsequent request.
The client tests also assert that empty/truncated replies to mutating
setup, bridge-control and updater requests fail without retransmission.

    python3 scripts/asr-container-provisioning-self-test.py
    python3 scripts/asr-container-updater-self-test.py
    python3 scripts/asr-container-updater-docker-self-test.py
    sh scripts/asr-asterisk-read.sh --self-test

## Isolated acceptance evidence (2026-11-27)

- Existing provisioning and updater self-tests: PASS.
- Disposable real-Docker updater acceptance: PASS.
- Restricted native Asterisk reader self-test and Python syntax: PASS.
- Actual systemd socket activation in a disposable Debian QEMU overlay
  with restricted networking: baseline produced exit 120 and a failed
  service instance; candidate logged the abandoned response and
  deactivated successfully without a failed instance. Both sockets
  handled the next request. This VM case used malformed/empty input
  (no privileged operation); success, authorization and helper outcomes
  are covered by the separate real-socket fixture tests.
- The initial VM harness attempt failed while polling for journal records
  before they existed. The corrected harness passed. Both VM runs
  powered off; no live host unit or file was modified.
- Checksum-verified atomic replacement and rollback rehearsed in a
  disposable directory: PASS (original bytes and mode restored).
- Candidate broker SHA-256:
  66005e1c7cd014d94539926440a799b86ccaf679d5788cdb101145d18b2eb8b3.
- Lab script and serial evidence retained on the development machine:
  `~/vms/asr-qa/issue331/socket-qa.py` and `serial2.log`.

## Integration and owner-approved deployment only

Keep #316's host-helper migration separate. When both branches are
integrated, confirm the host-install/update bundle contains this broker
and rerun combined provisioning/updater tests. No #327 frontend change.

Before an approved host deployment, recheck the installed broker hash
against the expected current baseline and check the latest #316 work
for overlapping host-install changes. If the hash differs, stop and
reconcile; never replace a newer broker blindly. Back up the installed
file with metadata (cp -a) into a new root-only directory, record its
SHA-256 and verify the backup matches the installed file. Stage the
candidate in the same installed directory with the existing owner,
group and mode, validate Python syntax, and atomically rename it over
the installed broker. The socket-activated one-shot service uses the
file on each new invocation; no socket or container restart is required
for this broker-only change.

Verify installed hash and metadata, an authorized read-only request,
subsequent socket health and journal. Do not induce disconnects against
the live host as a substitute for isolated testing. Preserve the prior
failed unit and journal until owner-approved post-deployment verification.
Rollback on regression: checksum-verify the backup, stage a metadata-
preserving copy in the installed directory, atomically rename it over the
candidate, then confirm the original hash and read-only request health.
Do not reset unrelated service failures. No deployment, merge or release
is authorized by this document.
