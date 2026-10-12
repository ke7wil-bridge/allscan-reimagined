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

## Implemented backend boundary

`ASR_CONTAINER_PROVISIONING_PROFILE` is the only selector for the optional
container backend. On a normal ASL3 host the planner emits `backend: native`
even when a Docker executable is present. A container-host plan fails closed if
Docker Compose is unavailable; a native build or activation failure rolls back
without attempting the container backend.

The shared native runtime is staged transactionally at
`/opt/allscan-reimagined-bridges/.runtime/urf-native/current`. Its manifest
binds the four pinned nostar revisions, source archive hashes, firmware hashes,
the pinned architecture-specific MMDVM_Bridge artifact, every ASR patch, and
every installed runtime file. URFD is built for the host. The qualified ARMv7
software-AMBE TCD is built from the pinned IMBE/MD380 inputs and runs directly
on armhf or through the staged `qemu-arm-static` on amd64/arm64.

Each bridge has a compatibility aggregate service plus restricted native URFD,
TCD, and TGIF component services. Components run as `asr-bridge` with a strict
read-only filesystem view and only their per-bridge data or health directory
writable. The TGIF key remains in a root-owned, `asr-bridge`-group-readable
0640 file and is passed only by file path. The container aggregate unit and its
Compose/image transaction remain unchanged behind the explicit profile.
