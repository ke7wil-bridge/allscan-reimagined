# ASR installation, updates, and recovery

This workflow is for the packaged release after sandbox qualification. The first installation uses a terminal once. Subsequent ASR updates and rollback use the browser.

## First installation

From an interactive terminal on the ASL3 node, run this single command after the release is published:

```bash
curl -fsSLo /tmp/asr-bootstrap.sh https://raw.githubusercontent.com/ke7wil-bridge/allscan-reimagined/main/bootstrap.sh && sudo bash /tmp/asr-bootstrap.sh
```

The bootstrap reads published releases from the fixed ASR GitHub repository. It downloads the matching archive and `.sha256` release asset, verifies SHA-256, rejects unsafe archive members, then starts `install.sh`. Follow the installer's prompts for node identity, the stock AllScan backend, and ASR setup. If the official AllScan installer is required, its own prompts remain interactive; the bootstrap does not bypass them.

Open `/asr/` after installation and log in with an existing AllScan administrator account. Stock `/allscan/` and ASR `/asr/` share users and data but keep separate browser sessions.

## ASR updates

In ASR, open **Admin → Update ASR**.

1. Choose **Check for Update**. ASR reports the current and latest published versions.
2. Choose **Run Preflight**. It checks compatibility with installed stock AllScan, service and configuration readiness, disk space, backup storage, and whether restart or reboot is required.
3. Choose **Install verified update**, then **Confirm update**. ASR queues one root-owned job. You may close the browser while it runs.
4. Return to **Admin → Update ASR** to see progress. Reload ASR after a successful health check.

The browser can request only fixed updater operations. The helper selects release assets from the ASR GitHub repository, checks their expected names and URLs, verifies the companion SHA-256 digest, validates archive contents, creates a rollback backup, applies the release, and runs application health checks. It does not install a GitHub branch, user-provided URL, path, shell command, or service name.

If stock AllScan needs its own interactive upstream update, ASR preflight stops before changing files. It does not silently run or bypass the official installer's prompts.

## Backups and recovery

Open **Admin → Reimagined Settings → Backups & Rollback** to review available backups and choose a prior ASR version. This action has its own confirmation. Ordinary Settings **Save** does not start a rollback.

If an ASR update fails during apply or health checks, the updater attempts to restore the previous known-good installation and checks its served page. The Update ASR dialog reports whether recovery succeeded. If a restart interrupted a job, use **Check interrupted update recovery** in that dialog; it refuses to act while the update service is still running.

The release installer retains the newest ten rollback backups by default. Browser status shows human-readable steps and sanitized results. Root-owned job logs contain the installer details and are not returned through the update API.

## Configuration and user state

Use **Admin → Reimagined Settings** for routine ASR configuration. AllStar node configuration and stock AllScan settings remain in their existing locations. The updater retains ASR configuration, Favorites and metadata, Custom Commands, scanning and bridge settings, branding, uploaded content, TGIF persistent state, and other user-owned paths according to the [preservation inventory](browser-updater-state-inventory.md). Browser-only dashboard preferences stay in the browser profile.

An isolated sandbox preserved the contents of 21 sampled user files through an update, forced failure, and queued rollback. A metadata-rich Favorites fixture also retained its description, order, and color through reapply. Browser-local preferences, visual rendering, and live radio operation still need targeted checks before publication.

## Release model and troubleshooting

`package.json` is the build version source. Packaged API version metadata must match it. `install.sh` reads the packaged manifest; the release checker reads the installed master. Each published archive must be named `allscan-reimagined-<version>.tar.gz` and have a matching `.sha256` asset under the same GitHub release tag.

If the update dialog says no update is available, check again after a release is published. If preflight fails, its reason is shown before any installation starts. If recovery needs attention, first review the available browser rollback backups; do not retry an update until the prior installation is healthy.
