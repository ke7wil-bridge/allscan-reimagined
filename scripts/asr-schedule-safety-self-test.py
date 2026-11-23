#!/usr/bin/env python3
"""Static contract regression checks for the native AllScan scheduler adapter."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
p = root / 'compat/allscan-v1.06/asr-settings/settings-controller.php'
s = p.read_text()
checks = {
    'administrator only': 'adminUser()',
    'csrf validation': 'asrSettingsSchedulePostValid($asrScheduleCsrf)',
    'keyed csrf token': "hash_hmac('sha256', 'asr-settings-schedule-v1|'",
    'same-origin post': 'asrSettingsRollbackPostIsSameOrigin(true) && hash_equals($token, $posted)',
    'scheduler enable control': 'value="scheduler-enable"',
    'scheduler disable control': 'value="scheduler-disable"',
    'enable mirrors stock cfg contract': 'schedCronSync($asrEnable, $asrCronError)',
    'upstream validation': 'schedValidate($asrInput, $asrScheduleError)',
    'upstream persistence': 'schedSave(array_values($asrEntries)',
    'disconnect all prohibited': "$asrEntry['node'] === '0'",
    'disconnect first prohibited': "!empty($asrEntry['autodisc'])",
    'unsafe legacy toggle prohibited': "empty($entry['enabled']) && (($entry['node'] ?? '') === '0'",
    'schedule editing': "$asrOperation === 'edit'",
    'duration input': "'duration'",
    'scheduler dry run': "'--dry-run'",
    'favorite selection': 'asrSettingsScheduleFavorites()',
    'favorites parsed from commands': 'schedFavoritesFromLines(',
    'timer csrf': "Invalid timer security token.",
    'timer helper argv': "@proc_open($command",
    'timer inventory': "['list','--scope','all'",
    'timer confirmation': "timerConfirmation",
}
for name, text in checks.items():
    assert text in s, f'Missing {name}'

# ASR runs without PHP sessions; a $_SESSION token is never persisted, so
# every scheduling POST failed with "Invalid scheduling security token" (#200).
assert "$_SESSION['asrScheduleCsrf']" not in s, 'scheduling CSRF must not depend on PHP sessions'

# Under Apache PHP_BINARY is '' and proc_open() throws a ValueError (HTTP 500).
assert '[PHP_BINARY, $runner' not in s, 'dry run must not exec PHP_BINARY from the web SAPI'
assert 'asrSettingsPhpCliBinary()' in s and "PHP_SAPI === 'cli'" in s
assert 'catch(\\Throwable $e) { $process = false; }' in s, 'dry-run proc_open must not fatal'

runner = (root / 'compat/allscan-v1.06/_tools/runSchedule.php').read_text()
assert 'if($disabled && !$dryRun) exit(0);' in runner, 'dry run must preview while the scheduler is disabled'
assert 'if(!schedEnabled()) exit(0);' not in runner

utils = (root / 'compat/allscan-v1.06/include/scheduleUtils.php').read_text()
enable = utils[utils.index('function schedCronEnable('):utils.index('function schedCronDisable(')]
assert 'schedRunnerActive()' not in enable, 'enable must install the ASR runner entry, not trust a heartbeat'
assert 'schedCronLine()' in enable

reapply = (root / 'scripts/asr-reapply.sh').read_text()
sweep = reapply.index('[ -e "$file" ] && chmod 644 "$file"')
restore = reapply.index('chmod 755 "$ALLSCAN_DIR/_tools/runSchedule.php"')
assert restore > sweep, 'the scheduler runner must be made executable after the blanket chmod 644'
print('ASR schedule safety contract: ok')
