# ASR Plus live deployment inventory - 2026-11-23

Read-only evidence only. No live file, queue, service, key, radio link, firewall
or configuration was changed.

| Node | Deployed authenticated-command components | Mode | Service state | Gap |
| --- | --- | --- | --- | --- |
| Kentucky MasterHub 674982 | Control `f4c3fb...`, endpoint `145c4e...`, integrity module `bdd5c1...`, central config `12842a...` | Phoenix/KY2 optional | Asterisk and status timer active; queues empty | Token-enabled API/helper/sudoers/store/audit absent |
| KY2 674980 | Agent `7766c6...`, integrity module `bdd5c1...`, canonical `ky2` config | Optional | Control timer and watcher active; fresh telemetry | Control oneshot lacks Phoenix-equivalent sandbox |
| Phoenix 641894 | Agent `7766c6...`, integrity module `bdd5c1...`, canonical `phoenix` config | Optional | Agent timer and watcher active; fresh telemetry | Required cutover pending approval |

Both remote ledgers are persistent, root-only, and contain completed records.
Historical KY2 ledger records may retain the former identifier for audit
traceability; active configuration and routing use only `ky2`.

MasterHub's production API hash `9024d5...` identifies the existing
session-only API and differs from the token-enabled repository API `06cbbe...`.
The following production token components are absent:

- `/usr/local/sbin/asrplus-token`
- `/etc/sudoers.d/asrplus-token`
- `/var/lib/asrplus/tokens.json`
- `/var/log/asrplus-token-audit.jsonl`

Per-hub key files are root-owned `0600`; key bytes were not read. MasterHub's
forced SSH entries bind Phoenix and KY2 to their own endpoint identities and
source addresses. A retired legacy source remains separately restricted and
is not an active KY2 mapping. Phoenix was inspected through the operator's
existing Mac SSH route with strict host-key checking. KY2 and Kentucky were
inspected directly over their verified production addresses.

All signing components match reviewed source and optional-mode production is
healthy. This inventory is not authorization to install tokens, harden KY2,
activate required mode, or install Texas.
