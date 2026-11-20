import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchDiagnosticsReport, type RuntimeConfig } from '../lib/allscanLive'

type Kind = 'bug' | 'question' | 'feature'
type Screenshot = { id: string; name: string; type: string; data: string }
type SubmittedIssue = { number: number; url: string }
type Props = { config: RuntimeConfig; isAdmin: boolean; onClose: () => void }
const ENDPOINT = String(import.meta.env.VITE_ASR_SUPPORT_ENDPOINT || '').trim()
const MAX_SCREENSHOTS = 3
const MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024

function readScreenshot(file: File): Promise<Screenshot> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error(`Could not read ${file.name}. Please select it again.`))
    reader.onload = () => resolve({
      id: crypto.randomUUID(), name: file.name, type: file.type,
      data: String(reader.result).split(',')[1] || '',
    })
    reader.readAsDataURL(file)
  })
}

function redactSupportText(value: string) {
  return value
    .replace(/-----BEGIN [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----/gi, '[REDACTED PRIVATE KEY]')
    .replace(/(authorization\s*:\s*(?:bearer|basic)\s+)[^\s"'<>]+/gi, '$1[REDACTED]')
    .replace(/((?:["']?(?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|github[_-]?token|tgif[_-]?(?:password|token)|password|passwd|secret|cookie|session|phpsessid)["']?\s*[:=]\s*)["']?)[^"'\s&;,}]+(["']?)/gi, '$1[REDACTED]$2')
}

export default function SupportFeedbackDialog({ config, isAdmin, onClose }: Props) {
  const [kind, setKind] = useState<Kind>('bug')
  const [message, setMessage] = useState('')
  const [context, setContext] = useState('')
  const [identity, setIdentity] = useState('')
  const [email, setEmail] = useState('')
  const [includeDiagnostics, setIncludeDiagnostics] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [reviewText, setReviewText] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [website, setWebsite] = useState('')
  const [screenshots, setScreenshots] = useState<Screenshot[]>([])
  const [endpoint, setEndpoint] = useState(ENDPOINT)
  const [submittedIssue, setSubmittedIssue] = useState<SubmittedIssue | null>(null)
  const fileReadingRef = useRef(false)
  const submittingRef = useRef(false)
  const submissionIdRef = useRef(crypto.randomUUID())

  // Operators can configure the public endpoint after installation, without
  // rebuilding the dashboard or shipping any GitHub credentials to nodes.
  useEffect(() => {
    if (ENDPOINT) return
    let cancelled = false
    void fetch(`${import.meta.env.BASE_URL}support-config.json`, { cache: 'no-store' })
      .then((response) => response.ok ? response.json() : null)
      .then((settings: unknown) => {
        if (!cancelled && settings && typeof settings === 'object' && 'endpoint' in settings &&
          typeof settings.endpoint === 'string') setEndpoint(settings.endpoint.trim())
      })
      .catch(() => { /* No runtime override; the form will explain how to get help. */ })
    return () => { cancelled = true }
  }, [])

  const labels = kind === 'bug' ? ['bug', 'asr-report'] : kind === 'question' ? ['question', 'asr-support'] : ['enhancement', 'asr-feedback']
  const safeAuto = useMemo(() => redactSupportText([
    `ASR: ${config.versionLabel}`,
    `Browser/platform: ${navigator.userAgent}`,
    `Node: ${config.node || 'unknown'}`,
    `Callsign: ${config.callsign || 'unknown'}`,
    `Bridges: ${config.bridges.map((bridge) => `${bridge.id}(${bridge.mode || 'unknown'}, node ${bridge.node || '-'})`).join(', ') || 'none'}`,
  ].join('\n')), [config])

  function makePreview(adminDiagnostics: string) {
    return redactSupportText([
      `Type: ${kind}`, `Labels: ${labels.join(', ')}`, '', message.trim(),
      context.trim() ? `\nContext / steps:\n${context.trim()}` : '',
      identity.trim() ? `\nSubmitted by: ${identity.trim()}` : '',
      email.trim() ? `Contact email: ${email.trim()}` : '',
      includeDiagnostics && kind === 'bug' ? `\nDiagnostics:\n${safeAuto}${adminDiagnostics ? `\n\nAdmin diagnostic excerpt:\n${adminDiagnostics}` : ''}` : '',
    ].filter(Boolean).join('\n'))
  }

  async function addScreenshots(files: FileList | null) {
    const selected = Array.from(files || [])
    if (!selected.length || fileReadingRef.current || submittedIssue) return
    if (screenshots.length + selected.length > MAX_SCREENSHOTS) {
      setStatus(`You can attach up to ${MAX_SCREENSHOTS} screenshots total. Remove one before adding more.`)
      return
    }
    if (selected.some((file) => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size === 0 || file.size > MAX_SCREENSHOT_BYTES)) {
      setStatus('Each screenshot must be a PNG, JPEG, or WebP image between 1 byte and 2 MB. No files were added.')
      return
    }
    fileReadingRef.current = true
    setBusy(true)
    setStatus('')
    try {
      const encoded = await Promise.all(selected.map(readScreenshot))
      submissionIdRef.current = crypto.randomUUID()
      setScreenshots((current) => [...current, ...encoded])
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Could not read the selected screenshots.')
    } finally {
      fileReadingRef.current = false
      setBusy(false)
    }
  }

  function removeScreenshot(id: string) {
    if (busy || submittedIssue) return
    submissionIdRef.current = crypto.randomUUID()
    setScreenshots((current) => current.filter((shot) => shot.id !== id))
    setStatus('')
  }

  async function beginReview() {
    if (busy || fileReadingRef.current || submittedIssue) return
    if (message.trim().length < 3) { setStatus('Please describe what you need help with (at least 3 characters).'); return }
    if (email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setStatus('Please enter a valid contact email, or leave it blank.')
      return
    }
    setBusy(true); setStatus('')
    try {
      let adminDiagnostics = ''
      if (kind === 'bug' && includeDiagnostics && isAdmin) {
        const report = await fetchDiagnosticsReport()
        adminDiagnostics = redactSupportText(report.report)
      }
      setReviewText(makePreview(adminDiagnostics))
      setReviewing(true)
    } catch (error) { setStatus(error instanceof Error ? error.message : 'Diagnostics could not be loaded.') }
    finally { setBusy(false) }
  }

  async function submit() {
    if (busy || submittingRef.current || submittedIssue) return
    if (reviewText.trim().length < 3) { setStatus('Please enter a report before submitting.'); return }
    if (!endpoint) {
      setStatus('Reporting is not configured on this ASR installation. Contact your ASR administrator; your report has not been sent.')
      return
    }
    try {
      const url = new URL(endpoint)
      if (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname))) {
        setStatus('The support endpoint is invalid. Contact your ASR administrator; your report has not been sent.')
        return
      }
    } catch {
      setStatus('The support endpoint is invalid. Contact your ASR administrator; your report has not been sent.')
      return
    }

    submittingRef.current = true
    setBusy(true)
    setStatus('Submitting…')
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, content: redactSupportText(reviewText), website, screenshots, submissionId: submissionIdRef.current }),
      })
      const payload = await response.json().catch(() => null) as {
        ok?: boolean; error?: string; issueNumber?: number; issueUrl?: string
      } | null
      if (!response.ok || !payload?.ok) {
        throw new Error(payload?.error || `Reporting service returned HTTP ${response.status}. Please try again later.`)
      }
      if (!Number.isSafeInteger(payload.issueNumber) || (payload.issueNumber || 0) <= 0) {
        throw new Error('The reporting service did not confirm an issue number. Please contact support before retrying.')
      }
      const issueUrl = `https://github.com/ke7wil-bridge/allscan-reimagined/issues/${payload.issueNumber}`
      setSubmittedIssue({ number: payload.issueNumber!, url: issueUrl })
      setStatus('')
    } catch (error) {
      const message = error instanceof TypeError ? 'Cannot reach the reporting service. Check your connection and try again.' :
        error instanceof Error ? error.message : 'Submission failed. Please try again.'
      setStatus(message)
    } finally {
      submittingRef.current = false
      setBusy(false)
    }
  }

  return <div className="allscan-drop-client-modal" onClick={() => { if (!busy) onClose() }}>
    <div className="allscan-drop-client-box asr-feedback-box" onClick={(event) => event.stopPropagation()}>
      <h3>Support &amp; Feedback</h3>
      {!reviewing ? <>
        <div className="asr-feedback-types">
          <button className={kind === 'bug' ? 'is-active' : ''} onClick={() => setKind('bug')}>Report a Bug</button>
          <button className={kind === 'question' ? 'is-active' : ''} onClick={() => setKind('question')}>Ask a Question</button>
          <button className={kind === 'feature' ? 'is-active' : ''} onClick={() => setKind('feature')}>Suggest a Feature</button>
        </div>
        <label>{kind === 'question' ? 'Your question' : kind === 'feature' ? 'Your suggestion' : 'What went wrong?'}<textarea value={message} onChange={(e) => setMessage(e.target.value)} /></label>
        <label>{kind === 'bug' ? 'Steps / relevant context' : 'Relevant context (optional)'}<textarea value={context} onChange={(e) => setContext(e.target.value)} /></label>
        <div className="asr-feedback-inline"><label>Callsign / name (optional)<input value={identity} onChange={(e) => setIdentity(e.target.value)} /></label><label>Contact email (optional)<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label></div>
        {kind === 'bug' ? <label className="asr-feedback-check"><input type="checkbox" checked={includeDiagnostics} onChange={(e) => setIncludeDiagnostics(e.target.checked)} /> Include sanitized diagnostics{isAdmin ? ' and local log excerpts' : ''}</label> : null}
        <label className="asr-feedback-file-picker">Screenshots (optional, up to 3 PNG/JPEG/WebP files; 2 MB each)
          <input className="asr-feedback-file-input" type="file" accept="image/png,image/jpeg,image/webp" multiple disabled={busy}
            onChange={(event) => {
              const input = event.currentTarget
              void addScreenshots(input.files)
              input.value = ''
            }} />
          <span className="asr-feedback-choose" aria-hidden="true">Choose screenshots</span>
        </label>
        <p className="asr-feedback-count" role="status">{screenshots.length
          ? `${screenshots.length} of ${MAX_SCREENSHOTS} screenshots selected. These files will remain attached when you review or go back.`
          : 'No screenshots selected.'}</p>
        {screenshots.length ? <div className="asr-feedback-files"><strong>Selected screenshots (will be public):</strong>{screenshots.map((shot) => <span key={shot.id}>{shot.name}<button type="button" disabled={busy} onClick={() => removeScreenshot(shot.id)} aria-label={`Remove screenshot ${shot.name}`}>Remove</button></span>)}</div> : null}
        <input className="asr-feedback-honeypot" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} aria-hidden="true" />
        <p className="asr-feedback-public">Your submission and screenshots will become public in a GitHub Issue. Do not include passwords, tokens, private keys, or other secrets. Text is automatically redacted where possible, but secrets inside images cannot be automatically detected. Review every screenshot carefully before submitting.</p>
        <div className="allscan-drop-client-status" role="alert">{status}</div>
        <div className="allscan-drop-client-actions"><button className="allscan-action-button" disabled={busy} onClick={() => void beginReview()}>Review</button><button className="allscan-action-button" disabled={busy} onClick={onClose}>Cancel</button></div>
      </> : <>
        <p className="asr-feedback-public"><strong>Public GitHub Issue:</strong> the text and screenshots below will be public. You may edit the text or remove screenshots. Secrets inside images cannot be automatically detected.</p>
        <textarea className="asr-feedback-preview" value={reviewText} disabled={Boolean(submittedIssue)}
          onChange={(event) => { setReviewText(event.target.value); setStatus(''); submissionIdRef.current = crypto.randomUUID() }} />
        <p className="asr-feedback-count" role="status">{screenshots.length
          ? `${screenshots.length} screenshot${screenshots.length === 1 ? '' : 's'} attached to this report:`
          : 'No screenshots attached to this report.'}</p>
        {screenshots.length ? <div className="asr-feedback-review-images">{screenshots.map((shot) => <figure key={shot.id}><img src={`data:${shot.type};base64,${shot.data}`} alt={`Screenshot ${shot.name}`} /><figcaption>{shot.name} — public</figcaption><button type="button" disabled={busy || Boolean(submittedIssue)} onClick={() => removeScreenshot(shot.id)} aria-label={`Remove screenshot ${shot.name}`}>Remove screenshot</button></figure>)}</div> : null}
        {submittedIssue ? <div className="asr-feedback-success" role="status">
          Your report was submitted successfully as <a href={submittedIssue.url} target="_blank" rel="noopener noreferrer">GitHub issue #{submittedIssue.number}</a>. You can safely close this window.
        </div> : <div className="allscan-drop-client-status" role="alert">{status}</div>}
        <div className="allscan-drop-client-actions">
          {!submittedIssue ? <><button className="allscan-action-button" disabled={busy} onClick={() => void submit()}>Submit Public Issue</button><button className="allscan-action-button" disabled={busy} onClick={() => { setReviewing(false); setStatus('') }}>Back</button></> : null}
          <button className="allscan-action-button" disabled={busy} onClick={onClose}>Close</button>
        </div>
      </>}
    </div>
  </div>
}
