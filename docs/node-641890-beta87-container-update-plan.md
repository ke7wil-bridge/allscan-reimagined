# Node 641890 Beta 8.7 container updater deployment plan

Status: prepared, not deployed. Node 641890 is the first acceptance target for issue #191 under tracker #198.

## Preconditions

- Beta 8.7 candidate archive and checksum pass the release build and container updater test suite.
- Exact current web container, Compose project/service/config paths, image ID, four writable mounts, free space, and Asterisk container identity are captured read-only.
- A separate host backup exists for the Compose files, protected `.env`, container provisioning profile, and current image reference.
- The updater-created persistent-volume backup path has at least 2 GiB free.
- Josh gives action-time approval for the exact host bootstrap and web-container recreation. The Asterisk container and host bridge services are out of scope.

## Bootstrap and upgrade

1. Add `ASR_IMAGE` and `ASR_UPDATE_MODE=container` to node 641890's Compose deployment without changing its volume, network, port, or bridge configuration.
2. Install the candidate's host broker with the exact current ASR web container and `allstarlink3` identities.
3. Verify the root-owned profile, broker socket peer policy, root update worker, and notification-only check before enabling the install action.
4. Bootstrap the Beta 8.7 web image from the verified candidate, recreating only the ASR web service with all existing persistent volumes.
5. Use the browser workflow for the next verified candidate update and observe queued, build, backup, install, health, and complete states.
6. Force a candidate health failure in a controlled build and verify automatic image/volume rollback. Separately reject an anonymous/direct update request.

## Acceptance evidence

- Successful update and forced failure job status/report.
- Before/after/restored hashes for representative files from all four persistent volumes, without exposing secrets.
- Installed version, served-page health, and container image IDs.
- Node 641890 identity; node 1999 Net Bridge; Standard DMR 1001, YSF 1002, P25 1003, NXDN 1004, and M17 1005 configurations.
- Bridge services remain healthy; Asterisk container ID is unchanged; exclusive USRP ownership is preserved; URF remains absent.
- Existing #198 live-audio and TGIF DMR credential gates remain open until separately accepted.

No push, merge, release publication, or node mutation is part of this preparation.
