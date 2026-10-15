# Node-health watchdog

This optional operational watchdog complements ASR's bounded AMI circuit
breaker. It verifies both `core show uptime` and `rpt lstats NODE` once per
minute. Three consecutive failures are required before restarting Asterisk,
and restarts have a 30-minute cooldown. A bounded health snapshot is retained
in `/var/log/asr-node-health.log` every five minutes and rotated for 14 days.

Configure `/etc/default/asr-node-health` with at least `NODE_NUMBER`. Native
nodes use `MODE=native`. Docker hosts use `MODE=docker` and may set
`ASTERISK_CONTAINER`. The `.user.service` and `.user.timer` variants support
an unprivileged Docker operator; configure `%h/.config/asr-node-health.env`
and install the script under `%h/.local/bin`. User-mode logs rotate internally
because system logrotate does not process files in a home directory.
