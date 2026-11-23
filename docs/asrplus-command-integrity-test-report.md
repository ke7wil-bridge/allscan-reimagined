# ASR Plus security test report

Date: 2026-11-23 (America/Phoenix)

Scope: local worktree only. No production command, queue injection, deployment,
restart, credential, radio link, firewall or Texas host was used.

## Result

All 12 ASR Plus self-test entrypoints passed on the completion branch:

```text
asrplus-agent-mode-self-test.py                    PASS
asrplus-ban-result-self-test.py                    PASS
asrplus-command-integrity-self-test.py             PASS
asrplus-directory-permissions-self-test.py         PASS
asrplus-import-layout-self-test.py                  PASS
asrplus-ky2-identity-migration-self-test.py         PASS
asrplus-ky2-watcher-self-test.py                    PASS
asrplus-post-command-status-self-test.py            PASS
asrplus-required-mode-readiness-self-test.py        PASS
asrplus-staging-config-self-test.py                 PASS
asrplus-texas-agent-config-self-test.py             PASS
asrplus-token-self-test.py                          PASS
```

Python compilation passed for the agent, control helper, endpoint, token
helper, integrity module and KY2 migration utility. `php -l` reported no syntax
errors in `ops/asrplus/api.php`. The staged manifest and inspection-evidence
JSON validated. `systemd-analyze verify` accepted the KY2 control drop-in when
composed with the repository agent unit and a harmless local `/bin/true`
ExecStart substitute; the development machine emitted only unrelated removed
`CPUAccounting` warnings from vendor units. No unit was started.

## Security coverage

| Area | Positive cases | Negative and recovery cases |
| --- | --- | --- |
| Command envelope | Valid signed command verifies | Modified action/node, wrong hub/key, invalid signature, future/expired window |
| Key handling | Root-only regular 256-bit key loads | Missing, short, group-readable and symlink keys fail closed |
| Acknowledgment | Bound signed result stores | Forged result/signature, wrong digest/hub and conflicting duplicate rejected |
| Replay | Exact retry returns cached acknowledgment | Changed-content ID and interrupted execution refuse repeat |
| Queue | Signed lease/ack and exact recovery | Invalid/stale work quarantined; fresh inflight blocks concurrency; full queue rejected |
| Agent outage | Normal sync reaches execution | Kentucky connection failure never reaches execution |
| Required mode | Signed Phoenix/KY2/Texas config parity and reversible mode-only plan | Unsigned command/ack rejected; key ID/path drift detected |
| SSH | Strict host key, dedicated known-hosts, no forwarding/TTY asserted | No host-key bypass path |
| API token | Scoped read/control authentication | Altered, expired, revoked, HTTP, cross-node and missing-permission denied |
| Hub compatibility | Phoenix outbound and KY2 masterhub-pull modes preserved | Invalid heartbeat/publish mode rejected; KY2 watcher remains keyless |
| Texas | Endpoint, node, required mode, HMAC/SSH separation and central parity | No production contact or installation |

## Compatibility conclusion

Existing Phoenix and KY2 optional-mode behavior remains intact. Signed work is
always verified even in optional mode, and unsigned compatibility work remains
bounded and deduplicated. The prepared required-mode change alters only mode
fields and preserves key IDs and paths; rollback restores the exact optional
configuration. KY2's staged systemd hardening does not override ExecStart,
timer, watcher, node, SSH, Asterisk or routing configuration.

Production optional-mode health was inspected separately and is not inferred
from these local tests. Required-mode activation, token installation and Texas
qualification remain separate approval gates.
