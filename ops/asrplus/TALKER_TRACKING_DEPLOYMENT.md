# ASR Plus real-time performance deployment

This change is staged only. It must not be run until deployment is approved.

## Order

1. Back up every file listed below before replacing it.
2. On Kentucky, install `asrplus-remote-endpoint.py`, `asrplus-status.py`, `asrplus.js`, `index.php`, `asrplus.json`, and `asrplus-status.path` at their existing paths. The config adds the existing Kentucky live-watcher JSON as the local talker source.
3. On Phoenix and Georgia, install `asrplus-agent.py` at `/usr/local/sbin/asrplus-agent`. No remote agent configuration or unit change is required; the existing watcher gains a serialized background transition publisher.
4. Reload systemd. Restart only `asrplus-talker-watcher.service` on Phoenix/Georgia and the ASR Plus status path/timer on Kentucky. Do not restart Asterisk or alter links.
5. Verify the central path watches the Kentucky live-talker file and `/var/lib/asrplus/status-event`, both agent paths remain active as fallbacks, all recovery timers remain active, and no unit has a start-limit failure.
6. Verify JSON freshness, three-hub connection rows, TX/unkey behavior, recent ordering, controls, and bridge/favorites status before removing backups.

## Backup

Create a timestamped root-only backup on each host before replacement. Kentucky must include the status builder, endpoint, JavaScript, index, config, and status path file. Phoenix and Georgia must include the agent. `/root/asrplus-backup-20261112-unkey-v2/` was verified to contain the complete previous deployment set, but it does not contain the newly changed `asrplus.json`; a new backup is mandatory.

## Live latency measurement

1. Open the authenticated dashboard console and inspect `window.asrPlusTelemetry` after key and unkey.
2. Record the Asterisk event timestamp when supplied, watcher detection, local state publication, agent start/transmit, Kentucky receipt, aggregation, API response, and browser render fields. All three hosts currently report NTP synchronization.
3. Correlate those fields with `journalctl -u asrplus-agent.service` on Phoenix and `journalctl -u asrplus-status.service` on Kentucky.
4. Confirm key and unkey each render in about one second, all three hub badges clear together, and the completed event appears once in Recent Talkers.
5. Repeat with 0.5, 1, 2, 3, and 10 second transmissions plus rapid consecutive transmissions.

## Rollback

Restore every replaced file from the new pre-deployment backup. The older unkey-v2 backup may be used only for files actually present in it. Reload systemd, reset failed ASR Plus units, and restart only ASR Plus path/timer/watcher units. Do not restart Asterisk. No AllStarLink connect/disconnect command is part of deployment or rollback.
