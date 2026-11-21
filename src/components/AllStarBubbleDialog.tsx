import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { asrPath } from '../lib/allscanLive'
import './AllStarBubbleDialog.css'

type GraphResult = { node?: string; links?: string[]; reported?: boolean; status?: string }
const nodePattern = /^[0-9]{3,7}$/
const safeNode = (value: string) => nodePattern.test(value) && !/^100[1-6]$/.test(value)
const EDGE_LIMIT = 250

export default function AllStarBubbleDialog({ node, onClose }: { node: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  const origin = useRef<HTMLElement | null>(null)
  const [edges, setEdges] = useState<string[]>([])
  const [status, setStatus] = useState('Loading verified AllStarLink network topology…')
  const [zoom, setZoom] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [details, setDetails] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const closeCallback = useRef(onClose)
  closeCallback.current = onClose

  useEffect(() => {
    const element = dialog.current
    origin.current = document.activeElement as HTMLElement | null
    if (!element) return
    const y = window.scrollY
    const prior = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    element.showModal()
    const closed = () => closeCallback.current()
    element.addEventListener('close', closed)
    return () => {
      element.removeEventListener('close', closed)
      if (element.open) element.close()
      document.body.style.overflow = prior
      window.scrollTo({ top: y, behavior: 'instant' })
      origin.current?.focus({ preventScroll: true })
    }
  }, [])

  useEffect(() => {
    if (!safeNode(node)) { setStatus('Not a valid AllStarLink node.'); return }
    let active = true
    const controller = new AbortController()
    const unique = new Set<string>()
    const queue = [node]
    const visited = new Set<string>()
    const run = async () => {
      while (active && queue.length && visited.size < 8 && unique.size < EDGE_LIMIT) {
        const current = queue.shift()!
        if (visited.has(current) || !safeNode(current)) continue
        visited.add(current)
        try {
          const response = await fetch(asrPath(`lookup/network-graph.php?node=${encodeURIComponent(current)}`), { credentials: 'same-origin', signal: controller.signal, cache: 'no-store' })
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          const data = await response.json() as GraphResult
          if (!data.reported) {
            if (current === node && unique.size === 0) setStatus('Live topology unavailable. Use the AllStarLink fallback below.')
            continue
          }
          for (const linked of data.links || []) {
            if (!safeNode(linked) || linked === current) continue
            unique.add([current, linked].sort().join(':'))
            if (!visited.has(linked) && queue.length < 24) queue.push(linked)
            if (unique.size >= EDGE_LIMIT) break
          }
          if (!active) break
          setEdges([...unique])
          setStatus(`${visited.size} nodes checked · ${unique.size} verified links${data.status === 'stale' ? ' (some cached)' : ''}`)
        } catch (error) {
          if (!active) break
          if (current === node && unique.size === 0) setStatus('Topology request failed. Use the AllStarLink fallback below.')
          if (error instanceof Error && error.name === 'AbortError') break
        }
      }
    }
    void run()
    return () => { active = false; controller.abort() }
  }, [node])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    fetch(asrPath(`lookup/node-info.php?node=${encodeURIComponent(selected)}`), { credentials: 'same-origin', signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error('Unavailable'); return response.json() as Promise<{ callsign?: string; description?: string; location?: string }> })
      .then(data => setDetails([data.callsign, data.description, data.location].filter(Boolean).join(' · ') || 'No station description available'))
      .catch(error => { if (!(error instanceof Error && error.name === 'AbortError')) setDetails('Station details unavailable') })
    return () => controller.abort()
  }, [selected])

  const allNodes = [...new Set([node, ...edges.flatMap(key => key.split(':'))])]
  const children = new Map<string, string[]>()
  edges.forEach(key => { const [a, b] = key.split(':'); children.set(a, [...(children.get(a) || []), b]); children.set(b, [...(children.get(b) || []), a]) })
  const depth = new Map([[node, 0]])
  const queue = [node]
  while (queue.length) { const current = queue.shift()!; for (const next of collapsed.has(current) ? [] : children.get(current) || []) if (!depth.has(next)) { depth.set(next, depth.get(current)! + 1); queue.push(next) } }
  const nodes = allNodes.filter(id => depth.has(id))
  const columns = new Map<number, string[]>()
  nodes.forEach(id => { const level = Math.min(depth.get(id) || 0, 9); columns.set(level, [...(columns.get(level) || []), id]) })
  const positions = new Map<string, { x: number; y: number }>()
  const laneWidth = 190, rowHeight = 76, maxPerColumn = 11
  const offsets = new Map<number, number>()
  let nextX = 110
  ;[...columns.keys()].sort((a, b) => a - b).forEach(level => { offsets.set(level, nextX); nextX += Math.ceil(columns.get(level)!.length / maxPerColumn) * laneWidth + 70 })
  columns.forEach((ids, level) => ids.sort((a, b) => Number(a) - Number(b)).forEach((id, i) => positions.set(id, { x: offsets.get(level)! + Math.floor(i / maxPerColumn) * laneWidth, y: 90 + (i % maxPerColumn) * rowHeight })))
  const width = Math.max(850, nextX + 90)
  const height = Math.max(520, 170 + Math.min(maxPerColumn, Math.max(0, ...[...columns.values()].map(ids => ids.length))) * rowHeight)
  const fit = frame.current ? Math.min(1, (frame.current.clientWidth - 24) / width, (frame.current.clientHeight - 24) / height) : 0.8
  const scale = Math.max(0.12, fit * zoom)

  return createPortal(<dialog ref={dialog} className="asr-bubble-dialog" aria-label={`AllStarLink network chart for ${node}`} onCancel={event => { event.preventDefault(); dialog.current?.close() }}>
    <header className="asr-bubble-header"><strong>AllStarLink Network · {node}</strong><div className="asr-bubble-actions">
      <button type="button" onClick={() => setZoom(value => Math.max(0.5, value / 1.25))}>− Zoom</button>
      <button type="button" onClick={() => setZoom(value => Math.min(5, value * 1.25))}>+ Zoom</button>
      <button type="button" onClick={() => { setZoom(1); frame.current?.scrollTo(0, 0) }}>Fill</button>
      <button type="button" onClick={() => dialog.current?.close()}>Close</button>
    </div></header>
    <div className="asr-bubble-frame" ref={frame} onWheel={event => { if (event.ctrlKey) { event.preventDefault(); setZoom(value => Math.min(5, Math.max(.5, value * (event.deltaY > 0 ? .9 : 1.1)))) } }}>
      <svg role="group" aria-label="Verified AllStarLink connections" viewBox={`0 0 ${width} ${height}`} style={{ width: width * scale, height: height * scale }}>
        {edges.map(key => { const [a, b] = key.split(':'); if (!depth.has(a) || !depth.has(b)) return null; const start = positions.get(a), end = positions.get(b); return start && end ? <line key={key} x1={start.x} x2={end.x} y1={start.y} y2={end.y} /> : null })}
        {nodes.map(id => { const point = positions.get(id); return point ? <g key={id} className={id === node ? 'asr-bubble-root' : ''} role="button" tabIndex={0} aria-label={`Node ${id}: view details`} onClick={() => { setSelected(id); setDetails('Loading…') }} onDoubleClick={() => setCollapsed(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next })} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(id); setDetails('Loading…') } }}><circle cx={point.x} cy={point.y} r={id === node ? 26 : 18} strokeDasharray={collapsed.has(id) ? '4 3' : undefined} /><text x={point.x} y={point.y + (id === node ? 47 : 37)} textAnchor="middle">{id}</text></g> : null })}
      </svg>
      {selected ? <aside className="asr-bubble-details"><button type="button" aria-label="Close node details" onClick={() => setSelected(null)}>×</button><strong>Node {selected}</strong><p>{details}</p></aside> : null}
    </div>
    <footer className="asr-bubble-footer"><span role="status">{status}</span><a href={`https://stats.allstarlink.org/stats/${encodeURIComponent(node)}/networkMap`} target="_blank" rel="noopener noreferrer">Open AllStarLink full map ↗</a></footer>
  </dialog>, document.body)
}
