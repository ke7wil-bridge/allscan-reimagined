# Beta 8.6.1 — remote tester and browser-updater validation

## Operator status

| Operator | Callsign | Node | Starting version | Result |
|---|---|---:|---|---|
| Del Webb | NY7S | 63916 | 8.4.2 | Terminal recovery/install validation PASS; production remained healthy. |
| Doug | KF8DRJ | 67098 | 8.6 | Operator-reported MobaXterm installation PASS. |
| Jim | N7YO | 637800 | Unknown | Pending; verify the installed version before offering an upgrade. |

## Disposable VM browser validation — 2026-11-23

- Genuine Debian 12 + ASL3 + stock AllScan v1.06 + ASR Beta 8.6 baseline.
- Authenticated browser update from Beta 8.6 to corrected Beta 8.6.1: PASS.
- Dashboard and runtime API after upgrade: HTTP 200.
- Apache/Asterisk active; zero failed systemd units.
- Rollback from corrected Beta 8.6.1 to Beta 8.6 through the browser: PASS, with no false Leave Site popup.
- Backup manifests passed byte-size and SHA-256 verification.
- Temporary VM test account remained intact.
- Final VM version: Beta 8.6.
- Production contacted or changed: NO.

The original RC3 archive is superseded. Build a new candidate from the corrected source; do not publish or deploy the old RC3 artifact.

## Corrected source changes

- Exclude `.asr-rollback-section` controls from ordinary Settings dirty-state tracking.
- Remove the rollback navigation guard immediately before intentional rollback form submission.
- Add release-version cache busting to the Settings modern JavaScript asset.
- Regression assertions updated for these behaviors.

## Distribution safety

The standard browser updater discovers releases from the public `ke7wil-bridge/allscan-reimagined` release API and requires the exact package archive and matching `.sha256` asset. The temporary private RC3 release is not discoverable by the production updater. Do not embed private credentials or signed private asset URLs in public endpoints. Public publication requires explicit owner approval after the fail-closed release qualification passes.

## Remaining operator validation

- Jim N7YO: read the installed version from node 637800 before planning any update; do not assume it matches Doug.
- Doug/Jim production browser update: not yet established by the disposable VM browser test; obtain separate operator approval before touching their nodes.
- Do not automatically retry a failed update.

Related: #129, #167, #224, #239.
