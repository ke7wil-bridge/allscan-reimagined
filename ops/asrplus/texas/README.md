# Texas 674984 staged package

This directory is source material only. It contains no credential and performs
no deployment.

- `asrplus-agent.json`: telemetry-only, required-integrity agent template.
- `asrplus-central-hub.json`: isolated central registration with capacity 60.
- `radio-secure-capacity.conf`: inbound-only admission block; local loopback
  bridges bypass the count.
- `asrplus-texas-agent.service`: dedicated-account systemd sandbox.
- `asrplus-texas-talker-watcher.service`: dedicated continuous AMI watcher.
- `asrplus-agent.sysusers`: non-login account and Asterisk group membership.
- `authorized_keys.template`: Kentucky-side source-restricted forced endpoint.

Use `scripts/asrplus-texas-install.py` and follow
`docs/texas-674984-install-runbook.md`. Live activation is prohibited until the
ten gates in `docs/texas-674984-activation-checklist.md` are individually
approved and recorded.
