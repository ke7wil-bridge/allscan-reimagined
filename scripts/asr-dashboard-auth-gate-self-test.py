#!/usr/bin/env python3
"""Guard against rendering protected dashboard before auth resolution (#111)."""
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'src/App.tsx').read_text()
assert 'const [authChecked, setAuthChecked] = useState(false)' in source
assert 'setAuthChecked(true)' in source
assert 'setAuthChecked(false)' in source
assert 'window.setTimeout(() => void refreshAuthStatus(), 10000)' in source
assert 'sequence === requestSequence' in source
assert 'if (!authChecked || !authStatus.canRead)' in source
assert 'role="status" aria-live="polite" aria-busy="true"' in source
assert 'Verifying dashboard access…' in source
assert 'Redirecting to login…' in source
assert source.index('if (!authChecked || !authStatus.canRead)') < source.rindex('return (\n    <div className="allscan-app')
assert "window.location.assign(asrPath('user/'))" in source
print('ASR dashboard authentication gate contract: PASS')
