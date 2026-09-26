# AllScan Reimagined

AllScan Reimagined is a configurable interface and security layer for David
Gleason's AllScan. Beta 8 installs the current official AllScan backend at
`/allscan/` and installs the Reimagined interface separately at `/asr/`.
The two interfaces share the node's existing AllScan accounts and data without
copying credentials between nodes.

AllScan Reimagined is customized by KE7WIL.

This archive is **Beta 8** and remains a prerelease. Beta 7.5 is the previous
published prerelease; the development line advances directly to Beta 8.

## What's New in Beta 8

- Connection Status now uses bounded Asterisk Manager reads and a per-node
  circuit breaker, so one unhealthy AMI request cannot indefinitely stall or
  erase otherwise healthy node status.
- Beta 8 development adds the bridge-management and dashboard work on top of
  the Beta 7.5 public baseline.
- Existing manual/shared bridges remain external and untouched. ASR-managed
  bridges retain immutable ownership recorded at creation, with
  retire/recreate required for managed mode or role changes.

[Read the Beta 8 development notes](release-notes/v1.0.0-beta.8.md).

## Install

After a Beta 8 release is published, run this one-time command from an
interactive terminal on the ASL3 node:

```bash
curl -fsSLo /tmp/asr-bootstrap.sh https://raw.githubusercontent.com/ke7wil-bridge/allscan-reimagined/main/bootstrap.sh && sudo bash /tmp/asr-bootstrap.sh
```

The bootstrap downloads a published ASR archive, verifies its companion
SHA-256 asset, and starts the installer. Complete its prompts, including those
from the official AllScan installer if stock AllScan needs an update. After
installation, use the browser for routine ASR updates and recovery. See the
[browser installation and update guide](docs/browser-install-update.md).

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
upload a PNG, JPEG, or WebP header logo under 1 MB, configure up to sixteen
bridge cards and optional client sources, maintain friendly bridge names, save
QRZ XML credentials, opt Standard Bridges into fixed-link recovery, import a
validated YSF reflector list, add persistent custom YSF reflectors, control whether ASR login is required, and roll back to
one of the five newest valid previous ASR versions.

**Admin → Help & Instructions** explains the dashboard, Favorites, bridge
cards, isolated DMR/YSF/P25/NXDN/M17 Net Bridge controls, Lookup and the station map, update notices,
rollback, diagnostics, and hard-refresh behavior.

P25 and NXDN controls require a separately provisioned bridge stack with
authenticated local MQTT, per-instance topic permissions, and matching
root-only controller credentials. ASR fails closed when that boundary is absent.

The Reimagined credit remains:

```text
by KE7WIL
customized by KE7WIL
```

Only the top header logo is customizable. The footer always uses the ASR logo.

If bridge services are detected, the installer reviews the bridge card node numbers before saving them. Press Enter/Return to accept a detected node number, type a corrected node number, or type `none` to hide that bridge card.

## Updates

Open **Admin → Update ASR** in `/asr/`. Check for a release, run preflight,
then confirm installation. The root-owned updater verifies the release and
checksum, creates a rollback backup, runs the existing installer, checks the
served application, and restores the previous version on failure. No update
starts automatically. The latest ten rollback backups are retained by default.

For manual recovery, open **Admin → Reimagined Settings → Backups & Rollback**.
A stopped, interrupted update can also be checked from the Update ASR dialog.
See the [full browser workflow](docs/browser-install-update.md).

## Personal Configuration

Node-specific settings are stored outside the web root:

```text
/etc/allscan-reimagined/config.json
```

Uploaded logos are stored in:

```text
/var/lib/allscan-reimagined/
```

For routine ASR configuration, use **Admin → Reimagined Settings** in
the browser. Browser-only dashboard preferences remain in the browser profile.

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

## Layered Install

The installer keeps stock AllScan at `/allscan/` and ASR at `/asr/`. If the stock backend needs installation or upgrading, the first installer invokes the upstream AllScan installer interactively. Later browser updates stop at preflight when a stock backend upgrade is required.

The repository itself does not vendor the full upstream AllScan source tree. The compat snapshot under `compat/allscan-v1.01/` contains the files needed for the overlay layer.

## Docker Layered Stack

This repository now includes a Docker stack that pulls stock AllScan from the upstream GitHub repository at runtime and then layers AllScan Reimagined into `/asr/` while keeping stock AllScan at `/allscan/`.

1. Copy `.env.example` to `.env` and set your AMI credentials (`ASR_AMI_PASS`) plus node values.
2. Ensure your ASL3 container is running on the `asl3-docker_default` network.
3. Build and run:

```bash
docker compose up --build
```

4. Open:
  - `http://localhost:4173/allscan/` (stock AllScan)
  - `http://localhost:4173/asr/` (AllScan Reimagined overlay)

## Documentation

- [Browser installation, updates, backups, and recovery](docs/browser-install-update.md)
- [Updater user-state preservation inventory](docs/browser-updater-state-inventory.md)
- [Lookup page and station origin map](docs/lookup-map.md)
- [Beta 8 development notes](release-notes/v1.0.0-beta.8.md)
- [Beta 7.5 release notes](release-notes/v1.0.0-beta.7.5.md)
- [Beta 7.3 release notes](https://github.com/ke7wil-bridge/allscan-reimagined/blob/main/release-notes/v1.0.0-beta.7.3.md)
- [Beta 7.1 release notes](https://github.com/ke7wil-bridge/allscan-reimagined/blob/main/release-notes/v1.0.0-beta.7.1.md)
- [Beta 7 release notes](https://github.com/ke7wil-bridge/allscan-reimagined/blob/main/release-notes/v1.0.0-beta.7.md)

## Original AllScan

AllScan Reimagined is based on AllScan by David Gleason, NR9V.

Original AllScan source:

https://github.com/davidgsd/AllScan

See [ATTRIBUTION.md](ATTRIBUTION.md) for more detail.
