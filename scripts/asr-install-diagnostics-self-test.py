#!/usr/bin/env python3
"""Static safety contract for installer event logging (never executes installer)."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
installer = root / 'install.sh'
if not installer.is_file():
    installer = root.parent / 'install.sh'
script = installer.read_text()
assert 'ASR_INSTALL_DIAGNOSTICS="/var/log/allscan-reimagined-install.log"' in script
assert 'started|preflight-passed|rollback-started|rollback-finished|completed|failed' in script
assert '[ ! -L "$ASR_INSTALL_DIAGNOSTICS" ]' in script
assert '[ -f "$ASR_INSTALL_DIAGNOSTICS" ]' in script
assert '(umask 077; printf' in script
for event in ('started', 'preflight-passed', 'rollback-started', 'rollback-finished', 'completed', 'failed'):
    assert f'asr_diagnostic {event}' in script
assert "trap 'asr_install_exit_status=$?; if [ \"$asr_install_exit_status\" -ne 0 ]; then asr_diagnostic failed; fi' EXIT" in script
assert script.index('asr_diagnostic preflight-passed') < script.index('DEBIAN_FRONTEND=noninteractive apt-get update', script.index('asr_diagnostic preflight-passed'))
# Fresh stock installs cannot enforce login-only without a stock administrator.
# This guard must execute before the access-policy update and provide guidance.
admin_guard = 'Stock AllScan login-only mode requires an existing stock administrator account.'
assert admin_guard in script
assert script.index(admin_guard) < script.index('The stock /allscan access policy could not be updated.')
assert 'count($admins) > 0 ? 0 : 1' in script
print('ASR installer diagnostic logging safety contract: PASS')
