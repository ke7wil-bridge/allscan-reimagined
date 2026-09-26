import { useEffect, useState } from 'react'
import {
  asrPath, checkAsrUpdate, fetchAsrUpdateJob, queueAsrUpdate,
  type UpdateCheck, type UpdateJob,
} from '../lib/allscanLive'

const STORED_JOB = 'asrUpdateJobId.v1'
const ACTIVE = new Set(['queued', 'preflight', 'downloading', 'verifying', 'staging', 'backup', 'installing', 'restoring', 'health'])
const LABELS: Record<string, string> = {
  queued: 'Waiting to start…', preflight: 'Checking update compatibility…',
  downloading: 'Downloading the release…', verifying: 'Verifying SHA-256…',
  staging: 'Staging the verified release…', backup: 'Creating a rollback backup…',
  installing: 'Installing ASR…', restoring: 'Restoring configuration…',
  health: 'Running health checks…', complete: 'Update complete',
  failed: 'Update needs attention',
}

export default function UpdateAsrDialog({ onClose, initialUpdate = null }: { onClose: () => void; initialUpdate?: UpdateCheck | null }) {
  const [available, setAvailable] = useState<UpdateCheck | null>(initialUpdate)
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [jobId, setJobId] = useState(() => window.localStorage.getItem(STORED_JOB) || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connectionMessage, setConnectionMessage] = useState('')

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose])

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    const poll = async () => {
      try {
        const next = await fetchAsrUpdateJob(jobId)
        if (cancelled) return
        setJob(next)
        setConnectionMessage('')
        if (next.state === 'complete' || next.state === 'failed') {
          window.localStorage.removeItem(STORED_JOB)
        }
      } catch {
        if (cancelled) return
        // A persisted job can outlive the server-side status file after a completed update.
        // If we have never observed this job as active, treat it as stale instead of
        // claiming that ASR is reconnecting forever.
        if (!job) {
          window.localStorage.removeItem(STORED_JOB)
          setJobId('')
          setConnectionMessage('')
          return
        }
        setConnectionMessage('ASR is reconnecting. This update job continues on the server.')
      }
    }
    void poll()
    const timer = window.setInterval(() => { if (!job || ACTIVE.has(job.state)) void poll() }, 2500)
    return () => { cancelled = true; window.clearInterval(timer) }
    // Polling intentionally keys off the job id/state; depending on the full job object would restart the timer on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, job?.state])

  const run = async (request: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try { await request() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'ASR update request failed.') }
    finally { setBusy(false) }
  }
  const check = () => void run(async () => {
    setAvailable(await checkAsrUpdate())
  })
  const apply = () => void run(async () => {
    const next = await queueAsrUpdate()
    window.localStorage.setItem(STORED_JOB, next.jobId)
    setJobId(next.jobId)
    setJob(next)
  })
  const running = Boolean(job && ACTIVE.has(job.state))
  const hasUpdate = available?.updateAvailable === true

  return (
    <div className="asr-update-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="asr-update-dialog" role="dialog" aria-modal="true" aria-labelledby="asr-update-title">
        <header className="asr-update-head">
          <h2 id="asr-update-title">Update ASR</h2>
          <button type="button" aria-label="Close Update ASR" onClick={onClose}>×</button>
        </header>
        <p>Check for an update, then install it when ready. ASR verifies compatibility and creates a rollback backup automatically.</p>
        {job ? <div className="asr-update-job" role="status" aria-live="polite">
          <strong>{LABELS[job.state] || 'Checking update status…'}</strong>
          {job.message && job.state !== 'complete' ? <p>{job.message}</p> : null}
          {running ? <p>Please do not close this window while the update is in progress.</p> : null}
          {job.state === 'complete' ? <>
            <p><strong>ASR {job.availableVersion || 'update'} is installed successfully.</strong></p>
            <button type="button" onClick={() => window.location.reload()}>Reload ASR</button>
          </> : null}
          {job.state === 'failed' ? <p><strong>Update not installed.</strong> {job.message || 'The safety checks or installation did not complete.'} Open Backups &amp; Rollback if recovery needs attention.</p> : null}
          {connectionMessage ? <p>{connectionMessage}</p> : null}
        </div> : null}
        {!job && !running ? <>
          <dl className="asr-update-versions">
            <div><dt>Current</dt><dd>{available?.installedVersion || 'Check for update'}</dd></div>
            <div><dt>Available</dt><dd>{available?.availableVersion || '—'}</dd></div>
          </dl>
          <div className="asr-update-actions">
            <button type="button" disabled={busy} onClick={check}>Check for Update</button>
            {hasUpdate ? <button type="button" disabled={busy} onClick={apply}>Update Now</button> : null}
          </div>
          {available && !hasUpdate ? <p>ASR is up to date.</p> : null}
        </> : null}
        {!job && connectionMessage ? <p role="status">{connectionMessage}</p> : null}
        {error ? <p className="asr-update-error" role="alert">{error}</p> : null}
        <footer><a href={asrPath('asr-settings/')}>Backups &amp; Rollback</a><span>Safety checks and rollback protection run automatically.</span></footer>
      </section>
    </div>
  )
}
