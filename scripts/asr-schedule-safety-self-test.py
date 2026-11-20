#!/usr/bin/env python3
"""Static contract regression checks for the native AllScan scheduler adapter."""
from pathlib import Path
p = Path(__file__).resolve().parents[1] / 'compat/allscan-v1.06/asr-settings/settings-controller.php'
s = p.read_text()
checks = {
    'administrator only': 'adminUser()',
    'csrf validation': 'hash_equals((string)$_SESSION[\'asrScheduleCsrf\']',
    'upstream validation': 'schedValidate($asrInput, $asrScheduleError)',
    'upstream persistence': 'schedSave(array_values($asrEntries)',
    'disconnect all prohibited': "$asrEntry['node'] === '0'",
    'disconnect first prohibited': "!empty($asrEntry['autodisc'])",
    'unsafe legacy toggle prohibited': "empty($entry['enabled']) && (($entry['node'] ?? '') === '0'",
    'schedule editing': "$asrOperation === 'edit'",
    'duration input': "'duration'",
    'scheduler dry run': "'--dry-run'",
    'favorite selection': 'asrSettingsScheduleFavorites()',
    'timer csrf': "Invalid timer security token.",
    'timer helper argv': "@proc_open($command",
    'timer inventory': "['list','--scope','all'",
    'timer confirmation': "timerConfirmation",
}
for name, text in checks.items():
    assert text in s, f'Missing {name}'
print('ASR schedule safety contract: ok')
