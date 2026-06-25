# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David Gleason's AllScan. It installs the current official AllScan backend first, then applies the Reimagined interface without copying user accounts or credentials between nodes.

AllScan Reimagined is customized by KE7WIL.

## Install

Download the current release archive from the GitHub release page, then run the installer as root on the AllStar node.

For v1.0.0 Beta 4:

```bash
bash <<'ASR'
set -e

url="https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v1.0.0-beta.4/allscan-reimagined-1.0.0-beta.4.tar.gz"
pkg="/tmp/allscan-reimagined-1.0.0-beta.4.tar.gz"
stage="/tmp/allscan-reimagined-install-beta4"
sum="5af5ce9ae06552b1ca86fc519abc376779a16e497208276ad810e8db314543bb"

rm -rf "$stage"
curl -fL "$url" -o "$pkg"
echo "$sum  $pkg" | sha256sum -c -

mkdir -p "$stage"
tar -xzf "$pkg" -C "$stage"

cd "$stage/allscan-reimagined-1.0.0-beta.4"
./install.sh
ASR
```

This command verifies the release archive before installing.

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

Press Enter/Return at the logo prompt to use the default ASR logo. That is the recommended choice for beta testing.

Custom logo support is prepared for PNG, JPEG, and WebP images. The current beta accepts a logo file path on the node. A friendlier Reimagined Settings page for uploading a logo from your desktop is planned for the Admin menu.

The Reimagined credit remains:

```text
by KE7WIL
customized by KE7WIL
```

The planned Reimagined Settings page will also support changing the header title and enabling or disabling bridge cards.

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

## Original AllScan

AllScan Reimagined is based on AllScan by David Gleason, NR9V.

Original AllScan source:

https://github.com/davidgsd/AllScan

See [ATTRIBUTION.md](ATTRIBUTION.md) for more detail.
