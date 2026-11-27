type ReadRefreshOptions = { intervalMs?: number; onFocus?: boolean }

// Retains each read-only effect's immediate request, interval/focus policy and
// cancellation guard. No new single-flight or abort behavior is introduced here.
export function startReadRefresh(refresh: (isActive: () => boolean) => Promise<void>, options: ReadRefreshOptions): () => void {
  let active = true
  const isActive = () => active
  const tick = () => { void refresh(isActive) }
  tick()
  if (options.onFocus) window.addEventListener('focus', tick)
  const timer = options.intervalMs === undefined ? undefined : window.setInterval(tick, options.intervalMs)
  return () => {
    active = false
    if (options.onFocus) window.removeEventListener('focus', tick)
    if (timer !== undefined) window.clearInterval(timer)
  }
}
