#!/usr/bin/env node
import { readFileSync } from 'node:fs'

const source = readFileSync(
  new URL('../src/components/UpdateAsrDialog.tsx', import.meta.url),
  'utf8',
)
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const liveSource = readFileSync(new URL('../src/lib/allscanLive.ts', import.meta.url), 'utf8')
const whatsNewSource = readFileSync(new URL('../src/config/whatsNew.ts', import.meta.url), 'utf8')
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
  source.includes('supported AllScan and ASR updates')
    && source.includes('install them here in the browser')
    && source.includes('verifies pinned release files'),
  'Update dialog does not clearly identify the verified browser installation path.',
)
check(
  liveSource.includes("action === 'update-job-status'")
    && liveSource.includes("state === 'failed'")
    && liveSource.includes('jobId === jobId')
    && liveSource.includes('payload.ok === false && !failedJobStatus'),
  'A valid failed-job response is still rejected as a generic API error.',
)
check(
  source.includes("next.state === 'complete' || next.state === 'failed'")
    && source.includes("window.localStorage.removeItem(STORED_JOB)\n          setJobId('')")
    && source.includes("job.state === 'failed'"),
  'Terminal update jobs keep polling or do not render the safe failure message.',
)
const failedView = source.slice(
  source.indexOf("{job.state === 'failed' ? <>"),
  source.indexOf('{connectionMessage ?', source.indexOf("{job.state === 'failed' ? <>")),
)
check(
  failedView.includes('job.failureReason')
    && failedView.includes("job.restoration === 'succeeded'")
    && failedView.includes("job.restoration === 'failed'")
    && failedView.includes('job.manualInterventionRequired')
    && failedView.includes('Download Error Log')
    && failedView.includes('updateJobReportUrl(job.jobId)')
    && liveSource.includes("url.searchParams.set('action', 'update-job-report')"),
  'Failed updates do not show the safe reason, restoration result, intervention status, and report download.',
)
check(
  appSource.includes("const UPDATE_NOTICE_DISMISS_KEY = 'asrUpdateNoticeDismissedVersion.v1'")
    && appSource.includes('releaseStatus.availableVersion !== dismissedUpdateVersion')
    && appSource.includes('writeDismissedUpdateVersion(releaseStatus.availableVersion)')
    && appSource.includes('Remind me with the next version'),
  'Update notice cannot be dismissed for only the currently offered version.',
)
check(
  source.includes('If you’re using AllScan Reimagined')
    && source.includes('mailto:ke7wil@gmail.com')
    && source.includes('KE7WIL'),
  'What’s New does not invite ASR users to email KE7WIL.',
)
check(
  source.includes("initialView = 'update'")
    && source.includes('>What’s New</button>')
    && source.includes('ASR_WHATS_NEW.items.map'),
  'Update ASR does not contain a reusable What’s New option.',
)
check(
  appSource.includes("const WHATS_NEW_SEEN_KEY = 'asrWhatsNewSeenVersion.v1'")
    && appSource.includes("setUpdateDialogView('whats-new')")
    && appSource.includes('writeWhatsNewSeenVersion(ASR_WHATS_NEW.version)')
    && appSource.includes('updateAsrParamHandled.current ||\n      readWhatsNewSeenVersion()'),
  'What’s New does not open once for each newly installed version.',
)
check(
  whatsNewSource.includes("version: '1.0.0-beta.8.4.3'")
    && whatsNewSource.includes('repairs a missing updater')
    && whatsNewSource.includes('clients for bridges supporting client listing')
    && !/SHA-256|systemd|container ID|CSS selector/.test(whatsNewSource),
  'What’s New copy is missing, version-mismatched, or too technical.',
)

console.log('ASR update dialog self-test passed')
