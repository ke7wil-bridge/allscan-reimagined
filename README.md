# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David Gleason's AllScan. It installs the current official AllScan backend first, then applies the Reimagined interface without copying user accounts or credentials between nodes.

AllScan Reimagined is customized by KE7WIL.

## Install

Download the current release archive from the GitHub release page, then run the installer as root on the AllStar node.

For v1.0.0 Beta 3:

```bash
bash <<'ASR'
set -e

url="https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v1.0.0-beta.3/allscan-reimagined-1.0.0-beta.3.tar.gz"
pkg="/tmp/allscan-reimagined-1.0.0-beta.3.tar.gz"
stage="/tmp/allscan-reimagined-install-beta3"
sum="REPLACE_WITH_RELEASE_SHA256"

rm -rf "$stage"
curl -fL "$url" -o "$pkg"
echo "$sum  $pkg" | sha256sum -c -

mkdir -p "$stage"
tar -xzf "$pkg" -C "$stage"

cd "$stage/allscan-reimagined-1.0.0-beta.3"
./install.sh
ASR
```

The release page provides the final SHA-256 checksum. Replace `REPLACE_WITH_RELEASE_SHA256` with the checksum from the release.

## Setup Prompts

The installer detects the node number, callsign, and known bridge services.

It asks for:

```text
Header title
Optional PNG/JPEG/WebP logo path
```

The browser tab title is set automatically from the header title:

```text
Header title | ASR
```

Press Enter at the logo prompt to use the included ASR logo. To use a custom logo during installation, first upload a PNG, JPEG, or WebP image to the node, then enter its path.

The Reimagined credit remains:

```text
by KE7WIL
customized by KE7WIL
```

Logo upload and Reimagined settings pages are planned for the Admin menu.

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
