import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { asrPath } from '../lib/allscanLive'
import './AllStarBubbleDialog.css'

type GraphResult = { node?: string; links?: string[]; reported?: boolean; status?: string }
type StationInfo = { callsign?: string; name?: string; description?: string; location?: string; links?: string[] }
type Point = { x: number; y: number }
type DragState = { id: string; pointerId: number; origin: Point; start: Point; moved: boolean }

const nodePattern = /^[0-9]{3,7}$/
const safeNode = (value: string) => nodePattern.test(value) && !/^100[1-6]$/.test(value)
const EDGE_LIMIT = 250

export default function AllStarBubbleDialog({ node, localNode, onClose }: { node: string; localNode: string; onClose: () => void }) {
  const seedEdge = safeNode(node) && safeNode(localNode) && node !== localNode ? [node, localNode].sort().join(':') : ''
  const dialog = useRef<HTMLDialogElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  const svg = useRef<SVGSVGElement>(null)
  const origin = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  const drag = useRef<DragState | null>(null)
  const suppressClick = useRef(false)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  const [edges, setEdges] = useState<string[]>(() => seedEdge ? [seedEdge] : [])
  const [verified, setVerified] = useState(Boolean(seedEdge))
  const [status, setStatus] = useState(() => safeNode(node) ? 'Loading verified AllStarLink network topology…' : 'Not a valid AllStarLink node.')
  const [zoom, setZoom] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [details, setDetails] = useState<StationInfo | null>(null)
  const [detailsError, setDetailsError] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [frameSize, setFrameSize] = useState({ width: 0, height: 0 })
  const [manualPositions, setManualPositions] = useState<Map<string, Point>>(() => new Map())

  useEffect(() => {
    const element = frame.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setFrameSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = dialog.current
    origin.current = document.activeElement as HTMLElement | null
    if (!element) return
    const y = window.scrollY
    const prior = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    element.showModal()
    // StrictMode can queue a close event during effect cleanup, then reopen the dialog.
    // Ignore that stale event rather than dismissing the newly opened chart.
    const closed = () => { if (!element.open) onCloseRef.current() }
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
    if (!safeNode(node)) return
    let active = true
    const controller = new AbortController()
    const unique = new Set<string>(seedEdge ? [seedEdge] : [])
    const pending = [...new Set([node, localNode].filter(safeNode))]
    const visited = new Set<string>()
    const run = async () => {
      while (active && pending.length && visited.size < 8 && unique.size < EDGE_LIMIT) {
        const current = pending.shift()!
        if (visited.has(current) || !safeNode(current)) continue
        visited.add(current)
        try {
          const response = await fetch(asrPath(`lookup/network-graph.php?node=${encodeURIComponent(current)}`), { credentials: 'same-origin', signal: controller.signal, cache: 'no-store' })
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          const data = await response.json() as GraphResult
          if (!data.reported) {
            if (current === node && unique.size === 0) setStatus(data.status === 'stale' ? 'Cached topology is too old to verify.' : 'Live topology unavailable.')
            continue
          }
          setVerified(true)
          for (const linked of data.links || []) {
            if (!safeNode(linked) || linked === current) continue
            unique.add([current, linked].sort().join(':'))
            if (!visited.has(linked) && pending.length < 24) pending.push(linked)
            if (unique.size >= EDGE_LIMIT) break
          }
          if (!active) break
          setEdges([...unique])
          setStatus(`${visited.size} nodes checked · ${unique.size} verified links${data.status === 'stale' ? ' (some cached)' : ''}`)
        } catch (error) {
          if (!active) break
          if (current === node && unique.size === 0) setStatus('Topology request failed.')
          if (error instanceof Error && error.name === 'AbortError') break
        }
      }
    }
    void run()
    return () => { active = false; controller.abort() }
  }, [localNode, node, seedEdge])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    fetch(asrPath(`lookup/node-info.php?node=${encodeURIComponent(selected)}`), { credentials: 'same-origin', signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error('Unavailable'); return response.json() as Promise<StationInfo> })
      .then(data => setDetails(data))
      .catch(error => { if (!(error instanceof Error && error.name === 'AbortError')) setDetailsError('Unable to load station details right now.') })
    return () => controller.abort()
  }, [selected])

  const allNodes = verified ? [...new Set([node, ...edges.flatMap(key => key.split(':'))])] : []
  const children = new Map<string, string[]>()
  edges.forEach(key => {
    const [a, b] = key.split(':')
    children.set(a, [...(children.get(a) || []), b])
    children.set(b, [...(children.get(b) || []), a])
  })
  const depth = new Map([[node, 0]])
  const pending = [node]
  while (pending.length) {
    const current = pending.shift()!
    for (const next of collapsed.has(current) ? [] : children.get(current) || []) {
      if (!depth.has(next)) { depth.set(next, depth.get(current)! + 1); pending.push(next) }
    }
  }
  const nodes = allNodes.filter(id => depth.has(id))
  const columns = new Map<number, string[]>()
  nodes.forEach(id => { const level = Math.min(depth.get(id) || 0, 9); columns.set(level, [...(columns.get(level) || []), id]) })
  const positions = new Map<string, Point>()
  const maxPerColumn = 11, laneWidth = 158, rowHeight = 66
  const layerWidths = new Map<number, number>()
  const offsets = new Map<number, number>()
  columns.forEach((ids, level) => layerWidths.set(level, Math.max(1, Math.ceil(ids.length / maxPerColumn))))
  let nextX = 95
  const maxDepth = Math.max(1, ...columns.keys())
  for (let level = 0; level <= maxDepth; level += 1) {
    offsets.set(level, nextX)
    nextX += (layerWidths.get(level) || 1) * laneWidth + 95
  }
  let width = Math.max(1000, nextX + 80)
  let height = Math.max(700, 130 + maxPerColumn * rowHeight)
  columns.forEach((ids, level) => ids.sort((a, b) => Number(a) - Number(b)).forEach((id, index) => {
    const column = Math.floor(index / maxPerColumn)
    const row = index % maxPerColumn
    const inColumn = Math.min(maxPerColumn, ids.length - column * maxPerColumn)
    positions.set(id, manualPositions.get(id) || { x: offsets.get(level)! + column * laneWidth, y: (height - (inColumn - 1) * rowHeight) / 2 + row * rowHeight })
  }))
  positions.forEach(point => { width = Math.max(width, point.x + 95); height = Math.max(height, point.y + 95) })
  const fit = frameSize.width && frameSize.height ? Math.min(1, Math.max(0.12, (frameSize.width - 24) / width), Math.max(0.12, (frameSize.height - 24) / height)) : 0.8
  const scale = Math.max(0.12, fit * zoom)

  const graphPoint = (event: React.PointerEvent<SVGGElement>) => {
    const rect = svg.current?.getBoundingClientRect()
    if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 }
    return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }
  }
  const selectNode = (id: string) => {
    if (suppressClick.current) { suppressClick.current = false; return }
    if (selected !== id) {
      setDetails(null)
      setDetailsError('')
    }
    setSelected(id)
  }
  const toggleNode = (id: string) => setCollapsed(previous => {
    const next = new Set(previous)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  const detailConnections = selected ? (details?.links?.length ? details.links : children.get(selected) || []) : []
  const detailName = selected && details ? [details.callsign, details.name, details.description].find(value => value && value !== selected) : ''

  return createPortal(<dialog ref={dialog} className="asr-bubble-dialog" aria-label={`AllStarLink network chart for ${node}`} onCancel={event => { event.preventDefault(); dialog.current?.close() }}>
    <header className="asr-bubble-header"><strong>AllStarLink Network Map · {node}</strong><div className="asr-bubble-actions">
      <button type="button" aria-label="Zoom out" onClick={() => setZoom(value => Math.max(0.5, value / 1.25))}>− Zoom</button>
      <button type="button" aria-label="Zoom in" onClick={() => setZoom(value => Math.min(5, value * 1.25))}>+ Zoom</button>
      <button type="button" onClick={() => { setZoom(1); frame.current?.scrollTo(0, 0) }}>Fill</button>
      <button type="button" onClick={() => dialog.current?.close()}>Close</button>
    </div></header>
    <div className="asr-bubble-body">
      <div className="asr-bubble-frame" ref={frame} data-zoom={zoom.toFixed(3)} onWheel={event => { if (event.ctrlKey || event.metaKey) { event.preventDefault(); setZoom(value => Math.min(5, Math.max(.5, value * (event.deltaY > 0 ? .9 : 1.1)))) } }}>
        <svg ref={svg} role="group" aria-label="Interactive AllStarLink network graph" viewBox={`0 0 ${width} ${height}`} style={{ width: width * scale, height: height * scale }}>
          {edges.map(key => { const [a, b] = key.split(':'); if (!depth.has(a) || !depth.has(b) || collapsed.has(a) || collapsed.has(b)) return null; const start = positions.get(a), end = positions.get(b); return start && end ? <line key={key} x1={start.x} x2={end.x} y1={start.y} y2={end.y} /> : null })}
          {nodes.map(id => {
            const point = positions.get(id)
            return point ? <g key={id} data-node={id} data-x={point.x.toFixed(2)} data-y={point.y.toFixed(2)} className={id === node ? 'asr-bubble-root' : ''} role="button" tabIndex={0} aria-label={`Node ${id}. Enter opens station details; C collapses connections`} onClick={() => selectNode(id)} onDoubleClick={() => toggleNode(id)} onPointerDown={event => {
              if (event.button !== 0) return
              drag.current = { id, pointerId: event.pointerId, origin: point, start: graphPoint(event), moved: false }
              event.currentTarget.setPointerCapture(event.pointerId)
            }} onPointerMove={event => {
              const active = drag.current
              if (!active || active.id !== id || active.pointerId !== event.pointerId) return
              const current = graphPoint(event)
              const dx = current.x - active.start.x, dy = current.y - active.start.y
              if (Math.hypot(dx, dy) > 4) active.moved = true
              if (active.moved) setManualPositions(previous => new Map(previous).set(id, { x: Math.max(30, active.origin.x + dx), y: Math.max(30, active.origin.y + dy) }))
            }} onPointerUp={event => {
              const active = drag.current
              if (!active || active.id !== id || active.pointerId !== event.pointerId) return
              suppressClick.current = active.moved
              drag.current = null
              if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
            }} onPointerCancel={() => { drag.current = null }} onKeyDown={event => {
              if (event.key.toLowerCase() === 'c') { event.preventDefault(); toggleNode(id) }
              else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(id) }
            }}>
              <circle cx={point.x} cy={point.y} r={id === node ? 26 : 16} strokeDasharray={collapsed.has(id) ? '4 3' : undefined} />
              <text x={point.x} y={point.y + (id === node ? 45 : 33)} textAnchor="middle">{id}</text>
              <title>{id} (click for details, double-click to toggle connections)</title>
            </g> : null
          })}
        </svg>
      </div>
      <div className="asr-bubble-help"><b>Click</b> for details · <b>Double-click</b> to expand/collapse · <b>Drag</b> to move stations</div>
      {!verified ? <div className="asr-bubble-details asr-bubble-unavailable" role="status">Topology unavailable — no verified network map to display.</div> : null}
      {selected ? <aside className="asr-bubble-details" aria-live="polite" data-station-panel>
        <button type="button" aria-label="Close node details" onClick={() => setSelected(null)}>×</button>
        <strong>{detailName ? `${detailName} · ${selected}` : `Node ${selected}`}</strong>
        {detailsError ? <p>{detailsError}</p> : details ? <dl>
          {details.callsign ? <><dt>Callsign</dt><dd>{details.callsign}</dd></> : null}
          {details.description || details.name ? <><dt>Description</dt><dd>{details.description || details.name}</dd></> : null}
          {details.location ? <><dt>Location</dt><dd>{details.location}</dd></> : null}
          <dt>Connections</dt><dd>{detailConnections.length ? detailConnections.join(', ') : 'Not currently available'}</dd>
        </dl> : <p>Loading AllStarLink details…</p>}
      </aside> : null}
    </div>
    <footer className="asr-bubble-footer"><span role="status">{status}</span></footer>
  </dialog>, document.body)
}
