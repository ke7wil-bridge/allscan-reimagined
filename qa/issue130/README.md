# Issue 130 isolated qualification

`qualify.py` is the host-side, fail-closed runner for the dedicated
`asr-130-offline` libvirt guest. It verifies the exact branch and commit,
checksum sidecars, the absence of any VM network interface, guest loopback-only
state, prerequisites, and the clean prepared guest state before mutation.

Run the non-mutating runner test:

```sh
python3 qa/issue130/qualify.py --self-test
```

After restoring the dedicated VM's prepared clean snapshot, run the native
installation/upgrade qualification:

```sh
python3 qa/issue130/qualify.py --run-native \
  --report /home/josh/vms/asr-qa/reports/issue130-qualification.json
```

The runner has bounded waits, writes a JSON PASS/FAIL/BLOCKED report, never
adds a network interface, never contacts production, removes its temporary
authentication material, and always powers off the dedicated VM. Exit status
is 0 for PASS, 1 for FAIL, and 2 when otherwise successful work has a BLOCKED
item. It deliberately reports the privileged host-broker qualification as
BLOCKED until run on a separately authorized disposable broker host; it does
not weaken or bypass host sudo authentication.

The prepared stock AllScan v1.06 tree is sourced from a checksum-verified
upstream archive. This tests installation from that staged tree, not the
upstream stock download process. Simulated browser fixtures remain regression
coverage and are not represented as native end-to-end acceptance.
