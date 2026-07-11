import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUpDown, ChevronDown, ChevronLeft, Menu } from 'lucide-react'
import { headerStats } from './mockData'
import {
  actionOptions,
  dropClientChannel,
  fetchBridgeCards,
  fetchAuthStatus,
  fetchCpuTemp,
  fetchDropClients,
  fetchFavorites,
  fetchFavoriteStats,
  restartAsteriskCommand,
  type FavoritesFileOption,
  type FavoriteStats,
  sendNodeCommand,
  subscribeConnectionFeed,
  type BridgeCardView,
  type AuthStatus,
  type DropClientEntry,
  type FavoriteNode,
  type LiveConnectionRow,
  type RuntimeConfig,
} from './lib/allscanLive'

const FAVORITES_DISPLAY_CACHE_KEY = 'asrFavoritesDisplayCache.v2'
const NODE_MESSAGE_LIVE_CACHE_KEY = 'asrNodeMessageState.liveOnly.v1'
const NODE_MESSAGE_LIVE_TTL = 300000
const BRIDGE_REFRESH_MS = 2000
const BRIDGE_REFRESH_TIMEOUT_MS = 5000
const BRIDGE_REFRESH_ERROR_BACKOFF_MS = 5000
const THEME_SETTINGS_KEY = 'asrThemeSettings.v1'

const loggedOutAuth: AuthStatus = {
  loggedIn: false,
  username: '',
  permission: 0,
  publicPermission: 2,
  canRead: true,
  canModify: false,
  canWrite: false,
  isAdmin: false,
}

type ThemeSettings = {
  theme?: string
  mode?: 'dark' | 'light'
}

const themeOptions = [
  { value: 'standard', label: 'Dark Side', mode: 'dark' },
  { value: 'standard', label: 'Bright Side', mode: 'light' },
  { value: 'deep-ocean-animated', label: 'Deep Ocean' },
  { value: 'matrix', label: 'Matrix' },
  { value: 'lcars-frame', label: 'ST:ASL' },
] as const

type HeaderMenuKey = 'resources' | 'admin' | 'theme'

const headerMenuGroups: Array<[HeaderMenuKey, string]> = [
  ['resources', 'Resources'],
  ['admin', 'Admin'],
  ['theme', 'Theme'],
]

const headerMenuLabels = Object.fromEntries(headerMenuGroups) as Record<HeaderMenuKey, string>

function applyThemeSettings(settings: ThemeSettings) {
  if (typeof document === 'undefined') return
  const normalized = normalizeThemeSettings(settings)
  let theme = normalized.theme
  let mode = normalized.mode || 'dark'
  if (theme === 'lcars-frame' && window.innerWidth < 1200) {
    theme = 'standard'
    mode = 'dark'
  }
  document.documentElement.dataset.asrTheme = theme
  document.documentElement.dataset.asrMode = mode
  document.body.dataset.asrTheme = theme
  document.body.dataset.asrMode = mode
}

function normalizeThemeSettings(settings: ThemeSettings): Required<ThemeSettings> {
  const allowedThemes = ['standard', 'deep-ocean-animated', 'matrix', 'lcars-frame']
  let theme = settings.theme || 'standard'
  if (theme === 'deep-ocean') theme = 'deep-ocean-animated'
  if (theme === 'matrix-static') theme = 'matrix'
  if (!allowedThemes.includes(theme)) theme = 'standard'
  const mode = settings.mode === 'light' ? 'light' : 'dark'
  return { theme, mode }
}

function readThemeSettings(): ThemeSettings {
  if (typeof window === 'undefined') return {}
  try {
    return JSON.parse(window.localStorage.getItem(THEME_SETTINGS_KEY) || '{}') as ThemeSettings
  } catch {
    return {}
  }
}

function isDesktopThemeViewport() {
  return typeof window === 'undefined' ? true : window.innerWidth >= 1200
}

function writeThemeSettings(settings: ThemeSettings) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(THEME_SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    // Ignore theme preference write failures.
  }
}

function compactBridgeDetailTitle(title: string) {
  if (title === 'Connected DMR Clients') return 'Connected Clients'
  if (title === 'Linked YSF Gateways') return 'Linked Gateways'
  if (title === 'Linked D-Star Gateways') return 'Linked Gateways'
  return title
}

function canPopulateNodeControl(value: string) {
  return /^\d{5,}$/.test(value.trim())
}

function loadFavoriteStatsCache(): Record<string, FavoriteStats> {
  if (typeof window === 'undefined') return {}

  try {
    const raw = window.localStorage.getItem(FAVORITES_DISPLAY_CACHE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    const now = Date.now()
    const next: Record<string, FavoriteStats> = {}

    Object.entries(parsed || {}).forEach(([node, value]) => {
      const item = value as Partial<FavoriteStats> & { time?: number }
      if (!item || now - Number(item.time || 0) > 3600000) return
      next[node] = {
        node,
        busyPct: String(item.busyPct ?? '').trim(),
        linkCnt: Number(item.linkCnt ?? 0),
        active: Boolean(item.active),
        keyed: Boolean(item.keyed),
        keyups: Number(item.keyups ?? 0),
        txtime: Number(item.txtime ?? 0),
        wt: Boolean(item.wt),
        status: String(item.status ?? ''),
        txPct: Number(item.txPct ?? 0),
      }
    })

    return next
  } catch {
    return {}
  }
}

function saveFavoriteStatsCache(statsMap: Record<string, FavoriteStats>) {
  if (typeof window === 'undefined') return

  const payload: Record<string, FavoriteStats & { time: number }> = {}
  Object.entries(statsMap).forEach(([node, stats]) => {
    payload[node] = {
      ...stats,
      time: Date.now(),
    }
  })

  try {
    window.localStorage.setItem(FAVORITES_DISPLAY_CACHE_KEY, JSON.stringify(payload))
  } catch {
    // Ignore cache write failures.
  }
}

function cleanNodeMessageLine(line: string) {
  return String(line || '').replace(/\s+/g, ' ').replace(/^Node Messages\s+/i, '').trim()
}

function isNodeMessageTransportNoise(line: string) {
  return /^Event Source error:/i.test(cleanNodeMessageLine(line))
}

function readLiveNodeMessageCache() {
  if (typeof window === 'undefined') return ''

  try {
    const state = JSON.parse(window.localStorage.getItem(NODE_MESSAGE_LIVE_CACHE_KEY) || '{}') as {
      line?: string
      time?: number
    }
    const age = Date.now() - Number(state.time || 0)
    const line = cleanNodeMessageLine(state.line || '')
    if (isNodeMessageTransportNoise(line)) {
      window.localStorage.removeItem(NODE_MESSAGE_LIVE_CACHE_KEY)
      return ''
    }
    return line && age <= NODE_MESSAGE_LIVE_TTL ? line : ''
  } catch {
    return ''
  }
}

function writeLiveNodeMessageCache(line: string) {
  if (typeof window === 'undefined') return

  const cleaned = cleanNodeMessageLine(line)
  if (!cleaned || /^No recent messages$/i.test(cleaned)) return
  if (isNodeMessageTransportNoise(cleaned)) return

  try {
    window.localStorage.setItem(
      NODE_MESSAGE_LIVE_CACHE_KEY,
      JSON.stringify({ line: cleaned, time: Date.now() }),
    )
  } catch {
    // Ignore cache write failures.
  }
}

const pillClasses = {
  idle: 'bg-[hsl(150,50%,15%)] text-[#eaf4f8] border-[#3a8c4a]',
  source: 'bg-[maroon] text-[yellow] border-[#d16a6a]',
  relay: 'bg-[#74560b] text-[#ffe37a] border-[#b38b24]',
  neutral: 'bg-[#1d2d38] text-[#d7ebf5] border-[#486476]',
}

const bridgeStatusClasses = {
  Idle: pillClasses.idle,
  'Source/TX': pillClasses.source,
  Relay: pillClasses.relay,
}

const bridgeRoleClasses = {
  Idle: 'allscan-bridge-card-idle',
  'Source/TX': 'allscan-bridge-card-source',
  Relay: 'allscan-bridge-card-relay',
}

const bridgeStatusLabels = {
  Idle: 'IDLE',
  'Source/TX': 'TX ACTIVE',
  Relay: 'RELAY',
}

const rowClasses = {
  idle: 'bg-[#133a21] text-[#ecf8ee]',
  talking: 'bg-[maroon] text-[yellow]',
  relay: 'bg-[#74560b] text-[#ffe37a]',
  both: 'bg-[#74560b] text-[#ffe37a]',
  normal: 'bg-transparent text-[#f1f6fb]',
  message: 'bg-transparent text-[#f1f6fb]',
}

const localRowStyles = {
  idle: { backgroundColor: 'hsl(150, 50%, 15%)', color: '#eaf4f8' },
  talking: { backgroundColor: 'maroon', color: '#eaf4f8' },
  relay: { backgroundColor: 'green', color: '#eaf4f8' },
  both: { backgroundColor: '#660', color: '#eaf4f8' },
}

const connectionColumns = [
  { key: 'node', label: 'Node', shortLabel: 'Node' },
  { key: 'info', label: 'Node Info', shortLabel: 'Info' },
  { key: 'received', label: 'Received', shortLabel: "RX'd" },
  { key: 'direction', label: 'Dir', shortLabel: 'Dir' },
  { key: 'connected', label: 'Connected', shortLabel: 'Conn' },
  { key: 'mode', label: 'Mode', shortLabel: 'Mode' },
] as const

type ConnectionSortKey = (typeof connectionColumns)[number]['key']
type SortDirection = 'asc' | 'desc'

const asset = (name: string) => `${import.meta.env.BASE_URL}${name}`

function parseDurationValue(value: string) {
  const text = String(value || '').trim()
  if (!text || text === '-' || /^never$/i.test(text)) return Number.POSITIVE_INFINITY
  const parts = text.split(':').map((part) => Number(part))
  if (parts.some((part) => !Number.isFinite(part))) return Number.POSITIVE_INFINITY
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  return parts[0]
}

function compareText(a: string, b: string, direction: SortDirection) {
  const dir = direction === 'asc' ? 1 : -1
  const av = String(a || '').toLowerCase()
  const bv = String(b || '').toLowerCase()
  if (av < bv) return -1 * dir
  if (av > bv) return 1 * dir
  return 0
}

function makeLcarsNumbers(count: number) {
  return Array.from({ length: count }, () => {
    const node = Math.floor(Math.random() * 9000 + 1000)
    const code = Math.floor(Math.random() * 90 + 10)
    return `${code}-${node}`
  })
}

function makeLcarsHeaderNumbers() {
  return Array.from({ length: 7 }, () => {
    const primary = Math.floor(Math.random() * 9000 + 100)
    const secondary = Math.floor(Math.random() * 90 + 1)
    return `${String(primary).padStart(5, ' ')}  ${String(secondary).padStart(2, ' ')}`
  }).join('\n')
}

function formatTime(date: Date) {
  return date.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

function formatUtc(date: Date) {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  })
}

function App({ config }: { config: RuntimeConfig }) {
  const [rows, setRows] = useState<LiveConnectionRow[]>([])
  const [connectedCount, setConnectedCount] = useState(0)
  const [directCount, setDirectCount] = useState(0)
  const [adjacentCount, setAdjacentCount] = useState(0)
  const [linkedNodes, setLinkedNodes] = useState<string[]>([])
  const [linkedNodeCounts, setLinkedNodeCounts] = useState<Record<string, number>>({})
  const [cpuValue, setCpuValue] = useState('131°F / 55°C')
  const [cpuBgColor, setCpuBgColor] = useState('#660')
  const [bridgeState, setBridgeState] = useState<{ updatedLabel: string; cards: BridgeCardView[] }>({
    updatedLabel: '--:--:--',
    cards: [],
  })
  const [nodeMessage, setNodeMessage] = useState('Loading live status...')
  const [nodeMessageLatest, setNodeMessageLatest] = useState('No recent messages')
  const [nodeMessageRaw, setNodeMessageRaw] = useState('')
  const [messagesOpen, setMessagesOpen] = useState(false)
  const [favorites, setFavorites] = useState<FavoriteNode[]>([])
  const [favoriteFiles, setFavoriteFiles] = useState<FavoritesFileOption[]>([])
  const [selectedFavoriteFile, setSelectedFavoriteFile] = useState('')
  const [favoriteStats, setFavoriteStats] = useState<Record<string, FavoriteStats>>(() => loadFavoriteStatsCache())
  const [favoritesOpen, setFavoritesOpen] = useState(false)
  const [favoritesScanIndex, setFavoritesScanIndex] = useState(0)
  const [favoriteSort, setFavoriteSort] = useState<{
    key: 'index' | 'node' | 'name' | 'desc' | 'location'
    direction: SortDirection
  }>({ key: 'index', direction: 'asc' })
  const [connectionSort, setConnectionSort] = useState<{
    key: ConnectionSortKey
    direction: SortDirection
  }>({ key: 'received', direction: 'asc' })
  const [dropClientOpen, setDropClientOpen] = useState(false)
  const [dropClients, setDropClients] = useState<DropClientEntry[]>([])
  const [dropClientStatus, setDropClientStatus] = useState('No named client channels loaded yet.')
  const [menuOpen, setMenuOpen] = useState(false)
  const [openSubmenu, setOpenSubmenu] = useState<HeaderMenuKey | null>(null)
  const [themeSettings, setThemeSettings] = useState<ThemeSettings>(() => readThemeSettings())
  const [desktopThemeViewport, setDesktopThemeViewport] = useState(() => isDesktopThemeViewport())
  const [nodeValue, setNodeValue] = useState('')
  const [actionValue, setActionValue] =
    useState<(typeof actionOptions)[number]['value']>('dropclient')
  const [permanent, setPermanent] = useState(false)
  const [autodisc, setAutodisc] = useState(false)
  const [clock, setClock] = useState(() => new Date())
  const [lcarsNumbers, setLcarsNumbers] = useState(() => makeLcarsNumbers(12))
  const [lcarsHeaderNumbers, setLcarsHeaderNumbers] = useState(() => makeLcarsHeaderNumbers())
  const [busy, setBusy] = useState(false)
  const [authStatus, setAuthStatus] = useState<AuthStatus>(loggedOutAuth)
  const favoriteTxHistory = useRef<Record<string, { keyups: number; txtime: number; time: number; txPct: number }>>({})
  const connectionRowsRef = useRef<LiveConnectionRow[]>([])
  const menuRef = useRef<HTMLDivElement>(null)
  const nodeMessagesArmed = useRef(false)
  const lastNodeMessage = useRef('')
  const nodeMessagesBodyRef = useRef<HTMLDivElement>(null)

  const browserTitle = config.browserTitle
  const titleText = config.headerTitle
  const effectiveThemeSettings = normalizeThemeSettings(themeSettings)
  const visibleThemeOptions = useMemo(
    () => themeOptions.filter((option) => desktopThemeViewport || option.value !== 'lcars-frame'),
    [desktopThemeViewport],
  )
  const isAddDeleteFavoriteAction = actionValue === 'addfav' || actionValue === 'delfav'
  const bridgeConnectionStates = useMemo(() => {
    const byId = new Map(bridgeState.cards.map((card) => [card.id, card.status]))
    const toRowState = (status?: BridgeCardView['status']): LiveConnectionRow['state'] | undefined => {
      if (status === 'Source/TX') return 'talking'
      if (status === 'Relay') return 'relay'
      return undefined
    }

    return Object.fromEntries(
      config.bridges
        .filter((bridge) => bridge.node)
        .map((bridge) => [bridge.node, toRowState(byId.get(bridge.id))]),
    ) as Record<string, LiveConnectionRow['state'] | undefined>
  }, [bridgeState.cards, config.bridges])

  useEffect(() => {
    document.title = browserTitle
  }, [browserTitle])

  function applyBridgeConnectionOverrides(next: { updatedLabel: string; cards: BridgeCardView[] }) {
    const rowsByNode = new Map(connectionRowsRef.current.map((row) => [row.node, row]))
    const bridgeNodeById = Object.fromEntries(config.bridges.map((bridge) => [bridge.id, bridge.node]))
    const localRow = rowsByNode.get(config.node) || connectionRowsRef.current[0]
    const localIsTransmitting = localRow?.state === 'talking' || localRow?.state === 'both'

    return {
      ...next,
      cards: next.cards.map((card) => {
        const row = rowsByNode.get(bridgeNodeById[card.id])
        if (row?.state === 'talking' && card.status === 'Idle') {
          return { ...card, status: 'Source/TX' as const }
        }
        if (row && localIsTransmitting && card.status === 'Idle') {
          return { ...card, status: 'Relay' as const }
        }
        return card
      }),
    }
  }

  function appendNodeMessage(message: string, options?: { persistLatest?: boolean }) {
    const bodyText = String(message || '')
      .replace(/\r\n?/g, '\n')
      .split(/\n+/)
      .map((line) => line.replace(/[\t ]+/g, ' ').trim())
      .filter(Boolean)
      .join('\n')
    const lines = bodyText.split('\n').filter(Boolean)
    const latest = cleanNodeMessageLine(lines[lines.length - 1] || '')
    if (!latest) return
    if (isNodeMessageTransportNoise(latest)) return
    if (/error/i.test(latest) && bodyText === lastNodeMessage.current) return
    lastNodeMessage.current = bodyText

    setNodeMessage(bodyText)
    setNodeMessageRaw((current) => {
      const next = current
        ? `${current.replace(/\s+$/, '')}\n${bodyText}`
        : bodyText
      return next.length > 50000 ? next.slice(-50000) : next
    })
    if (options?.persistLatest !== false) {
      writeLiveNodeMessageCache(latest)
      setNodeMessageLatest(latest)
    }
  }

  function mergeFavoriteTxAverage(stats: FavoriteStats): FavoriteStats {
    const now = Math.floor(Date.now() / 1000)
    const previous = favoriteTxHistory.current[stats.node]

    if (!previous || stats.keyups < previous.keyups || stats.txtime < previous.txtime) {
      favoriteTxHistory.current[stats.node] = {
        keyups: stats.keyups,
        txtime: stats.txtime,
        time: now,
        txPct: 0,
      }
      return { ...stats, txPct: stats.keyed ? 100 : 0 }
    }

    let txDelta = stats.keyups - previous.keyups
    let timeDeltaTotal = stats.txtime - previous.txtime
    const elapsed = Math.max(0, now - previous.time)

    if (timeDeltaTotal > 2 * elapsed || txDelta > elapsed / 3) {
      txDelta = 0
      timeDeltaTotal = 0
    }

    const instantPct = elapsed ? Math.min(100, Math.round((100 * timeDeltaTotal) / elapsed)) : 0
    let txPct = 0
    if (stats.keyed) txPct = 100
    else if (previous.txPct + instantPct > 2) txPct = Math.round(previous.txPct / 2 + instantPct / 2)

    favoriteTxHistory.current[stats.node] = {
      keyups: stats.keyups,
      txtime: stats.txtime,
      time: now,
      txPct,
    }

    return { ...stats, txPct }
  }

  const sortedFavorites = useMemo(() => {
    const items = [...favorites]
    const dir = favoriteSort.direction === 'asc' ? 1 : -1
    const key = favoriteSort.key

    items.sort((a, b) => {
      if (key === 'index' || key === 'node') {
        const av = Number(key === 'index' ? a.index : a.node)
        const bv = Number(key === 'index' ? b.index : b.node)
        if (Number.isFinite(av) && Number.isFinite(bv) && av !== bv) return (av - bv) * dir
      }

      const av = String(a[key] || '').toLowerCase()
      const bv = String(b[key] || '').toLowerCase()
      if (av < bv) return -1 * dir
      if (av > bv) return 1 * dir
      return 0
    })

    return items
  }, [favorites, favoriteSort])

  const sortedConnectionRows = useMemo(() => {
    const pinnedRows = rows
      .map((row, index) => ({ row, index }))
      .filter((item) => item.index === 0 || item.row.node === config.node)
    const items = rows
      .map((row, index) => ({ row, index }))
      .filter((item) => item.index !== 0 && item.row.node !== config.node)
    const dir = connectionSort.direction === 'asc' ? 1 : -1
    const key = connectionSort.key

    items.sort((a, b) => {
      if (key === 'node') {
        const av = Number(a.row.node)
        const bv = Number(b.row.node)
        if (Number.isFinite(av) && Number.isFinite(bv) && av !== bv) return (av - bv) * dir
      }

      if (key === 'received' || key === 'connected') {
        const av = parseDurationValue(a.row[key])
        const bv = parseDurationValue(b.row[key])
        if (av !== bv) return (av - bv) * dir
      }

      const result = compareText(String(a.row[key] || ''), String(b.row[key] || ''), connectionSort.direction)
      return result || a.index - b.index
    })

    return [...pinnedRows, ...items].map((item) => item.row)
  }, [rows, connectionSort, config.node])

  const faviconStatus = useMemo<'idle' | 'ptt' | 'cos' | 'both'>(() => {
    const localRow = rows.find((row) => row.node === config.node) || rows[0]
    if (localRow?.state === 'talking') return 'ptt'
    if (localRow?.state === 'relay') return 'cos'
    if (localRow?.state === 'both') return 'both'
    return 'idle'
  }, [rows, config.node])

  useEffect(() => {
    const favicon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]')
    if (!favicon) return

    const normalHref = favicon.dataset.normalHref || favicon.href
    favicon.dataset.normalHref = normalHref
    if (faviconStatus === 'idle') {
      favicon.href = normalHref
      return
    }

    const statusColors = {
      ptt: {
        ring: '#ff2b2b',
        shadow: '#ff0000',
        gradient: ['rgba(255, 45, 45, 0.72)', 'rgba(255, 0, 0, 0.5)'],
      },
      cos: {
        ring: '#31e86d',
        shadow: '#00d64a',
        gradient: ['rgba(49, 232, 109, 0.68)', 'rgba(0, 214, 74, 0.46)'],
      },
      both: {
        ring: '#ffd447',
        shadow: '#ffb000',
        gradient: ['rgba(255, 212, 71, 0.72)', 'rgba(255, 176, 0, 0.48)'],
      },
    }[faviconStatus]

    let cancelled = false
    const image = new Image()
    image.onload = () => {
      if (cancelled) return
      const canvas = document.createElement('canvas')
      canvas.width = 64
      canvas.height = 64
      const context = canvas.getContext('2d')
      if (!context) return

      const glow = context.createRadialGradient(32, 32, 11, 32, 32, 32)
      glow.addColorStop(0, statusColors.gradient[0])
      glow.addColorStop(0.58, statusColors.gradient[1])
      glow.addColorStop(1, 'rgba(255, 0, 0, 0)')
      context.fillStyle = glow
      context.fillRect(0, 0, 64, 64)

      context.beginPath()
      context.arc(32, 32, 27, 0, Math.PI * 2)
      context.strokeStyle = statusColors.ring
      context.lineWidth = 5
      context.shadowColor = statusColors.shadow
      context.shadowBlur = 12
      context.stroke()
      context.shadowBlur = 0
      context.drawImage(image, 2, 1, 60, 62)
      favicon.href = canvas.toDataURL('image/png')
    }
    image.src = normalHref

    return () => {
      cancelled = true
    }
  }, [faviconStatus])

  useEffect(() => {
    applyThemeSettings(themeSettings)
  }, [themeSettings])

  useEffect(() => {
    const handleResize = () => setDesktopThemeViewport(isDesktopThemeViewport())
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const updateTheme = (next: ThemeSettings) => {
    if (next.theme === 'lcars-frame' && !desktopThemeViewport) return
    const merged = { ...themeSettings, ...next }
    writeThemeSettings(merged)
    setThemeSettings(merged)
    setMenuOpen(false)
    setOpenSubmenu(null)
  }

  const restartAsterisk = () => {
    if (!authStatus.canWrite) return
    setMenuOpen(false)
    setOpenSubmenu(null)
    void restartAsteriskCommand(config.node)
      .then((message) => appendNodeMessage(message || 'Restart Asterisk command sent.'))
      .catch((error) => {
        const message = error instanceof Error ? error.message : 'Restart Asterisk failed.'
        appendNodeMessage(message)
      })
  }

  const logoutAllScan = async () => {
    setMenuOpen(false)
    setOpenSubmenu(null)
    try {
      await fetch('/allscan/user/?logout=1', { credentials: 'same-origin' })
    } finally {
      window.location.assign('/allscan/')
    }
  }

  useEffect(() => {
    let cancelled = false

    const refreshAuthStatus = async () => {
      try {
        const next = await fetchAuthStatus()
        if (!cancelled) {
          setAuthStatus(next)
          if (!next.canRead && !next.loggedIn) {
            window.location.assign('/allscan/user/')
          }
        }
      } catch {
        if (!cancelled) setAuthStatus(loggedOutAuth)
      }
    }

    void refreshAuthStatus()
    window.addEventListener('focus', refreshAuthStatus)
    const timer = window.setInterval(refreshAuthStatus, 30000)
    return () => {
      cancelled = true
      window.removeEventListener('focus', refreshAuthStatus)
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!config.node) {
      appendNodeMessage('No local node number was detected. Run the Reimagined setup again.')
      return
    }

    const stop = subscribeConnectionFeed(
      config.node,
      config.bridges.map((bridge) => bridge.node),
      (snapshot) => {
        connectionRowsRef.current = snapshot.rows
        setRows(snapshot.rows)
        setConnectedCount(snapshot.connectedCount)
        setDirectCount(snapshot.directCount)
        setAdjacentCount(snapshot.adjacentCount)
        setLinkedNodes(snapshot.linkedNodes)
        setLinkedNodeCounts(snapshot.linkedNodeCounts)
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      },
      (message) => {
        appendNodeMessage(message, { persistLatest: nodeMessagesArmed.current })
      },
    )

    return stop
  }, [config.node])

  useEffect(() => {
    let cancelled = false

    const refreshCpu = async () => {
      try {
        const next = await fetchCpuTemp()
        if (!cancelled && next) {
          setCpuValue(next.value)
          setCpuBgColor(next.bgColor)
        }
      } catch {
        if (!cancelled) appendNodeMessage('CPU temperature request failed.')
      }
    }

    void refreshCpu()
    const timer = window.setInterval(refreshCpu, 60000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let failureNotified = false

    if (config.bridges.length === 0) {
      setBridgeState({ updatedLabel: '--:--:--', cards: [] })
      return
    }

    const refreshBridgeCards = async () => {
      const controller = new AbortController()
      const timeout = window.setTimeout(() => controller.abort(), BRIDGE_REFRESH_TIMEOUT_MS)
      try {
        const next = await fetchBridgeCards(config, controller.signal)
        if (!cancelled) {
          setBridgeState(applyBridgeConnectionOverrides(next))
          failureNotified = false
        }
      } catch (error) {
        if (!cancelled && !failureNotified) {
          const aborted = error instanceof DOMException && error.name === 'AbortError'
          setNodeMessage(aborted ? 'Bridge status refresh timed out.' : 'Bridge status refresh failed.')
          failureNotified = true
        }
      } finally {
        window.clearTimeout(timeout)
        if (!cancelled) {
          timer = window.setTimeout(
            refreshBridgeCards,
            failureNotified ? BRIDGE_REFRESH_ERROR_BACKOFF_MS : BRIDGE_REFRESH_MS,
          )
        }
      }
    }

    void refreshBridgeCards()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [config])

  useEffect(() => {
    let cancelled = false

    const loadFavorites = async () => {
      try {
        const next = await fetchFavorites(selectedFavoriteFile)
        if (!cancelled) {
          setFavorites(next.rows)
          setFavoriteFiles(next.files)
          if (next.selectedFile !== selectedFavoriteFile) {
            setSelectedFavoriteFile(next.selectedFile)
          }
        }
      } catch {
        if (!cancelled) appendNodeMessage('Favorites list could not be loaded.')
      }
    }

    void loadFavorites()
    return () => {
      cancelled = true
    }
  }, [selectedFavoriteFile])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const armTimer = window.setInterval(() => {
      setLcarsNumbers((current) => {
        const next = [...current]
        next[Math.floor(Math.random() * next.length)] = makeLcarsNumbers(1)[0]
        return next
      })
    }, 800)
    const headerTimer = window.setInterval(() => setLcarsHeaderNumbers(makeLcarsHeaderNumbers()), 1200)
    return () => {
      window.clearInterval(armTimer)
      window.clearInterval(headerTimer)
    }
  }, [])

  useEffect(() => {
    saveFavoriteStatsCache(favoriteStats)
  }, [favoriteStats])

  useEffect(() => {
    try {
      window.localStorage.removeItem('asrNodeMessageState.v4')
      window.localStorage.removeItem('asrNodeMessageState.final')
      window.localStorage.removeItem('asrNodeMessageState.isolated')
      window.localStorage.removeItem('asrNodeMessageState.isolated.v2')
    } catch {
      // Ignore storage cleanup failures.
    }

    setNodeMessageLatest(readLiveNodeMessageCache() || 'No recent messages')
    const armTimer = window.setTimeout(() => {
      nodeMessagesArmed.current = true
    }, 1500)
    const staleTimer = window.setInterval(() => {
      setNodeMessageLatest(readLiveNodeMessageCache() || 'No recent messages')
    }, 15000)

    return () => {
      window.clearTimeout(armTimer)
      window.clearInterval(staleTimer)
    }
  }, [])

  useEffect(() => {
    if (!messagesOpen || !nodeMessagesBodyRef.current) return
    nodeMessagesBodyRef.current.scrollTop = nodeMessagesBodyRef.current.scrollHeight
  }, [messagesOpen, nodeMessageRaw])

  useEffect(() => {
    if (!favoritesOpen || sortedFavorites.length === 0) return

    let cancelled = false
    let timer = 0
    let scanIndex = 0

    const tick = async () => {
      const index = scanIndex % sortedFavorites.length
      const favorite = sortedFavorites[index]
      if (!favorite) return

      scanIndex = (index + 1) % sortedFavorites.length
      setFavoritesScanIndex(scanIndex)

      try {
        const stats = await fetchFavoriteStats(favorite.node)
        if (!cancelled && stats) {
          const mergedStats = mergeFavoriteTxAverage(stats)
          setFavoriteStats((current) => ({ ...current, [favorite.node]: mergedStats }))
        }
      } catch {
        // Keep scanning even if one stats call fails.
      }

      if (!cancelled) timer = window.setTimeout(tick, 4000)
    }

    timer = window.setTimeout(tick, 1000)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [favoritesOpen, sortedFavorites])

  useEffect(() => {
    setFavoritesScanIndex(0)
  }, [favoritesOpen, favoriteSort, selectedFavoriteFile])

  function isNetworkedFavorite(node: string) {
    return rows.some((row, index) => index > 0 && row.node === node) || linkedNodes.includes(node)
  }

  function toggleFavoriteSort(key: 'index' | 'node' | 'name' | 'desc' | 'location') {
    setFavoriteSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' }
    ))
  }

  function toggleConnectionSort(key: ConnectionSortKey) {
    setConnectionSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' }
    ))
  }

  async function loadDropClients() {
    if (!authStatus.canModify) return
    const connectedClients = connectionRowsRef.current
      .filter((row, index) => {
        if (index === 0 || row.state === 'message') return false
        if (config.bridges.some((bridge) => bridge.node === row.node)) return false
        return !/^\d+$/.test(row.node)
      })

    try {
      setDropClientStatus('Loading live named client channels...')
      const clients = await fetchDropClients()
      const enriched = clients.map((client) => {
        const match = connectedClients.find((row) => (
          client.label.toLowerCase().includes(row.node.toLowerCase())
          || row.node.toLowerCase().includes(client.label.toLowerCase())
        )) || (connectedClients.length === 1 ? connectedClients[0] : undefined)

        return {
          ...client,
          callerId: match ? `${match.node} - ${match.info}` : client.callerId,
        }
      })

      setDropClients(enriched)
      setDropClientStatus(
        enriched.length
          ? `${enriched.length} connected client(s) found.`
          : 'No IAX, Web Transceiver, Phone Portal, or named client channels found right now.',
      )
    } catch (error) {
      setDropClients([])
      setDropClientStatus(error instanceof Error ? error.message : 'Could not load clients.')
    }
  }

  async function runCommand(action: string) {
    return runCommandForNode(action, nodeValue)
  }

  async function runCommandForNode(action: string, node: string) {
    if (!authStatus.canModify) return
    if (action === 'dropclient') {
      setDropClientOpen(true)
      void loadDropClients()
      return
    }

    try {
      setBusy(true)
      const message = await sendNodeCommand({
        localNode: config.node,
        node,
        action,
        permanent,
        autodisc,
        connectedCount,
        favsfile: selectedFavoriteFile,
      })
      appendNodeMessage(message || 'Command sent.')
      if (action === 'addfav' || action === 'delfav') setFavoritesOpen(true)
      if (action === 'addfav' || action === 'delfav') {
        const next = await fetchFavorites(selectedFavoriteFile)
        setFavorites(next.rows)
        setFavoriteFiles(next.files)
        if (next.selectedFile !== selectedFavoriteFile) {
          setSelectedFavoriteFile(next.selectedFile)
        }
      }
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'Command failed.')
    } finally {
      setBusy(false)
    }
  }

  async function handleDropClient(channel: string) {
    if (!channel || !authStatus.canModify) return

    try {
      setBusy(true)
      setDropClientStatus(`Sending drop command for ${channel}...`)
      const message = await dropClientChannel(channel)
      appendNodeMessage(message)
      setDropClientStatus(message)
      window.setTimeout(() => {
        void loadDropClients()
      }, 900)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Drop failed.'
      appendNodeMessage(message)
      setDropClientStatus(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="allscan-app min-h-screen bg-[#151515] text-[#eaf4f8]">
      <div className="w-full px-0 pb-6">
        <header className="allscan-header">
          <div className="allscan-brand">
            <a className="allscan-brand-main" href="/allscan/" aria-label="Return to main AllScan page">
              <div className="allscan-wordmark">
                <strong className="allscan-wordmark-mark">
                  <span className="allscan-wordmark-silver allscan-wordmark-all">All</span>
                  <span className="allscan-wordmark-bolt-wrap" aria-hidden="true">
                    <img className="allscan-wordmark-bolt" src={asset('bolt-test-tight.png')} alt="" />
                  </span>
                  <span className="allscan-wordmark-silver allscan-wordmark-can">can</span>
                </strong>
                <span className="allscan-tagline font-georgia">Reimagined</span>
                <small className="allscan-brand-version">{config.versionLabel}</small>
                {config.brandByline ? <span className="allscan-byline">{config.brandByline}</span> : null}
              </div>
            </a>
          </div>

          <div className="allscan-header-center">
            <img className="allscan-header-ke7wil-logo" src={config.headerLogo} alt="Header logo" />
            <h1 className="allscan-title">{titleText}</h1>
            <div className="allscan-cpu">
              <span className="allscan-meta-label">CPU Temp:</span>
              <b className="allscan-cpu-pill" style={{ backgroundColor: cpuBgColor }}>{cpuValue}</b>
            </div>
            <div className="allscan-clockline">
              <span><span className="allscan-meta-label">Local</span> {formatTime(clock)}</span>
              <span><span className="allscan-meta-label">UTC</span> {formatUtc(clock)}</span>
            </div>
            <div className="allscan-status-row">
              <span className="allscan-status-label allscan-meta-label">Status</span>
              {headerStats.map((item) => (
                <span key={item.label} className={`allscan-pill ${pillClasses[item.tone]}`}>
                  {item.label}
                </span>
              ))}
            </div>
          </div>

          <div className={`allscan-menu-slot${menuOpen ? ' is-open' : ''}`} ref={menuRef}>
            <div className="allscan-access-numbers" aria-hidden="true">
              {lcarsHeaderNumbers}
            </div>
            <div className="allscan-lcars-access-buttons" aria-label="ST:ASL menu groups">
              {headerMenuGroups.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`allscan-lcars-access-button allscan-lcars-access-${key}${openSubmenu === key ? ' is-active' : ''}`}
                  aria-haspopup="menu"
                  aria-expanded={menuOpen && openSubmenu === key ? 'true' : 'false'}
                  onClick={(event) => {
                    event.stopPropagation()
                    setMenuOpen(true)
                    setOpenSubmenu((current) => current === key ? null : key)
                  }}
                >
                  {label} <ChevronDown className="h-3 w-3" />
                </button>
              ))}
              <button
                type="button"
                className="allscan-lcars-access-button allscan-lcars-access-aux"
                aria-label="Aux"
                onClick={(event) => {
                  event.stopPropagation()
                  setMenuOpen(false)
                  setOpenSubmenu(null)
                }}
              >
                Aux
              </button>
            </div>
            <button
              type="button"
              className="allscan-menu-button"
              aria-haspopup="menu"
              aria-expanded={menuOpen ? 'true' : 'false'}
              onClick={(event) => {
                event.stopPropagation()
                setMenuOpen((open) => {
                  const next = !open
                  if (!next) setOpenSubmenu(null)
                  return next
                })
              }}
            >
              <span className="allscan-menu-desktop">
                Menu <ChevronDown className="h-4 w-4" />
              </span>
              <span className="allscan-menu-mobile">
                <Menu className="h-7 w-7" />
              </span>
            </button>
            {menuOpen ? (
              <div className={`allscan-menu-panel${openSubmenu ? ' has-active-submenu' : ''}`} role="menu">
                <div className="allscan-menu-proxy-list">
                  {headerMenuGroups.map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={`allscan-menu-proxy-row${openSubmenu === key ? ' is-active' : ''}`}
                      aria-expanded={openSubmenu === key ? 'true' : 'false'}
                      onClick={(event) => {
                        event.stopPropagation()
                        setOpenSubmenu((current) => current === key ? null : key)
                      }}
                    >
                      <span>{label}</span>
                      <ChevronDown className="allscan-menu-row-icon" />
                    </button>
                  ))}
                </div>

                {openSubmenu ? (
                  <button
                    type="button"
                    className="allscan-submenu-back"
                    onClick={(event) => {
                      event.stopPropagation()
                      setOpenSubmenu(null)
                    }}
                  >
                    <ChevronLeft className="allscan-submenu-back-icon" />
                    <span>{headerMenuLabels[openSubmenu]}</span>
                  </button>
                ) : null}

                <div className={`allscan-submenu allscan-submenu-resources${openSubmenu === 'resources' ? ' is-open' : ''}`}>
                  <a role="menuitem" href="https://allscan.info/" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>AllScan.info</a>
                  <a role="menuitem" href="https://github.com/ke7wil-bridge/allscan-reimagined#updates" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>Updates</a>
                  <a role="menuitem" href="https://github.com/davidgsd/AllScan#allscan" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>Original AllScan</a>
                  <a role="menuitem" href="https://www.allstarlink.org/" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>AllStarLink.org</a>
                  <a role="menuitem" href="http://stats.allstarlink.org/stats/keyed" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>Keyed Nodes</a>
                  <a role="menuitem" href="https://community.allstarlink.org/" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>ASL Forum</a>
                  <a role="menuitem" href="https://www.facebook.com/groups/allscan" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>AllScan FB</a>
                  <a role="menuitem" href="https://www.eham.net/" target="_blank" rel="noreferrer" onClick={() => setMenuOpen(false)}>eHam.net</a>
                </div>

                <div className={`allscan-submenu allscan-submenu-admin${openSubmenu === 'admin' ? ' is-open' : ''}`}>
                  {authStatus.isAdmin ? <a role="menuitem" href="/allscan/cfg/" onClick={() => setMenuOpen(false)}>Cfgs</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href="/allscan/user/" onClick={() => setMenuOpen(false)}>Users</a> : null}
                  {authStatus.loggedIn ? <a role="menuitem" href="/allscan/user/settings/" onClick={() => setMenuOpen(false)}>Settings</a> : null}
                  {authStatus.isAdmin ? <button type="button" role="menuitem" className="allscan-menu-disabled" disabled>Reimagined Settings <small>Header, Logo, Bridges - Coming Soon</small></button> : null}
                  <a role="menuitem" href={`http://stats.allstarlink.org/stats/${config.node}`} onClick={() => setMenuOpen(false)}>Node Stats</a>
                  {authStatus.canWrite ? <button type="button" role="menuitem" onClick={restartAsterisk}>Restart Asterisk</button> : null}
                  {authStatus.loggedIn ? (
                    <button type="button" role="menuitem" onClick={() => void logoutAllScan()}>Logout</button>
                  ) : (
                    <a role="menuitem" href="/allscan/user/" onClick={() => setMenuOpen(false)}>Login</a>
                  )}
                </div>

                <div className={`allscan-submenu allscan-submenu-theme${openSubmenu === 'theme' ? ' is-open' : ''}`}>
                  {visibleThemeOptions.map((option) => {
                    const optionMode = 'mode' in option ? option.mode : 'dark'
                    const selected = effectiveThemeSettings.theme === option.value &&
                      effectiveThemeSettings.mode === optionMode
                    return (
                      <button
                        key={`${option.value}-${'mode' in option ? option.mode : 'auto'}`}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected ? 'true' : 'false'}
                        className={selected ? 'is-selected' : undefined}
                        onClick={() => updateTheme({
                          theme: option.value,
                          mode: optionMode,
                        })}
                      >
                        {option.label}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : null}
          </div>
        </header>

        <div className="allscan-lcars-arm" aria-hidden="true">
          <div className="allscan-lcars-left-arm-corner" />
          <div className="allscan-lcars-left-arm-labels">
            {lcarsNumbers.map((item, index) => (
              <span
                className="allscan-lcars-left-arm-segment"
                data-lcars-num={item}
                key={`${index}-${item}`}
              />
            ))}
          </div>
        </div>

        <main className="mx-auto max-w-[1280px] px-1 pt-[2px] sm:px-3">
          <section className="allscan-main-section allscan-controls-section">
            <h2 className="allscan-section-title">
              Node Controls
            </h2>

            <div className={`allscan-controls-shell mx-auto rounded-[8px]${messagesOpen ? ' allscan-controls-shell-messages-open' : ''}`}>
              <div className="allscan-controls-row flex flex-wrap items-center justify-center">
                <div className="allscan-node-field">
                  <label className="allscan-control-label" htmlFor="allscan-node-box">Node#</label>
                  <input
                    id="allscan-node-box"
                    value={nodeValue}
                    onChange={(event) => setNodeValue(event.target.value.replace(/[^\dA-D#*]/gi, '').slice(0, 7))}
                    maxLength={7}
                    className="allscan-node-input"
                    disabled={!authStatus.canModify}
                  />
                </div>
                <button
                  className="allscan-action-button allscan-connect-button"
                  disabled={busy || !authStatus.canModify}
                  onClick={() => void runCommand('connect')}
                >
                  Connect
                </button>
                <button
                  className="allscan-action-button allscan-disconnect-button"
                  disabled={busy || !authStatus.canModify}
                  onClick={() => void runCommand('disconnect')}
                >
                  Disconnect
                </button>
                <div className="allscan-favorites-wrap">
                  <button
                    className={`allscan-favs-button${favoritesOpen ? ' is-open' : ''}`}
                    aria-expanded={favoritesOpen ? 'true' : 'false'}
                    disabled={busy}
                    onClick={() => setFavoritesOpen((open) => !open)}
                  >
                    Favorites <ChevronDown className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="allscan-action-field">
                  <label className="allscan-control-label" htmlFor="allscan-action-select">Action</label>
                  <select
                    id="allscan-action-select"
                    className="allscan-action-select"
                    value={actionValue}
                    onChange={(event) => setActionValue(event.target.value as (typeof actionOptions)[number]['value'])}
                    disabled={!authStatus.canModify}
                  >
                    {actionOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  className="allscan-action-button allscan-go-button min-w-[46px]"
                  disabled={busy || !authStatus.canModify}
                  onClick={() => void runCommand(actionValue)}
                >
                  Go
                </button>
              </div>

              <div className="allscan-checks-row mt-[8px] flex flex-wrap items-center justify-center gap-x-4 gap-y-2">
                <label className="inline-flex items-center gap-[5px]">
                  <input
                    type="checkbox"
                    className="allscan-checkbox"
                    checked={permanent}
                    onChange={(event) => setPermanent(event.target.checked)}
                    disabled={!authStatus.canModify}
                  />
                  Permanent
                </label>
                <label className="inline-flex items-center gap-[5px]">
                  <input
                    type="checkbox"
                    className="allscan-checkbox"
                    checked={autodisc}
                    onChange={(event) => setAutodisc(event.target.checked)}
                    disabled={!authStatus.canModify}
                  />
                  Disconnect before Connect
                </label>
              </div>

              <details
                className="allscan-node-messages mx-auto mt-[8px] w-full max-w-[500px] rounded-[6px]"
                open={messagesOpen}
                onToggle={(event) => setMessagesOpen((event.currentTarget as HTMLDetailsElement).open)}
              >
                <summary className="allscan-node-messages-summary">
                  <span className="allscan-node-messages-label">Node Messages</span>
                  <span className="allscan-node-messages-text min-w-0 flex-1 truncate">
                    {nodeMessageLatest}
                  </span>
                </summary>
                <div ref={nodeMessagesBodyRef} className="allscan-node-messages-body">
                  {nodeMessageRaw || nodeMessage || nodeMessageLatest}
                </div>
              </details>
            </div>

            {favoritesOpen ? (
              <section className="allscan-favorites-panel">
                <div className="allscan-favorites-inner">
                  <div className="allscan-favorites-legend">
                    <span><i className="allscan-fav-dot allscan-fav-dot-networked" />Already Networked</span>
                    <span><i className="allscan-fav-dot allscan-fav-dot-tx" />Recent TX</span>
                    <span><i className="allscan-fav-rxbar" />Rx Busy</span>
                    <span><i className="allscan-fav-dot allscan-fav-dot-links" />Links</span>
                    <span><i className="allscan-fav-underline" />Scanning</span>
                  </div>
                  <div className="allscan-favorites-table-wrap">
                  <table className="allscan-favorites-table">
                    <thead>
                      <tr>
                        <th aria-sort={favoriteSort.key === 'index' ? (favoriteSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className={`allscan-favorites-sort-button${favoriteSort.key === 'index' ? ' is-active' : ''}`} onClick={() => toggleFavoriteSort('index')}>
                            <span>#</span>
                            <span className="allscan-sort-badge allscan-favorites-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                        <th aria-sort={favoriteSort.key === 'node' ? (favoriteSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className={`allscan-favorites-sort-button${favoriteSort.key === 'node' ? ' is-active' : ''}`} onClick={() => toggleFavoriteSort('node')}>
                            <span>Node</span>
                            <span className="allscan-sort-badge allscan-favorites-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                        <th aria-sort={favoriteSort.key === 'name' ? (favoriteSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className={`allscan-favorites-sort-button${favoriteSort.key === 'name' ? ' is-active' : ''}`} onClick={() => toggleFavoriteSort('name')}>
                            <span>Name</span>
                            <span className="allscan-sort-badge allscan-favorites-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                        <th aria-sort={favoriteSort.key === 'desc' ? (favoriteSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className={`allscan-favorites-sort-button${favoriteSort.key === 'desc' ? ' is-active' : ''}`} onClick={() => toggleFavoriteSort('desc')}>
                            <span>Desc</span>
                            <span className="allscan-sort-badge allscan-favorites-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                        <th aria-sort={favoriteSort.key === 'location' ? (favoriteSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
                          <button type="button" className={`allscan-favorites-sort-button${favoriteSort.key === 'location' ? ' is-active' : ''}`} onClick={() => toggleFavoriteSort('location')}>
                            <span>Location</span>
                            <span className="allscan-sort-badge allscan-favorites-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                        <th><small>Rx%</small></th>
                        <th><small>LCnt</small></th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedFavorites.map((favorite) => {
                        const stats = favoriteStats[favorite.node]
                        const scanning = sortedFavorites[favoritesScanIndex]?.node === favorite.node
                        const networked = isNetworkedFavorite(favorite.node)
                        const txActive = stats?.keyed || Number(stats?.txPct || 0) > 5
                        const favCellClass = txActive ? 'allscan-fav-cell-source' : undefined
                        const nodeCellClass = networked ? 'allscan-fav-cell-networked' : undefined
                        const rxText = String(stats?.busyPct ?? favorite.rx ?? '').trim()
                        const rxBusy = Number(rxText || 0)
                        const fallbackLinkCount = linkedNodeCounts[favorite.node]
                        const hasStatsLinkCount = stats && Number.isFinite(stats.linkCnt)
                        const linkText = String(hasStatsLinkCount ? stats.linkCnt : fallbackLinkCount ?? favorite.lcnt ?? '').trim()
                        const linkCount = Number(linkText || 0)
                        return (
                        <tr
                          key={favorite.node}
                        >
                          <td className={[favCellClass, scanning ? 'allscan-fav-scanning' : ''].filter(Boolean).join(' ') || undefined}>
                            <span>{favorite.index}</span>
                          </td>
                          <td
                            className={nodeCellClass}
                            onClick={() => {
                              if (!canPopulateNodeControl(favorite.node)) return
                              setNodeValue(favorite.node)
                              window.setTimeout(() => {
                                setFavoritesOpen(isAddDeleteFavoriteAction)
                              }, isAddDeleteFavoriteAction ? 120 : 80)
                            }}
                            onDoubleClick={() => {
                              if (!canPopulateNodeControl(favorite.node)) return
                              setNodeValue(favorite.node)
                              setFavoritesOpen(false)
                              void runCommandForNode('connect', favorite.node)
                            }}
                          >
                            {favorite.node}
                          </td>
                          <td>
                            {favorite.href ? (
                              <a href={favorite.href} target="_blank" rel="noreferrer">{favorite.name}</a>
                            ) : favorite.name}
                          </td>
                          <td>{favorite.desc}</td>
                          <td>{favorite.location}</td>
                          <td className={rxBusy > 2 ? 'allscan-fav-cell-rx' : undefined}>{rxText}</td>
                          <td className={linkCount >= 3 ? 'allscan-fav-cell-links' : undefined}>{linkText}</td>
                        </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  </div>
                  <form className="allscan-favorites-file-form" onSubmit={(event) => event.preventDefault()}>
                    <label htmlFor="allscan-favsfile">Favorites File</label>
                    <select
                      id="allscan-favsfile"
                      value={selectedFavoriteFile}
                      onChange={(event) => setSelectedFavoriteFile(event.target.value)}
                    >
                      {favoriteFiles.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </form>
                </div>
              </section>
            ) : null}
          </section>

          <section className="allscan-main-section allscan-connection-section">
            <h2 className="allscan-section-title" data-lcars-title={`CONNECTION STATUS - NODE ${config.node}`}>
              Connection Status
            </h2>

            <div className="allscan-status-shell overflow-hidden rounded-[14px] border border-[rgba(255,255,255,.16)] bg-black/35 shadow-[0_6px_18px_rgba(0,0,0,0.22)]">
              <div className="allscan-status-table-wrap">
                <table className="allscan-connection-table w-full border-collapse text-center">
                  <thead>
                    <tr className="bg-[rgba(255,255,255,.06)] text-[#edf4f8]">
                      {connectionColumns.map((column) => (
                        <th
                          key={column.key}
                          aria-sort={connectionSort.key === column.key ? (connectionSort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                          className="border-b border-r border-[rgba(255,255,255,.14)] px-4 py-[5px] text-[14px] font-extrabold leading-[19px] last:border-r-0"
                        >
                          <button
                            type="button"
                            className={`allscan-connection-sort-button${connectionSort.key === column.key ? ' is-active' : ''}`}
                            onClick={() => toggleConnectionSort(column.key)}
                          >
                            <span className="allscan-header-label-full">{column.label}</span>
                            <span className="allscan-header-label-short">{column.shortLabel}</span>
                            <span className="allscan-sort-badge">
                              <ArrowUpDown className="allscan-sort-icon" />
                            </span>
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedConnectionRows.map((row) => {
                      const isLocalRow = row.node === config.node
                      const displayState = isLocalRow
                        ? row.state
                        : row.state === 'talking'
                          ? row.state
                          : bridgeConnectionStates[row.node] || row.state
                      const canUseRowNode = canPopulateNodeControl(row.node)
                      const localStyle = isLocalRow
                        ? localRowStyles[row.state as keyof typeof localRowStyles]
                        : undefined

                      return (
                        <tr
                          key={`${row.node}-${row.direction}-${row.mode}`}
                          className={`allscan-connection-row allscan-connection-row-${isLocalRow ? 'local' : displayState} border-b border-[rgba(255,255,255,.12)] text-[14px] leading-[19px] ${isLocalRow ? '' : rowClasses[displayState]}`}
                        >
                          {row.state === 'message' ? (
                            <td colSpan={6} className="px-4 py-[5px]">
                              {row.info}
                            </td>
                          ) : isLocalRow ? (
                            <>
                              <td style={localStyle} className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.node}
                              </td>
                              <td style={localStyle} className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.info}
                              </td>
                              <td style={localStyle} colSpan={4} className="px-4 py-[5px]">
                                {row.received}
                              </td>
                            </>
                          ) : (
                            <>
                              <td
                                className={`${canUseRowNode ? 'cursor-pointer' : ''} border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]`}
                                title={canUseRowNode ? `Use node ${row.node}` : undefined}
                                onClick={() => {
                                  if (canUseRowNode) setNodeValue(row.node)
                                }}
                              >
                                {row.node}
                              </td>
                              <td className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.info}
                              </td>
                              <td className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.received}
                              </td>
                              <td className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.direction}
                              </td>
                              <td className="border-r border-[rgba(255,255,255,.14)] px-4 py-[5px]">
                                {row.connected}
                              </td>
                              <td className="px-4 py-[5px]">{row.mode}</td>
                            </>
                          )}
                        </tr>
                      )
                    })}
                    {connectedCount > 0 ? (
                      <tr className="allscan-status-count-row border-b border-[rgba(255,255,255,.12)] text-[14px] leading-[19px] text-[#edf4f8]">
                        <td colSpan={6} className="px-4 py-[5px]">
                          {connectedCount} total linked ({directCount} direct, {adjacentCount} adjacent)
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          {bridgeState.cards.length ? <section className="allscan-main-section allscan-bridge-section">
            <h2
              className="allscan-section-title"
              data-lcars-title={`DIGITAL BRIDGE STATUS - ${bridgeState.cards.map((card) => card.id.toUpperCase()).join(' / ')}`}
            >
              Digital Bridge Status
            </h2>
            <p className="allscan-section-subtitle">
              LAST ACTIVITY: {bridgeState.updatedLabel}
            </p>

            <div
              className="allscan-bridge-grid"
              style={{ gridTemplateColumns: `repeat(${Math.min(bridgeState.cards.length, 4)}, minmax(0, 1fr))` }}
            >
              {bridgeState.cards.map((card) => (
                <article
                  key={card.id}
                  className={`allscan-bridge-card ${bridgeRoleClasses[card.status]}`}
                >
                  <div className="allscan-bridge-head">
                    <span className="allscan-bridge-head-title">{card.title}</span>
                    <span
                      className={`allscan-bridge-status rounded-full border ${bridgeStatusClasses[card.status]}`}
                    >
                      {bridgeStatusLabels[card.status]}
                    </span>
                  </div>

                  <div className="allscan-bridge-body">
                    <div className="allscan-bridge-row">
                      <span>Talking</span>
                      <b>{card.lastCaller}</b>
                    </div>
                    <div className="allscan-bridge-row">
                      <span>Warning / Error</span>
                      <b>{card.warning}</b>
                    </div>
                  </div>

                  <div className="allscan-bridge-detail-wrap">
                    <div className="allscan-bridge-detail-title">
                      {compactBridgeDetailTitle(card.detailTitle)}
                    </div>
                    <div className="allscan-bridge-detail-box">
                      {card.detailRows.map((detail) => (
                        <div
                          key={detail.key}
                          className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}
                        >
                          <div className="allscan-bridge-client-user">{detail.label}</div>
                          {detail.meta && (
                            <div className="allscan-bridge-client-meta">{detail.meta}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section> : null}

          <footer className="allscan-footer">
            <div className="allscan-footer-copy">
              <img className="allscan-footer-micro-logo" src={config.footerLogo} alt="Footer logo" />
              {config.footerByline ? <div className="allscan-footer-byline">{config.footerByline}</div> : null}
              <div className="allscan-footer-credit">
                Based on AllScan by <strong>David Gleason, NR9V</strong>
              </div>
            </div>
          </footer>
        </main>
      </div>

      {dropClientOpen ? (
        <div className="allscan-drop-client-modal" onClick={() => setDropClientOpen(false)}>
          <div className="allscan-drop-client-box" onClick={(event) => event.stopPropagation()}>
            <h3>Drop Client</h3>
            <div className="allscan-drop-client-help">
              This targets one live named IAX, EchoLink, or Web client channel.
            </div>
            <div className="allscan-drop-client-status">{dropClientStatus}</div>
            <div className="allscan-drop-client-list">
              {dropClients.map((client) => (
                <div key={client.channel} className="allscan-drop-client-row">
                  <div className="allscan-drop-client-meta">
                    <span className="allscan-drop-client-name">{client.label || 'Client'}</span>
                    <span className="allscan-drop-client-caller">{client.callerId || 'Caller ID unavailable'}</span>
                    {client.channel !== client.label ? (
                      <span className="allscan-drop-client-channel">{client.channel}</span>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="allscan-drop-client-drop"
                    disabled={busy}
                    onClick={() => void handleDropClient(client.channel)}
                  >
                    Drop
                  </button>
                </div>
              ))}
            </div>
            <div className="allscan-drop-client-actions">
              <button type="button" className="allscan-action-button" disabled={busy} onClick={() => void loadDropClients()}>
                Refresh
              </button>
              <button type="button" className="allscan-action-button" onClick={() => setDropClientOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default App
