# Windows Local QA VM

Independent Windows/QEMU alternative to the Linux/libvirt recipe. No Docker,
production node identities, or Josh's credentials are used. This single-VM
profile is named `thomas-asr-qa`, using unregistered private node `1999`.

Requirements: Windows Hypervisor Platform, QEMU in `C:\Program Files\qemu`,
OpenSSH client, Python 3.11+, and `uv`. Guest: Debian 13, 2 vCPU, 2048 MiB RAM,
30 GiB sparse QCOW2. Runtime files and fresh dedicated SSH credentials are kept
outside Git in `%USERPROFILE%\Documents\Codex\ASR-VM`. Protect the private key
with a Windows account-only ACL. Never copy production credentials here.

## Routine Commands

Run from the repository root in PowerShell:

```powershell
$vm = 'qa/disposable-node/windows/asr_vm.py'
uv run --with pycdlib python $vm start
uv run --with pycdlib python $vm trust
uv run --with pycdlib python $vm check
uv run --with pycdlib python $vm logs
uv run --with pycdlib python $vm regression
uv run --with pycdlib python $vm stop
```

Wait for startup before `trust`/`check`. `trust` verifies the loopback listener
belongs to this QEMU PID before pinning its SSH key. It also reports whether
the fingerprint appears in the serial console (some boots do not print it).
This intentionally trusts the locally launched guest, never a remote endpoint.
Dashboard: `http://127.0.0.1:18093/asr/`. SSH: `127.0.0.1:22293`.
QMP: `127.0.0.1:49193`. AMI/IAX bind to guest localhost and are not forwarded.
Local listeners are accessible to other Windows users, so use a trusted host.

Normal startup uses QEMU `restrict=on`, blocking guest-originated external
traffic. Only explicit `start --provision` enables NAT for reviewed downloads.
Stop afterward and restart normally. No live radio connections are authorized.
Keep the VM off between Linux-dependent tests to conserve host RAM.

## Initial Provisioning

Download the official Debian 13 generic amd64 cloud image and verify its
published SHA512SUMS. Convert it to standalone `asr-qa-asl3-template.qcow2` and
resize to 30 GiB. Generate a dedicated key at `ssh\id_ed25519`, run `seed`, then
`start --template --provision`. Review and transfer `provision-guest.sh` and run
it **only inside this fresh guest**. It refuses an already-provisioned guest.
It installs official ASL3 packages with private USRP node 1999 and random AMI
credentials retained only in guest `/root/asr-qa/ami.json` (mode 600).

Use the reviewed upstream ASR bootstrap in an interactive SSH terminal. Allow
anonymous read-only monitoring for dashboard checks; control still needs login.
Complete the official first-user form, or use the optional Edge-based helper:

```powershell
uv run --with playwright python qa/disposable-node/windows/initialize-web.py
```

It generates a QA-only login stored in guest `/root/asr-qa/web-admin.json`, mode
600, never printed or committed. Existing users are preserved.

After `check` and browser validation, `seal` gracefully stops the template,
checks its QCOW2 structure, and records its SHA256. `create-overlay` creates the
first disposable disk. The sealed base cannot be booted by this helper.

Before sealing, run the reviewed `offline-guest.sh` inside the guest. It disables
ASL directory-download services and their timers, which otherwise fail
on intentionally offline boots. It does not change Asterisk routing.
It restores Asterisk's read access to `manager.conf` after the stock installer,
keeps Apache in the Asterisk group, and restarts these two **guest-only** services.

## Reset and Branch Testing

With the VM off, discard only disposable changes:

```powershell
uv run --with pycdlib python $vm reset --confirm-reset
uv run --with pycdlib python $vm start
uv run --with pycdlib python $vm trust
uv run --with pycdlib python $vm check
```

Reset verifies the base digest, preserves the template, replaces the overlay,
and creates a new cloud-init identity and SSH host keys. Repeat `trust` afterward.

Normal build/lint/tests stay on Windows. For Linux fixture regressions, upload a
reviewed `git archive` into guest `/home/asrqa/asr-source`, then run `regression`.
This tests synthetic DMR/YSF/P25/NXDN/M17/URF cases, not live bridges. Zello,
real audio interoperability, and upstream application update/rollback acceptance
remain unqualified. Reports always retain `releaseQualified: false`.

For branch deployment, build and inspect the supported release package, then
use its normal installer only in a disposable guest. Whole-VM reset is the
verified environment rollback; no automatic production deployment is provided.
