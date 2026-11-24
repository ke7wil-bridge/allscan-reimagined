# Texas 674984 preapproval package

This package is review material, not deployment authorization. Texas has not
been contacted, installed or linked. No credential is included.

Read in this order:

1. `texas-674984-isolated-qualification-report-2026-11-24.md`
2. `texas-674984-activation-checklist.md`
3. `texas-674984-operator-decision-record.md`
4. `texas-674984-install-runbook.md`
5. `texas-hub-674984-readiness-254.md`
6. `asrplus-security-inspection-package-2026-11-24.pdf`

The JSON files are machine-readable evidence. The `scripts` and `ops` directories
contain the reviewed installer, narrow helper and non-secret configuration
templates. Do not substitute real addresses, public keys or key IDs until the
operator gates are approved. Never put a private key, HMAC key, AMI secret,
session cookie or bearer token in this package.

The proposed policy is 60 non-loopback inbound radio connections. It is not a
total Asterisk channel limit and is not Texas hardware certification. Local IPv4 loopback bridges and outbound links are excluded; inbound non-loopback inter-hub links count toward the cap. Actual link behavior requires host validation. The command
queue operational ceiling is 80 and its hard implementation limit is 100.

The exact remaining approvals are gates 1-10 in the activation checklist.
