# Texas 674984 installation, rollback and emergency isolation

Status: operator-ready repository workflow; not deployed. Every live step is
blocked on the approval gates in `docs/texas-674984-activation-checklist.md`.

## Safety model

Texas starts telemetry-only. `controlPolling` is `false`, so the agent uses
only `asrplus-status`; it cannot lease or execute a queued command. The Texas
HMAC identity, SSH identity, ledger and state paths are distinct. The
installer never generates, prints or copies a credential. Host-specific
values are supplied in a protected JSON file outside the repository.

The admission block counts only non-loopback inbound channels in
`${EXTEN}@rptin`. Channels 1–60 are admitted, channel 61 is rejected, and the
group count is released automatically when a channel ends. Local loopback
bridges, outbound links and inter-hub links are not part of this inbound cap.
ASR Plus separately displays capacity 60. This is a policy limit, not a claim
that an untested Texas VM can sustain 60 radio connections.

## Host-specific inputs

Before any live action, create a mode-0600 values file outside the checkout:

```json
{
  "kentuckyAddress": "APPROVED_KENTUCKY_ADDRESS",
  "texasSourceIp": "APPROVED_TEXAS_SOURCE_IP",
  "texasPublicKey": "APPROVED_TEXAS_PUBLIC_KEY",
  "keyId": "APPROVED_TEXAS_HMAC_KEY_ID"
}
```

Do not put private keys, HMAC bytes, AMI secrets or bearer tokens in this file,
the repository, terminal output or the operator package. Provision the Texas
SSH private key, pinned Kentucky host key, HMAC key and dedicated AMI file by
the approved out-of-band process. Expected owner-only paths are named in
`ops/asrplus/texas/asrplus-agent.json`.

## Read-only preflight

Run from the reviewed commit:

```text
python3 scripts/asrplus-texas-install.py preflight --values /protected/texas-values.json
```

Additionally record the OS/ASL3/ASR versions, node identity 674984, hostname,
source address, DNS and NTP state, provider console access, disk/inodes, current
Asterisk links, service state, firewall export, `/etc/asterisk` integrity and
credential ownership/modes. The operator must verify the generated admission
patch against the actual `radio-secure` context. Any mismatch stops the install.
The reviewed compatibility target is Debian 12/ASL3 with AllScan Reimagined
Beta 8.6.1 behavior preserved; a different major OS, ASL or Asterisk layout is
a new qualification, not permission to force the patch.

## Backup and install

Immediately before installation, take the approved provider snapshot and a
host backup of Asterisk configuration, unit state and any existing ASR Plus
paths. The guarded installer creates a second file-level backup with SHA-256
metadata under `/var/backups/asrplus-texas/` and rolls back automatically on
an interrupted/partial write.

The exact live command must be reviewed and approved at action time:

```text
python3 scripts/asrplus-texas-install.py install --apply \
  --approval-id APPROVAL_RECORD --values /protected/texas-values.json
python3 scripts/asrplus-texas-install.py verify
```

The installer lays down the agent, integrity module, narrow link-control
helper, hardened watcher/path/unit/timer set, telemetry-only configuration,
forced-key fragment,
60-inbound dialplan block and central registration fragment. It does not start
or enable a unit, reload Asterisk, add the Kentucky authorized key, register
Texas centrally, open a firewall or create a radio link. Those are separate
approved actions. Re-running the installer is idempotent.

Apply the installed `asrplus-texas.conf` with `systemd-sysusers` only after the
file and approval are verified; it creates the dedicated `asrplus-agent`
service account without login or home and adds only the Asterisk group.
Grant only read access to its dedicated AMI file and the Asterisk control socket
group needed by the validated helper. The service keeps `NoNewPrivileges`, an
empty capability set, strict filesystem protection and two writable state
paths. No sudo rule is installed. If the actual Texas Asterisk socket cannot be
made group-accessible, stop and obtain an operator decision; do not weaken the
unit or grant a shell.

## Post-install verification

Before enabling telemetry, verify all hashes and modes, `sshd -T`, the exact
forced/source-restricted key, `systemd-analyze verify`, Asterisk configuration
syntax, node 674984 identity, admission block count, empty ledger/queue and no
unexpected links. Enable only the telemetry timer, confirm a signed identity-
bound status reaches the isolated Texas status path, and confirm the dashboard
shows capacity 60 and freshness. Control polling remains false.

## Rollback and uninstall

Stop only the Texas agent unit/timer after approval. Do not restart Asterisk or
delete a ledger. Restore the exact backup named by the install result:

```text
python3 scripts/asrplus-texas-install.py rollback --apply \
  --approval-id APPROVAL_RECORD --backup /var/backups/asrplus-texas/TIMESTAMP
```

For a full pre-activation uninstall, use the same reviewed backup with the
`uninstall` command; it intentionally follows the identical integrity-checked
restore path rather than inventing a second removal procedure.

Verify restored hashes, Asterisk syntax, link state, service state and absence
of Texas central registration. Revoke the dedicated Kentucky authorized key,
HMAC identity and Texas SSH private key as separate recorded actions. Provider
snapshot restoration is the final rollback option and requires its own approval.

## Emergency isolation

The first safe action is to stop new controls while preserving evidence:

```text
python3 scripts/asrplus-texas-install.py emergency-isolate --apply \
  --approval-id INCIDENT_RECORD
```

This sets `controlPolling` false; it does not stop radio service or clear state.
Then, with incident authorization, disable the Texas forced key on Kentucky,
stop the Texas agent timer, preserve queue/result/archive/ledger/audit files,
and isolate the radio link through the approved Asterisk procedure. Never
delete or edit inflight work to force recovery.
