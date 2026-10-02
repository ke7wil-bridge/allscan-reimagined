import { useEffect, useRef } from 'react'
import { SUPPORT_ASR_URL } from '../config/support'

type SupportAsrDialogProps = {
  onClose: () => void
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export default function SupportAsrDialog({ onClose }: SupportAsrDialogProps) {
  const dialogRef = useRef<HTMLElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeButtonRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
      )
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose])

  return (
    <div
      className="asr-support-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        ref={dialogRef}
        className="asr-support-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="asr-support-title"
        aria-describedby="asr-support-description asr-support-optional"
      >
        <header className="asr-support-head">
          <h2 id="asr-support-title">Support AllScan Reimagined</h2>
          <button
            ref={closeButtonRef}
            type="button"
            className="asr-support-close"
            aria-label="Close Support AllScan Reimagined"
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <p id="asr-support-description">
          AllScan Reimagined is independently developed and maintained. If you’ve found ASR useful and would
          like to help support continued development, you can make an optional contribution through PayPal.
        </p>
        <p id="asr-support-optional">
          Contributions are completely optional and aren’t required to use any ASR features.
        </p>
        <div className="asr-support-actions">
          <a href={SUPPORT_ASR_URL} target="_blank" rel="noopener noreferrer">
            <span>Support ASR with PayPal</span>
            <small>Opens in a new tab</small>
          </a>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
      </section>
    </div>
  )
}
