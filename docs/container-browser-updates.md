# Layered Docker browser updates

Beta 8.7 supports three startup-selected update modes:

- `native` keeps the traditional host updater behavior. This remains the default when the variable is unset.
- `external` checks releases and shows installed/latest versions, but the UI offers no install action and the API rejects update, preflight, and recovery requests.
- `container` enables authenticated browser updates through the existing host broker.

The supported Compose service declares both variables explicitly:

```yaml
image: ${ASR_IMAGE:-allscan-reimagined:local}
environment:
  ASR_UPDATE_MODE: ${ASR_UPDATE_MODE:-external}
```

Set `ASR_UPDATE_MODE=container` only after the Beta 8.7 host broker has been installed. Startup rejects unknown values. The setting survives restarts through Compose/environment state; startup and release checks do not edit tracked source.

## Security boundary

The browser API requires an authenticated AllScan administrator. State-changing calls also require POST, same-origin validation, and the fixed `X-ASR-Requested-With` header. The container client can submit only six fixed operations to `/run/allscan-reimagined-host/bridge-setup.sock`. The root broker validates the Unix peer UID against its root-owned profile and runs only the installed updater. Neither the web container nor PHP receives `/var/run/docker.sock`, a shell command, image name, URL, path, Compose service, or release version from the browser.

The host updater accepts only the fixed GitHub release assets already enforced by the native updater. It verifies the companion SHA-256, validates the archive, requires a packaged container Dockerfile, and derives the Compose project/service/config files from Docker labels on the uniquely configured ASR container.

## Packaged stock AllScan

Both the source-tree development image and the staged release image package the exact
AllScan commit in `stock-allscan.json` at image-build time. The shared packager validates
the manifest, checks out the immutable commit, verifies the Git object graph and confirms
the upstream version before either Dockerfile sets `ASR_PACKAGED_ALLSCAN=1`. At startup,
the entrypoint rejects a packaged image whose `/opt/upstream-allscan` commit does not match
the manifest; it never fetches the upstream repository in packaged mode. Network access is
therefore required while building the image, not while initializing fresh or restored
persistent volumes.

`python3 scripts/asr-packaged-allscan-self-test.py` is part of the release gate and checks
both Docker build paths and the entrypoint contract. After building a uniquely tagged QA
image, run the opt-in real-image gate with
`python3 scripts/asr-packaged-allscan-self-test.py --image <qa-tag>`. It uses no network,
publishes no ports, applies CPU/memory/PID limits, creates disposable volumes, checks HTTP,
SQLite, PHP and JSON, then recreates the container to verify persisted data. Never point
this test at production volumes or replace a production image tag.

## Update sequence

1. **Check for Update** reads release metadata without changing the host.
2. **Update Now** queues one root-owned systemd job.
3. The host downloads and verifies the release, then builds `allscan-reimagined:<exact-version>` before downtime.
4. The ASR web container stops. The updater archives the complete writable mounts at `/var/www/html`, `/etc/allscan`, `/etc/allscan-reimagined`, and `/var/lib/allscan-reimagined` under `/var/backups/allscan-reimagined-container/<job-id>/`.
5. Compose recreates only the ASR web service with the new image. Persistent volumes, networks, ports, environment, node identity, and host bridge/Asterisk runtimes remain attached.
6. Health checks require the expected version and a served `/asr/` page. A failure stops the replacement, restores all volume archives, recreates the previous image, and verifies the restored page.

The update notice clears after the replacement reports the installed release version on the next check. No update runs without an administrator choosing **Update Now**.

## One-time host bootstrap

Use a locally verified Beta 8.7 candidate archive. Do not substitute a branch checkout or unverified download. From the extracted archive, an administrator runs:

```bash
sudo python3 payload/scripts/asr-container-host-install.py install \
  --web-container <current-asr-web-container> \
  --asterisk-container allstarlink3 \
  --preserve-bridge-runtime
```

For an already-running radio node, use `--preserve-bridge-runtime` to install the updater boundary without restarting managed bridge services. The provisioning and Asterisk broker sockets and recovery timer are still installed/reloaded; bridge processes are not explicitly restarted.

Before that command, update the deployment's Compose service to the `ASR_IMAGE`/`ASR_UPDATE_MODE` form above and set `ASR_UPDATE_MODE=container` in its protected host `.env`. The installer records the exact web and Asterisk container identities, persistent mount roots, and authorized web UID in `/etc/allscan-reimagined/container-provisioning.json`; installs the broker/updater and root worker unit; and restarts the broker socket. This is a host mutation and requires a fresh backup, rollback plan, and action-time approval.

## Recovery and rollback

Failed updates roll back automatically. Keep the job ID shown by the browser; its root-only backup directory contains `manifest.json` and one archive per persistent volume. Do not delete that directory until bridge and dashboard acceptance are complete.

If automatic rollback reports manual intervention, stop the ASR web service and restore only through the installed host updater or a reviewed recovery procedure using that exact manifest. Never unpack a backup over a running container, never restore one node's backup to another node, and do not alter the Asterisk or bridge host configuration as part of web-container recovery.

For planned rollback testing, use a disposable candidate/version pair or an approved non-critical node. Confirm afterward:

- installed ASR version and `/asr/` health;
- all four volume sentinels/configuration hashes;
- node number, Favorites, users, uploaded assets, and ASR settings;
- Standard and Net Bridge configuration, service health, and exclusive USRP ownership;
- no Asterisk container recreation and no retired URF runtime.
