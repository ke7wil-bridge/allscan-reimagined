# Native bridge runtime requirement

ASR bridge provisioning has two separate deployment concerns:

- **Native ASL3 (default):** a physical/normal ASL3 node must not require Docker to install or run ASR-managed bridges. Bridge processes run as restricted systemd services on the host.
- **Container-host (optional):** containerized/virtual layouts may use the existing Docker-backed runtime and host broker. Docker must never be inferred merely because it is installed.

The provisioning backend already defaults to native ASL3. The remaining Docker-only component is the URFWIL/TGIF runtime implementation. Native URFWIL/TGIF must preserve the qualified protocol/configuration behavior while replacing Docker image/Compose lifecycle with host build/stage and systemd lifecycle.

## Native URFWIL/TGIF runtime

The native implementation must use the same pinned upstream revisions and ASR patches as the qualified container runtime. It must:

1. build/stage `urfd`, `tcd`, and required vocoder/runtime assets from pinned, checksum-verifiable sources;
2. install the TGIF adapter and pinned `MMDVM_Bridge` artifact without a Docker image;
3. render the same per-bridge `urfd.ini`, `tcd.ini`, TGIF configuration, ports, identities, and secrets;
4. run each component under dedicated restricted systemd units and the existing ASR bridge service account/boundaries;
5. retain plan-digest, ownership, rollback, health-check, audit, and second-install guarantees;
6. leave the current Docker/Compose implementation available only when the explicit container-host provisioning profile is active.

Do not weaken bridge qualification or silently fall back to Docker on a native node.
