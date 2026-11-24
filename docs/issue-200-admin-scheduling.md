# Issue #200 — Admin Scheduling

ASR Beta 8.7 integrates administrator scheduling with the native AllScan v1.06 scheduler and adds a separately guarded systemd timer console. It does not install a second connection scheduler or a second cron entry.

## Scheduled AllStar connections

Open **Admin → Settings → Scheduling** as an AllScan administrator. Entries use `/etc/allscan/schedule.json`, the native AllScan scheduler configuration, cfg key, AMI execution path, and `_tools/runSchedule.php` minute runner. Existing upstream entries remain readable and executable.

ASR supports Connect, Monitor, Local Monitor, and single-node Disconnect; daily or selected weekdays; node numbers or discovered Favorites; permanent links; and an optional 1–1440 minute duration. Duration state, runner state, and bounded JSONL history are stored beside the native scheduler data under `/etc/allscan/` and are included in ASR install/upgrade rollback backups.

Times use the node's configured system timezone. During the fall DST transition, an entry runs only once for a repeated local wall-clock minute. A time skipped by the spring DST transition does not run. The runner uses a nonblocking file lock. When multiple entries for the same node are due together, Disconnect wins; otherwise the lowest stable schedule ID wins, and skipped work is logged. Disconnect-all and upstream's disconnect-first option cannot be created or enabled through ASR.

**Dry Run Current Minute** invokes the native runner with `--dry-run`; it does not send AMI commands or modify scheduler claim state. Heartbeat and recent execution/error history appear on the same page.

## Systemd timers

The timer console inventories system timers and the invoking web account's user-manager timers. Inventory merges unit files with runtime timers so inactive, disabled, static, generated, transient, and package-provided timers remain visible. Details include enablement, active/sub states, next and previous execution, associated service, result, unit definition, and bounded journal history.

Existing noncritical system timers and timers in the invoking web account's user manager can be enabled, disabled, started, stopped, restarted, and manually triggered. Manual execution starts only the timer's single declared service; it never accepts a command or path. Critical timers, protected services, and units in a critical dependency chain are denied. Generated and transient timers can be operated when safe but cannot be edited as persistent files.

New ASR definitions remain limited to `allscan-reimagined-admin-*.timer` and may trigger only `allscan-reimagined-*.service`. Existing package or OS timer schedules are edited only through `90-allscan-reimagined-schedule.conf` in that timer's drop-in directory; the vendor unit is never replaced or deleted. Calendar expressions and monotonic `OnBootSec`, `OnStartupSec`, `OnActiveSec`, `OnUnitActiveSec`, and `OnUnitInactiveSec` intervals are supported and validated by `systemd-analyze`. Only ASR Admin-owned timer files can be deleted.

Before a definition or override change, ASR records the prior content plus enabled/active state in a root-only timestamped backup. Rollback first creates a safety backup, restores or removes only the ASR-owned file/drop-in, reloads the correct system or user manager, and restores enablement/activity. Every mutation and failed attempt requires an exact action-and-unit confirmation and is written to the JSONL audit with actor, scope, action, unit, outcome, and bounded detail.

The root helper uses fixed argument arrays, strict unit/service/schedule validation, derived fixed storage locations, a process lock, atomic replacement, root-only timestamped backups, and JSONL audit records. It never accepts shell commands or arbitrary paths. A failed definition or override apply restores its pre-change backup before returning an error.

User-manager inventory requires an active user systemd manager for the web account. If none exists, the page reports a partial-inventory warning while continuing to show system timers; ASR does not start a user manager or alter login lingering.

## Upgrade and rollback

The v1.06 compatibility overlay supplies the extended native `scheduleUtils.php` and `runSchedule.php`. Installer and release rollback state includes `schedule.pending.json`, `schedule.state.json`, and `schedule.history.jsonl` in addition to the native schedule, heartbeat, activity, log, and web crontab state. Reapply installs the constrained timer helper and its sudoers entry, then validates sudoers normally.

No deployment or scheduler activation occurs merely by building or testing this repository.
