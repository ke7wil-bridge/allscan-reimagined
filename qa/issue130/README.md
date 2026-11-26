# Issue 130 isolated qualification

`qualify.py` is the host-side, fail-closed runner for the dedicated
`asr-130-offline` libvirt guest. It verifies the exact branch and commit,
trusted artifact digests and actual prepared release contents, the absence of any VM network interface, guest loopback-only
state, prerequisites, and the clean prepared guest state before mutation.

Run the non-mutating runner test:

```sh
python3 qa/issue130/qualify.py --self-test
```

After provisioning a clean, dedicated networkless guest with read-only QA media (the retained evidence VM is already installed; there is no prepared snapshot), run the native
installation/upgrade qualification:

```sh
python3 qa/issue130/qualify.py --run-native \
  --candidate-sha256 TRUSTED_CI_ARTIFACT_SHA256 \
  --report /home/josh/vms/asr-qa/reports/issue130-qualification.json
```

The runner has bounded waits, writes a JSON PASS/FAIL/BLOCKED report, never
adds a network interface, never contacts production, removes its temporary
authentication material, and powers off only the dedicated VM it started. Exit status
is 0 for PASS, 1 for FAIL, and 2 when otherwise successful work has a BLOCKED
item. It deliberately reports the privileged host-broker qualification as
BLOCKED until run on a separately authorized disposable broker host; it does
not weaken or bypass host sudo authentication.

The prepared stock AllScan v1.06 tree is sourced from a checksum-verified
upstream archive. This tests installation from that staged tree, not the
upstream stock download process. Simulated browser fixtures remain regression
coverage and are not represented as native end-to-end acceptance.
