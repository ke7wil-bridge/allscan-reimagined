# Issue #201 — AllScan v1.06 compatibility assessment (initial audit)

Date: 2026-11-20. Owner: Josh / KE7WIL. Separate from Thomas's #134. Status: **IN PROGRESS, not qualified, not deployable**. #200 remains blocked.

## Provenance and exact upstream changes
- Prior pinned v1.05: `db1800c53515f9fddd079d1ec6968e686136d82b`.
- Verified v1.06 upstream main commit: `bbfd2a444f05091ea6e66907fca0d804780c65f6`; upstream `include/common.php` says v1.06.
- v1.05→v1.06 commits: `e4abfc6` and `bbfd2a4`. Git diff: **12 files changed; 554 additions, 13 deletions**:
  `AllScanInstallUpdate.php`, `README.md`, `_tools/runSchedule.php`, `astapi/schedule.php`, `astapi/server.php`, `cfg/index.php`, `css/main.css`, `include/CfgModel.php`, `include/common.php`, `include/scheduleUtils.php`, `include/viewUtils.php`, `js/main.js`.
- Exact codeload source URL: https://codeload.github.com/davidgsd/AllScan/tar.gz/bbfd2a444f05091ea6e66907fca0d804780c65f6
- SHA-256 of downloaded upstream archive: `a06cb8e197f8b461482cdf6aec60740e1bcaf68345f57ca3694b51ba7176cc5b`.
- PHP lint passed: `scheduleUtils.php`, `schedule.php`, `runSchedule.php`, `cfg/index.php`.

## Confirmed blockers

**P0 — shared database config ID 14 collision.** ASR v1.05 `include/CfgModel.php` uses `showcmdbuttons = 14`, default **1**; AllScan v1.06 uses **the same key 14** for `schedulerEnabled`, default **0**. Shared `/etc/allscan/allscan.db` values from ASR can be mistaken for an enabled native scheduler after upgrading AllScan. An existing ASR 14=1 is particularly hazardous. Before any upgrade: choose nonconflicting ASR key (candidate 15), migrate ASR-owned key 14 into 15 **before stock v1.06 is exposed**, make scheduler 14=0 for migrated pre-v1.06 installations, distinguish already-stock-v1.06 databases, preserve rollback semantics. Add tests using SQLite fixtures; do not enable cron by accident.

**ASR status overlay hides native scheduling activity.** ASR `compat/allscan-v1.05/astapi/server.php` shadows upstream `astapi/server.php`. Upstream v1.06 adds `scheduleUtils.php`, activity-file tracking and `checkScheduleActivity()`. Rebase these onto existing ASR SSE, AMI guard, EchoLink, caching, status and talker logic without regression.

**Exact compatibility overlay required.** ASR `include/common.php` identifies v1.05, its `CfgModel.php` uses ASR policy and custom cfg 14. Add a separate `compat/allscan-v1.06` overlay; retain prior v1.02–v1.05. Update `stock-allscan.json`, `build-release.sh` and all compatibility/version self-tests together, not independently.

**Persistence and rollback gap.** Existing installer backs up the AllScan webroot and `/etc/allscan/allscan.db` but does not explicitly handle new `/etc/allscan/schedule.json`, `schedule.activity`, `schedule.heartbeat`, `schedule.log` or the web user's scheduler crontab entry. Add safe preservation/rollback and ensure single runner. Upstream minute runner supports `--dry-run`; it was not executed here.

**Other integration points.** Native scheduling adds `astapi/schedule.php`, `include/scheduleUtils.php`, `_tools/runSchedule.php`, stock `cfg/index.php`, `js/main.js`, `include/viewUtils.php` and `css/main.css` behavior. Reuse stock files and avoid shadowing/duplicating. Keep stock AllScan scheduling intact; ASR-specific scheduling controls must later appear in **ASR Admin only** (#200), never ASR's main dashboard.

## Next work and gates

1. Implement isolated v1.06 overlay and safe, reversible DB cfg migration with regression tests for 14=0, 14=1, already-native-v1.06 and rollback.
2. Integrate native scheduler activity into ASR SSE without breaking existing service/connection views; preserve native stock APIs and cron behavior.
3. Pin exact verified upstream v1.06 source + archive hash in a *candidate only*, update build manifest/tests.
4. Test clean install, v1.05 upgrade, cron/config preservation, dry run, native scheduling, ASR dashboard/admin, roles, settings/favorites, bridge links, rollback.
5. Full release gate and controlled acceptance required before #201 close; **#200 blocked** until #201 is accepted.
6. No push, merge, production changes, scheduler activation or deployment authorized.

## Audit environment

Isolated worktree `/home/josh/worktrees/asr-issue-201-allscan-v106`, branch `feature/issue-201-allscan-v106`, based on `origin/main` `f46bd55`. Upstream clone and tarball stored locally in ignored `.audit/`. No production changes.
