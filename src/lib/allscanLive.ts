const ALLSCAN_BASE = '/allscan'
const ASR_API = `${ALLSCAN_BASE}/asr-api.php`
const CONNECTION_RECONNECT_INITIAL_MS = 2000
const CONNECTION_RECONNECT_MAX_MS = 30000

export type BridgeId = string

export type RuntimeBridgeConfig = {
  id: BridgeId
  node: string
  title: string
  detailTitle: string
}

export type RuntimeConfig = {
  node: string
  callsign: string
  headerTitle: string
  browserTitle: string
  brandByline: string
  footerByline: string
  headerLogo: string
  footerLogo: string
  versionLabel: string
  lowPowerMode: boolean
  bridges: RuntimeBridgeConfig[]
}

export const defaultRuntimeConfig: RuntimeConfig = {
  node: '',
  callsign: '',
  headerTitle: 'AllScan Reimagined',
  browserTitle: 'AllScan Reimagined',
  brandByline: 'by KE7WIL',
  footerByline: 'customized by KE7WIL',
  headerLogo: `${ALLSCAN_BASE}/asr-logo-bright-r-tight.png`,
  footerLogo: `${ALLSCAN_BASE}/asr-logo-bright-r-tight.png`,
  versionLabel: 'v1.0.0 Beta 5.8',
  lowPowerMode: false,
  bridges: [],
}

export type RowState = 'idle' | 'talking' | 'relay' | 'both' | 'normal' | 'message'

export type LiveConnectionRow = {
  node: string
  info: string
  received: string
  direction: string
  connected: string
  mode: string
  state: RowState
  sourceIndex?: number
  linkedNodes?: string[]
}

export type ConnectionSnapshot = {
  rows: LiveConnectionRow[]
  connectedCount: number
  directCount: number
  adjacentCount: number
  linkedNodes: string[]
  keyedNodes: string[]
  linkedNodeCounts: Record<string, number>
}

export type FavoriteNode = {
  index: string
  node: string
  label: string
  name: string
  desc: string
  location: string
  rx: string
  lcnt: string
  href: string
}

export type FavoritesFileOption = {
  value: string
  label: string
  selected: boolean
}

export type FavoritesPayload = {
  rows: FavoriteNode[]
  files: FavoritesFileOption[]
  selectedFile: string
}

export type BridgeCardView = {
  id: string
  title: string
  status: 'Idle' | 'Source/TX' | 'Relay'
  lastCaller: string
  warning: string
  detailTitle: string
  detailRows: BridgeDetailItem[]
}

export type BridgeDetailItem = {
  key: string
  label: string
  meta: string
  empty?: boolean
}

export type FavoriteStats = {
  node: string
  busyPct: string
  linkCnt: number
  active: boolean
  keyed: boolean
  keyups: number
  txtime: number
  wt: boolean
  status: string
  txPct?: number
}

export type AuthStatus = {
  loggedIn: boolean
  username: string
  permission: number
  publicPermission: number
  canRead: boolean
  canModify: boolean
  canWrite: boolean
  isAdmin: boolean
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfig> {
  const response = await fetch(`${ASR_API}?action=runtime-config`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  const payload = (await response.json()) as Partial<RuntimeConfig> & { ok?: boolean; error?: string }
  if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Runtime configuration could not be loaded.')

  return {
    ...defaultRuntimeConfig,
    ...payload,
    bridges: Array.isArray(payload.bridges) ? payload.bridges : [],
  }
}

type FeedNode = {
  node: string | number
  info: string
  link: string
  ip: string
  direction: string
  keyed: string
  mode: string
  elapsed: string
  last_keyed: string
  cos_keyed: number
  tx_keyed: number
  lnodes: string[]
  num_links?: string | number
  num_alinks?: string | number
}

type FeedPayload = Record<
  string,
  {
    node: string
    info: string
    remote_nodes: FeedNode[]
  }
>

type BridgeLiveResponse = {
  updated?: string
  updated_epoch?: number
  dmr?: BridgeEntry
  ysf?: BridgeEntry
  zello?: BridgeEntry
  dstar?: BridgeEntry
} & Record<string, BridgeEntry | string | number | undefined>

type BridgeEntry = {
  active?: boolean
  role?: string
  state?: string
  active_start_epoch?: number
  warning?: string
  caller?: string
  current_user?: string
  last_user?: string
  recent_users?: Array<Record<string, unknown> & { name?: string }>
}

type ConnectedClientsResponse = {
  dmr?: Array<Record<string, unknown>>
  ysf?: Array<Record<string, unknown>>
  zello?: Array<Record<string, unknown>>
} & Record<string, Array<Record<string, unknown>> | undefined>

export const actionOptions = [
  { value: 'dropclient', label: 'Drop Client' },
  { value: 'monitor', label: 'Monitor' },
  { value: 'localmonitor', label: 'Local Monitor' },
  { value: 'dtmf', label: 'Send DTMF' },
  { value: 'addfav', label: 'Add Favorite' },
  { value: 'delfav', label: 'Delete Favorite' },
] as const

const parser = new DOMParser()

function htmlToText(html: string) {
  const doc = parser.parseFromString(html, 'text/html')
  return (doc.body.textContent || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function htmlToMessageText(html: string) {
  const withBreaks = html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(div|p|li)>/gi, '\n')
  const doc = parser.parseFromString(withBreaks, 'text/html')
  return (doc.body.textContent || '')
    .replace(/\u00a0/g, ' ')
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
}

function toModeLabel(mode: string) {
  if (mode === 'R') return 'Receive Only'
  if (mode === 'T') return 'Transceive'
  if (mode === 'C') return 'Connecting'
  return mode || ''
}

function normalizeFeedTime(value: string | undefined, fallback = '') {
  const text = String(value ?? '')
  const marker = text.trim().toLowerCase()
  if (
    marker === '&nbsp;'
    || marker === '&#160;'
    || marker === '&#xa0;'
    || (marker === '' && text.includes('\u00a0'))
  ) return fallback
  return text
}

function buildLocalRow(node: string, remoteNodes: FeedNode[]): LiveConnectionRow {
  let cosKeyed = 0
  let txKeyed = 0

  for (const row of remoteNodes) {
    if (row.cos_keyed === 1) cosKeyed = 1
    if (row.tx_keyed === 1) txKeyed = 1
  }

  if (cosKeyed === 0 && txKeyed === 0) {
    return { node, info: 'Idle', received: '', direction: '', connected: '', mode: '', state: 'idle' }
  }
  if (cosKeyed === 0 && txKeyed === 1) {
    return { node, info: 'PTT-Keyed', received: '', direction: '', connected: '', mode: '', state: 'talking' }
  }
  if (cosKeyed === 1 && txKeyed === 0) {
    return { node, info: 'COS-Detected', received: '', direction: '', connected: '', mode: '', state: 'relay' }
  }
  return { node, info: 'COS-Detected, PTT-Keyed', received: '', direction: '', connected: '', mode: '', state: 'both' }
}

function liveBridgeRowState(keyed: string, mode: string): RowState {
  if (keyed === 'yes') return 'talking'
  if (mode === 'C') return 'relay'

  return 'normal'
}

function buildSnapshot(payload: FeedPayload, bridgeNodes: Set<string>): ConnectionSnapshot {
  const nodeKey = Object.keys(payload)[0]
  if (!nodeKey) return { rows: [], connectedCount: 0, directCount: 0, adjacentCount: 0, linkedNodes: [], keyedNodes: [], linkedNodeCounts: {} }

  const nodeData = payload[nodeKey]
  const remoteRows: LiveConnectionRow[] = []
  const linkedNodes = new Set<string>()
  const keyedNodes = new Set<string>()
  const linkedNodeCounts: Record<string, number> = {}
  const firstExternalNode = nodeData.remote_nodes.find((row) => {
    const node = String(row.node)
    return node !== '1' && row.info !== 'NO CONNECTION' && !bridgeNodes.has(node)
  })

  let hasNoConnectionRow = false

  for (let index = 0; index < nodeData.remote_nodes.length; index += 1) {
    const row = nodeData.remote_nodes[index]
    const linkedList = (row.lnodes || []).map(String)
    linkedList.forEach((linkedNode) => linkedNodes.add(linkedNode))
    const node = String(row.node)
    if (linkedList.length > 0 && node !== '1') linkedNodeCounts[node] = linkedList.length
    if (linkedList.length > 0 && node === '1' && firstExternalNode) linkedNodeCounts[String(firstExternalNode.node)] = linkedList.length
    if (row.keyed === 'yes') keyedNodes.add(String(row.node))
    if (row.info === 'NO CONNECTION') {
      hasNoConnectionRow = true
      continue
    }
    if (String(row.node) === '1') continue

    const info = row.info ? htmlToText(row.info) : row.ip

    remoteRows.push({
      node,
      info,
      received: normalizeFeedTime(row.last_keyed),
      direction: row.direction || '',
      connected: normalizeFeedTime(row.elapsed),
      mode: toModeLabel(row.mode),
      state: liveBridgeRowState(row.keyed, row.mode),
      sourceIndex: index,
      linkedNodes: linkedList,
    })
  }

  const detailRows = remoteRows.length
    ? remoteRows
    : hasNoConnectionRow
      ? [{ node: '', info: 'No Connections', received: '', direction: '', connected: '', mode: '', state: 'message' as const }]
      : remoteRows

  const localStatus = nodeData.remote_nodes.find((row) => String(row.node) === '1')
  const officialDirectCount = Number(localStatus?.num_alinks)
  const hasOfficialDirectCount = String(localStatus?.num_alinks ?? '').trim() !== '' && Number.isFinite(officialDirectCount)
  const directCount = hasOfficialDirectCount ? officialDirectCount : remoteRows.length
  // RPT_NUMLINKS is not a reliable full-network total on every installed
  // app_rpt version. LinkedNodes contains the propagated numeric topology;
  // merge it with visible direct rows so named IAX/EchoLink clients are also
  // counted without double-counting numeric direct nodes.
  const allLinkedIds = new Set([...linkedNodes, ...remoteRows.map((row) => row.node).filter(Boolean)])
  const connectedCount = Math.max(allLinkedIds.size, directCount)
  const adjacentCount = Math.max(connectedCount - directCount, 0)

  return {
    rows: [buildLocalRow(nodeKey, nodeData.remote_nodes), ...detailRows],
    connectedCount,
    directCount,
    adjacentCount,
    linkedNodes: Array.from(linkedNodes),
    keyedNodes: Array.from(keyedNodes),
    linkedNodeCounts,
  }
}

function patchSnapshotTimes(snapshot: ConnectionSnapshot, payload: FeedPayload): ConnectionSnapshot {
  const nodeKey = Object.keys(payload)[0]
  if (!nodeKey || snapshot.rows.length === 0) return snapshot

  const remoteNodes = payload[nodeKey].remote_nodes
  const nextRows = snapshot.rows.map((row, rowIndex) => {
    if (rowIndex === 0 || row.sourceIndex === undefined) return row
    const update = remoteNodes[row.sourceIndex]
    if (!update) return row

    return {
      ...row,
      received: normalizeFeedTime(update.last_keyed, row.received),
      connected: normalizeFeedTime(update.elapsed, row.connected),
    }
  })

  return { ...snapshot, rows: nextRows }
}

function preserveSnapshotTimes(previous: ConnectionSnapshot, next: ConnectionSnapshot): ConnectionSnapshot {
  const previousByNode = new Map(previous.rows.map((row) => [row.node, row]))
  return {
    ...next,
    rows: next.rows.map((row) => {
      const oldRow = previousByNode.get(row.node)
      if (!oldRow) return row
      return {
        ...row,
        received: row.received || oldRow.received,
        connected: row.connected || oldRow.connected,
      }
    }),
  }
}

export function subscribeConnectionFeed(
  localNode: string,
  configuredBridgeNodes: string[],
  onSnapshot: (snapshot: ConnectionSnapshot) => void,
  onMessage: (message: string) => void,
) {
  const bridgeNodes = new Set(configuredBridgeNodes.filter(Boolean))
  let snapshot: ConnectionSnapshot = { rows: [], connectedCount: 0, directCount: 0, adjacentCount: 0, linkedNodes: [], keyedNodes: [], linkedNodeCounts: {} }
  let source: EventSource | undefined
  let reconnectTimer: number | undefined
  let reconnectDelay = CONNECTION_RECONNECT_INITIAL_MS
  let stopped = false
  let leaderTimer: number | undefined
  let isLeader = false
  const sharedMode = typeof BroadcastChannel !== 'undefined' && typeof window.localStorage !== 'undefined'
  const channel = sharedMode ? new BroadcastChannel(`asr-feed-${localNode}`) : undefined
  const tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const leaderKey = `asrFeedLeader.${localNode}`

  const resetReconnectDelay = () => {
    reconnectDelay = CONNECTION_RECONNECT_INITIAL_MS
  }

  const closeSource = () => {
    source?.close()
    source = undefined
  }

  const scheduleReconnect = () => {
    if (stopped || !isLeader || reconnectTimer !== undefined) return
    closeSource()
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = undefined
      connect()
    }, reconnectDelay + Math.floor(Math.random() * 750))
    reconnectDelay = Math.min(reconnectDelay * 2, CONNECTION_RECONNECT_MAX_MS)
  }

  const deliver = (eventName: string, rawData: string, share = false) => {
    if (share) channel?.postMessage({ eventName, rawData })
    if (eventName === 'nodes') {
      resetReconnectDelay()
      const next = preserveSnapshotTimes(
        snapshot,
        buildSnapshot(JSON.parse(rawData) as FeedPayload, bridgeNodes),
      )
      snapshot = next
      onSnapshot(next)
    } else if (eventName === 'nodetimes') {
      resetReconnectDelay()
      snapshot = patchSnapshotTimes(snapshot, JSON.parse(rawData) as FeedPayload)
      onSnapshot(snapshot)
    } else if (eventName === 'connection') {
      resetReconnectDelay()
      const data = JSON.parse(rawData) as { status?: string }
      if (data.status) onMessage(htmlToMessageText(data.status))
    } else if (eventName === 'errMsg') {
      const data = JSON.parse(rawData) as { status?: string }
      if (data.status) onMessage(`ERROR: ${htmlToMessageText(data.status)}`)
    }
  }

  if (channel) {
    channel.onmessage = (event: MessageEvent<{ eventName?: string; rawData?: string }>) => {
      if (event.data?.eventName && typeof event.data.rawData === 'string') {
        deliver(event.data.eventName, event.data.rawData)
      }
    }
  }

  const connect = () => {
    if (stopped) return

    const nextSource = new EventSource(`${ALLSCAN_BASE}/astapi/server.php?nodes=${encodeURIComponent(localNode)}`)
    source = nextSource

    nextSource.addEventListener('nodes', (event) => {
      deliver('nodes', (event as MessageEvent).data, sharedMode)
    })

    nextSource.addEventListener('nodetimes', (event) => {
      deliver('nodetimes', (event as MessageEvent).data, sharedMode)
    })

    nextSource.addEventListener('connection', (event) => {
      deliver('connection', (event as MessageEvent).data, sharedMode)
    })

    nextSource.addEventListener('errMsg', (event) => {
      deliver('errMsg', (event as MessageEvent).data, sharedMode)
    })

    nextSource.onerror = scheduleReconnect
  }

  const releaseLease = () => {
    if (!sharedMode) return
    try {
      const lease = JSON.parse(window.localStorage.getItem(leaderKey) || '{}') as { id?: string }
      if (lease.id === tabId) window.localStorage.removeItem(leaderKey)
    } catch {
      // Ignore local storage cleanup failures.
    }
  }

  const checkLeadership = () => {
    if (!sharedMode || stopped) return
    try {
      const now = Date.now()
      const lease = JSON.parse(window.localStorage.getItem(leaderKey) || '{}') as { id?: string; expires?: number }
      const available = !lease.id || Number(lease.expires || 0) < now || lease.id === tabId
      if (available) {
        window.localStorage.setItem(leaderKey, JSON.stringify({ id: tabId, expires: now + 3500 }))
      }
      const confirmed = JSON.parse(window.localStorage.getItem(leaderKey) || '{}') as { id?: string }
      if (confirmed.id === tabId) {
        if (!isLeader) {
          isLeader = true
          connect()
        }
      } else if (isLeader) {
        isLeader = false
        if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
        reconnectTimer = undefined
        closeSource()
      }
    } catch {
      if (!isLeader) {
        isLeader = true
        connect()
      }
    }
  }

  const releaseForPageExit = () => {
    if (!sharedMode) return
    isLeader = false
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
    reconnectTimer = undefined
    closeSource()
    releaseLease()
  }

  const resumeAfterPageRestore = () => {
    if (!stopped) checkLeadership()
  }

  if (sharedMode) {
    checkLeadership()
    leaderTimer = window.setInterval(checkLeadership, 1000)
    window.addEventListener('pagehide', releaseForPageExit)
    window.addEventListener('pageshow', resumeAfterPageRestore)
  } else {
    isLeader = true
    connect()
  }

  return () => {
    stopped = true
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
    if (leaderTimer !== undefined) window.clearInterval(leaderTimer)
    window.removeEventListener('pagehide', releaseForPageExit)
    window.removeEventListener('pageshow', resumeAfterPageRestore)
    releaseLease()
    channel?.close()
    closeSource()
  }
}

export async function fetchCpuTemp() {
  const response = await fetch(`${ASR_API}?action=cpu-temp`, { credentials: 'same-origin', cache: 'no-store' })
  const payload = (await response.json()) as { value?: string; bgColor?: string }
  return {
    value: payload.value || '',
    bgColor: payload.bgColor || '#59461c',
  }
}

export async function fetchFavorites(favsfile = ''): Promise<FavoritesPayload> {
  const url = new URL(ASR_API, window.location.origin)
  url.searchParams.set('action', 'favorites')
  if (favsfile) url.searchParams.set('favsfile', favsfile)

  const response = await fetch(url.toString(), {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  const payload = (await response.json()) as FavoritesPayload & { ok?: boolean; error?: string }
  if (payload.ok === false) throw new Error(payload.error || 'Favorites list could not be loaded.')
  return {
    rows: payload.rows || [],
    files: payload.files || [],
    selectedFile: payload.selectedFile || '',
  }
}

export type DropClientEntry = {
  label: string
  channel: string
  callerId?: string
  ip?: string
}

export type DiagnosticsReport = {
  email: string
  subject: string
  report: string
}

export async function fetchDiagnosticsReport(): Promise<DiagnosticsReport> {
  const response = await fetch(`${ASR_API}?action=diagnostics-report`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })

  const payload = (await response.json()) as DiagnosticsReport & { ok?: boolean; error?: string }
  if (!response.ok || !payload.ok) throw new Error(payload.error || 'Diagnostics report could not be generated.')
  return {
    email: payload.email || 'ke7wil@gmail.com',
    subject: payload.subject || 'ASR Bug Report',
    report: payload.report || '',
  }
}

export async function fetchDropClients() {
  const response = await fetch(`${ASR_API}?action=drop-clients`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })

  const payload = (await response.json()) as {
    ok?: boolean
    error?: string
    clients?: DropClientEntry[]
  }

  if (!payload.ok) throw new Error(payload.error || 'Could not load clients.')
  return payload.clients || []
}

export async function dropClientChannel(channel: string) {
  const body = new URLSearchParams({ channel })
  const response = await fetch(`${ASR_API}?action=drop-client`, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })

  const payload = (await response.json()) as {
    ok?: boolean
    error?: string
    message?: string
  }

  if (!payload.ok) throw new Error(payload.error || 'Drop failed.')
  return payload.message || `Drop command sent for ${channel}.`
}

function rawBridgeRole(entry?: BridgeEntry): 'idle' | 'source' | 'relay' {
  if (!entry) return 'idle'
  const role = String(entry.role || entry.state || 'idle').toLowerCase()
  if (role === 'tx' || role === 'transmit' || role === 'source' || role === 'source/tx' || role === 'tx active') {
    return 'source'
  }
  if (role === 'relay' || role === 'rx') return 'relay'
  return 'idle'
}

function mapBridgeStatus(entry?: BridgeEntry): 'Idle' | 'Source/TX' | 'Relay' {
  const role = rawBridgeRole(entry)
  if (role === 'source') return 'Source/TX'
  if (role === 'relay') return 'Relay'
  return 'Idle'
}

function markBridgeRelay(entry: BridgeEntry | undefined, peer: BridgeEntry | undefined) {
  if (!entry) return
  entry.active = true
  entry.role = 'relay'
  entry.state = 'Relay'
  if (!Number(entry.active_start_epoch || 0)) {
    entry.active_start_epoch = Number(peer?.active_start_epoch || 0) || Math.floor(Date.now() / 1000)
  }
}

function normalizeBridgeRoles(bridge: BridgeLiveResponse): BridgeLiveResponse {
  if (!bridge.dmr || !bridge.zello) return bridge

  const next: BridgeLiveResponse = {
    ...bridge,
    dmr: { ...bridge.dmr },
    ysf: bridge.ysf ? { ...bridge.ysf } : bridge.ysf,
    zello: { ...bridge.zello },
    dstar: bridge.dstar ? { ...bridge.dstar } : bridge.dstar,
  }
  const dmrRole = rawBridgeRole(next.dmr)
  const zelloRole = rawBridgeRole(next.zello)

  if (dmrRole === 'source') markBridgeRelay(next.zello, next.dmr)
  if (zelloRole === 'source') markBridgeRelay(next.dmr, next.zello)

  return next
}

function cleanBridgeCaller(value: string | undefined, config: RuntimeConfig) {
  let caller = String(value || '')
  if (config.node) {
    caller = caller
      .replace(new RegExp(`\\s*\\|\\s*Node\\s*${config.node}\\b`, 'gi'), '')
      .replace(new RegExp(`\\s+Node\\s*${config.node}\\b`, 'gi'), '')
  }
  return caller.trim()
}

function bridgeLastCaller(entry: BridgeEntry | undefined, status: 'Idle' | 'Source/TX' | 'Relay', config: RuntimeConfig) {
  if (!entry || status === 'Idle') return '-'
  if (status === 'Relay') return '-'

  const candidates = [entry.current_user, entry.caller, entry.last_user]

  const caller = candidates
    .map((value) => String(value || '').trim())
    .find((value) => value && value !== '-')

  return cleanBridgeCaller(caller, config) || '-'
}

type BridgeClientMode = string

function relativeBridgeTime(epoch: number) {
  if (!epoch) return ''
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - epoch))
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function bridgeClientEpoch(record: Record<string, unknown>, mode: BridgeClientMode) {
  const value = mode === 'zello'
    ? record.last_tx_epoch || record.tx_epoch || record.last_talk_epoch
    : record.last_tx_epoch || record.tx_epoch || record.last_talk_epoch
  return Number(value || 0) || 0
}

function bridgeClientName(record: Record<string, unknown>) {
  return String(record.callsign || record.call || record.station || record.username || record.name || record.current_user || record.display_name || record.displayName || record.user || '-').trim() || '-'
}

function formatBridgeDetailRows(value: unknown, fallback: string, mode: BridgeClientMode): BridgeDetailItem[] {
  if (!Array.isArray(value) || value.length === 0) {
    return [{ key: `${mode}-empty`, label: fallback, meta: '', empty: true }]
  }

  const rows = value
    .map((item) => typeof item === 'string'
        ? { name: item }
        : item && typeof item === 'object'
          ? item as Record<string, unknown>
          : null)
    .filter((record): record is Record<string, unknown> => Boolean(record))
    .sort((left, right) => {
      const leftEpoch = bridgeClientEpoch(left, mode)
      const rightEpoch = bridgeClientEpoch(right, mode)
      if (leftEpoch && rightEpoch && leftEpoch !== rightEpoch) return rightEpoch - leftEpoch
      if (leftEpoch && !rightEpoch) return -1
      if (!leftEpoch && rightEpoch) return 1
      return bridgeClientName(left).localeCompare(bridgeClientName(right), undefined, { sensitivity: 'base' })
    })
    .map((record, index) => {
      const user = bridgeClientName(record)
      const id = String(record.dmrid || record.dmr_id || record.id || '').trim()
      const label = mode === 'dmr' && id ? `${user} · ${id}` : user
      const age = relativeBridgeTime(bridgeClientEpoch(record, mode))
      return {
        key: `${mode}-${label}-${index}`,
        label,
        meta: age ? `Last TX ${age}` : 'No recent TX',
      }
    })

  return rows.length
    ? rows
    : [{ key: `${mode}-empty`, label: fallback, meta: '', empty: true }]
}

const ZELLO_RECENT_TALKERS_MAX_AGE = 180

function zelloRecentTalkerEpoch(item: Record<string, unknown>) {
  return Number(
    item.last_tx_epoch ||
      item.tx_epoch ||
      item.last_talk_epoch ||
      0,
  )
}

function zelloRecentTalkerName(item: Record<string, unknown>) {
  return String(item.name || item.callsign || item.username || item.user || '').trim()
}

function liveZelloRecentTalkers(entry?: BridgeEntry) {
  const now = Date.now() / 1000
  return (entry?.recent_users || [])
    .filter((item) => {
      const epoch = zelloRecentTalkerEpoch(item)
      return epoch > 0 && now - epoch <= ZELLO_RECENT_TALKERS_MAX_AGE
    })
    .filter((item) => Boolean(zelloRecentTalkerName(item)))
}

export async function fetchBridgeCards(
  config: RuntimeConfig,
  signal?: AbortSignal,
): Promise<{ updatedLabel: string; cards: BridgeCardView[] }> {
  const response = await fetch(`${ASR_API}?action=bridge-status`, { credentials: 'same-origin', cache: 'no-store', signal })
  const payload = (await response.json()) as { bridge?: BridgeLiveResponse; clients?: ConnectedClientsResponse }
  const bridge = normalizeBridgeRoles(payload.bridge || {})
  const clients = payload.clients || {}

  const cards = config.bridges.map((bridgeConfig): BridgeCardView => {
    const entryValue = bridge[bridgeConfig.id]
    const entry = entryValue && typeof entryValue === 'object' && !Array.isArray(entryValue)
      ? entryValue as BridgeEntry
      : undefined
    const cachedDetailRows = clients[bridgeConfig.id] || []
    const detailRows = bridgeConfig.id === 'zello' && cachedDetailRows.length === 0
      ? liveZelloRecentTalkers(bridge.zello)
      : cachedDetailRows
    const status = mapBridgeStatus(entry)
    return {
      id: bridgeConfig.id,
      title: bridgeConfig.title,
      status,
      lastCaller: bridgeLastCaller(entry, status, config),
      warning: entry?.warning || '-',
      detailTitle: bridgeConfig.detailTitle,
      detailRows: formatBridgeDetailRows(detailRows, 'None', bridgeConfig.id),
    }
  })

  const updatedLabel = bridge.updated
    ? bridge.updated.replace(/^\d{4}-\d{2}-\d{2}\s+/, '').replace(/\s+Local$/i, '')
    : '--:--:--'

  return { updatedLabel, cards }
}

export async function fetchFavoriteStats(node: string): Promise<FavoriteStats | null> {
  const response = await fetch(`${ALLSCAN_BASE}/stats/stats.php`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ node }).toString(),
  })

  const payload = (await response.json()) as {
    data?: {
      status?: string
      stats?: {
        node?: string | number
        busyPct?: string | number
        linkCnt?: string | number
        active?: string | number
        keyed?: string | number
        keyups?: string | number
        txtime?: string | number
        wt?: string | number
      }
    }
  }

  const stats = payload.data?.stats
  if (!stats) throw new Error(payload.data?.status || 'No stats returned.')

  return {
    node: String(stats.node || node),
    busyPct: String(stats.busyPct ?? '').trim(),
    linkCnt: Number(stats.linkCnt ?? 0),
    active: String(stats.active ?? '0') === '1',
    keyed: String(stats.keyed ?? '0') === '1',
    keyups: Number(stats.keyups ?? 0),
    txtime: Number(stats.txtime ?? 0),
    wt: String(stats.wt ?? '0') === '1',
    status: String(payload.data?.status || '').trim(),
  }
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(`${ASR_API}?action=auth-status`, {
    credentials: 'same-origin',
    cache: 'no-store',
  })
  const payload = (await response.json()) as AuthStatus & { ok?: boolean; error?: string }
  if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Login status could not be checked.')
  return payload
}

export async function sendNodeCommand(args: {
  localNode: string
  node: string
  action: string
  permanent: boolean
  autodisc: boolean
  connectedCount: number
  favsfile?: string
}) {
  const node = args.node.trim()
  if (!node) throw new Error('Enter a node number first.')

  if (args.action === 'addfav' || args.action === 'delfav') {
    const response = await fetch(`${ASR_API}?action=favorite-command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        action: 'favorite-command',
        favoriteAction: args.action,
        node,
        favsfile: args.favsfile || '',
      }).toString(),
    })

    if (!response.ok) throw new Error(`Favorite request failed (${response.status}).`)
    const payload = (await response.json()) as { ok?: boolean; error?: string; message?: string }
    if (!payload.ok) throw new Error(payload.error || `Favorite request failed (${response.status}).`)
    return payload.message || (args.action === 'addfav' ? `Added ${node} to Favorites.` : `Deleted ${node} from Favorites.`)
  }

  if (args.action === 'dtmf') {
    const response = await fetch(`${ALLSCAN_BASE}/astapi/cmd.php`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        button: 'dtmf',
        cmd: node,
        localnode: args.localNode,
      }).toString(),
    })

    if (!response.ok) throw new Error(`DTMF request failed (${response.status}).`)
    return (await response.text()).trim()
  }

  const response = await fetch(`${ALLSCAN_BASE}/astapi/connect.php`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      remotenode: node,
      perm: String(args.permanent),
      button: args.action,
      localnode: args.localNode,
      autodisc: String(args.connectedCount > 0 ? args.autodisc : false),
    }).toString(),
  })

  if (!response.ok) throw new Error(`Node command failed (${response.status}).`)
  return (await response.text()).trim()
}

export async function restartAsteriskCommand(localNode: string) {
  const response = await fetch(`${ALLSCAN_BASE}/astapi/cmd.php`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      button: 'restart',
      localnode: localNode,
    }).toString(),
  })

  if (!response.ok) throw new Error(`Restart Asterisk failed (${response.status}).`)
  return (await response.text()).trim()
}
