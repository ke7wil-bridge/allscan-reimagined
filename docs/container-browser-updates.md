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

Set `ASR_UPDATE_MODE=container` only for a deployment using the supported host broker. Startup rejects unknown values. The setting survives restarts through Compose/environment state; startup and release checks do not edit tracked source. The #316-capable layered image can start before the host broker socket exists: it installs only the narrow host clients for delegated operations, leaves read-only host status mounts unchanged, and rejects host control/update requests until the authorized broker is available. A missing socket does not grant a local substitute for delegated controls.

On a clean deployment, supply the authoritative ASL node-database directory, all four writable persistent volumes, and the read-only runtime/socket directory mounts. Start the isolated web and Asterisk services, run the verified host installer below, then validate the broker and approve the Compose configuration from the host. Do not make host runtime directories writable to solve startup ordering. Older images without the cold-start correction may require a staged first startup without the host status mounts, followed by host installation and recreation using the final read-only mounts.

## Security boundary

The browser API requires an authenticated AllScan administrator. State-changing calls also require POST, same-origin validation, and the fixed `X-ASR-Requested-With` header. The container client can submit only six fixed operations to `/run/allscan-reimagined-host/bridge-setup.sock`. The root broker validates the Unix peer UID against its root-owned profile and runs only the installed updater. Neither the web container nor PHP receives `/var/run/docker.sock`, a shell command, image name, URL, path, Compose service, or release version from the browser.

The host updater accepts only the fixed GitHub release assets already enforced by the native updater. It verifies the companion SHA-256, validates the archive, requires a packaged container Dockerfile, and derives the Compose project/service/config files from Docker labels on the uniquely configured ASR container.

## Update sequence

1. **Check for Update** reads release metadata without changing the host.
2. **Update Now** queues one root-owned systemd job.
3. The host downloads and verifies the release, then builds `allscan-reimagined:<exact-version>` before downtime.
4. The updater durably records the original image identity and approved Compose snapshot before stopping the ASR web container. The updater archives the complete writable mounts at `/var/www/html`, `/etc/allscan`, `/etc/allscan-reimagined`, and `/var/lib/allscan-reimagined` under `/var/backups/allscan-reimagined-container/<job-id>/`.
5. On a host with the #316-capable updater installed, the worker also snapshots the release-managed host Python helpers, including files that do not yet exist. It verifies the backup hashes, installs the helpers from the verified release, and verifies the installed bytes and modes before starting the new web service.
6. Compose recreates only the ASR web service with the new image. Persistent volumes, networks, ports, environment, node identity, and host bridge/Asterisk runtimes remain attached.
7. Health checks require the expected version and a served `/asr/` page. A failed replacement restores the previous host helper set and volume archives, recreates the previous image, and verifies the restored page.

The update notice clears after the replacement reports the installed release version on the next check. No update runs without an administrator choosing **Update Now**.

## One-time host bootstrap

Use a locally verified release archive containing the #316-capable host updater and worker unit. Do not substitute a branch checkout or unverified download. Hosts running the original Beta 8.7/8.7.1 updater cannot bootstrap this capability through a browser update alone: the old worker neither installs host helper updates nor has the required systemd write paths. An authorized administrator must perform the one-time migration **before** the next browser upgrade. Subsequent supported releases can migrate their managed Python helper inventories automatically. Changes to systemd units, tmpfiles, host packages, or other host infrastructure still require an explicitly reviewed migration path; do not assume that copying a new installer updates those resources. The release installer declares `HOST_UPDATER_INFRASTRUCTURE_SCHEMA`; an incompatible or absent schema is rejected before stopping the web container. Increment this contract when changing the updater infrastructure and qualify the migration separately.

From the extracted archive, an administrator runs:

```bash
sudo python3 payload/scripts/asr-container-host-install.py install \
  --web-container <current-asr-web-container> \
  --asterisk-container allstarlink3 \
  --preserve-bridge-runtime
```

For an already-running radio node, use `--preserve-bridge-runtime` to install the updater boundary without restarting managed bridge services. The provisioning and Asterisk broker sockets and recovery timer are still installed/reloaded; bridge processes are not explicitly restarted.

Before that command, update the deployment's Compose service to the `ASR_IMAGE`/`ASR_UPDATE_MODE` form above and set `ASR_UPDATE_MODE=container` in its protected host `.env`. The installer records the exact web and Asterisk container identities, persistent mount roots, and authorized web UID in `/etc/allscan-reimagined/container-provisioning.json`; installs the broker/updater and root worker unit; and restarts the broker socket. This is a host mutation and requires a fresh backup, rollback plan, and action-time approval.

## Recovery and rollback

Handled update failures roll back automatically. Keep the job ID shown by the browser; its root-only backup directory contains `manifest.json`, one archive per persistent volume, and (for the #316-capable updater) `host-helpers/manifest.json` with the original file contents, permissions, checksums, and absent-file records. Do not delete that directory until bridge and dashboard acceptance are complete. The #316 worker writes a durable phase record before downtime and marks the backup ready before changing any helper. The installed recovery service runs on worker failure and at host boot; it restores the original verified helper set, volume archives, and immutable image if an interrupted transaction reached the backup-ready phase. Earlier interruptions restart the unchanged original container or clear a job that never reached downtime. A corrupt or legacy ambiguous transaction fails closed and blocks new updates. Browser recovery requests launch the same fixed root worker rather than modifying privileged files inside the broker's sandbox.

The supported restart policies are `no` and `unless-stopped` (a manually stopped container must remain stopped during an interrupted host migration). The worker rejects `always` and other policies before downtime. An installed recovery service can be invoked by an administrator with `sudo systemctl start allscan-reimagined-container-update-recovery.service`. Review the original job status and journal if the recovery service fails; do not clear the state simply to enable another update.

If automatic rollback reports manual intervention, stop the ASR web service and restore only through the installed host updater or a reviewed recovery procedure using that exact manifest. Never unpack a backup over a running container, never restore one node's backup to another node, and do not alter the Asterisk or bridge host configuration as part of web-container recovery.

For planned rollback testing, use a disposable candidate/version pair or an approved non-critical node. Confirm afterward:

- installed ASR version and `/asr/` health;
- all four volume sentinels/configuration hashes;
- node number, Favorites, users, uploaded assets, and ASR settings;
- Standard and Net Bridge configuration, service health, and exclusive USRP ownership;
- no Asterisk container recreation and no retired URF runtime.


## Cold-start smoke test

Development hosts with the local Beta 8.7.1 image and Docker can run `python3 scripts/asr-container-cold-start-docker-self-test.py`. The test uses the real layered entrypoint, current reapply script, fresh disposable volumes, read-only runtime directories, an offline node-database fixture and an isolated network namespace. It checks that startup succeeds, mount permissions/content remain unchanged, a missing broker fails closed, and a late fixed read-only protocol responder can be reached without recreating the web container. Add `--browser` (with Playwright installed) for the real AllScan administrator login, anonymous and cross-origin rejection, update dialog, and one authenticated Update Now request. The fixed protocol fixture deliberately refuses host mutations. No production service or Docker socket is exposed to the test container. These are not substitutes for the full clean host installer or an authenticated browser upgrade with a real root broker.
