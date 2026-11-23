# ASR Plus live deployment inventory — 2026-11-23

Read-only inspection only. No live files, services, queues, keys, or configuration were changed.

| Host | Existing entrypoint | Shared integrity module | Signing config | Existing services |
|---|---|---|---|---|
| Kentucky MasterHub 674982 | `/usr/local/sbin/asrplus-control`, `/usr/local/sbin/asrplus-remote-endpoint` present, `0755 root:root` | **Missing** beside deployed entrypoints | `/etc/asrplus/command-integrity.json` **missing** | Not changed or restarted |
| Phoenix 641894 | `/usr/local/sbin/asrplus-agent` present, `0755 root:root` | **Missing** beside deployed entrypoint | Existing `/etc/asrplus-agent.json`; signing config not staged/verified | `asrplus-agent.timer`, `asrplus-agent.path`, `asrplus-talker-watcher.service` active |
| KY2 674980 | `/usr/local/sbin/asrplus-ky-agent` present, `0755 root:root` | **Missing** beside deployed entrypoint | Existing `/etc/asrplus-ky-control.json` and `/etc/asrplus-ky.json`; signing config not staged/verified | `asrplus-ky-control.timer`, `asrplus-ky-watcher.service` active |

All three hosts' existing Asterisk service was reconfirmed active. The Phoenix
and KY2 queues were inspected and had zero pending `.json`/`.inflight` entries.
Nine offline regression suites pass at `b1f9e96`.

Phoenix was reached through the Mac's existing `az` alias
(`node64189.ddns.net`) with strict host-key checking. The direct dynamic address
is not an approved deployment route. All three hosts have stale staged
entrypoints: Phoenix and KY2 have agent hash `f4874d...` instead of reviewed
`7e4c41...`; MasterHub has control/endpoint hashes `098144...`/`324630...`
instead of `ba857f...`/`4ef829...`. They must be restaged and reverified before
activation. The staged integrity-module hash matches the reviewed bundle on all
three hosts.

## Consequence

The signed-command implementation is **not installed or activated** on any production host. In particular, deploying the new entrypoints now would fail because their sibling `asrplus_command_integrity.py` module is absent; MasterHub also has no `/etc/asrplus/command-integrity.json`. This independently confirms the import/config deployment blockers. Do not replace entrypoints or enable signing until the module, per-hub keys/configuration, and coordinated compatibility steps are staged and verified.

The current live services remain on their existing code and configurations. No signing keys were generated, read, or printed.
