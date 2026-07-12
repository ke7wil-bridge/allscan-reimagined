# ASR v1.0.0 Beta 5 Issue Tracker

Use this as the source list for GitHub Issues under milestone `v1.0.0 Beta 5`.

## Current Status

The installer, authentication, Favorites, packaging, recovery, branding, connected-client, lookup, performance, and shared admin-menu items below are implemented in Beta 5. The detailed sections are retained as acceptance criteria and historical context.

Remaining release checks and final notes:

- The two noncritical admin-page mobile reviews are deferred to a patch or Beta 6.
- D-Star follow-up on Thomas is deferred. It previously worked and is being repaired separately; ASR intentionally does not invent D-Star client rows without verified external talker data.
- The unused QRZ API Key input was removed. QRZ XML map enrichment uses the saved QRZ username and password; any legacy stored key remains private and ignored.

Suggested labels:

- `beta 5`
- `known issue`
- `priority high`
- `installer`
- `packaging`
- `auth`
- `favorites`
- `recovery`
- `branding`
- `bridge clients`

## Branding Rule

For Beta 5, only the top header logo should be customizable. The footer must always show the ASR logo.

Do not treat the footer logo as node-specific configuration. Header logo uploads/configuration should not change the footer logo.

## High Priority

### Run official AllScan updater with PHP for `/tmp noexec` systems

Labels: `beta 5`, `known issue`, `installer`, `priority high`

Symptom:

```text
/tmp/allscan-official-installer.../AllScanInstallUpdate.php: Permission denied
```

Cause: ASR downloads the official PHP installer to `/tmp` and executes it directly.

Fix:

```bash
php "$official_installer"
```

instead of:

```bash
"$official_installer"
```

This blocks installation on hardened/noexec systems.

### Refresh auth status even when no bridge cards are configured

Labels: `beta 5`, `known issue`, `auth`, `priority high`

Symptom: home page menu only showed `Node Stats` and `Login`, while direct admin pages `/allscan/cfg/` and `/allscan/user/` worked and recognized the user as Superuser.

Cause: auth-status refresh was inside the bridge-status effect, and the effect returned early when `config.bridges.length === 0`.

Fix: auth-status refresh must run independently of bridge configuration.

This makes admins think permissions are broken even when the database is correct.

### Do not cap bridge connected-client/detail lists at 8

Labels: `beta 5`, `known issue`, `bridge clients`

Current behavior: `src/lib/allscanLive.ts` uses `.slice(0, 8)` inside `formatBridgeDetailRows()`, so DMR, YSF, Zello, and any future D-Star detail rows are capped at 8 displayed entries.

Required Beta 5 behavior: never limit bridge connected clients or bridge detail users to only 8 visible entries. Show the full list received from the bridge/client source.

Note: Zello recent talkers may still be filtered by recency, but should not be capped to 8 after filtering.

Additional evidence from node674982 testing: the public status generator used for Netoholics also capped bridge clients with `clients[:40]` and Zello/recent users with `recent_users[:20]`. Beta 5 should avoid arbitrary bridge client/talker caps in every output path, not only the React UI.

### Preserve verified Zello talker writer behavior

Labels: `beta 5`, `known issue`, `bridge clients`

Node674982 verification showed the loaded Zello module is:

```text
/opt/asl-zello-bridge/venv/lib/python3.11/site-packages/asl_zello_bridge/zello.py
```

That loaded module contains:

- `zello-talkers.json`
- `_ke7wil_write_zello_talker`
- keyed user writer hook
- unkeyed user writer hook

Beta 5 should preserve this behavior and ensure any bridge/public-status reader can consume `zello-talkers.json` when it appears.

Zello talkers only appear after someone talks from Zello into the channel. ASL talking out to Zello does not create a Zello talker entry.

### Keep D-Star client detail deferred until external D-Star evidence exists

Labels: `beta 5`, `known issue`, `bridge clients`

Node674982 D-Star logs still showed only local bridge traffic:

```text
My: KN4EWT  /DVSW
metadata=KN4EWT
```

Beta 5 should keep D-Star status/relay supported, but should not invent D-Star connected-client/detail rows without verified external D-Star talker data.

### Preserve and expose `/etc/allscan/favorites.ini`

Labels: `beta 5`, `known issue`, `favorites`, `installer`, `priority high`

Bug: ASR Beta 4 did not surface existing Favorites stored at `/etc/allscan/favorites.ini`, so upgraded nodes could appear to lose Favorites and only show sample/local web Favorites files.

Required behavior:

1. Treat `/etc/allscan/favorites.ini` as a real Favorites source, not just `/var/www/html/allscan/favorites*.ini`.
2. Back up `/etc/allscan/favorites.ini` before install.
3. Restore `/etc/allscan/favorites.ini` on rollback.
4. During install/reapply, if `/etc/allscan/favorites.ini` exists, copy or link it into `/var/www/html/allscan/favorites.ini`.
5. Make ASR prefer `/etc/allscan/favorites.ini` or the copied `/var/www/html/allscan/favorites.ini` over sample Favorites files.
6. Never overwrite an existing real `favorites.ini` with the sample file.
7. If copying into the web folder, use web-writable permissions consistent with the rest of ASR runtime files.

## Installer And Packaging

### Package `install.sh` with executable permission

Labels: `beta 5`, `known issue`, `packaging`, `installer`

Symptom:

```text
./install.sh: Permission denied
```

Fix: package `install.sh` with executable mode. Documentation can mention `bash ./install.sh` as a fallback, but the best fix is for the tarball to preserve executable permission.

### Build release tarballs without macOS extended attributes

Labels: `beta 5`, `known issue`, `packaging`

Symptom:

```text
tar: Ignoring unknown extended header keyword 'LIBARCHIVE.xattr.com.apple.provenance'
```

Fix: build release archives without macOS xattrs/resource metadata.

### Detect non-interactive installer wrapper before backup/update work

Labels: `beta 5`, `known issue`, `installer`

Symptom:

```text
ERROR: The official AllScan update requires an interactive terminal.
```

Fix: docs should clearly say the final `bash ./install.sh` must run directly in an interactive shell. The installer should also detect this earlier and show a clearer message before doing backup work.

## Recovery

### Include fixed built asset so live JS patching is not needed

Labels: `beta 5`, `known issue`, `recovery`

Background: a Beta 4 built asset had to be patched live because there was no built-in recovery/update path for the auth/menu bug.

Fix: Beta 5 should include the fixed built asset so nobody has to manually patch `/var/www/html/allscan/assets/index-*.js`.
