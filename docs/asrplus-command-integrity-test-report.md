# ASR Plus command-integrity security test report

Date: 2026-11-23 (America/Phoenix)

Scope: local development branch `security/252-254-texas-hub`, draft PR #255.
No production command, deployment, restart, credential, queue, or radio link
was used by these tests.

## Implemented security contract

- Per-hub HMAC-SHA-256 keys with owner-only file permission validation.
- Canonical signed command content, key ID, 128-bit nonce, SHA-256 digest,
  issue time, and bounded expiry (120-second default; 300-second maximum).
- Verification at Kentucky before leasing and again at the remote agent.
- Signed acknowledgment bound to hub, command ID, command digest, key ID,
  completion time, result content, and independent nonce.
- Root-only `0700` queue/result/archive/ledger directories and atomic `0600`
  command, result, and ledger files.
- One inflight lease per hub, safe redelivery after a Kentucky connection loss,
  expiry/quarantine, and queue saturation rejection.
- Durable agent execute-once ledger. Completed acknowledgments are resent
  exactly; an interrupted execution is reported uncertain and never repeated.
- Optional legacy mode solely for staged Phoenix/Georgia migration. Legacy
  queue entries are bounded to five minutes. Required mode rejects all unsigned
  commands and is mandatory for Texas from first installation.
- Strict SSH host-key checking, dedicated known-hosts file, forced-command hub
  identity, and systemd filesystem/network sandboxing remain enforced.

## Automated cases

| Area | Positive cases | Negative/failure cases |
| --- | --- | --- |
| Command envelope | Valid hub command verifies | Modified action/node, wrong hub, wrong key, invalid signature, expiry |
| Acknowledgment | Correct result verifies and stores | Modified result/signature, wrong digest/hub, conflicting duplicate |
| Replay/duplicate | Exact retry returns cached acknowledgment | Same ID with changed content; interrupted prior execution refuses repeat |
| Queue | Signed command leases and acknowledges | Invalid file, tampering, stale command, full queue, unsigned required-mode command |
| Recovery | Expired lease safely redelivers; exact ACK retry is idempotent | Fresh inflight blocks concurrent lease; expired work is archived |
| Agent | Authorized action executes once | Wrong hub, expired/unsigned command, Kentucky outage; no execution on failed sync |
| Telemetry compatibility | Existing talker key/unkey history and bounded status sanitation pass | Stale/invalid identity rules remain in the existing collectors |
| Compatibility | Optional endpoint and agent accept/deduplicate bounded legacy commands | Required mode rejects legacy command and acknowledgment |
| API token | Valid scoped read/control grants | Altered, expired, revoked, cross-node, HTTP, missing-permission requests |
| SSH/config | Strict host-key and dedicated known-hosts options asserted | Georgia host-key mismatch remains an external verification blocker |

## Commands and results

```text
python3 scripts/asrplus-command-integrity-self-test.py
ASR Plus scoped token security contract: PASS
ASR Plus command-integrity security lifecycle: PASS

python3 -m py_compile [ASR Plus security Python sources]
PASS

php -l ops/asrplus/api.php
No syntax errors detected
```

`systemd-analyze verify` accepted the unit syntax. The development machine
reported expected environment warnings because `/usr/local/sbin/asrplus-agent`
and `asterisk.service` are not installed there; no unit was started.

## Compatibility result

The upgraded agent accepts the existing unsigned format only when explicitly
configured `optional`, applies the same hub/node/action validation, and records
it in the execute-once ledger. Signed commands always require a valid signature
even in optional mode. The Kentucky endpoint accepts an unsigned acknowledgment
only for an unsigned legacy inflight command. This permits agent-first rollout
without allowing downgrade of a signed command.

The existing production agents themselves were not modified or restarted.
Georgia could not be inspected because its changed host key was not bypassed.
Live compatibility remains a staged deployment acceptance test, not a result
claimed from local simulation.
