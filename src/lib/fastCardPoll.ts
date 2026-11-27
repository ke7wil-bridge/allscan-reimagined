// Each caller owns a separate single-flight loop and independent cancellation.
export function startFastCardPoll<T>(fetchCards: () => Promise<T>, cardsRef: { current: T }, onChange: () => void, pollMs: number): () => void {
  let cancelled = false
  let previous = ''
  let requestSequence = 0
  let appliedSequence = 0
  let timer: number | undefined
  const tick = async () => {
    const started = performance.now()
    const sequence = ++requestSequence
    try {
      const cards = await fetchCards()
      if (cancelled || sequence < appliedSequence) return
      appliedSequence = sequence
      const fingerprint = JSON.stringify(cards)
      cardsRef.current = cards
      if (fingerprint === previous) return
      previous = fingerprint
      onChange()
    } catch { /* full bridge and connection status remain the fallback */ }
    finally {
      if (!cancelled) timer = window.setTimeout(() => void tick(), document.hidden ? 1000 : Math.max(0, pollMs - (performance.now() - started)))
    }
  }
  void tick()
  return () => {
    cancelled = true
    if (timer !== undefined) window.clearTimeout(timer)
  }
}
