# ASR Plus security test report

Date: 2026-11-24 (America/Phoenix)

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

The new `asrplus-security-regression-self-test.sh` CI entrypoint runs those 12
tests plus the disposable Texas qualification harness. The Texas harness passed
35 quick-gate checks; its separate 30-minute evidence covers sustained
telemetry. Token coverage now includes replacement-token rotation after
revocation.

Python compilation passed for the agent, control helper, endpoint, token
helper, integrity module and KY2 migration utility. `php -l` reported no syntax
errors in `ops/asrplus/api.php`. The staged manifest and inspection-evidence
JSON validated. `systemd-analyze verify` accepted the KY2 control drop-in when
composed with the repository agent unit and a harmless local `/bin/true`
ExecStart substitute; the development machine emitted only unrelated removed
`CPUAccounting` warnings from vendor units. No unit was started.

The full Beta 8.6.1 `build-release.sh` gate also passed with the new security
entrypoint included. Its local archive hash was
`4f3c7d85b6bfae66e4a52050af6b3803724ff6a21a1544daf234ac9783c7ab47`;
the archive was not published or deployed.

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
| Texas | Endpoint, node, required mode, HMAC/SSH separation, telemetry-only mode, capacity 60 and central parity | 61st inbound rejection, cross-key/hub, no control polling, install/rollback and no production contact |

## Compatibility conclusion

Existing Phoenix and KY2 behavior remains intact and production required-mode
activation is recorded separately. Signed work is always verified; duplicates
remain bounded and deduplicated. Texas starts in required integrity mode with
control polling disabled. KY2's systemd hardening does not override ExecStart,
timer, watcher, node, SSH, Asterisk or routing configuration.

Production health is not inferred from local tests. Texas operator agreement,
host installation, radio linking and incremental controls remain separate gates.
