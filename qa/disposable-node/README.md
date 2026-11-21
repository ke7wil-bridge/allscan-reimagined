# Disposable ASR release-qualification VM (#129)

Host: `josh-Latitude-5420`; hypervisor: system libvirt/KVM; isolated test guest name: `asr-qa-disposable`.

## Design

1. Provision **once** a clean Debian 12 + ASL3 guest named `asr-qa-asl3-template`, with its sole qcow2 disk at `$HOME/vms/asr-qa/asr-qa-asl3-template.qcow2`. Do not install ASR in the template. Shut down the template before cloning. **Do not use any production node numbers, passwords, credentials, external reflector bridges, or network registrations.**
2. Run `qa/disposable-node/asr-vm-cycle.sh doctor`. It must report KVM/libvirt readiness and the base template present.
3. Run `qa/disposable-node/asr-vm-cycle.sh create` to create a 2-vCPU/2-GiB RAM overlay guest on the libvirt default NAT network. The template remains off; no boot-time autostart is configured for the disposable guest.
4. Qualify the release candidate inside the disposable guest: fresh install, upgrade, failed-install rollback, frontend integrity, reboot and health checks, config preservation, low-resource cases. Store structured results under `$HOME/vms/asr-qa/reports` **on the host**, not on the disposable guest.
5. After exporting logs/reports, run `qa/disposable-node/asr-vm-cycle.sh destroy` to remove only the named disposable guest and its overlay. The base image and host reports remain.

**Current scope:** The lifecycle script and host readiness checks are implemented; guest template provisioning and the test orchestrator are not yet implemented. `create` fails closed if the base template is missing or running. No ASR release is qualified by the host readiness check alone. The host currently has limited available memory and 46 GiB free disk; avoid simultaneous high-memory jobs and check resources before guest startup.

**Security/operational constraints:** Never target production hosts. No automatic publishing, merging, node registration, or deployment. Do not expose the test VM to inbound public ports. Do not modify other libvirt domains. Keep the template immutable while clones exist; rebase overlays deliberately if updating it.

## Host readiness report

`python3 qa/disposable-node/asr-qa-gate.py --output "$HOME/vms/asr-qa/reports/readiness.json"` checks the named disposable guest is running, the template is shut off, and the expected overlay disk is attached. It exits nonzero until ready and **always sets `releaseQualified: false`**; no installer tests are implied. The separate gate self-test runs in `build-release.sh`.

## Host storage blocker (discovered during inspection)

The Latitude's `$HOME/.local` and `$HOME/.local/share` are mode `0700`, so system-libvirt's QEMU account cannot traverse the proposed VM disk location. The lifecycle script now fails closed on this common case instead of producing a VM that cannot boot. A dedicated libvirt-accessible QA storage pool and corrected disk location must be configured **before** creating the ASL3 template. The existing `asr-beta8-test` pool has stale paths and must not be reused or modified without investigation.

## Dedicated storage prepared

`$HOME/vms/asr-qa` was created with an explicit `libvirt-qemu` ACL; unrelated libvirt storage pools and VMs remain untouched. The template itself still needs a clean ASL3 installation and verification before cloning.

## Debian staging guest (2026-11-06)

Created separate `asr-qa-debian-staging` libvirt guest, booted with 2 vCPU, 1536 MiB RAM, 10 GiB Debian 12 qcow2 disk at `$HOME/vms/asr-qa/debian12-staging.qcow2`. `virt-inspector` confirmed Debian 12 x86_64 and `qemu-img check` found no errors. Offline guest customization disabled the random root password, created `asrqa` with the host's existing SSH public key, and configured local test-only passwordless sudo. Guest is currently running, but libvirt NAT has not issued a DHCP lease, so guest SSH and ASL3 installation are **not yet verified**. Do not rename or clone this as the ASL3 template until ASL3 installation and guest networking are verified. No production nodes are involved.

## Verified ASL3 template and first clone (2026-11-06)

Official AllStarLink Debian 12 repository package `asl-apt-repos` was installed; `asl3` 3.18.2-2.deb12 and `asl3-asterisk` 2:22.10.1+asl3-3.10.8-1.deb12 installed successfully. Asterisk 22.10.1 and `app_rpt.so` were verified running in staging; `rpt nodes 1999` showed no connected nodes, and sample IAX registration lines were commented out. To prevent any unintended RF/network activity in clones, Asterisk was **disabled and stopped** before sealing the template. Staging guest was shut down, its standalone qcow2 image promoted to `asr-qa-asl3-template.qcow2`, and the powered-off libvirt template defined. Disposable overlay guest `asr-qa-disposable` was created and booted; private NAT DHCP and SSH work, packages were verified, and Asterisk remains inactive. The host readiness report is at `$HOME/vms/asr-qa/reports/host-ready-20261106.json` with `ready=true`, `releaseQualified=false`, `releaseTests=not-run`. Release tests have NOT run; no production nodes touched. Template SSH host keys and machine identity are currently inherited by clones; regenerate per clone before any wider distribution or parallel VM use.

## Guest qualification report (Issue #129)

Run `python3 qa/disposable-node/asr-guest-qualification.py --output "$HOME/vms/asr-qa/reports/guest-qualification.json"` from the development host. This verifies the named isolated ASL3 guest's baseline via libvirt NAT and SSH, records the temporary filesystem characteristics, and **fails closed** while install, upgrade, rollback, reboot, asset-integrity, and bridge-lifecycle tests remain unexecuted. A ready VM is never treated as a qualified release. No production host is contacted.

### Temporary filesystem preflight (Doug's node 67098)

Doug reported `/tmp` as a 128-MiB `tmpfs` mounted `noexec`, despite having nearly all bytes and inodes free. This does **not** prove a disk-full failure. The installer preflight now rejects `noexec` temporary staging with an actionable diagnostic, in addition to checking space and inodes. Future integration tests must cover both a constrained `noexec` `/tmp` and a usable alternate staging directory; never remount a production node as part of a test.
