# Issue #200 — Admin Scheduling

ASR Beta 8.6 integrates administrator scheduling with the native AllScan v1.06 scheduler and adds a separately guarded systemd timer console. It does not install a second connection scheduler or a second cron entry.

## Scheduled AllStar connections

Open **Admin → Settings → Scheduling** as an AllScan administrator. Entries use `/etc/allscan/schedule.json`, the native AllScan scheduler configuration, cfg key, AMI execution path, and `_tools/runSchedule.php` minute runner. Existing upstream entries remain readable and executable.

ASR supports Connect, Monitor, Local Monitor, and single-node Disconnect; daily or selected weekdays; node numbers or discovered Favorites; permanent links; and an optional 1–1440 minute duration. Duration state, runner state, and bounded JSONL history are stored beside the native scheduler data under `/etc/allscan/` and are included in ASR install/upgrade rollback backups.

Times use the node's configured system timezone. During the fall DST transition, an entry runs only once for a repeated local wall-clock minute. A time skipped by the spring DST transition does not run. The runner uses a nonblocking file lock. When multiple entries for the same node are due together, Disconnect wins; otherwise the lowest stable schedule ID wins, and skipped work is logged. Disconnect-all and upstream's disconnect-first option cannot be created or enabled through ASR.

**Dry Run Current Minute** invokes the native runner with `--dry-run`; it does not send AMI commands or modify scheduler claim state. Heartbeat and recent execution/error history appear on the same page.

## Systemd timers

The timer console inventories system timers and the invoking web account's user-manager timers. Inventory merges unit files with runtime timers so inactive, disabled, static, generated, transient, and package-provided timers remain visible. Details include enablement, active/sub states, next and previous execution, associated service, result, unit definition, and bounded journal history.

All non-ASR timers are inspect-only. A protected critical-timer denylist applies in addition to that ownership boundary. Actions are limited to ASR-owned units. New definitions, edits, deletion, and rollback are limited to `allscan-reimagined-admin-*.timer`, and associated services must be `allscan-reimagined-*.service`. The interface supports preview plus `systemd-analyze calendar`/`verify`, enable, disable, start, stop, restart, manual service trigger, delete, and rollback. Every mutation requires an exact action-and-unit confirmation.

The root helper uses fixed argument arrays, strict unit/service/calendar validation, a process lock, atomic replacement, root-only timestamped backups, and JSONL audit records. It never accepts shell commands or arbitrary paths. A failed definition apply restores its pre-change backup before returning an error.

User-manager inventory requires an active user systemd manager for the web account. If none exists, the page reports a partial-inventory warning while continuing to show system timers; ASR does not start a user manager or alter login lingering.

## Upgrade and rollback

The v1.06 compatibility overlay supplies the extended native `scheduleUtils.php` and `runSchedule.php`. Installer and release rollback state includes `schedule.pending.json`, `schedule.state.json`, and `schedule.history.jsonl` in addition to the native schedule, heartbeat, activity, log, and web crontab state. Reapply installs the constrained timer helper and its sudoers entry, then validates sudoers normally.

No deployment or scheduler activation occurs merely by building or testing this repository.
