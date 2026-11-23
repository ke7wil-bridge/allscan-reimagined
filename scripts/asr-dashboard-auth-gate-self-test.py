#!/usr/bin/env python3
"""Guard against rendering protected dashboard before auth resolution (#111)."""
from pathlib import Path

source = (Path(__file__).resolve().parents[1] / 'src/App.tsx').read_text()
assert 'const [authChecked, setAuthChecked] = useState(false)' in source
assert 'setAuthChecked(true)' in source
assert 'setAuthChecked(false)' in source
assert 'window.setTimeout(() => void refreshAuthStatus(), 10000)' in source
assert 'if (cancelled || inFlight) return' in source
assert "window.addEventListener('online', refreshAuthStatus)" in source
assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
assert 'inFlight = false' in source
api = (Path(__file__).resolve().parents[1] / 'src/lib/allscanLive.ts').read_text()
auth = api.split('export async function fetchAuthStatus()', 1)[1].split('export type TgifUserStatus', 1)[0]
assert 'AbortSignal.timeout(10000)' in auth
assert 'requestSequence' not in source.split('const refreshAuthStatus = async () => {', 1)[1].split('const onVisibilityChange', 1)[0]
assert 'if (!authChecked || !authStatus.canRead)' in source
assert 'role="status" aria-live="polite" aria-busy="true"' in source
assert 'Verifying dashboard access…' in source
assert 'Redirecting to login…' in source
assert source.index('if (!authChecked || !authStatus.canRead)') < source.rindex('return (\n    <div className="allscan-app')
assert "window.location.assign(asrPath('user/'))" in source
print('ASR dashboard authentication gate contract: PASS')
