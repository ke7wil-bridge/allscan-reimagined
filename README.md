# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David Gleason's AllScan. It installs the current official AllScan backend first, then adds the Reimagined interface without copying user accounts or credentials between nodes.

## Installation behavior

The installer:

1. Reports the installed and latest official AllScan versions.
2. Backs up the existing AllScan web directory and private database.
3. Runs the official AllScan installer/updater when required.
4. Preserves existing users, passwords, permissions, Favorites, and node settings.
5. Detects the primary node number, callsign, and known bridge services.
6. Lets the owner choose the header, browser title, bylines, and optional logo.
7. Shows bridge cards only for configured or detected bridges.
8. Applies Apache, session, file-permission, and endpoint hardening.
9. Installs an integrity service that restores the Reimagined overlay after official AllScan updates.
10. Verifies the page and runtime configuration before reporting success.

## Personal configuration

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
sudo /opt/allscan-reimagined/current/scripts/asr-configure.sh --force
sudo /usr/local/sbin/allscan-reimagined-reapply
```

## Accounts and secrets

AllScan's account database remains local to each node at `/etc/allscan/allscan.db`. It is never included in this repository or an installation package. No AMI password, API token, login, private key, or node-specific credential belongs in this repository.

## Building a release

```bash
pnpm install
./build-release.sh
```

The release archive and SHA-256 checksum are written under `release/`.

## Upstream project

AllScan Reimagined is based on [AllScan by David Gleason, NR9V](https://github.com/davidgsd/AllScan).
