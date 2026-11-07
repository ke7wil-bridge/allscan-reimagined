#!/usr/bin/env python3
"""Guard the URF save-vs-runtime activation contract across supported settings versions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for version in ('1.02', '1.03', '1.04', '1.05'):
    source = (ROOT / f'compat/allscan-v{version}/asr-settings/settings-controller.php').read_text()
    assert 'data-urf-install-runtime' in source, version
    assert 'Saving alone does not activate runtime services.' in source, version
    assert 'Preview &amp; Apply URF Runtime' in source, version
installer = (ROOT / 'scripts/asr-bridge-setup-urf-install.py').read_text()
for marker in ('verify_native_runtime(', 'verify_asterisk_link(', 'wait_for_active_units(',
               'restore_native_runtime(', 'capture_runtime_diagnostics('):
    assert marker in installer, marker
print('URF save/activation contract self-test: PASS')
