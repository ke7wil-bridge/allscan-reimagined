# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David
Gleason's AllScan. Beta 6 installs the current official AllScan backend at
`/allscan/` and installs the Reimagined interface separately at `/asr/`.
The two interfaces share the node's existing AllScan accounts and data without
copying credentials between nodes.

AllScan Reimagined is customized by KE7WIL.

This archive is **Beta 6** and remains a prerelease.

## Install

Download, verify, and extract the current release archive, then run the
installer directly in an interactive root shell on the AllStar node:

```bash
set -e

base="https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v1.0.0-beta.6.1"
pkg="/tmp/allscan-reimagined-1.0.0-beta.6.1.tar.gz"
checksum="/tmp/allscan-reimagined-1.0.0-beta.6.1.tar.gz.sha256"
stage="/tmp/asr-beta-6.1-install"

curl -fL "$base/$(basename "$pkg")" -o "$pkg"
curl -fL "$base/$(basename "$checksum")" -o "$checksum"
(cd /tmp && sha256sum -c "$(basename "$checksum")")

rm -rf "$stage"
mkdir -p "$stage"
tar -xzf "$pkg" -C "$stage"
cd "$stage/allscan-reimagined-1.0.0-beta.6.1"

php -l payload/server/asr-api.php
php -l payload/compat/allscan-v1.01/asr-settings/index.php
php -l payload/compat/allscan-v1.01/asr-instructions/index.php
php -l payload/scripts/asr-bridge-clients.php
php payload/scripts/asr-bridge-clients.php --self-test
php payload/scripts/asr-access-policy-self-test.php
php payload/scripts/asr-lookup-map-self-test.php
sh -n payload/scripts/asr-asterisk-read.sh
bash -n payload/scripts/asr-reapply.sh
bash -n payload/scripts/asr-integrity-check.sh
bash payload/scripts/asr-favorites-permissions.sh --self-test
bash payload/scripts/asr-side-by-side-self-test.sh
python3 payload/scripts/asr-patch-connected-clients.py --self-test
python3 payload/scripts/asr-patch-allscan-index.py --self-test
python3 payload/scripts/asr-migrate-tgif-environment.py --self-test
python3 payload/scripts/asr-release-check.py --self-test
python3 payload/scripts/asr-rollback.py self-test
python3 payload/scripts/asr-bridge-control.py --self-test
python3 payload/scripts/asr-favorites-update.py --self-test
python3 payload/scripts/asr-favorites-source.py --self-test
python3 payload/scripts/asr-instructions-self-test.py
python3 payload/scripts/asr-stock-count-helper.py --self-test

bash ./install.sh
```

Do not run the final installer through a heredoc or other non-interactive wrapper. When the official AllScan backend needs an update, both installers require an interactive terminal.

## Setup Prompts

The installer explains and configures the two web interfaces:

```text
/allscan/  Original stock AllScan
/asr/      AllScan Reimagined
```

They share users, Favorites, the database, and node settings, but each path
keeps its own browser login session. The installer also explains that enabling
the optional stock `/allscan/` login requirement blocks unauthenticated
read-only dashboard monitoring as well as control.

The installer detects the node number, callsign, and known bridge services.

It asks for:

```text
Header title
Optional PNG/JPEG/WebP logo path
Bridge card node numbers when bridge services are detected
```

The browser tab title is set automatically from the header title:

```text
Header title | ASR
```

Press Enter/Return at the logo prompt to use the default ASR logo. After
installation, **Admin → Reimagined Settings** can change the header title,
upload a PNG, JPEG, or WebP header logo under 1 MB, configure up to eight
bridge cards and optional client sources, maintain friendly bridge names, save
QRZ XML credentials, control whether ASR login is required, and roll back to
one of the five newest valid previous ASR versions.

**Admin → Help & Instructions** explains the dashboard, Favorites, bridge
cards, DMR Net Bridge controls, Lookup and the station map, update notices,
rollback, diagnostics, and hard-refresh behavior.

The Reimagined credit remains:

```text
by KE7WIL
customized by KE7WIL
```

Only the top header logo is customizable. The footer always uses the ASR logo.

If bridge services are detected, the installer reviews the bridge card node numbers before saving them. Press Enter/Return to accept a detected node number, type a corrected node number, or type `none` to hide that bridge card.

## Updates

To update AllScan Reimagined, install the latest Reimagined release. The installer:

1. Reports the installed and latest official AllScan backend versions.
2. Backs up the existing stock and Reimagined state before changing files.
3. Runs the official AllScan installer/updater when required.
4. Preserves existing users, passwords, permissions, Favorites, and node settings.
5. Detects the primary node number, callsign, and known bridge services.
6. Applies Apache, session, file-permission, and endpoint hardening.
7. Installs persistence services that maintain `/asr/` without replacing stock
   `/allscan/`.
8. Installs a low-frequency cached release checker that never installs updates
   automatically.
9. Verifies the pages and runtime configuration before reporting success.

If the official AllScan backend is already current, the official updater is skipped.

After a successful installation, ASR automatically retains the newest 10 rollback backups under `/root/allscan-reimagined-backups/` and removes older timestamped ASR backups. Set `ASR_BACKUP_RETENTION` to a different positive number when running `bash ./install.sh` if the node needs a different retention policy.

The on-screen rollback control is the final expandable section above Save in
Reimagined Settings. It has its own confirmation button; the normal Save
button never starts a rollback.

## Personal Configuration

Node-specific settings are stored outside the web root:

```text
/etc/allscan-reimagined/config.json
```

Uploaded logos are stored in:

```text
/var/lib/allscan-reimagined/
```

Rerun personalization without reinstalling:

```bash
/opt/allscan-reimagined/current/scripts/asr-configure.sh --force
/usr/local/sbin/allscan-reimagined-reapply
```

Back up `/etc/allscan-reimagined/config.json` before forced reconfiguration if the node has hand-tuned bridge mappings.

### Smaller-node performance

The Access section includes **Low-Power Node Mode**. It reduces bridge and temperature refresh frequency, disables animated themes, uses adaptive Asterisk polling, and keeps transient status caches in RAM. The admin-only **Performance Stats** page shows load, temperature, memory, disk use, request activity, active viewers, and ASR timer state without reloading the page.

## Accounts and Secrets

AllScan's account database remains local to each node at:

```text
/etc/allscan/allscan.db
```

It is never included in this repository or an installation package. No AMI password, API token, login, private key, or node-specific credential belongs in this repository.

## Building a Release

```bash
pnpm install
./build-release.sh
```

The release archive and SHA-256 checksum are written under:

```text
release/
```

## Documentation

- [Lookup page and station origin map](docs/lookup-map.md)
- [Beta 6.1 release notes](release-notes/v1.0.0-beta.6.1.md)
- [Beta 6 release notes](release-notes/v1.0.0-beta.6.md)

## Original AllScan

AllScan Reimagined is based on AllScan by David Gleason, NR9V.

Original AllScan source:

https://github.com/davidgsd/AllScan

See [ATTRIBUTION.md](ATTRIBUTION.md) for more detail.
