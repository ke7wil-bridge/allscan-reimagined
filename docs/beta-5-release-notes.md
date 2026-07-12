# AllScan Reimagined v1.0.0 Beta 5

Beta 5 is a major reliability, performance, administration, and lookup release.

## Highlights

- Preserves existing AllScan users, permissions, Favorites, node settings, and private databases during installation and updates.
- Requires login by default while preserving valid signed-in sessions.
- Adds Reimagined Settings, Performance Stats, lookup, EchoLink lookup, station-origin mapping, bridge diagnostics, and bug-report diagnostics.
- Adds shared responsive admin headers and menus for desktop, portrait, and landscape layouts.
- Improves AllStar connection counts, current-talker handling, bridge relay display, and transient ASTAPI failure recovery.
- Removes bridge-client display caps and filters stale bridge-client entries.
- Adds QRZ-enriched map markers with a public-location fallback when QRZ credentials are unavailable.
- Reduces load on smaller nodes through shared browser feeds, RAM caching, adaptive polling, hidden-tab handling, low-power mode, and cached system statistics.
- Keeps custom branding in the top header while locking the footer to the ASR logo.

## Installation

Copy this complete block into an interactive terminal on the AllStar node. It downloads the published archive, verifies its exact SHA-256 checksum, extracts it, and starts the installer:

```bash
set -e

base="https://github.com/ke7wil-bridge/allscan-reimagined/releases/download/v1.0.0-beta.5"
pkg="/tmp/allscan-reimagined-1.0.0-beta.5.tar.gz"
checksum="/tmp/allscan-reimagined-1.0.0-beta.5.tar.gz.sha256"
stage="/tmp/allscan-reimagined-install-beta5"

sudo rm -rf "$stage"
sudo rm -f "$pkg" "$checksum"
curl -fL "$base/allscan-reimagined-1.0.0-beta.5.tar.gz" -o "$pkg"
curl -fL "$base/allscan-reimagined-1.0.0-beta.5.tar.gz.sha256" -o "$checksum"
(cd /tmp && sha256sum -c "$(basename "$checksum")")

mkdir -p "$stage"
tar -xzf "$pkg" -C "$stage"

cd "$stage/allscan-reimagined-1.0.0-beta.5"
bash ./install.sh
```

Do not wrap the final installer in a heredoc. The installer and the official AllScan updater require an interactive terminal.

## Deferred Follow-up

- Two noncritical admin-page mobile reviews may be included in a patch or Beta 6.
- D-Star repair and external talker verification on Thomas are being handled separately and do not block Beta 5.
