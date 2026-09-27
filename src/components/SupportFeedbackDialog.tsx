import { useMemo, useState } from 'react'
import { fetchDiagnosticsReport, type RuntimeConfig } from '../lib/allscanLive'

type Kind = 'bug' | 'question' | 'feature'
type Props = { config: RuntimeConfig; isAdmin: boolean; onClose: () => void }
const ENDPOINT = String(import.meta.env.VITE_ASR_SUPPORT_ENDPOINT || '').trim()

function redactSupportText(value: string) {
  return value
    .replace(/-----BEGIN [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----/gi, '[REDACTED PRIVATE KEY]')
    .replace(/(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+/gi, '$1[REDACTED]')
    .replace(/((?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|github[_-]?token|tgif[_-]?(?:password|token)|password|passwd|secret|cookie|session|phpsessid)\s*[:=]\s*)[^\s&;]+/gi, '$1[REDACTED]')
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
  const [screenshots, setScreenshots] = useState<Array<{ name: string; type: string; data: string }>>([])

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
      screenshots.length ? `Screenshots to be made public: ${screenshots.map((shot) => shot.name).join(', ')}` : '',
      includeDiagnostics && kind === 'bug' ? `\nDiagnostics:\n${safeAuto}${adminDiagnostics ? `\n\nAdmin diagnostic excerpt:\n${adminDiagnostics}` : ''}` : '',
    ].filter(Boolean).join('\n'))
  }

  async function addScreenshots(files: FileList | null) {
    const selected = Array.from(files || []).slice(0, 3)
    const allowed = selected.filter((file) => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type) && file.size <= 2 * 1024 * 1024)
    if (allowed.length !== selected.length) setStatus('Screenshots must be PNG, JPEG, or WebP and 2 MB or smaller (maximum 3).')
    const encoded = await Promise.all(allowed.map((file) => new Promise<{ name: string; type: string; data: string }>((resolve, reject) => {
      const reader = new FileReader(); reader.onerror = () => reject(reader.error); reader.onload = () => resolve({ name: file.name, type: file.type, data: String(reader.result).split(',')[1] || '' }); reader.readAsDataURL(file)
    })))
    setScreenshots(encoded)
  }

  async function beginReview() {
    if (!message.trim()) { setStatus('Please describe what you need help with.'); return }
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
    if (!ENDPOINT) { setStatus('Support service is not configured on this ASR build yet.'); return }
    setBusy(true); setStatus('Submitting…')
    try {
      const response = await fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        kind, content: redactSupportText(reviewText), website, screenshots,
      }) })
      const payload = await response.json() as { ok?: boolean; error?: string; issueNumber?: number }
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'Submission failed.')
      setStatus(`Submitted as GitHub issue #${payload.issueNumber}.`)
    } catch (error) { setStatus(error instanceof Error ? error.message : 'Submission failed.') }
    finally { setBusy(false) }
  }

  return <div className="allscan-drop-client-modal" onClick={onClose}>
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
        <label>Screenshots (optional, maximum 3; PNG/JPEG/WebP; 2 MB each)<input type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={(e) => void addScreenshots(e.target.files)} /></label>
        {screenshots.length ? <div className="asr-feedback-files">Will be public: {screenshots.map((shot) => shot.name).join(', ')}</div> : null}
        <input className="asr-feedback-honeypot" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} aria-hidden="true" />
        <p className="asr-feedback-public">Your submission will become a public GitHub Issue. Do not include passwords, tokens, private keys, or other secrets. You will review exactly what is sent before submitting.</p>
        <div className="allscan-drop-client-status">{status}</div>
        <div className="allscan-drop-client-actions"><button className="allscan-action-button" disabled={busy} onClick={() => void beginReview()}>Review</button><button className="allscan-action-button" onClick={onClose}>Cancel</button></div>
      </> : <>
        <p className="asr-feedback-public"><strong>Public GitHub Issue:</strong> this is exactly what will be submitted. You may edit it.</p>
        <textarea className="asr-feedback-preview" value={reviewText} onChange={(e) => setReviewText(e.target.value)} />
        {screenshots.length ? <div className="asr-feedback-review-images">{screenshots.map((shot) => <figure key={shot.name}><img src={`data:${shot.type};base64,${shot.data}`} alt={`Screenshot ${shot.name}`} /><figcaption>{shot.name} — public</figcaption></figure>)}</div> : null}
        <div className="allscan-drop-client-status">{status}</div>
        <div className="allscan-drop-client-actions"><button className="allscan-action-button" disabled={busy} onClick={() => void submit()}>Submit Public Issue</button><button className="allscan-action-button" disabled={busy} onClick={() => setReviewing(false)}>Back</button><button className="allscan-action-button" onClick={onClose}>Close</button></div>
      </>}
    </div>
  </div>
}
