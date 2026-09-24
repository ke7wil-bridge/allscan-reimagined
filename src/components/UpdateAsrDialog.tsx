import { useEffect, useState } from 'react'
import {
  asrPath, checkAsrUpdate, fetchAsrUpdateJob, preflightAsrUpdate, queueAsrUpdate, recoverAsrUpdate,
  type UpdateCheck, type UpdateJob, type UpdatePreflight,
} from '../lib/allscanLive'

const STORED_JOB = 'asrUpdateJobId.v1'
const ACTIVE = new Set(['queued', 'preflight', 'downloading', 'verifying', 'staging', 'backup', 'installing', 'restoring', 'health'])
const LABELS: Record<string, string> = {
  queued: 'Waiting to start…', preflight: 'Running preflight checks…',
  downloading: 'Downloading the release…', verifying: 'Verifying SHA-256…',
  staging: 'Staging the verified release…', backup: 'Creating a rollback backup…',
  installing: 'Installing ASR…', restoring: 'Restoring configuration…',
  health: 'Running health checks…', complete: 'Update complete',
  failed: 'Update needs attention',
}

export default function UpdateAsrDialog({ onClose }: { onClose: () => void }) {
  const [available, setAvailable] = useState<UpdateCheck | null>(null)
  const [preflight, setPreflight] = useState<UpdatePreflight | null>(null)
  const [job, setJob] = useState<UpdateJob | null>(null)
  const [jobId, setJobId] = useState(() => window.localStorage.getItem(STORED_JOB) || '')
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState(false)
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
      } catch {
        if (!cancelled) setConnectionMessage('ASR is reconnecting. This update job continues on the server.')
      }
    }
    void poll()
    const timer = window.setInterval(() => { if (!job || ACTIVE.has(job.state)) void poll() }, 2500)
    return () => { cancelled = true; window.clearInterval(timer) }
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
    setPreflight(null)
    setConfirming(false)
  })
  const verify = () => void run(async () => {
    setPreflight(await preflightAsrUpdate())
    setConfirming(false)
  })
  const apply = () => void run(async () => {
    const next = await queueAsrUpdate()
    window.localStorage.setItem(STORED_JOB, next.jobId)
    setJobId(next.jobId)
    setJob(next)
    setConfirming(false)
  })
  const recover = () => void run(async () => {
    const result = await recoverAsrUpdate()
    setConnectionMessage(result.status === 'nothing_to_recover'
      ? 'No interrupted update needs recovery.'
      : 'Recovery check finished. Review the update status.')
    if (jobId) setJob(await fetchAsrUpdateJob(jobId))
  })
  const running = Boolean(job && ACTIVE.has(job.state))
  const hasUpdate = available?.updateAvailable === true
  const ready = Boolean(preflight?.ok && preflight.availableVersion === available?.availableVersion)

  return (
    <div className="asr-update-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="asr-update-dialog" role="dialog" aria-modal="true" aria-labelledby="asr-update-title">
        <header className="asr-update-head">
          <h2 id="asr-update-title">Update ASR</h2>
          <button type="button" aria-label="Close Update ASR" onClick={onClose}>×</button>
        </header>
        <p>Check the release, run preflight, then choose when to install. ASR creates a rollback backup first.</p>
        {job ? <div className="asr-update-job" role="status" aria-live="polite">
          <strong>{LABELS[job.state] || 'Checking update status…'}</strong>
          {job.message ? <p>{job.message}</p> : null}
          {running ? <p>You can close this window. The server will continue the update.</p> : null}
          {job.state === 'complete' ? <button type="button" onClick={() => window.location.reload()}>Reload ASR</button> : null}
          {job.state === 'failed' ? <p>Open Backups &amp; Rollback if recovery needs attention.</p> : null}
          {connectionMessage ? <p>{connectionMessage}</p> : null}
        </div> : null}
        {!running ? <>
          <dl className="asr-update-versions">
            <div><dt>Current</dt><dd>{available?.installedVersion || 'Check for update'}</dd></div>
            <div><dt>Available</dt><dd>{available?.availableVersion || '—'}</dd></div>
          </dl>
          <div className="asr-update-actions">
            <button type="button" disabled={busy} onClick={check}>Check for Update</button>
            <button type="button" disabled={busy || !hasUpdate} onClick={verify}>Run Preflight</button>
          </div>
          {available && !hasUpdate ? <p>ASR is up to date.</p> : null}
          {preflight ? <div className="asr-update-preflight" role="status">
            <strong>Preflight passed for {preflight.availableVersion}</strong>
            <p>Compatibility, services, configuration, backup storage, and disk space are ready.</p>
            <p>{preflight.restartRequired ? 'ASR components will restart.' : 'No restart is required.'} {preflight.rebootRequired ? 'A reboot is required.' : 'No reboot is required.'}</p>
          </div> : null}
          {ready && !confirming ? <button type="button" disabled={busy} onClick={() => setConfirming(true)}>Install verified update</button> : null}
          {ready && confirming ? <div className="asr-update-confirm">
            <strong>Install {preflight?.availableVersion} now?</strong>
            <p>ASR will back up, install, run health checks, and restore the prior version if installation fails.</p>
            <div className="asr-update-actions">
              <button type="button" disabled={busy} onClick={apply}>Confirm update</button>
              <button type="button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div> : null}
        </> : null}
        <button type="button" disabled={busy} onClick={recover}>Check interrupted update recovery</button>
        {!job && connectionMessage ? <p role="status">{connectionMessage}</p> : null}
        {error ? <p className="asr-update-error" role="alert">{error}</p> : null}
        <footer><a href={asrPath('asr-settings/')}>Backups &amp; Rollback</a><span>Updates run only when an administrator confirms them.</span></footer>
      </section>
    </div>
  )
}
