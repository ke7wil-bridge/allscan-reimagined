# ASR Plus live deployment inventory - 2026-11-23

Historical baseline captured November 23. Updated with production token/HTTPS deployment evidence November 24.

| Node | Deployed authenticated-command components | Mode | Service state | Gap |
| --- | --- | --- | --- | --- |
| Kentucky MasterHub 674982 | Control `f4c3fb...`, endpoint `145c4e...`, integrity module `bdd5c1...`, central config `12842a...` | Phoenix/KY2 required | Asterisk and status timer active; queues empty | Token API/helper/sudoers/store/audit installed; HTTPS active |
| KY2 674980 | Agent `7766c6...`, integrity module `bdd5c1...`, canonical `ky2` config | Optional | Control timer and watcher active; fresh telemetry | Control oneshot lacks Phoenix-equivalent sandbox |
| Phoenix 641894 | Agent `7766c6...`, integrity module `bdd5c1...`, canonical `phoenix` config | Optional | Agent timer and watcher active; fresh telemetry | Required mode active; signed read-only checks passed |

Both remote ledgers are persistent, root-only, and contain completed records.
Historical KY2 ledger records may retain the former identifier for audit
traceability; active configuration and routing use only `ky2`.

MasterHub's production API hash `9024d5...` identifies the existing
session-only API and differs from the token-enabled repository API `06cbbe...`.
The following production token components are absent:

- `/usr/local/sbin/asrplus-token`
- `/etc/sudoers.d/asrplus-token`
- `/var/lib/asrplus/tokens/tokens.json`
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

## November 24 production update

- Kentucky API source and web copy now match SHA-256 ea23e5848cf8adcc7faddb71973d8ab9fbfbb1d7ada6869a164aa11e1cf842a3.
- Token helper and authentication-only sudoers rule installed. Apache passes Authorization to PHP using a scoped SetEnvIfNoCase rule.
- Let's Encrypt certificate for asrplus.netoholicsnetwork.net expires 2027-02-22. Certbot timer active; renewal dry-run succeeded.
- UFW permits TCP 443; external TLS validation succeeded. HTTP remains available for session compatibility; bearer tokens are denied over HTTP.
- Token store directory mode 0700 root:root; token database 0600 root:root; audit log 0640 root:root.
- Live validation passed: HTTPS status, one-hub-only response, cross-hub denial, read-only control denial, HTTP denial, expiry and revocation. Test tokens revoked; no permanent bearer token issued.
- Periodic asrplus-reapply was run after source update; authoritative source and deployed web copy hashes match.
- Apache and Asterisk remained active. No Asterisk restart or radio-link change.
- Authenticated browser-session regression testing remains outstanding; existing session path was retained in API source.
