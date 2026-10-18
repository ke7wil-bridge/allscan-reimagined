#!/usr/bin/env node
import { readFileSync } from 'node:fs'

const source = readFileSync(
  new URL('../src/components/UpdateAsrDialog.tsx', import.meta.url),
  'utf8',
)
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const check = (condition, message) => { if (!condition) throw new Error(message) }

check(
  source.includes('const [available, setAvailable] = useState<UpdateCheck | null>(null)'),
  'Update dialog trusts cached release data before completing a live check.',
)

const automaticCheck = source.slice(
  source.indexOf('useEffect(() => {\n    if (jobId) return'),
  source.indexOf('\n\n  const run = async'),
)
check(
  automaticCheck.includes('void checkAsrUpdate()') && automaticCheck.includes('}, [jobId])'),
  'Opening the update dialog does not perform a live release check.',
)

const manualCheck = source.slice(
  source.indexOf('const check = () => {'),
  source.indexOf('  const apply ='),
)
check(
  manualCheck.indexOf('setAvailable(null)') >= 0
    && manualCheck.indexOf('setAvailable(null)') < manualCheck.indexOf('checkAsrUpdate()'),
  'Manual update check does not clear stale release data before refreshing.',
)
check(
  source.includes("{busy ? 'Checking…' : (available?.availableVersion || '—')}")
    && !source.includes('initialUpdate?.availableVersion'),
  'Update dialog can present a cached available release as current.',
)
check(
  source.includes('install it here in the browser'),
  'Update dialog does not clearly identify the browser installation path.',
)
check(
  appSource.includes("const UPDATE_NOTICE_DISMISS_KEY = 'asrUpdateNoticeDismissedVersion.v1'")
    && appSource.includes('releaseStatus.availableVersion !== dismissedUpdateVersion')
    && appSource.includes('writeDismissedUpdateVersion(releaseStatus.availableVersion)')
    && appSource.includes('Remind me with the next version'),
  'Update notice cannot be dismissed for only the currently offered version.',
)

console.log('ASR update dialog self-test passed')
