# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David Gleason's AllScan. It installs the current official AllScan backend first, then applies the Reimagined interface without copying user accounts or credentials between nodes.

AllScan Reimagined is customized by KE7WIL.

This archive is **Beta 5.9 Rollup 1**. It contains the complete Beta 5.9
release plus the verified Favorite-click and Disconnect-before-Connect
persistence fixes, and TGIF credential-storage hardening planned for Beta 6.

## Install

Download the current release archive and its published SHA-256 checksum from the GitHub release page. Verify and extract the archive, then run the installer directly in an interactive root shell on the AllStar node:

```bash
set -e

pkg="allscan-reimagined-1.0.0-beta.5.9-Rollup-1.tar.gz"
sum="PASTE_THE_PUBLISHED_SHA256_HERE"

echo "$sum  $pkg" | sha256sum -c -
tar -xzf "$pkg"
cd allscan-reimagined-1.0.0-beta.5.9-Rollup-1
bash ./install.sh
```

Do not run the final installer through a heredoc or other non-interactive wrapper. When the official AllScan backend needs an update, both installers require an interactive terminal.

## Setup Prompts

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

Press Enter/Return at the logo prompt to use the default ASR logo. After installation, **Admin → Reimagined Settings** can change the header title, upload a PNG, JPEG, or WebP header logo under 1 MB, configure up to eight bridge cards and optional client sources, maintain friendly bridge names, save QRZ XML credentials, and control whether login is required.

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
2. Backs up the existing AllScan web directory and private database.
3. Runs the official AllScan installer/updater when required.
4. Preserves existing users, passwords, permissions, Favorites, and node settings.
5. Detects the primary node number, callsign, and known bridge services.
6. Applies Apache, session, file-permission, and endpoint hardening.
7. Installs an integrity service that restores the Reimagined overlay after official AllScan updates.
8. Verifies the page and runtime configuration before reporting success.

If the official AllScan backend is already current, the official updater is skipped.

After a successful installation, ASR automatically retains the newest 10 rollback backups under `/root/allscan-reimagined-backups/` and removes older timestamped ASR backups. Set `ASR_BACKUP_RETENTION` to a different positive number when running `bash ./install.sh` if the node needs a different retention policy.

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

## Original AllScan

AllScan Reimagined is based on AllScan by David Gleason, NR9V.

Original AllScan source:

https://github.com/davidgsd/AllScan

See [ATTRIBUTION.md](ATTRIBUTION.md) for more detail.
