import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { flushSync } from 'react-dom'
import { AlertTriangle, ArrowUpDown, ChevronDown, ChevronLeft, GripVertical, Menu, Pencil, RotateCcw, Search, Trash2, Palette } from 'lucide-react'
import { headerStats } from './mockData'
import { canPopulateNodeControl } from './lib/nodeNumbers'
import { connectionCallsign, identityFromConnection, isBannableConnection } from './lib/participantIdentity'
import {
  actionOptions,
  asrPath,
  bridgeCardShowsClientDetails,
  relativeBridgeTime,
  disconnectBridge,
  dropClientChannel,
  fetchBridgeCards,
  fetchBridgeDestinations,
  fetchAuthStatus,
  fetchCpuTemp,
  fetchCustomCommands,
  fetchDiagnosticsReport,
  fetchDropClients,
  fetchFavorites,
  fetchFavoriteStats,
  manageFavorite,
  fetchReleaseStatus,
  fetchUrfBlacklist,
  updateUrfBlacklist,
  kickUrfClient,
  kickStandaloneClient,
  restartAsteriskCommand,
  saveCustomCommands,
  summarizeConnectionTotal,
  connectBridge,
  type DiagnosticsReport,
  type FavoritesFileOption,
  type FavoriteStats,
  sendNodeCommand,
  subscribeConnectionFeed,
  type BridgeCardView,
  type BridgeDestination,
  type AuthStatus,
  type DropClientEntry,
  type CustomCommand,
  type FavoriteNode,
  type LiveConnectionRow,
  type TalkerFeedEntry,
  type RuntimeConfig,
  type ReleaseStatus,
  type UrfBan,
  type UrfAuditEvent,
} from './lib/allscanLive'

const FAVORITES_DISPLAY_CACHE_KEY = 'asrFavoritesDisplayCache.v2'
const NODE_MESSAGE_LIVE_CACHE_KEY = 'asrNodeMessageState.liveOnly.v1'
const NODE_MESSAGE_LIVE_TTL = 300000
const BRIDGE_REFRESH_MS = 2000
const BRIDGE_REFRESH_TIMEOUT_MS = 5000
const BRIDGE_REFRESH_ERROR_BACKOFF_MS = 5000
const THEME_SETTINGS_KEY = 'asrThemeSettings.v1'
const AUTODISC_PREFERENCE_KEY = 'asrDisconnectBeforeConnect.v1'
const FAVORITES_PLACEMENT_KEY = 'asrFavoritesPlacement.v1'
const DASHBOARD_MODULE_ORDER_KEY = 'asrDashboardModuleOrder.v1'
const FAVORITES_OPEN_KEY = 'asrFavoritesOpen.v1'
const TALKERS_OPEN_KEY = 'asrTalkersOpen.v1'
const FAVORITES_LOAD_ERROR = 'Favorites list could not be loaded.'
const URF_BAN_DURATIONS = [
  { value: '15m', label: '15 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '1w', label: '1 week' },
  { value: '30d', label: '30 days' },
  { value: 'permanent', label: 'Permanent' },
] as const

type UrfBanDuration = (typeof URF_BAN_DURATIONS)[number]['value']

function formatUrfBanRemaining(expiresAt: number, now: Date) {
  if (expiresAt <= 0) return 'PERMANENT'
  const seconds = Math.max(0, expiresAt - Math.floor(now.getTime() / 1000))
  if (seconds <= 0) return 'EXPIRING'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  if (days > 0) return `${days}d ${hours}h remaining`
  if (hours > 0) return `${hours}h ${minutes}m remaining`
  if (minutes > 0) return `${minutes}m ${remainder}s remaining`
  return `${remainder}s remaining`
}

function formatUrfBanExpiration(expiresAt: number) {
  return expiresAt > 0 ? `until ${new Date(expiresAt * 1000).toLocaleString()}` : 'PERMANENT'
}

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

type DashboardModuleKey = 'talkers' | 'controls' | 'favorites' | 'connections' | 'bridges'

const DASHBOARD_MODULES: DashboardModuleKey[] = ['talkers', 'controls', 'favorites', 'connections', 'bridges']
const themeOptions = [
  { value: 'standard', label: 'Dark Side', mode: 'dark' },
  { value: 'standard', label: 'Bright Side', mode: 'light' },
  { value: 'deep-ocean-animated', label: 'Deep Ocean' },
  { value: 'matrix', label: 'Matrix' },
  { value: 'lcars-frame', label: 'ST:ASL' },
] as const

type HeaderMenuKey = 'resources' | 'admin' | 'theme'

const headerMenuGroups: Array<[HeaderMenuKey, string]> = [
  ['admin', 'Admin'],
  ['theme', 'Theme'],
  ['resources', 'Resources'],
]

const headerMenuLabels = Object.fromEntries(headerMenuGroups) as Record<HeaderMenuKey, string>

function applyThemeSettings(settings: ThemeSettings, lowPowerMode = false) {
  if (typeof document === 'undefined') return
  const normalized = normalizeThemeSettings(settings)
  let theme = normalized.theme
  let mode = normalized.mode || 'dark'
  if (lowPowerMode && (theme === 'deep-ocean-animated' || theme === 'matrix')) {
    theme = 'standard'
    mode = 'dark'
  }
  if (theme === 'lcars-frame' && window.innerWidth < 1200) {
    theme = 'standard'
    mode = 'dark'
  }
  document.documentElement.dataset.asrTheme = theme
  document.documentElement.dataset.asrMode = mode
  document.body.dataset.asrTheme = theme
  document.body.dataset.asrMode = mode
  document.documentElement.dataset.asrLowPower = lowPowerMode ? 'true' : 'false'
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

function readAutodiscPreference() {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(AUTODISC_PREFERENCE_KEY) === 'true'
  } catch {
    return false
  }
}

function writeAutodiscPreference(checked: boolean) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(AUTODISC_PREFERENCE_KEY, checked ? 'true' : 'false')
  } catch {
    // Ignore preference write failures.
  }
}

function normalizeDashboardModuleOrder(value: unknown): DashboardModuleKey[] {
  if (!Array.isArray(value)) return [...DASHBOARD_MODULES]
  const known = value.filter((item): item is DashboardModuleKey => (
    typeof item === 'string' && DASHBOARD_MODULES.includes(item as DashboardModuleKey)
  ))
  return [...new Set(known), ...DASHBOARD_MODULES.filter((item) => !known.includes(item))]
}

function readDashboardModuleOrder(): DashboardModuleKey[] {
  if (typeof window === 'undefined') return [...DASHBOARD_MODULES]
  try {
    const saved = window.localStorage.getItem(DASHBOARD_MODULE_ORDER_KEY)
    if (saved) return normalizeDashboardModuleOrder(JSON.parse(saved))
    const legacyPlacement = window.localStorage.getItem(FAVORITES_PLACEMENT_KEY)
    return legacyPlacement === 'below'
      ? ['talkers', 'controls', 'connections', 'favorites', 'bridges']
      : [...DASHBOARD_MODULES]
  } catch {
    return [...DASHBOARD_MODULES]
  }
}

function writeDashboardModuleOrder(order: DashboardModuleKey[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(DASHBOARD_MODULE_ORDER_KEY, JSON.stringify(order))
  } catch {
    // Ignore preference write failures.
  }
}

function compactBridgeDetailTitle(title: string) {
  if (title === 'Connected DMR Clients') return 'Connected Clients'
  if (title === 'Linked YSF Gateways') return 'Connected Clients'
  if (title === 'Linked Gateways' || title === 'Linked Clients' || title === 'Bridge Status') return 'Connected Clients'
  return title
}

function bridgeLastTalker(card: BridgeCardView) {
  return card.lastTxEpoch > 0 && card.lastTransmitter !== '-' ? `${card.lastTransmitter} · ${relativeBridgeTime(card.lastTxEpoch)}` : '–'
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

const bridgeHealthLabels = {
  warning: 'WARNING',
  unhealthy: 'UNHEALTHY',
  offline: 'OFFLINE',
}

const bridgeHealthTriangleClasses = {
  warning: 'is-warning',
  unhealthy: 'is-unhealthy',
  offline: 'is-offline',
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
  const [currentTalker, setCurrentTalker] = useState<TalkerFeedEntry | null>(null)
  const [recentTalkers, setRecentTalkers] = useState<TalkerFeedEntry[]>([])
  const [talkersOpen, setTalkersOpen] = useState(() => window.localStorage.getItem(TALKERS_OPEN_KEY) !== '0')
  useEffect(() => { window.localStorage.setItem(TALKERS_OPEN_KEY, talkersOpen ? '1' : '0') }, [talkersOpen])
  const [backgroundNodeState, setBackgroundNodeState] = useState<LiveConnectionRow['state'] | null>(null)
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
  const [dmrTalkgroupInputs, setDmrTalkgroupInputs] = useState<Record<string, string>>({})
  const [bridgeDestinationInputs, setBridgeDestinationInputs] = useState<Record<string, string>>({})
  const [bridgeDestinations, setBridgeDestinations] = useState<Record<string, BridgeDestination[]>>({})
  const [bridgeControlBusy, setBridgeControlBusy] = useState('')
  const [bridgeControlAction, setBridgeControlAction] = useState<'connect' | 'disconnect' | ''>('')
  const [bridgeClientsOpen, setBridgeClientsOpen] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('asr.bridge.clientsOpen') || '[]')) } catch { return new Set() } })
  const [bridgeHistoryOpen, setBridgeHistoryOpen] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('asr.bridge.historyOpen') || '[]')) } catch { return new Set() } })
  const [nodeMessage, setNodeMessage] = useState('Loading live status...')
  const [nodeMessageLatest, setNodeMessageLatest] = useState(() => readLiveNodeMessageCache() || 'No recent messages')
  const [nodeMessageRaw, setNodeMessageRaw] = useState('')
  const [messagesOpen, setMessagesOpen] = useState(false)
  const [favorites, setFavorites] = useState<FavoriteNode[]>([])
  const [favoriteFiles, setFavoriteFiles] = useState<FavoritesFileOption[]>([])
  const [selectedFavoriteFile, setSelectedFavoriteFile] = useState('')
  const [favoriteStats, setFavoriteStats] = useState<Record<string, FavoriteStats>>(() => loadFavoriteStatsCache())
  const [favoritesOpen, setFavoritesOpen] = useState(() => window.localStorage.getItem(FAVORITES_OPEN_KEY) === '1')
  useEffect(() => {
    window.localStorage.setItem(FAVORITES_OPEN_KEY, favoritesOpen ? '1' : '0')
  }, [favoritesOpen])
  const [customCommandsEnabled, setCustomCommandsEnabled] = useState(false)
  const [customCommands, setCustomCommands] = useState<CustomCommand[]>([])
  const [customCommandsOpen, setCustomCommandsOpen] = useState(false)
  const [customCommandsManaging, setCustomCommandsManaging] = useState(false)
  const [customCommandsDraft, setCustomCommandsDraft] = useState<CustomCommand[]>([])
  const [customCommandsStatus, setCustomCommandsStatus] = useState('')
  const [customCommandsSaving, setCustomCommandsSaving] = useState(false)
  const [favoriteEditing, setFavoriteEditing] = useState<string | null>(null)
  const [favoriteDescription, setFavoriteDescription] = useState('')
  const [favoriteColorDrafts, setFavoriteColorDrafts] = useState<Record<string, string>>({})
  const [favoriteColorOpen, setFavoriteColorOpen] = useState<string | null>(null)
  const [favoriteColorPosition, setFavoriteColorPosition] = useState<{ top: number; left: number } | null>(null)
  const [favoriteColorHue, setFavoriteColorHue] = useState(200)
  const [favoriteDragNode, setFavoriteDragNode] = useState<string | null>(null)
  const [favoriteStatus, setFavoriteStatus] = useState('')
  const [dashboardModuleOrder, setDashboardModuleOrder] = useState<DashboardModuleKey[]>(readDashboardModuleOrder)
  const [dashboardModuleDragging, setDashboardModuleDragging] = useState<DashboardModuleKey | null>(null)
  const [dashboardModuleOver, setDashboardModuleOver] = useState<DashboardModuleKey | null>(null)
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
  const [managementTab, setManagementTab] = useState<'clients' | 'bans'>('clients')
  const [aslEnforcementStatus, setAslEnforcementStatus] = useState('')
  const [aslExternal, setAslExternal] = useState<{ allstar: string[]; echolink: string[] }>({ allstar: [], echolink: [] })
  const [banDialog, setBanDialog] = useState<{ row?: LiveConnectionRow; callsign: string; value: string } | null>(null)
  const [banDuration, setBanDuration] = useState<UrfBanDuration>('1h')
  const [banStatus, setBanStatus] = useState('')
  const [banBusy, setBanBusy] = useState(false)
  const [rowActions, setRowActions] = useState<{ row: LiveConnectionRow; left: number; top: number } | null>(null)
  const [dropClients, setDropClients] = useState<DropClientEntry[]>([])
  const [dropClientStatus, setDropClientStatus] = useState('No named client channels loaded yet.')
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [diagnosticsReport, setDiagnosticsReport] = useState<DiagnosticsReport | null>(null)
  const [diagnosticsStatus, setDiagnosticsStatus] = useState('No diagnostics report loaded yet.')
  const [menuOpen, setMenuOpen] = useState(false)
  const [openSubmenu, setOpenSubmenu] = useState<HeaderMenuKey | null>(null)
  const [themeSettings, setThemeSettings] = useState<ThemeSettings>(() => readThemeSettings())
  const [desktopThemeViewport, setDesktopThemeViewport] = useState(() => isDesktopThemeViewport())
  const [nodeValue, setNodeValue] = useState('')
  const [actionValue, setActionValue] =
    useState<(typeof actionOptions)[number]['value']>('dropclient')
  const [permanent, setPermanent] = useState(false)
  const [autodisc, setAutodisc] = useState(readAutodiscPreference)
  const [clock, setClock] = useState(() => new Date())
  const [lcarsNumbers, setLcarsNumbers] = useState(() => makeLcarsNumbers(12))
  const [lcarsHeaderNumbers, setLcarsHeaderNumbers] = useState(() => makeLcarsHeaderNumbers())
  const [busy, setBusy] = useState(false)
  const [authStatus, setAuthStatus] = useState<AuthStatus>(loggedOutAuth)
  const [urfAccessOpen, setUrfAccessOpen] = useState(false)
  const [urfBlacklist, setUrfBlacklist] = useState<string[]>([])
  const [urfBans, setUrfBans] = useState<UrfBan[]>([])
  const [urfAudit, setUrfAudit] = useState<UrfAuditEvent[]>([])
  const [urfAccessRule, setUrfAccessRule] = useState('')
  const [urfBanDuration, setUrfBanDuration] = useState<UrfBanDuration>('15m')
  const [urfClientFilter, setUrfClientFilter] = useState('')
  const [urfAccessBusy, setUrfAccessBusy] = useState(false)
  const [urfAccessStatus, setUrfAccessStatus] = useState('')
  const [releaseStatus, setReleaseStatus] = useState<ReleaseStatus | null>(null)
  const favoriteTxHistory = useRef<Record<string, { keyups: number; txtime: number; time: number; txPct: number }>>({})
  const connectionRowsRef = useRef<LiveConnectionRow[]>([])
  const nodeInputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const diagnosticsTextRef = useRef<HTMLTextAreaElement>(null)
  const reportBugParamHandled = useRef(false)
  const nodeMessagesArmed = useRef(false)
  const lastNodeMessage = useRef('')
  const nodeMessagesBodyRef = useRef<HTMLDivElement>(null)
  const customCommandsRef = useRef<HTMLDivElement>(null)
  const dashboardModuleOverRef = useRef<DashboardModuleKey | null>(null)
  const dashboardDragPointerRef = useRef({ x: 0, y: 0 })
  const dashboardDragScrollFrameRef = useRef<number | null>(null)
  const dashboardModuleOrderRef = useRef<DashboardModuleKey[]>(dashboardModuleOrder)
  const dashboardDragStartOrderRef = useRef<DashboardModuleKey[] | null>(null)

  const browserTitle = config.browserTitle
  const titleText = config.headerTitle
  const effectiveThemeSettings = normalizeThemeSettings(themeSettings)
  const visibleThemeOptions = useMemo(
    () => themeOptions.filter((option) => desktopThemeViewport || option.value !== 'lcars-frame'),
    [desktopThemeViewport],
  )
  const bridgeConnectionLabels = useMemo(
    () => ({
      byId: new Map(
        config.bridges.map((bridge) => [bridge.id, bridge.friendlyName?.trim() || bridge.title]),
      ),
      byNode: new Map(
        config.bridges
          .filter((bridge) => bridge.node && !bridge.linkAlias)
          .map((bridge) => [bridge.node, bridge.friendlyName?.trim() || bridge.title]),
      ),
    }),
    [config.bridges],
  )
  const bridgeConnectionStates = useMemo(() => {
    const byId = new Map(bridgeState.cards.map((card) => [card.id, card.status]))
    const toRowState = (status?: BridgeCardView['status']): LiveConnectionRow['state'] | undefined => {
      if (status === 'Source/TX') return 'talking'
      if (status === 'Relay') return 'relay'
      return undefined
    }

    return {
      byId: new Map(
        config.bridges.map((bridge) => [bridge.id, toRowState(byId.get(bridge.id))]),
      ),
      byNode: new Map(
        config.bridges
          .filter((bridge) => bridge.node && !bridge.linkAlias)
          .map((bridge) => [bridge.node, toRowState(byId.get(bridge.id)) || 'normal']),
      ),
    }
  }, [bridgeState.cards, config.bridges])
  const connectionTotal = useMemo(
    () => summarizeConnectionTotal(directCount, adjacentCount, bridgeState.cards),
    [adjacentCount, bridgeState.cards, directCount],
  )

  const talkerCards = useMemo(() => {
    const history = recentTalkers.filter((entry) => (!currentTalker || entry.node !== currentTalker.node || entry.eventEpoch !== currentTalker.eventEpoch))
    return [currentTalker, ...history, null, null, null, null].slice(0, 5)
  }, [currentTalker, recentTalkers])
  const talkerDuration = (talker: TalkerFeedEntry | null, index: number) => {
    if (!talker) return '—'
    if (index === 0 && talker.startedEpoch > 0) {
      const elapsed = Math.max(0, Math.floor(clock.getTime() / 1000) - talker.startedEpoch)
      const hours = Math.floor(elapsed / 3600)
      const minutes = Math.floor((elapsed % 3600) / 60)
      const seconds = elapsed % 60
      return hours > 0
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    }
    const raw = String(talker.duration || '').trim()
    if (/^\d+$/.test(raw)) {
      const elapsed = Number(raw)
      const minutes = Math.floor(elapsed / 60)
      const seconds = elapsed % 60
      return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    }
    return raw || '—'
  }

  useEffect(() => {
    document.title = browserTitle
  }, [browserTitle])

  useEffect(() => {
    let cancelled = false
    void fetchReleaseStatus()
      .then((status) => {
        if (!cancelled) setReleaseStatus(status)
      })
      .catch(() => {
        // Offline and rate-limit failures stay quiet; the server keeps the last good daily result.
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!urfAccessOpen) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setUrfAccessOpen(false)
    }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [urfAccessOpen])

  function applyBridgeConnectionOverrides(next: { updatedLabel: string; cards: BridgeCardView[] }) {
    const rowsByNode = new Map(connectionRowsRef.current.map((row) => [row.node, row]))
    const localRow = rowsByNode.get(config.node) || connectionRowsRef.current[0]
    const localIsTransmitting = localRow?.state === 'talking' || localRow?.state === 'both'
    const withStatus = (card: BridgeCardView, status: BridgeCardView['status']) => ({
      ...card,
      status,
      lastCaller: status === 'Relay' ? '-' : card.lastCaller,
    })

    return {
      ...next,
      cards: next.cards.map((card) => {
        const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id)
        const isUrfModeCard = bridgeConfig?.urfReflector === true
        const isTunableDigitalBridge = card.cardType !== 'standard'
        const row = isUrfModeCard
          ? connectionRowsRef.current.find((candidate) => candidate.bridgeId === card.id && candidate.direction.toUpperCase() === 'OUT' && candidate.state !== 'message')
          : isTunableDigitalBridge
            ? connectionRowsRef.current.find((candidate) => candidate.bridgeId === card.id && candidate.direction.toUpperCase() === 'OUT' && candidate.state !== 'message')
            : rowsByNode.get(card.node)
        // A bridge-specific Source/TX or Relay role is more authoritative than
        // its Asterisk transport row. The row remains a safe fallback only when
        // the bridge collector reports idle or has not produced data yet.
        if (card.status !== 'Idle') {
          return card
        }
        if (card.cardType !== 'standard' && card.cardType !== 'dmr_net' && !card.controlLinked) {
          return withStatus(card, 'Idle')
        }
        if (row?.state === 'talking') {
          return withStatus(card, 'Source/TX')
        }
        if (row) {
          return withStatus(card, localIsTransmitting ? 'Relay' : 'Idle')
        }
        if (isTunableDigitalBridge) {
          return withStatus(card, 'Idle')
        }
        return withStatus(card, card.status)
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

  function clearNodeMessage(message: string) {
    const target = cleanNodeMessageLine(message)
    if (!target) return
    const removeTarget = (current: string) => {
      const remaining = String(current || '')
        .replace(/\r\n?/g, '\n')
        .split('\n')
        .filter((line) => cleanNodeMessageLine(line) !== target)
        .join('\n')
        .trim()
      return remaining
    }
    setNodeMessageRaw(removeTarget)
    setNodeMessage((current) => removeTarget(current) || readLiveNodeMessageCache() || 'No recent messages')
    setNodeMessageLatest((current) => (
      cleanNodeMessageLine(current) === target
        ? readLiveNodeMessageCache() || 'No recent messages'
        : current
    ))
    if (cleanNodeMessageLine(lastNodeMessage.current) === target) {
      lastNodeMessage.current = ''
    }
    try {
      if (cleanNodeMessageLine(readLiveNodeMessageCache()) === target) {
        window.localStorage.removeItem(NODE_MESSAGE_LIVE_CACHE_KEY)
      }
    } catch {
      // Ignore cache cleanup failures.
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

    const txDelta = stats.keyups - previous.keyups
    let timeDeltaTotal = stats.txtime - previous.txtime
    const elapsed = Math.max(0, now - previous.time)

    if (timeDeltaTotal > 2 * elapsed || txDelta > elapsed / 3) {
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

  const sortedFavorites = favorites

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
    const localState = backgroundNodeState
      || (rows.find((row) => row.node === config.node) || rows[0])?.state
    if (localState === 'talking') return 'ptt'
    if (localState === 'relay') return 'cos'
    if (localState === 'both') return 'both'
    return 'idle'
  }, [rows, config.node, backgroundNodeState])

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
    applyThemeSettings(themeSettings, config.lowPowerMode)
  }, [themeSettings, config.lowPowerMode])

  useEffect(() => {
    const handleResize = () => setDesktopThemeViewport(isDesktopThemeViewport())
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => () => {
    if (dashboardDragScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(dashboardDragScrollFrameRef.current)
    }
  }, [])

  useEffect(() => {
    if (!dashboardModuleDragging) return
    const finish = () => {
      if (dashboardModuleDragging) {
        writeDashboardModuleOrder(dashboardModuleOrderRef.current)
        dashboardDragStartOrderRef.current = null
        stopDashboardDragAutoScroll()
        dashboardModuleOverRef.current = null
        setDashboardModuleDragging(null)
        setDashboardModuleOver(null)
      }
    }
    window.addEventListener('pointerup', finish)
    window.addEventListener('blur', finish)
    return () => {
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('blur', finish)
    }
  }, [dashboardModuleDragging])

  useEffect(() => {
    if (reportBugParamHandled.current || !authStatus.isAdmin) return
    const params = new URLSearchParams(window.location.search)
    if (params.get('reportBug') !== '1') return
    reportBugParamHandled.current = true
    window.history.replaceState(null, '', window.location.pathname)
    openDiagnosticsReport()
    // This effect intentionally reacts only when admin authorization becomes available.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authStatus.isAdmin])

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false)
      if (!customCommandsRef.current?.contains(event.target as Node)) {
        setCustomCommandsOpen(false)
        setCustomCommandsManaging(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      setCustomCommandsOpen(false)
      setCustomCommandsManaging(false)
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
      await fetch(asrPath('user/?logout=1'), { credentials: 'same-origin' })
    } finally {
      window.location.assign(asrPath())
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
            window.location.assign(asrPath('user/'))
          }
        }
      } catch {
        if (!cancelled) setAuthStatus(loggedOutAuth)
      }
    }

    void refreshAuthStatus()
    window.addEventListener('focus', refreshAuthStatus)
    const timer = window.setInterval(refreshAuthStatus, 120000)
    return () => {
      cancelled = true
      window.removeEventListener('focus', refreshAuthStatus)
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (!authStatus.canRead) return
    let cancelled = false
    const refreshCustomCommands = async () => {
      try {
        const next = await fetchCustomCommands()
        if (cancelled) return
        setCustomCommandsEnabled(next.enabled)
        setCustomCommands(next.commands)
      } catch (error) {
        if (!cancelled) setCustomCommandsStatus(error instanceof Error ? error.message : 'Custom commands could not be loaded.')
      }
    }
    void refreshCustomCommands()
    window.addEventListener('focus', refreshCustomCommands)
    return () => {
      cancelled = true
      window.removeEventListener('focus', refreshCustomCommands)
    }
  }, [authStatus.canRead])

  useEffect(() => {
    if (!config.node) {
      const messageTimer = window.setTimeout(() => {
        appendNodeMessage('No local node number was detected. Run the Reimagined setup again.')
      }, 0)
      return () => window.clearTimeout(messageTimer)
    }

    const stop = subscribeConnectionFeed(
      config.node,
      config.bridges,
      (snapshot) => {
        const labeledRows = snapshot.rows.map((row) => {
          const configuredLabel = row.bridgeId
            ? bridgeConnectionLabels.byId.get(row.bridgeId)
            : bridgeConnectionLabels.byNode.get(row.node)
          return configuredLabel ? { ...row, info: configuredLabel } : row
        })
        if (document.hidden) {
          const localRow = labeledRows.find((row) => row.node === config.node) || labeledRows[0]
          setBackgroundNodeState(localRow?.state || 'idle')
          return
        }
        setBackgroundNodeState(null)
        connectionRowsRef.current = labeledRows
        setRows(labeledRows)
        setConnectedCount(snapshot.connectedCount)
        setDirectCount(snapshot.directCount)
        setAdjacentCount(snapshot.adjacentCount)
        setLinkedNodes(snapshot.linkedNodes)
        setLinkedNodeCounts(snapshot.linkedNodeCounts)
        setCurrentTalker(snapshot.currentTalker)
        setRecentTalkers(snapshot.recentTalkers)
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      },
      (message) => {
        appendNodeMessage(message, { persistLatest: nodeMessagesArmed.current })
      },
    )

    return stop
    // Bridge nodes are read when this node subscription is created; reconnecting
    // is intentionally keyed to the primary node only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    const timer = window.setInterval(refreshCpu, config.lowPowerMode ? 120000 : 60000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [config.lowPowerMode])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let failureNotified = false

    if (config.bridges.length === 0) {
      const resetTimer = window.setTimeout(() => {
        setBridgeState({ updatedLabel: '--:--:--', cards: [] })
      }, 0)
      return () => window.clearTimeout(resetTimer)
    }

    const refreshBridgeCards = async () => {
      if (document.hidden) return
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
        if (!cancelled) {
          // Counts and retained callers are certified only by a current bridge
          // response. Do not leave either displayed indefinitely on failures.
          setBridgeState((current) => ({
            ...current,
            cards: current.cards.map((card) => ({
              ...card,
              lastCaller: '-',
              connectedClientCount: 0,
            })),
          }))
        }
      } finally {
        window.clearTimeout(timeout)
        if (!cancelled) {
          timer = window.setTimeout(
            refreshBridgeCards,
            failureNotified ? BRIDGE_REFRESH_ERROR_BACKOFF_MS : (config.lowPowerMode ? 5000 : BRIDGE_REFRESH_MS),
          )
        }
      }
    }

    const refreshWhenVisible = () => {
      if (document.hidden || cancelled) return
      if (timer !== undefined) window.clearTimeout(timer)
      timer = undefined
      void refreshBridgeCards()
    }

    void refreshBridgeCards()
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
    // applyBridgeConnectionOverrides reads the latest connection-row ref and
    // must not restart bridge polling when connection rows change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config])

  useEffect(() => {
    let cancelled = false
    const netCards = config.bridges.filter((bridge) =>
      bridge.adminCapabilities?.bridgeControl.includes('changeDestination')
      && bridge.cardType !== 'dmr_net'
      && bridge.cardType !== 'ysf_net')
    const load = () => {
      void Promise.all(netCards.map(async (bridge) => {
        try {
          return [bridge.id, await fetchBridgeDestinations(bridge.id)] as const
        } catch {
          return [bridge.id, []] as const
        }
      })).then((entries) => {
        if (!cancelled) setBridgeDestinations(Object.fromEntries(entries))
      })
    }
    load()
    const timer = window.setInterval(load, 10_000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [config.bridges])

  useEffect(() => {
    let cancelled = false
    let retryTimer: number | undefined
    let attempts = 0
    let failureNotified = false

    const loadFavorites = async () => {
      try {
        const next = await fetchFavorites(selectedFavoriteFile)
        if (!cancelled) {
          clearNodeMessage(FAVORITES_LOAD_ERROR)
          setFavorites(next.rows)
          setFavoriteFiles(next.files)
          attempts = 0
          failureNotified = false
          if (next.selectedFile !== selectedFavoriteFile) {
            setSelectedFavoriteFile(next.selectedFile)
          }
        }
      } catch {
        if (cancelled) return
        attempts += 1
        if (!failureNotified) {
          appendNodeMessage(FAVORITES_LOAD_ERROR)
          failureNotified = true
        }
        if (attempts < 4) {
          retryTimer = window.setTimeout(loadFavorites, Math.min(5000, attempts * 1500))
        }
      }
    }

    void loadFavorites()
    return () => {
      cancelled = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
    }
  }, [selectedFavoriteFile])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!urfAccessOpen || !authStatus.isAdmin) return
    const refresh = async () => {
      try {
        const result = await fetchUrfBlacklist()
        setUrfBlacklist(result.rules)
        setUrfBans(result.bans)
      setUrfAudit(result.audit)
      } catch { /* Keep the current list; explicit actions report errors. */ }
    }
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => window.clearInterval(timer)
  }, [urfAccessOpen, authStatus.isAdmin])

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

      setFavoritesScanIndex(index)
      scanIndex = (index + 1) % sortedFavorites.length

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
    const resetTimer = window.setTimeout(() => setFavoritesScanIndex(0), 0)
    return () => window.clearTimeout(resetTimer)
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

  async function reloadFavorites() {
    const next = await fetchFavorites(selectedFavoriteFile)
    setFavorites(next.rows)
    setFavoriteFiles(next.files)
    if (next.selectedFile !== selectedFavoriteFile) setSelectedFavoriteFile(next.selectedFile)
  }

  async function favoriteOperation(
    operation: Parameters<typeof manageFavorite>[0]['operation'],
    options: { node?: string; value?: string } = {},
  ) {
    if (!authStatus.canModify) return false
    try {
      setBusy(true)
      const result = await manageFavorite({
        operation,
        favsfile: selectedFavoriteFile,
        node: options.node,
        value: options.value,
      })
      if (operation !== 'preview-import') await reloadFavorites()
      if (operation === 'import') {
        setFavoriteStatus(`Imported ${Number(result.added || 0)} new Favorite(s); existing entries and customizations were preserved.`)
      } else {
        setFavoriteStatus('Favorites saved.')
      }
      return true
    } catch (error) {
      setFavoriteStatus(error instanceof Error ? error.message : 'Favorites operation failed.')
      return false
    } finally {
      setBusy(false)
    }
  }

  function moveDashboardModule(source: DashboardModuleKey, target: DashboardModuleKey) {
    if (source === target) return
    const applyOrder = () => setDashboardModuleOrder((current) => {
      const next = [...current]
      const sourceIndex = next.indexOf(source)
      const targetIndex = next.indexOf(target)
      if (sourceIndex < 0 || targetIndex < 0) return current
      next.splice(sourceIndex, 1)
      next.splice(targetIndex, 0, source)
      writeDashboardModuleOrder(next)
      return next
    })
    const viewTransitionDocument = document as Document & {
      startViewTransition?: (callback: () => void) => unknown
    }
    if (typeof viewTransitionDocument.startViewTransition === 'function') {
      viewTransitionDocument.startViewTransition(() => flushSync(applyOrder))
    } else {
      applyOrder()
    }
  }

  function previewDashboardModuleMove(source: DashboardModuleKey, target: DashboardModuleKey) {
    if (source === target) return
    const current = dashboardModuleOrderRef.current
    const sourceIndex = current.indexOf(source)
    const targetIndex = current.indexOf(target)
    if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return
    const next = [...current]
    next.splice(sourceIndex, 1)
    next.splice(targetIndex, 0, source)
    dashboardModuleOrderRef.current = next
    const apply = () => setDashboardModuleOrder(next)
    const viewTransitionDocument = document as Document & {
      startViewTransition?: (callback: () => void) => unknown
    }
    if (typeof viewTransitionDocument.startViewTransition === 'function') {
      viewTransitionDocument.startViewTransition(() => flushSync(apply))
    } else {
      apply()
    }
  }

  function updateDashboardDropTarget(x: number, y: number) {
    const module = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-dashboard-module]')
    const target = module?.dataset.dashboardModule as DashboardModuleKey | undefined
    if (!target || !DASHBOARD_MODULES.includes(target) || dashboardModuleOverRef.current === target) return
    dashboardModuleOverRef.current = target
    setDashboardModuleOver(target)
    if (dashboardModuleDragging && target !== dashboardModuleDragging) {
      previewDashboardModuleMove(dashboardModuleDragging, target)
    }
  }

  function stopDashboardDragAutoScroll() {
    if (dashboardDragScrollFrameRef.current === null) return
    window.cancelAnimationFrame(dashboardDragScrollFrameRef.current)
    dashboardDragScrollFrameRef.current = null
  }

  function runDashboardDragAutoScroll() {
    dashboardDragScrollFrameRef.current = null
    if (!dashboardModuleDragging) return
    const { x, y } = dashboardDragPointerRef.current
    const edge = Math.min(120, Math.max(72, window.innerHeight * .12))
    let delta = 0
    if (y < edge) delta = -Math.min(24, Math.max(4, Math.ceil((edge - y) / 5)))
    if (y > window.innerHeight - edge) delta = Math.min(24, Math.max(4, Math.ceil((y - (window.innerHeight - edge)) / 5)))
    if (!delta) return
    const before = window.scrollY
    window.scrollBy(0, delta)
    updateDashboardDropTarget(x, y)
    if (window.scrollY !== before) {
      dashboardDragScrollFrameRef.current = window.requestAnimationFrame(runDashboardDragAutoScroll)
    }
  }

  function scheduleDashboardDragAutoScroll() {
    if (dashboardDragScrollFrameRef.current !== null) return
    dashboardDragScrollFrameRef.current = window.requestAnimationFrame(runDashboardDragAutoScroll)
  }

  function beginDashboardModuleDrag(key: DashboardModuleKey, event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dashboardModuleOverRef.current = key
    dashboardModuleOrderRef.current = dashboardModuleOrder
    dashboardDragStartOrderRef.current = [...dashboardModuleOrder]
    dashboardDragPointerRef.current = { x: event.clientX, y: event.clientY }
    setDashboardModuleDragging(key)
    setDashboardModuleOver(key)
  }

  function updateDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (!dashboardModuleDragging) return
    event.preventDefault()
    dashboardDragPointerRef.current = { x: event.clientX, y: event.clientY }
    updateDashboardDropTarget(event.clientX, event.clientY)
    scheduleDashboardDragAutoScroll()
  }

  function clearDashboardModuleDrag() {
    stopDashboardDragAutoScroll()
    dashboardModuleOverRef.current = null
    setDashboardModuleDragging(null)
    setDashboardModuleOver(null)
  }

  function endDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (dashboardModuleDragging) writeDashboardModuleOrder(dashboardModuleOrderRef.current)
    dashboardDragStartOrderRef.current = null
    clearDashboardModuleDrag()
  }

  function cancelDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    const startOrder = dashboardDragStartOrderRef.current
    if (startOrder) {
      dashboardModuleOrderRef.current = startOrder
      setDashboardModuleOrder(startOrder)
    }
    dashboardDragStartOrderRef.current = null
    clearDashboardModuleDrag()
  }

  function moveDashboardModuleByKeyboard(key: DashboardModuleKey, event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    event.preventDefault()
    const index = dashboardModuleOrder.indexOf(key)
    const targetIndex = index + (event.key === 'ArrowUp' ? -1 : 1)
    const target = dashboardModuleOrder[targetIndex]
    if (target) moveDashboardModule(key, target)
  }

  function dashboardModuleHandle(key: DashboardModuleKey, label: string) {
    return (
      <button
        type="button"
        className="allscan-module-drag-handle"
        aria-label={`Move ${label}. Use arrow keys or drag.`}
        title={`Move ${label}`}
        onPointerDown={(event) => beginDashboardModuleDrag(key, event)}
        onPointerMove={updateDashboardModuleDrag}
        onPointerUp={endDashboardModuleDrag}
        onPointerCancel={cancelDashboardModuleDrag}
        onLostPointerCapture={() => { if (dashboardModuleDragging) clearDashboardModuleDrag() }}
        onKeyDown={(event) => moveDashboardModuleByKeyboard(key, event)}
      >
        <span />
        <span />
      </button>
    )
  }

  async function reorderFavorite(sourceNode: string, targetNode: string) {
    if (sourceNode === targetNode) return
    const order = favorites.map((favorite) => favorite.node)
    const sourceIndex = order.indexOf(sourceNode)
    const targetIndex = order.indexOf(targetNode)
    if (sourceIndex < 0 || targetIndex < 0) return
    const [moved] = order.splice(sourceIndex, 1)
    order.splice(targetIndex, 0, moved)
    setFavorites(order.map((node) => favorites.find((favorite) => favorite.node === node)!).filter(Boolean))
    await favoriteOperation('reorder', { value: JSON.stringify(order) })
  }

  function toggleConnectionSort(key: ConnectionSortKey) {
    setConnectionSort((current) => (
      current.key === key
        ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' }
        : { key, direction: 'asc' }
    ))
  }

  function isProtectedBanConnection(row: LiveConnectionRow) {
    const protectedIdentities = new Set(config.protectedBanIdentities.map((value) => value.toUpperCase()))
    const rowNode = row.node.trim().toUpperCase()
    const infoTokens = row.info.toUpperCase().split(/[^A-Z0-9_./-]+/).filter(Boolean)
    return protectedIdentities.has(rowNode) || infoTokens.some((value) => protectedIdentities.has(value))
  }

  function globalBanDurationSelect(identity: string) {
    const callsign = identity.replace(/\s+[A-Z]$/i, '').trim().toUpperCase()
    if (!/^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/.test(callsign)) return null
    const banned = urfBlacklist.some((rule) => rule.endsWith('*') ? callsign.startsWith(rule.slice(0, -1)) : callsign === rule)
    if (banned) return null
    return <select value="" aria-label={`Ban ${callsign}`} disabled={urfAccessBusy} onChange={(event) => {
      const duration = event.target.value as UrfBanDuration
      if (duration) void changeUrfBlacklist('ban', callsign, duration)
    }}>
      <option value="" disabled>Ban</option>
      {URF_BAN_DURATIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  }

  function openParticipantBan(row?: LiveConnectionRow) {
    const callsign = row ? (identityFromConnection(row).callsign || '') : ''
    if (row && isProtectedBanConnection(row)) {
      setBanStatus('This installed service bridge is protected and cannot be banned.')
      setRowActions(null)
      return
    }
    if (row && !callsign) {
      setBanStatus('A verified callsign is required because every ASR ban is global.')
      setRowActions(null)
      return
    }
    setBanDialog({ row, callsign, value: callsign })
    setBanDuration('1h')
    setBanStatus('')
    setRowActions(null)
  }

  async function loadAslBans() {
    if (!authStatus.isAdmin) return
    try {
      const result = await fetchUrfBlacklist()
      setUrfBlacklist(result.rules)
      setUrfBans(result.bans)
      setUrfAudit(result.audit)
      setAslEnforcementStatus(result.asl?.status === 'applied' ? 'Global policy applied everywhere' : `Global policy pending: ${result.asl?.error || 'Adapter unavailable'}`)
      setAslExternal({
        allstar: result.asl?.applied?.externalAst || [],
        echolink: result.asl?.applied?.externalEcho || [],
      })
      setBanStatus('')
    } catch (error) {
      setBanStatus(error instanceof Error ? error.message : 'Global Ban list could not be loaded.')
    }
  }

  async function submitParticipantBan() {
    if (!authStatus.isAdmin || !banDialog || banBusy) return
    const value = banDialog.value.trim().toUpperCase()
    try {
      setBanBusy(true)
      setBanStatus('Applying Global Ban…')
      if (!/^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/.test(value)) throw new Error('Enter a valid callsign. Every ASR ban is global.')
      if (banDialog.row && isProtectedBanConnection(banDialog.row)) throw new Error('This installed service bridge is protected and cannot be banned.')
      const result = await updateUrfBlacklist('ban', value, banDuration)
      setUrfBlacklist(result.rules)
      setUrfBans(result.bans)
      setUrfAudit(result.audit)
      setAslEnforcementStatus(result.asl?.status === 'applied' ? 'Global policy applied everywhere' : `Global policy pending: ${result.asl?.error || 'Adapter unavailable'}`)
      if (!result.verified || result.asl?.applied?.unmappedGlobalRules?.includes(value)) {
        throw new Error(`Global Ban was saved, but full enforcement is pending: ${result.asl?.error || 'The callsign could not be mapped.'}`)
      }
      let disconnectStatus = ''
      if (banDialog.row) {
        try {
          if (/^[0-9]{3,10}$/.test(banDialog.row.node)) {
            await sendNodeCommand({
              localNode: config.node, node: banDialog.row.node, action: 'disconnect',
              permanent: false, autodisc: false, connectedCount, favsfile: selectedFavoriteFile,
            })
          } else {
            const client = (await fetchDropClients()).find((entry) =>
              entry.label.toUpperCase().includes(banDialog.row!.node.toUpperCase()) ||
              entry.label.toUpperCase().includes(value))
            if (!client) throw new Error('Active named client was not found.')
            await dropClientChannel(client.channel)
          }
          disconnectStatus = ' Current connection was disconnected.'
        } catch {
          disconnectStatus = ' Ban is active; the connection may already have been removed by the backend.'
        }
      }
      setBanDialog(null)
      setBanStatus(`Globally banned ${value} for ${URF_BAN_DURATIONS.find((option) => option.value === banDuration)?.label || banDuration}.${disconnectStatus}`)
    } catch (error) {
      setBanStatus(error instanceof Error ? error.message : 'Global Ban could not be applied.')
    } finally {
      setBanBusy(false)
    }
  }

  async function removeGlobalBan(rule: string) {
    if (!authStatus.isAdmin || banBusy) return
    try {
      setBanBusy(true)
      const result = await updateUrfBlacklist('unban', rule)
      setUrfBlacklist(result.rules)
      setUrfBans(result.bans)
      setUrfAudit(result.audit)
      setBanStatus(result.verified ? `Removed global ban for ${rule}.` : `Global Ban removal is pending full enforcement: ${result.asl?.error || 'Retrying.'}`)
    } catch (error) {
      setBanStatus(error instanceof Error ? error.message : 'Global unban failed.')
    } finally { setBanBusy(false) }
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

  function toggleNodeConnectionFromInput() {
    const node = nodeValue.trim()
    if (!canPopulateNodeControl(node) || node === config.node || config.bridges.some((bridge) => bridge.node === node)) {
      appendNodeMessage('Enter a valid remote node number.')
      return
    }
    const currentRows = connectionRowsRef.current
    if (!currentRows.some((row) => row.node === config.node && row.state !== 'message')) {
      appendNodeMessage('Connection status is still loading. Try again in a moment.')
      return
    }
    const connected = currentRows.some((row) => row.node === node && row.state !== 'message' && !row.bridgeId)
    void runCommandForNode(connected ? 'disconnect' : 'connect', node)
  }

  async function runCommandForNode(
    action: string,
    node: string,
    disconnectBeforeConnect = autodisc,
    permanentLink = permanent,
  ) {
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
        permanent: permanentLink,
        autodisc: disconnectBeforeConnect,
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

  function beginCustomCommandManagement() {
    setCustomCommandsDraft(customCommands.map(({ label, command }) => ({ label, command })))
    setCustomCommandsStatus('')
    setCustomCommandsManaging(true)
  }

  function moveCustomCommand(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= customCommandsDraft.length) return
    setCustomCommandsDraft((current) => {
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  async function persistCustomCommands() {
    const commands = customCommandsDraft
      .map(({ label, command }) => ({ label: label.trim(), command: command.trim() }))
      .filter(({ label, command }) => label || command)
    const invalid = commands.find(({ label, command }) => (
      !label || label.length > 40 || label.includes(',') || !/^\*[0-9A-Da-d#;]{1,40}$/.test(command)
    ))
    if (invalid) {
      setCustomCommandsStatus('Each command needs a name and a valid DTMF value beginning with *.')
      return
    }
    try {
      setCustomCommandsSaving(true)
      setCustomCommandsStatus('Saving commands…')
      const next = await saveCustomCommands(commands, customCommandsEnabled)
      setCustomCommands(next.commands)
      setCustomCommandsEnabled(next.enabled)
      setCustomCommandsDraft(next.commands.map(({ label, command }) => ({ label, command })))
      setCustomCommandsStatus('Commands saved.')
      setCustomCommandsManaging(false)
    } catch (error) {
      setCustomCommandsStatus(error instanceof Error ? error.message : 'Custom commands could not be saved.')
    } finally {
      setCustomCommandsSaving(false)
    }
  }

  async function confirmBridgeControlState(
    bridgeId: string,
    linked: boolean,
    destination = '',
    timeoutMs = 7_000,
  ) {
    let last = applyBridgeConnectionOverrides(await fetchBridgeCards(config))
    const attempts = Math.max(1, Math.ceil(timeoutMs / 250))
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const card = last.cards.find((item) => item.id === bridgeId)
      const confirmed = card
        && card.controlLinked === linked
        && (!linked || !destination || card.currentDestination === destination)
      if (confirmed) {
        setBridgeState(last)
        return card
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 250))
      last = applyBridgeConnectionOverrides(await fetchBridgeCards(config))
    }
    setBridgeState(last)
    throw new Error('The bridge command completed, but canonical status did not confirm it in time.')
  }

  async function connectDmrNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || bridgeControlBusy) return
    const talkgroup = String(dmrTalkgroupInputs[card.id] || '').trim()
    if (!/^\d{1,8}$/.test(talkgroup) || Number(talkgroup) < 1 || Number(talkgroup) > 16777215) {
      appendNodeMessage('Enter a valid DMR talkgroup.')
      return
    }
    if (Number(talkgroup) === 4000) {
      appendNodeMessage('Use Disconnect instead of entering TG 4000.')
      return
    }
    try {
      setBridgeControlBusy(card.id)
      setBridgeControlAction('connect')
      await connectBridge(card.id, talkgroup)
      const canonical = await confirmBridgeControlState(card.id, true, talkgroup)
      setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: '' }))
      appendNodeMessage(`DMR Net Bridge connected to ${canonical.currentDestinationLabel || `TG ${canonical.currentDestination}`}.`)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'DMR Net Bridge connection failed.')
    } finally {
      setBridgeControlBusy('')
      setBridgeControlAction('')
    }
  }

  async function disconnectDmrNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || bridgeControlBusy) return

    try {
      setBridgeControlBusy(card.id)
      setBridgeControlAction('disconnect')
      await disconnectBridge(card.id)
      await confirmBridgeControlState(card.id, false)
      setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: '' }))
      appendNodeMessage('DMR Net Bridge disconnected.')
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'DMR Net Bridge disconnect failed.')
    } finally {
      setBridgeControlBusy('')
      setBridgeControlAction('')
    }
  }

  async function connectReflectorNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || bridgeControlBusy) return
    const destination = String(bridgeDestinationInputs[card.id] || '').trim()
    if (!destination) {
      appendNodeMessage(card.cardType === 'ysf_net'
        ? 'Enter an exact YSF reflector name or five-digit ID.'
        : `Enter an approved ${card.mode.toUpperCase()} destination.`)
      return
    }

    try {
      setBridgeControlBusy(card.id)
      setBridgeControlAction('connect')
      const result = await connectBridge(card.id, destination)
      const canonicalId = String(result.currentDestination || '').trim()
      if (!canonicalId || canonicalId.length > 80) {
        throw new Error(`${card.title} returned an invalid canonical destination.`)
      }
      if (card.cardType === 'p25_net' || card.cardType === 'nxdn_net') {
        setBridgeDestinationInputs((current) => ({ ...current, [card.id]: '' }))
        setBridgeState(applyBridgeConnectionOverrides(await fetchBridgeCards(config)))
        appendNodeMessage(
          `${card.title} selected ${canonicalId} and confirmed its AllStar transport. ASR counts that selected Net Bridge locally, but Gateway selection is not proof that the remote reflector is reachable, so reflector reachability remains unverified.`,
        )
        return
      }
      const canonical = await confirmBridgeControlState(
        card.id,
        true,
        canonicalId,
        card.cardType === 'm17_net' ? 22_000 : 7_000,
      )
      setBridgeDestinationInputs((current) => ({
        ...current,
        [card.id]: '',
      }))
      appendNodeMessage(`${card.title} connected to ${canonical.currentDestinationLabel || canonical.currentDestination}.`)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : `${card.title} connection failed.`)
    } finally {
      setBridgeControlBusy('')
      setBridgeControlAction('')
    }
  }

  async function disconnectReflectorNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || bridgeControlBusy) return

    try {
      setBridgeControlBusy(card.id)
      setBridgeControlAction('disconnect')
      await disconnectBridge(card.id)
      await confirmBridgeControlState(
        card.id,
        false,
        '',
        card.cardType === 'm17_net' ? 22_000 : 7_000,
      )
      setBridgeDestinationInputs((current) => ({ ...current, [card.id]: '' }))
      appendNodeMessage(`${card.title} disconnected.`)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : `${card.title} disconnect failed.`)
    } finally {
      setBridgeControlBusy('')
      setBridgeControlAction('')
    }
  }

  async function changeUrfBlacklist(verb: 'ban' | 'unban', rule: string, duration: UrfBanDuration = 'permanent') {
    if (!authStatus.isAdmin || urfAccessBusy) return
    const normalized = rule.trim().toUpperCase()
    if (!/^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/.test(normalized)) { setUrfAccessStatus('Enter a valid callsign. Every ASR ban is global.'); return }
    try {
      setUrfAccessBusy(true)
      const result = await updateUrfBlacklist(verb, normalized, duration)
      setUrfBlacklist(result.rules)
      setUrfBans(result.bans)
      setUrfAudit(result.audit)
      setUrfAccessRule('')
      const action = verb === 'ban' ? 'added to' : 'removed from'
      const durationLabel = verb === 'ban' ? ` for ${URF_BAN_DURATIONS.find((option) => option.value === duration)?.label || duration}` : ''
      const removal = verb === 'ban' && result.removed > 0 ? ` ${result.removed} active backend session${result.removed === 1 ? ' was' : 's were'} removed immediately.` : ''
      const aslLimit = result.asl?.status !== 'applied' || result.asl?.applied?.unmappedGlobalRules?.includes(normalized) ? ` Global enforcement pending: ${result.asl?.error || 'This callsign could not be mapped.'}` : ''
      setUrfAccessStatus(`${normalized} was ${action} the ASR Global Ban List${durationLabel}${result.verified ? ' and the saved logical/backend state was verified' : ''}.${removal}${aslLimit}`)
    } catch (error) {
      setUrfAccessStatus(error instanceof Error ? error.message : 'Global Ban update failed.')
    } finally { setUrfAccessBusy(false) }
  }

  async function kickBridgeConnectedClient(callsign: string, bridgeId: string, mode: string) {
    if (!authStatus.isAdmin || urfAccessBusy) return
    const bridge = config.bridges.find((item) => item.id === bridgeId)
    if (!bridge) { setUrfAccessStatus('Bridge configuration is unavailable.'); return }
    const protocol = mode.toUpperCase() === 'DMR' ? 'DMRMMDVM' : mode.toUpperCase()
    try {
      setUrfAccessBusy(true)
      setUrfAccessStatus(`Kicking ${callsign} from ${mode.toUpperCase()}…`)
      const result = bridge.urfReflector
        ? await kickUrfClient(callsign, protocol)
        : await kickStandaloneClient(bridgeId, callsign)
      setUrfAccessStatus(`${callsign} was kicked from ${mode.toUpperCase()}${result.verified ? ' and backend removal was verified' : ''}. The client may reconnect immediately unless banned.`)
    } catch (error) {
      setUrfAccessStatus(error instanceof Error ? error.message : 'Bridge client Kick failed.')
    } finally { setUrfAccessBusy(false) }
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

  async function loadDiagnosticsReport() {
    if (!authStatus.isAdmin) {
      setDiagnosticsStatus('Admin permission is required to generate a bug report.')
      return
    }

    try {
      setBusy(true)
      setDiagnosticsStatus('Generating diagnostics report...')
      const report = await fetchDiagnosticsReport()
      setDiagnosticsReport(report)
      setDiagnosticsStatus('Review this report before sending it.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Diagnostics report could not be generated.'
      setDiagnosticsStatus(message)
    } finally {
      setBusy(false)
    }
  }

  function openDiagnosticsReport() {
    setMenuOpen(false)
    setOpenSubmenu(null)
    setDiagnosticsOpen(true)
    void loadDiagnosticsReport()
  }

  async function copyDiagnosticsReport() {
    const report = diagnosticsReport?.report || ''
    if (!report) return

    try {
      await navigator.clipboard.writeText(report)
      setDiagnosticsStatus('Diagnostics report copied.')
      return
    } catch {
      const textarea = diagnosticsTextRef.current
      if (!textarea) {
        setDiagnosticsStatus('Copy failed. Select the report text and copy it manually.')
        return
      }

      textarea.focus()
      textarea.select()
      try {
        if (document.execCommand('copy')) {
          setDiagnosticsStatus('Diagnostics report copied.')
          return
        }
      } catch {
        // Fall through to the manual-copy message below.
      }
      setDiagnosticsStatus('Copy failed. The report text is selected; press Ctrl+C or Cmd+C.')
    }
  }

  function emailDiagnosticsReport() {
    if (!diagnosticsReport?.report) return
    const subject = encodeURIComponent(diagnosticsReport.subject || 'ASR Bug Report')
    const body = encodeURIComponent(diagnosticsReport.report)
    window.location.href = `mailto:${diagnosticsReport.email}?subject=${subject}&body=${body}`
  }

  const favoritesPanel = favoritesOpen ? (
    <section
      id="allscan-favorites-panel"
      data-dashboard-module="favorites"
      style={{ order: dashboardModuleOrder.indexOf('favorites') + 10 }}
      className={`allscan-main-section allscan-favorites-panel allscan-dashboard-module${dashboardModuleDragging === 'favorites' ? ' is-dragging' : ''}${dashboardModuleOver === 'favorites' && dashboardModuleDragging !== 'favorites' ? ' is-drag-over' : ''}`}
      aria-label="Favorites"
    >
      <h2 className="allscan-section-title">
        <span className="allscan-module-title-wrap">{dashboardModuleHandle('favorites', 'Favorites')}<span className="allscan-module-title-text">Favorites</span></span>
      </h2>
      <div className="allscan-favorites-inner">
        <div className="allscan-favorites-legend">
          <span><i className="allscan-fav-dot allscan-fav-dot-networked" />Already Networked</span>
          <span><i className="allscan-fav-dot allscan-fav-dot-tx" />Recent TX</span>
          <span><i className="allscan-fav-rxbar" />Rx Busy</span>
          <span><i className="allscan-fav-underline" />Scanning</span>
        </div>
        <div className="allscan-favorites-options">
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
          <div className="allscan-favorites-tools">
            <button type="button" disabled={busy || !authStatus.canModify} onClick={() => {
              if (window.confirm('Reset the saved custom order for this Favorites list? Descriptions and colors will stay unchanged.')) void favoriteOperation('reset-order')
            }}><RotateCcw /> Reset order</button>
            <button type="button" disabled={busy || !authStatus.canModify} onClick={() => {
              if (window.confirm('Reset all Favorite colors for this list? Descriptions and order will stay unchanged.')) void favoriteOperation('reset-appearance')
            }}><RotateCcw /> Reset colors</button>
          </div>
        </div>
        {favoriteStatus ? <p className="allscan-favorites-status" role="status">{favoriteStatus}</p> : null}

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
              return (
              <tr
                key={favorite.node}
                draggable={false}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  const sourceNode = event.dataTransfer.getData('text/plain') || favoriteDragNode
                  if (sourceNode) void reorderFavorite(sourceNode, favorite.node)
                  setFavoriteDragNode(null)
                }}
                style={favorite.color ? { borderInlineStartColor: favorite.color } : undefined}
              >
                <td className={[favCellClass, scanning ? 'allscan-fav-scanning' : ''].filter(Boolean).join(' ') || undefined}>
                  <button type="button" className="allscan-favorite-drag" draggable={authStatus.canModify} aria-label={`Move Favorite ${favorite.node}`} title="Drag to reorder"
                    onDragStart={(event) => {
                      event.stopPropagation()
                      setFavoriteDragNode(favorite.node)
                      event.dataTransfer.effectAllowed = 'move'
                      event.dataTransfer.setData('text/plain', favorite.node)
                      const row = event.currentTarget.closest('tr')
                      if (row instanceof HTMLElement) {
                        const ghost = row.cloneNode(true) as HTMLElement
                        const rect = row.getBoundingClientRect()
                        ghost.classList.add('allscan-favorite-drag-ghost')
                        ghost.style.width = `${rect.width}px`
                        document.body.appendChild(ghost)
                        event.dataTransfer.setDragImage(ghost, Math.min(28, rect.width / 2), Math.min(20, rect.height / 2))
                        requestAnimationFrame(() => ghost.remove())
                      }
                    }}
                    onDragEnd={() => setFavoriteDragNode(null)}><GripVertical /></button>
                </td>
                <td
                  className={nodeCellClass}
                  onClick={() => {
                    if (!canPopulateNodeControl(favorite.node)) return
                    setNodeValue(favorite.node)
                    nodeInputRef.current?.focus()
                  }}
                  onDoubleClick={() => {
                    if (favoritesOpen && canPopulateNodeControl(favorite.node)) void runCommandForNode('connect', favorite.node)
                  }}
                  title={favoritesOpen ? 'Double-click to connect' : 'Select node'}
                >
                  <span className="allscan-favorite-node-number">{favorite.node}</span>
                  <span className={rxBusy > 2 ? 'allscan-favorite-rx allscan-fav-cell-rx' : 'allscan-favorite-rx'}>{rxText !== '' ? `Rx: ${rxText}%` : 'Rx: —'}</span>
                  <span className="allscan-favorite-linked-inline">{linkText !== '' ? `Linked: ${linkText}` : 'Linked: —'}</span>
                </td>
                <td aria-hidden="true">{favorite.name}</td>
                <td>
                  {favoriteEditing === favorite.node ? (
                    <form className="allscan-favorite-description-editor" onSubmit={(event) => {
                      event.preventDefault()
                      void favoriteOperation('update-description', { node: favorite.node, value: favoriteDescription }).then(() => setFavoriteEditing(null))
                    }}>
                      <input autoFocus value={favoriteDescription} onChange={(event) => setFavoriteDescription(event.target.value)} maxLength={200} aria-label={`Friendly description for ${favorite.node}`} />
                      <button type="submit" disabled={busy}>Save</button>
                      <button type="button" onClick={() => setFavoriteEditing(null)}>Cancel</button>
                    </form>
                  ) : (
                    <span className="allscan-favorite-details">
                      <span className="allscan-favorite-description" title={favorite.description || favorite.name || 'No description'}>
                        {favorite.href ? (
                          <a href={favorite.href} target="_blank" rel="noreferrer">{favorite.description || favorite.name || <em>No description</em>}</a>
                        ) : (favorite.description || favorite.name || <em>No description</em>)}
                        {favorite.customDescription ? <small>User description</small> : null}
                      </span>
                      <span className="allscan-favorite-meta">
                        <span className="allscan-favorite-frequency" title="Frequency or node details">
                          {(() => {
                            const detail = (favorite.frequency || favorite.referenceDesc || '').trim()
                            return detail === '-' || detail === '–' || detail === '—' ? '' : detail
                          })()}
                        </span>
                        <span className="allscan-favorite-location" title={favorite.location}>{favorite.location}</span>
                      </span>
                    </span>
                  )}
                </td>
                <td aria-hidden="true" />
                <td aria-hidden="true" />
                <td className="allscan-favorite-actions-cell">
                  {authStatus.canModify ? <span className="allscan-favorite-actions">
                    <button type="button" title="Edit friendly description" onClick={() => {
                      setFavoriteEditing(favorite.node)
                      setFavoriteDescription(favorite.customDescription || favorite.description || favorite.name)
                    }}><Pencil /></button>
                    <span className="allscan-favorite-color-control">
                      <button type="button" className="allscan-favorite-color-trigger" title="Favorite accent color" aria-label={'Choose color for node ' + favorite.node} onClick={(event) => {
                        const rect = event.currentTarget.getBoundingClientRect()
                        const popupWidth = 224
                        const popupHeight = 300
                        setFavoriteColorPosition({
                          left: Math.max(8, Math.min(window.innerWidth - popupWidth - 8, rect.left - popupWidth - 7)),
                          top: Math.max(8, Math.min(window.innerHeight - popupHeight - 8, rect.top)),
                        })
                        setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: current[favorite.node] || favorite.color || '#4aa3df' }))
                        setFavoriteColorOpen((current) => current === favorite.node ? null : favorite.node)
                      }}><Palette aria-hidden="true" /></button>
                      {favoriteColorOpen === favorite.node && favoriteColorPosition ? <span className="allscan-favorite-color-popover" style={{ top: favoriteColorPosition.top, left: favoriteColorPosition.left }} role="dialog" aria-label={'Favorite color for node ' + favorite.node}>
                        <span className="allscan-favorite-color-preview" style={{ backgroundColor: favoriteColorDrafts[favorite.node] || favorite.color || '#4aa3df' }} />
                        <label className="allscan-favorite-color-hex"><span>Color</span>
                          <input type="text" maxLength={7} aria-label="Hex color" value={favoriteColorDrafts[favorite.node] || favorite.color || '#4aa3df'} onChange={(event) => {
                            let value = event.target.value.trim()
                            if (!value.startsWith('#')) value = '#' + value
                            if (/^#[0-9a-fA-F]{0,6}$/.test(value)) setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: value }))
                          }} />
                        </label>
                        <span className="allscan-favorite-color-field" aria-label="Color palette"
                          onPointerDown={(event) => {
                            event.preventDefault()
                            event.stopPropagation()
                            event.currentTarget.setPointerCapture(event.pointerId)
                            const pick = (clientX: number, clientY: number) => {
                              const rect = event.currentTarget.getBoundingClientRect()
                              const saturation = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
                              const lightness = Math.max(0, Math.min(1, 1 - ((clientY - rect.top) / rect.height)))
                              const hue = favoriteColorHue
                              const a = saturation * Math.min(lightness, 1 - lightness)
                              const f = (n: number) => {
                                const k = (n + hue / 30) % 12
                                return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))
                              }
                              const hex = '#' + [f(0), f(8), f(4)].map((v) => Math.round(255 * v).toString(16).padStart(2, '0')).join('')
                              setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: hex }))
                            }
                            pick(event.clientX, event.clientY)
                          }}
                          onPointerMove={(event) => {
                            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
                            event.preventDefault()
                            event.stopPropagation()
                            const rect = event.currentTarget.getBoundingClientRect()
                            const saturation = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
                            const lightness = Math.max(0, Math.min(1, 1 - ((event.clientY - rect.top) / rect.height)))
                            const hue = favoriteColorHue
                            const a = saturation * Math.min(lightness, 1 - lightness)
                            const f = (n: number) => { const k = (n + hue / 30) % 12; return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)) }
                            const hex = '#' + [f(0), f(8), f(4)].map((v) => Math.round(255 * v).toString(16).padStart(2, '0')).join('')
                            setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: hex }))
                          }}
                          onPointerUp={(event) => { event.preventDefault(); event.stopPropagation(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
                          onPointerCancel={(event) => { event.stopPropagation(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
                        style={{ backgroundColor: `hsl(${favoriteColorHue} 100% 50%)` }}><span className="allscan-favorite-color-field-white" /><span className="allscan-favorite-color-field-black" /></span>
                        <input className="allscan-favorite-hue" aria-label="Hue" type="range" min="0" max="360" value={(360 - favoriteColorHue) % 360} onChange={(event) => {
                          const slider = Number(event.target.value)
                          const hue = (360 - slider) % 360
                          setFavoriteColorHue(hue)
                          const lightness = 0.5
                          const saturation = 1
                          const a = saturation * Math.min(lightness, 1 - lightness)
                          const f = (n: number) => { const k = (n + hue / 30) % 12; return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)) }
                          const hex = '#' + [f(0), f(8), f(4)].map((v) => Math.round(255 * v).toString(16).padStart(2, '0')).join('')
                          setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: hex }))
                        }} onPointerDown={(event) => event.stopPropagation()} />
                        <span className="allscan-favorite-color-palette">
                          {['#ef4444','#f97316','#eab308','#22c55e','#14b8a6','#4aa3df','#3b82f6','#6366f1','#a855f7','#ec4899','#f8fafc','#94a3b8','#475569','#111827'].map((color) => <button type="button" key={color} title={color} aria-label={'Select ' + color} style={{ backgroundColor: color }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: color })) }} />)}
                        </span>
                        <span className="allscan-favorite-color-popover-actions">
                          <button type="button" onClick={() => {
                            const value = favoriteColorDrafts[favorite.node]
                            if (!/^#[0-9a-fA-F]{6}$/.test(value || '')) return
                            void favoriteOperation('set-color', { node: favorite.node, value }).then((saved) => {
                              if (!saved) return
                              setFavoriteColorOpen(null)
                              setFavoriteColorDrafts((current) => { const next = { ...current }; delete next[favorite.node]; return next })
                            })
                          }}>Apply</button>
                          <button type="button" onClick={() => {
                            setFavoriteColorOpen(null)
                            setFavoriteColorDrafts((current) => { const next = { ...current }; delete next[favorite.node]; return next })
                          }}>Cancel</button>
                        </span>
                      </span> : null}
                    </span>
                    <button type="button" title="Reset this Favorite's description and color" disabled={!favorite.customDescription && !favorite.color} onClick={() => {
                      if (window.confirm(`Reset the custom description and color for node ${favorite.node}? Its position and Favorite membership will stay unchanged.`)) void favoriteOperation('reset-favorite', { node: favorite.node })
                    }}><RotateCcw /></button>
                    <button type="button" title="Remove Favorite" onClick={() => {
                      if (window.confirm(`Remove node ${favorite.node} from Favorites?`)) void runCommandForNode('delfav', favorite.node).then(() => reloadFavorites())
                    }}><Trash2 /></button>
                  </span> : null}
                </td>
              </tr>
              )
            })}
          </tbody>
        </table>
        </div>
      </div>
    </section>
  ) : null

  return (
    <div className="allscan-app min-h-screen bg-[#151515] text-[#eaf4f8]">
      <div className="w-full px-0 pb-6">
        <header className="allscan-header">
          <div className="allscan-brand">
            <a className="allscan-brand-main" href={asrPath()} aria-label="Return to main AllScan page">
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
                aria-label="Lookup"
                onClick={(event) => {
                  event.stopPropagation()
                  setMenuOpen(false)
                  setOpenSubmenu(null)
                  window.location.href = asrPath('lookup/')
                }}
              >
                Lookup
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
            <a
              className="allscan-lookup-main-button"
              href={asrPath('lookup/')}
              title="Lookup callsigns, nodes, EchoLink, and map"
              onClick={() => {
                setMenuOpen(false)
                setOpenSubmenu(null)
              }}
            >
              <Search className="h-4 w-4" />
              <span>Lookup</span>
            </a>
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
                  <a
                    role="menuitem"
                    className="allscan-menu-proxy-row allscan-menu-direct-row"
                    href={asrPath('lookup/')}
                    onClick={() => {
                      setMenuOpen(false)
                      setOpenSubmenu(null)
                    }}
                  >
                    <span>Lookup</span>
                  </a>
                  {authStatus.isAdmin ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="allscan-menu-proxy-row allscan-menu-report-row"
                      onClick={openDiagnosticsReport}
                    >
                      <span>Report a Bug</span>
                    </button>
                  ) : null}
                  {authStatus.loggedIn ? (
                    <button
                      type="button"
                      role="menuitem"
                      className="allscan-menu-proxy-row allscan-menu-logout-row"
                      onClick={() => void logoutAllScan()}
                    >
                      <span>Logout</span>
                    </button>
                  ) : null}
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
                  {authStatus.loggedIn ? <a role="menuitem" href={asrPath('user/settings/')} onClick={() => setMenuOpen(false)}>Settings</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('asr-settings/')} onClick={() => setMenuOpen(false)}>Reimagined Settings</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('asr-instructions/')} onClick={() => setMenuOpen(false)}>Help &amp; Instructions</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('performance/')} onClick={() => setMenuOpen(false)}>Performance Stats</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('user/')} onClick={() => setMenuOpen(false)}>Users</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('cfg/')} onClick={() => setMenuOpen(false)}>Configs</a> : null}
                  <a role="menuitem" href={`http://stats.allstarlink.org/stats/${config.node}`} onClick={() => setMenuOpen(false)}>Node Status</a>
                  {authStatus.canWrite ? <button type="button" role="menuitem" onClick={restartAsterisk}>Restart Asterisk</button> : null}
                  {authStatus.loggedIn
                    && effectiveThemeSettings.theme === 'lcars-frame'
                    && desktopThemeViewport ? (
                      <button type="button" role="menuitem" onClick={() => void logoutAllScan()}>Logout</button>
                    ) : null}
                  {!authStatus.loggedIn ? (
                    <a role="menuitem" href={asrPath('user/')} onClick={() => setMenuOpen(false)}>Login</a>
                  ) : null}
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

        <main className="allscan-dashboard mx-auto max-w-[1280px] px-1 pt-[2px] sm:px-3">
          {releaseStatus?.updateAvailable ? (
            <aside
              className="allscan-update-notice"
              role="status"
              aria-live="polite"
              aria-label="AllScan Reimagined update available"
            >
              <div className="allscan-update-notice-copy">
                <strong>ASR update available: {releaseStatus.availableLabel}</strong>
                <span>
                  This node has {releaseStatus.installedLabel}. Nothing will install automatically.
                </span>
                {releaseStatus.package.name ? (
                  <span className="allscan-update-package">
                    Package: {releaseStatus.package.name}
                    {releaseStatus.package.sha256 ? (
                      <> · SHA-256: <code>{releaseStatus.package.sha256}</code></>
                    ) : null}
                  </span>
                ) : null}
              </div>
              <div className="allscan-update-actions">
                {releaseStatus.releaseUrl ? (
                  <a href={releaseStatus.releaseUrl} target="_blank" rel="noreferrer">Update instructions (recommended)</a>
                ) : null}
                {releaseStatus.package.url ? (
                  <a href={releaseStatus.package.url}>Download archive (advanced)</a>
                ) : null}
              </div>
            </aside>
          ) : null}
          {talkersOpen ? <section
            data-dashboard-module="talkers"
            style={{ order: dashboardModuleOrder.indexOf('talkers') + 10 }}
            className={`allscan-main-section allscan-talkers-section allscan-dashboard-module${dashboardModuleDragging === 'talkers' ? ' is-dragging' : ''}${dashboardModuleOver === 'talkers' && dashboardModuleDragging !== 'talkers' ? ' is-drag-over' : ''}`}
          >
            <h2 className="allscan-section-title">
              <span className="allscan-module-title-wrap">{dashboardModuleHandle('talkers', 'Talkers')}<span className="allscan-module-title-text">Talkers</span></span>
            </h2>
            <div className="allscan-talker-cards" aria-label="Current and recent talkers">
              {talkerCards.map((talker, index) => (
                <article
                  className={`allscan-talker-card${index === 0 && talker ? ' is-current' : ''}${!talker ? ' is-empty' : ''}`}
                  key={`${index}-${talker?.node || 'empty'}`}
                >
                  <div className="allscan-talker-card-title">{index === 0 ? 'Current Talker' : `Last Talker ${index}`}</div>
                  <div className="allscan-talker-callsign">{talker ? (talker.info || talker.node) : '\u00a0'}</div>
                  <div className="allscan-talker-source">{talker ? `${talker.source}${talker.node ? ` · ${talker.node}` : ''}` : (index === 0 ? 'No one talking' : 'No recent talker')}</div>
                  <div className="allscan-talker-description">{talker?.description || '\u00a0'}</div>
                  <div className="allscan-talker-location">{talker?.location || '\u00a0'}</div>
                  <div className="allscan-talker-duration"><span>Duration</span><strong>{talkerDuration(talker, index)}</strong></div>
                </article>
              ))}
            </div>
          </section> : null}

          <section
            data-dashboard-module="controls"
            style={{ order: dashboardModuleOrder.indexOf('controls') + 10 }}
            className={`allscan-main-section allscan-controls-section allscan-dashboard-module${dashboardModuleDragging === 'controls' ? ' is-dragging' : ''}${dashboardModuleOver === 'controls' && dashboardModuleDragging !== 'controls' ? ' is-drag-over' : ''}`}
          >
            <h2 className="allscan-section-title">
              <span className="allscan-module-title-wrap">{dashboardModuleHandle('controls', 'Node Controls')}<span className="allscan-module-title-text">Node Controls</span></span>
            </h2>

            <div className={`allscan-controls-shell mx-auto rounded-[8px]${messagesOpen ? ' allscan-controls-shell-messages-open' : ''}`}>
              <div className="allscan-controls-row flex flex-wrap items-center justify-center">
                <div className="allscan-node-field">
                  <label className="allscan-control-label" htmlFor="allscan-node-box">Node#</label>
                  <input
                    id="allscan-node-box"
                    ref={nodeInputRef}
                    value={nodeValue}
                    onChange={(event) => setNodeValue(event.target.value.replace(/[^\dA-D#*]/gi, '').slice(0, 7))}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' || event.repeat || event.nativeEvent.isComposing) return
                      event.preventDefault()
                      if (busy || !authStatus.canModify) return
                      toggleNodeConnectionFromInput()
                    }}
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

              </div>

              <div className="allscan-controls-lower-row">
                <div className="allscan-controls-action-group">
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
                {authStatus.isAdmin ? <button type="button" className="allscan-action-button allscan-kicks-bans-button" onClick={() => { setManagementTab('clients'); setUrfAccessOpen(true); void loadAslBans() }}>Manage Kicks & Bans</button> : null}
              </div>

              <div className="allscan-checks-row allscan-checks-grid">
                <label className="inline-flex items-center gap-[5px]">
                  <input
                    type="checkbox"
                    className="allscan-checkbox"
                    checked={autodisc}
                    onChange={(event) => {
                      const checked = event.target.checked
                      setAutodisc(checked)
                      writeAutodiscPreference(checked)
                    }}
                    disabled={!authStatus.canModify}
                  />
                  Disc. before Connect
                </label>
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
                  <input type="checkbox" className="allscan-checkbox" checked={talkersOpen} onChange={(event) => setTalkersOpen(event.target.checked)} aria-label="Show Talker Cards" />
                  Talkers
                </label>
                <label className="inline-flex items-center gap-[5px]">
                  <input type="checkbox" className="allscan-checkbox" checked={favoritesOpen} onChange={(event) => setFavoritesOpen(event.target.checked)} />
                  Favs
                </label>
                {(authStatus.isAdmin || (customCommandsEnabled && customCommands.length > 0)) ? (
                  <div ref={customCommandsRef} className="allscan-custom-commands">
                    <button
                      type="button"
                      className="allscan-custom-commands-button"
                      aria-expanded={customCommandsOpen}
                      disabled={busy}
                      onClick={() => {
                        setCustomCommandsOpen((open) => !open)
                        setCustomCommandsManaging(false)
                        setCustomCommandsStatus('')
                      }}
                    >
                      Commands <ChevronDown />
                    </button>
                    {customCommandsOpen ? (
                      <div className={`allscan-custom-commands-menu${customCommandsManaging ? ' is-managing' : ''}`}>
                        {customCommandsManaging ? (
                          <div className="allscan-custom-commands-editor">
                            <strong>Manage Custom Cmd Buttons</strong>
                            <small>Commands must begin with *.</small>
                            <div className="allscan-custom-commands-editor-rows">
                              {customCommandsDraft.map((item, index) => (
                                <div className="allscan-custom-command-edit-row" key={`custom-command-${index}`}>
                                  <input
                                    value={item.label}
                                    maxLength={40}
                                    aria-label={`Command ${index + 1} name`}
                                    placeholder="Button name"
                                    onChange={(event) => setCustomCommandsDraft((current) => current.map((entry, entryIndex) => (
                                      entryIndex === index ? { ...entry, label: event.target.value } : entry
                                    )))}
                                  />
                                  <input
                                    value={item.command}
                                    maxLength={41}
                                    aria-label={`Command ${index + 1} DTMF value`}
                                    placeholder="*712"
                                    onChange={(event) => setCustomCommandsDraft((current) => current.map((entry, entryIndex) => (
                                      entryIndex === index ? { ...entry, command: event.target.value } : entry
                                    )))}
                                  />
                                  <span className="allscan-custom-command-edit-actions">
                                    <button type="button" title="Move up" disabled={index === 0} onClick={() => moveCustomCommand(index, -1)}>↑</button>
                                    <button type="button" title="Move down" disabled={index === customCommandsDraft.length - 1} onClick={() => moveCustomCommand(index, 1)}>↓</button>
                                    <button type="button" title="Delete command" onClick={() => {
                                      const name = item.label || item.command || 'this command'
                                      if (window.confirm(`Delete ${name}? The change takes effect when you save.`)) {
                                        setCustomCommandsDraft((current) => current.filter((_, entryIndex) => entryIndex !== index))
                                      }
                                    }}><Trash2 /></button>
                                  </span>
                                </div>
                              ))}
                            </div>
                            <button
                              type="button"
                              className="allscan-custom-command-add"
                              disabled={customCommandsDraft.length >= 24}
                              onClick={() => setCustomCommandsDraft((current) => [...current, { label: '', command: '' }])}
                            >
                              + Add command
                            </button>
                            {customCommandsStatus ? <small role="status">{customCommandsStatus}</small> : null}
                            <div className="allscan-custom-command-save-actions">
                              <button type="button" disabled={customCommandsSaving} onClick={() => void persistCustomCommands()}>Save</button>
                              <button type="button" disabled={customCommandsSaving} onClick={() => setCustomCommandsManaging(false)}>Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="allscan-custom-command-list">
                              {customCommands.length ? customCommands.map((item, index) => (
                                <button
                                  type="button"
                                  key={item.id || `${item.command}-${index}`}
                                  disabled={busy || !authStatus.canWrite}
                                  onClick={() => {
                                    setCustomCommandsOpen(false)
                                    void runCommandForNode('dtmf', item.command, false, false)
                                  }}
                                >
                                  <span>{item.label}</span>
                                  <small>{item.command}</small>
                                </button>
                              )) : <span className="allscan-custom-command-empty">No commands configured.</span>}
                            </div>
                            {customCommandsStatus ? <small role="status">{customCommandsStatus}</small> : null}
                            {authStatus.isAdmin ? (
                              <button type="button" className="allscan-custom-command-manage" onClick={beginCustomCommandManagement}>
                                Manage Commands…
                              </button>
                            ) : null}
                          </>
                        )}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>

              <details
                className="allscan-node-messages mx-auto mt-[8px] rounded-[6px]"
                open={messagesOpen}
                onToggle={(event) => setMessagesOpen((event.currentTarget as HTMLDetailsElement).open)}
              >
                <summary className="allscan-node-messages-summary">
                  <span className="allscan-node-messages-label">Node Messages</span>
                  <span className="allscan-node-messages-text min-w-0 flex-1">
                    {nodeMessageLatest}
                  </span>
                </summary>
                <div ref={nodeMessagesBodyRef} className="allscan-node-messages-body">
                  {nodeMessageRaw || nodeMessage || nodeMessageLatest}
                </div>
              </details>
            </div>

          </section>

          {favoritesPanel}

          <section
            data-dashboard-module="connections"
            style={{ order: dashboardModuleOrder.indexOf('connections') + 10 }}
            className={`allscan-main-section allscan-connection-section allscan-dashboard-module${dashboardModuleDragging === 'connections' ? ' is-dragging' : ''}${dashboardModuleOver === 'connections' && dashboardModuleDragging !== 'connections' ? ' is-drag-over' : ''}`}
          >
            <h2 className="allscan-section-title" data-lcars-title={`CONNECTION STATUS - NODE ${config.node}`}>
              <span className="allscan-module-title-wrap">{dashboardModuleHandle('connections', 'Connection Status')}<span className="allscan-module-title-text">Connection Status</span></span>
            </h2>
            {banStatus && !banDialog ? <p role="status" className="allscan-connection-ban-status">{banStatus}</p> : null}

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
                      const configuredBridgeState = row.bridgeId
                        ? bridgeConnectionStates.byId.get(row.bridgeId)
                        : bridgeConnectionStates.byNode.get(row.node)
                      const displayState = isLocalRow
                        ? row.state
                        : row.state === 'talking'
                          ? row.state
                          : configuredBridgeState || row.state
                      const canUseRowNode = canPopulateNodeControl(row.node)
                      const localStyle = isLocalRow
                        ? localRowStyles[row.state as keyof typeof localRowStyles]
                        : undefined

                      return (
                        <tr
                          key={`${row.bridgeId || row.node}-${row.direction}-${row.mode}-${row.sourceIndex ?? 'local'}`}
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
                                onClick={() => {
                                  if (canUseRowNode) {
                                    setNodeValue(row.node)
                                    nodeInputRef.current?.focus()
                                  }
                                }}
                              >
                                <div className="allscan-connection-node-cell">
                                  <span>{row.node}</span>
                                  {authStatus.isAdmin && !row.bridgeId && !config.bridges.some((bridge) => bridge.node === row.node)
                                    && isBannableConnection(row) && !isProtectedBanConnection(row) ? (
                                    <button type="button" className="allscan-connection-more" aria-label={`Manage connection ${row.node}`}
                                      title={`Manage connection ${row.node}`}
                                      aria-expanded={rowActions?.row === row}
                                      onClick={(event) => {
                                        event.stopPropagation()
                                        const box = event.currentTarget.getBoundingClientRect()
                                        setRowActions((current) => current?.row === row ? null : {
                                          row, left: Math.max(8, Math.min(window.innerWidth - 172, box.right - 165)),
                                          top: Math.max(8, Math.min(window.innerHeight - 150, box.bottom + 3)),
                                        })
                                      }}>⋮</button>
                                  ) : null}
                                </div>
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
                              <td className="px-4 py-[5px]">
                                <span className="allscan-connection-mode">{row.mode}</span>
                              </td>
                            </>
                          )}
                        </tr>
                      )
                    })}
                    {connectionTotal.total > 0 ? (
                      <tr className="allscan-status-count-row border-b border-[rgba(255,255,255,.12)] text-[14px] leading-[19px] text-[#edf4f8]">
                        <td colSpan={6} className="px-4 py-[5px]">
                          {connectionTotal.total} total linked ({connectionTotal.parts.join(', ')})
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          {rowActions ? (
            <>
              <button type="button" className="allscan-connection-menu-backdrop" aria-label="Close connection actions" onClick={() => setRowActions(null)} />
              <div role="menu" className="allscan-connection-menu" style={{ left: rowActions.left, top: rowActions.top }}>
                {/^[0-9]{3,10}$/.test(rowActions.row.node) ? (
                  <button type="button" role="menuitem" onClick={() => {
                    const node = rowActions.row.node
                    setRowActions(null)
                    void runCommandForNode('disconnect', node)
                  }}>Disconnect</button>
                ) : (
                  <button type="button" role="menuitem" onClick={() => {
                    setRowActions(null)
                    setDropClientOpen(true)
                    void loadDropClients()
                  }}>Drop Client…</button>
                )}
                {connectionCallsign(rowActions.row) && !isProtectedBanConnection(rowActions.row) ? <button type="button" role="menuitem" onClick={() => openParticipantBan(rowActions.row)}>Ban…</button> : null}
              </div>
            </>
          ) : null}

          {bridgeState.cards.length || urfAccessOpen ? <section
            data-dashboard-module="bridges"
            style={{ order: dashboardModuleOrder.indexOf('bridges') + 10 }}
            className={`allscan-main-section allscan-bridge-section allscan-dashboard-module${dashboardModuleDragging === 'bridges' ? ' is-dragging' : ''}${dashboardModuleOver === 'bridges' && dashboardModuleDragging !== 'bridges' ? ' is-drag-over' : ''}`}
          >
            <h2
              className="allscan-section-title"
              data-lcars-title={`DIGITAL BRIDGE STATUS - ${bridgeState.cards.map((card) => card.id.toUpperCase()).join(' / ')}`}
            >
              <span className="allscan-module-title-wrap">{dashboardModuleHandle('bridges', 'Digital Bridge Status')}<span className="allscan-module-title-text">Digital Bridge Status</span></span>
            </h2>
            <p className="allscan-section-subtitle">
              LAST ACTIVITY: {bridgeState.updatedLabel}
            </p>

            {(() => {
              const urfCards = bridgeState.cards.filter((card) => config.bridges.find((bridge) => bridge.id === card.id)?.urfReflector)
              const managedCards = bridgeState.cards.filter((card) => config.bridges.find((bridge) => bridge.id === card.id)?.adminCapabilities?.clientAdmin.includes('listBans'))
              const managedConnectedCards = managedCards.filter((card) => config.bridges.find((bridge) => bridge.id === card.id)?.adminCapabilities?.clientAdmin.includes('listClients'))
              const zelloTalkers = managedCards
                .filter((card) => card.mode.toUpperCase() === 'ZELLO')
                .flatMap((card) => card.detailRows
                  .filter((detail) => !detail.empty)
                  .map((detail) => ({ card, identity: detail.label.trim().toUpperCase(), active: card.lastCaller.trim().toUpperCase() === detail.label.trim().toUpperCase() })))
              if (!urfCards.length && !urfAccessOpen) return null
              return <div className="allscan-urf-group">
                {urfCards.length ? <div className="allscan-urf-group-title"><span>URFWIL Bridge</span></div> : null}
                <div className="allscan-urf-mini-grid">
                  {urfCards.map((card) => {
                    const clientsOpen = bridgeClientsOpen.has(card.id)
                    const historyOpen = bridgeHistoryOpen.has(card.id)
                    return <article key={card.id} className={`allscan-bridge-card allscan-urf-mini-card ${bridgeRoleClasses[card.status]}`}>
                      <div className="allscan-bridge-head">
                        <span className="allscan-bridge-head-title">{card.title.replace(/\s+Bridge$/i, '')}</span>
                        <span className="allscan-urf-mini-head-actions">
                          <button
                            type="button"
                            className={`allscan-bridge-status-control${card.healthSeverity ? ' has-health-issue' : ''}`}
                            aria-label={card.healthSeverity ? `${bridgeHealthLabels[card.healthSeverity]}: ${card.healthIssues.join(' ')}` : `Status: ${bridgeStatusLabels[card.status]}`}
                            title={card.healthSeverity ? card.healthIssues.join('\n') : bridgeStatusLabels[card.status]}
                            onClick={() => { if (card.healthSeverity && card.healthIssues.length) window.alert(card.healthIssues.join('\n')) }}
                          >
                            {card.healthSeverity ? <AlertTriangle className={`allscan-bridge-health-triangle ${bridgeHealthTriangleClasses[card.healthSeverity]}`} aria-hidden="true" /> : null}
                            <span className={`allscan-bridge-status rounded-full border ${card.healthSeverity ? pillClasses.neutral : bridgeStatusClasses[card.status]}`}>{card.healthSeverity ? bridgeHealthLabels[card.healthSeverity] : bridgeStatusLabels[card.status]}</span>
                          </button>
                        </span>
                      </div>
                      <div className="allscan-bridge-body">
                        <div className="allscan-bridge-row allscan-bridge-status-row"><span>Talking</span><b title={card.lastCaller === '-' ? undefined : card.lastCaller}>{card.lastCaller === '-' ? '–' : card.lastCaller}</b></div>
                        <div className="allscan-bridge-row allscan-bridge-status-row"><span>Last Talker</span><b title={card.lastTxEpoch > 0 ? bridgeLastTalker(card) : undefined}>{card.lastTxEpoch > 0 ? bridgeLastTalker(card) : '–'}</b></div>
                      </div>
                      <div className="allscan-bridge-detail-wrap">
                        <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={clientsOpen} onClick={() => setBridgeClientsOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.clientsOpen', JSON.stringify([...next])); return next })}>
                          <span>Connected Clients</span><b>{card.detailAvailable ? card.detailCount : '–'}</b><ChevronDown className={clientsOpen ? 'is-open' : ''} aria-hidden="true" />
                        </button>
                        {clientsOpen ? <div className="allscan-bridge-detail-box">{card.detailRows.map((detail) => {
                          const callsign = detail.label.replace(/\s+[A-Z]$/i, '').trim().toUpperCase()
                          const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id)
                          const canKick = card.mode.toUpperCase() !== 'ZELLO' && bridgeConfig?.adminCapabilities?.clientAdmin.includes('kickClient') === true
                          const canBan = bridgeConfig?.adminCapabilities?.clientAdmin.includes('banClient') === true
                          const banned = urfBlacklist.some((rule) => rule.endsWith('*') ? callsign.startsWith(rule.slice(0, -1)) : callsign === rule)
                          return <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}><div className="allscan-bridge-client-user"><span>{detail.label}</span></div>{detail.meta && <div className="allscan-bridge-client-meta">{detail.meta}</div>}{!detail.empty && authStatus.isAdmin && (canKick || canBan) ? <div className="allscan-bridge-inline-client-actions">{canKick ? <button type="button" disabled={urfAccessBusy} onClick={() => void kickBridgeConnectedClient(callsign, card.id, card.mode)}>Kick</button> : null}{canBan && !banned ? globalBanDurationSelect(callsign) : null}</div> : null}</div>
                        })}</div> : null}
                      </div>
                      <div className="allscan-bridge-detail-wrap allscan-bridge-recent">
                        <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={historyOpen} onClick={() => setBridgeHistoryOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.historyOpen', JSON.stringify([...next])); return next })}>
                          <span>Recent Activity</span><b>{card.recentRows.filter((detail) => !detail.empty).length}</b><ChevronDown className={historyOpen ? 'is-open' : ''} aria-hidden="true" />
                        </button>
                        {historyOpen ? <div className="allscan-bridge-detail-box">{card.recentRows.map((detail) => (
                          <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}><div className="allscan-bridge-client-user"><span>{detail.label}</span></div>{detail.meta && <div className="allscan-bridge-client-meta">{detail.meta}</div>}</div>
                        ))}</div> : null}
                      </div>
                    </article>
                  })}
                </div>
                {urfAccessOpen && authStatus.isAdmin ? <div className="allscan-urf-access-modal" role="dialog" aria-modal="true" aria-label="Manage Kicks & Bans" onMouseDown={(event) => { if (event.target === event.currentTarget) setUrfAccessOpen(false) }}><div className="allscan-urf-access-panel"><div className="allscan-urf-access-modal-head"><strong>Manage Kicks & Bans</strong><button type="button" className="allscan-urf-access-close" aria-label="Close Manage Kicks & Bans" onClick={() => setUrfAccessOpen(false)}>×</button></div><div className="allscan-management-tabs" role="tablist" aria-label="Connection management"><button type="button" role="tab" aria-selected={managementTab === 'clients'} onClick={() => setManagementTab('clients')}>Connected clients</button><button type="button" role="tab" aria-selected={managementTab === 'bans'} onClick={() => { setManagementTab('bans'); void loadAslBans() }}>Bans</button></div>{managementTab === 'clients' ? <>
                  <div className="allscan-urf-admin-scope"><strong>Client administration</strong><span>Ban is global across ASR bridges. Enforcement is applied by each supported backend. Protected service and health-probe identities cannot be banned. Kick affects only the selected current bridge session.</span></div>
                  <div className="allscan-urf-admin-tools">
                    <label>Find connected client<input value={urfClientFilter} onChange={(event) => setUrfClientFilter(event.target.value.toUpperCase())} placeholder="Callsign or mode" maxLength={32} /></label>
                    <label>Ban callsign<input value={urfAccessRule} onChange={(event) => setUrfAccessRule(event.target.value.toUpperCase())} placeholder="Callsign" maxLength={12} disabled={urfAccessBusy} /></label>
                    <label>Ban duration<select value={urfBanDuration} disabled={urfAccessBusy} onChange={(event) => setUrfBanDuration(event.target.value as UrfBanDuration)}>{URF_BAN_DURATIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                    <button type="button" disabled={urfAccessBusy || !urfAccessRule.trim()} onClick={() => void changeUrfBlacklist('ban', urfAccessRule, urfBanDuration)}>Ban</button>
                  </div>
                  <div className="allscan-urf-admin-clients"><b>AllStar / EchoLink connections</b>{sortedConnectionRows.filter((row) => row.state !== 'message' && isBannableConnection(row) && !isProtectedBanConnection(row) && row.node !== config.node && !row.bridgeId && !config.bridges.some((bridge) => bridge.node === row.node) && (!urfClientFilter.trim() || `${row.node} ${row.info} ${row.mode}`.toUpperCase().includes(urfClientFilter.trim().toUpperCase()))).map((row, index) => <span key={`${row.node}-${row.mode}-${index}`}><code>{row.node}</code><small>{row.info} · {row.mode}</small><span className="allscan-urf-client-actions"><button type="button" onClick={() => { if (/^[0-9]{3,10}$/.test(row.node)) void runCommandForNode('disconnect', row.node); else { setUrfAccessOpen(false); setDropClientOpen(true); void loadDropClients() } }}>{/^[0-9]{3,10}$/.test(row.node) ? 'Disconnect' : 'Drop Client…'}</button>{connectionCallsign(row) && !isProtectedBanConnection(row) ? <button type="button" onClick={() => { setUrfAccessOpen(false); openParticipantBan(row) }}>Ban…</button> : null}</span></span>)}</div>
                  <div className="allscan-urf-admin-clients"><b>Digital bridge clients</b>{managedConnectedCards.flatMap((managedCard) => { const canKick = managedCard.mode.toUpperCase() !== 'ZELLO' && config.bridges.find((bridge) => bridge.id === managedCard.id)?.adminCapabilities?.clientAdmin.includes('kickClient') === true; return managedCard.detailRows.filter((detail) => !detail.empty).map((detail) => ({ ...detail, mode: managedCard.mode, bridgeId: managedCard.id, canKick })) }).filter((detail) => !urfClientFilter.trim() || `${detail.label} ${detail.mode}`.toUpperCase().includes(urfClientFilter.trim().toUpperCase())).map((detail) => { const callsign = detail.label.replace(/\s+[A-Z]$/i, '').trim().toUpperCase(); const banned = urfBlacklist.some((rule) => rule.endsWith('*') ? callsign.startsWith(rule.slice(0, -1)) : callsign === rule); return <span key={`${detail.mode}-${detail.key}`}><code>{detail.label}</code><small>{detail.mode.toUpperCase()}{banned ? ' · BANNED' : ''}</small><span className="allscan-urf-client-actions">{detail.canKick ? <button type="button" disabled={urfAccessBusy} onClick={() => void kickBridgeConnectedClient(callsign, detail.bridgeId, detail.mode)}>Kick</button> : null}{banned ? null : globalBanDurationSelect(callsign)}</span></span> })}</div>
                  {zelloTalkers.length ? <div className="allscan-urf-admin-clients"><b>Recent Zello Talkers</b>{zelloTalkers.filter(({ identity }) => !urfClientFilter.trim() || `${identity} ZELLO`.includes(urfClientFilter.trim().toUpperCase())).map(({ card, identity, active }) => { const banned = urfBlacklist.some((rule) => rule.endsWith('*') ? identity.startsWith(rule.slice(0, -1)) : identity === rule); return <span key={`zello-${card.id}-${identity}`}><code>{identity}</code><small>ZELLO · {active ? 'CURRENT TRANSMISSION' : 'RECENT TALKER'}{banned ? ' · BANNED' : ''}</small><span className="allscan-urf-client-actions">{banned ? null : globalBanDurationSelect(identity)}</span></span> })}</div> : null}
                  </> : <div className="allscan-management-bans">
                    <button type="button" className="allscan-action-button" onClick={() => { setUrfAccessOpen(false); openParticipantBan() }}>Add ban…</button>
                    {aslEnforcementStatus ? <p role="status">{aslEnforcementStatus}</p> : null}
            <div className="allscan-connection-ban-list">
              <strong>Global callsigns</strong>
              {urfBans.length ? urfBans.map((ban) => <div key={`global-${ban.rule}`}>
                <strong>{ban.rule}</strong><span>Everywhere · {formatUrfBanRemaining(ban.expiresAt, clock)} · {formatUrfBanExpiration(ban.expiresAt)}</span>
                <button type="button" disabled={banBusy} onClick={() => void removeGlobalBan(ban.rule)}>Unban</button>
              </div>) : <p>No global bans.</p>}
              {aslExternal.allstar.length || aslExternal.echolink.length ? <>
                <strong>Linux / manual restrictions (read only)</strong>
                {aslExternal.allstar.map((value) => <div key={`external-node-${value}`}><strong>{value}</strong><span>AllStar · manual</span></div>)}
                {aslExternal.echolink.map((value) => <div key={`external-echo-${value}`}><strong>{value}</strong><span>EchoLink · manual</span></div>)}
              </> : null}
            </div>
                    {urfAudit.length ? <div className="allscan-management-history"><strong>Recent bridge actions</strong>{[...urfAudit].reverse().slice(0, 12).map((event, index) => <p key={`${event.timestamp}-${index}`}>{event.action.toUpperCase()} · {event.rule || event.callsign || "—"} · {new Date(event.timestamp * 1000).toLocaleString()}</p>)}</div> : null}
                    {banStatus ? <p role="status">{banStatus}</p> : null}
                    <button type="button" className="allscan-action-button" onClick={() => void loadAslBans()}>Refresh</button>
                  </div>}
                  {urfAccessStatus ? <div className="allscan-urf-access-status" role="status">{urfAccessStatus}</div> : null}
                </div></div> : null}
              </div>
            })()}

            <div className="allscan-bridge-grid">
              {bridgeState.cards.filter((card) => !config.bridges.find((bridge) => bridge.id === card.id)?.urfReflector).map((card) => {
                const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id)
                const bridgeCapabilities = bridgeConfig?.adminCapabilities?.bridgeControl || []
                const canConnect = bridgeCapabilities.includes('connect')
                const canDisconnect = bridgeCapabilities.includes('disconnect')
                const canChangeDestination = bridgeCapabilities.includes('changeDestination')
                const bridgeLinked = card.cardType !== 'standard' && card.cardType !== 'dmr_net'
                  ? card.controlLinked
                  : card.controlLinked || rows.some(
                  (row) => row.bridgeId === card.id
                    && row.direction.toUpperCase() === 'OUT'
                    && row.state !== 'message',
                )
                const cardBusy = bridgeControlBusy === card.id
                const destinationInput = bridgeDestinationInputs[card.id] || ''
                const approvedDestinations = bridgeDestinations[card.id] || []
                const approvedDestinationValues = new Set(approvedDestinations.map((destination) => destination.value))
                const dmrTalkgroupCandidate = dmrTalkgroupInputs[card.id] || ''
                const validDmrTalkgroup = /^\d{1,8}$/.test(dmrTalkgroupCandidate)
                  && Number(dmrTalkgroupCandidate) >= 1
                  && Number(dmrTalkgroupCandidate) <= 16777215
                  && Number(dmrTalkgroupCandidate) !== 4000
                const approvedDestinationInput = approvedDestinationValues.has(destinationInput) ? destinationInput : ''
                const connectDestination = card.cardType === 'ysf_net'
                  ? destinationInput.trim()
                  : approvedDestinationInput
                const destinationLabel = card.cardType === 'ysf_net'
                  ? 'Reflector name or ID'
                  : card.cardType === 'm17_net'
                    ? 'Approved reflector and module'
                    : `Approved ${card.mode.toUpperCase()} designator`
                return (
                <article
                  key={card.id}
                  className={`allscan-bridge-card ${bridgeRoleClasses[card.status]}`}
                >
                  <div className="allscan-bridge-head">
                    <span className="allscan-bridge-head-title">{card.title}</span>
                    <span className="allscan-urf-mini-head-actions">
                      <button
                        type="button"
                        className={`allscan-bridge-status-control${card.healthSeverity ? ' has-health-issue' : ''}`}
                        aria-label={card.healthSeverity ? `${bridgeHealthLabels[card.healthSeverity]}: ${card.healthIssues.join(' ')}` : `Status: ${bridgeStatusLabels[card.status]}`}
                        title={card.healthSeverity ? card.healthIssues.join('\n') : bridgeStatusLabels[card.status]}
                        onClick={() => {
                          if (card.healthSeverity && card.healthIssues.length) window.alert(card.healthIssues.join('\n'))
                        }}
                      >
                        {card.healthSeverity ? <AlertTriangle className={`allscan-bridge-health-triangle ${bridgeHealthTriangleClasses[card.healthSeverity]}`} aria-hidden="true" /> : null}
                        <span className={`allscan-bridge-status rounded-full border ${card.healthSeverity ? pillClasses.neutral : bridgeStatusClasses[card.status]}`}>
                          {card.healthSeverity ? bridgeHealthLabels[card.healthSeverity] : bridgeStatusLabels[card.status]}
                        </span>
                      </button>
                    </span>
                  </div>

                  <div className="allscan-bridge-body">
                    {card.cardType === 'dmr_net' ? (
                      <div className="allscan-bridge-row allscan-bridge-status-row allscan-bridge-current-row">
                        <span>Current TG</span>
                        <b>{bridgeLinked ? (card.currentDestinationLabel || card.currentTg || '–') : '–'}</b>
                      </div>
                    ) : null}
                    {card.cardType !== 'standard' && card.cardType !== 'dmr_net' ? (
                      <div className="allscan-bridge-row allscan-bridge-status-row allscan-bridge-current-row">
                        <span>{card.cardType === 'ysf_net' || card.cardType === 'm17_net' ? 'Current Reflector' : 'Current Destination'}</span>
                        <b>{bridgeLinked ? (card.currentDestinationLabel || card.currentDestination || '–') : '–'}</b>
                      </div>
                    ) : null}
                    <div className="allscan-bridge-row allscan-bridge-status-row">
                      <span>Talking</span>
                      <b>{card.lastCaller === '-' ? '–' : card.lastCaller}</b>
                    </div>
                    {card.cardType === 'standard' ? (
                      <div className="allscan-bridge-row allscan-bridge-status-row">
                        <span>Last Talker</span>
                        <b title={card.lastTxEpoch > 0 ? bridgeLastTalker(card) : undefined}>{card.lastTxEpoch > 0
                          ? bridgeLastTalker(card)
                          : '–'}</b>
                      </div>
                    ) : null}
                  </div>

                  {card.cardType === 'dmr_net' && authStatus.canModify && (canConnect || canDisconnect || canChangeDestination) ? (
                    <div className="allscan-bridge-controls">
                      <div className="allscan-bridge-tune">
                        <label htmlFor={`dmr-net-tg-${card.id}`}>Talkgroup</label>
                        <input
                          id={`dmr-net-tg-${card.id}`}
                          type="text"
                          inputMode="numeric"
                          autoComplete="off"
                          spellCheck={false}
                          maxLength={8}
                          value={dmrTalkgroupCandidate}
                          placeholder=""
                          disabled={busy || cardBusy}
                          onChange={(event) => {
                            const talkgroup = event.target.value.replace(/\D/g, '').slice(0, 8)
                            setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: talkgroup }))
                          }}
                        />
                      </div>
                      <div className="allscan-bridge-link-buttons">
                        <button
                          type="button"
                          className="allscan-action-button allscan-connect-button"
                          disabled={busy || cardBusy || !canConnect || !canChangeDestination || !card.controlReady || !validDmrTalkgroup}
                          onClick={() => void connectDmrNetCard(card)}
                        >
                          {cardBusy && bridgeControlAction === 'connect' ? 'Connecting…' : 'Connect'}
                        </button>
                        <button
                          type="button"
                          className="allscan-action-button allscan-disconnect-button"
                          disabled={busy || cardBusy || !canDisconnect || !card.controlReady}
                          onClick={() => void disconnectDmrNetCard(card)}
                        >
                          {cardBusy && bridgeControlAction === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {card.cardType !== 'standard' && card.cardType !== 'dmr_net' && authStatus.canModify && (canConnect || canDisconnect || canChangeDestination) ? (
                    <div className="allscan-bridge-controls">
                      <div className="allscan-bridge-tune">
                        <label htmlFor={`digital-net-destination-${card.id}`}>{destinationLabel}</label>
                        {card.cardType === 'ysf_net' ? (
                          <input
                            id={`digital-net-destination-${card.id}`}
                            type="text"
                            autoComplete="off"
                            spellCheck={false}
                            maxLength={80}
                            value={destinationInput}
                            placeholder=""
                            disabled={busy || cardBusy}
                            onChange={(event) => {
                              setBridgeDestinationInputs((current) => ({
                                ...current,
                                [card.id]: event.target.value.slice(0, 80),
                              }))
                            }}
                          />
                        ) : (
                          <select
                            id={`digital-net-destination-${card.id}`}
                            value={approvedDestinationInput}
                            disabled={busy || cardBusy || approvedDestinations.length === 0}
                            onChange={(event) => {
                              setBridgeDestinationInputs((current) => ({
                                ...current,
                                [card.id]: event.target.value,
                              }))
                            }}
                          >
                            <option value=""></option>
                            {approvedDestinations.map((destination) => (
                              <option key={destination.value} value={destination.value}>{destination.label}</option>
                            ))}
                          </select>
                        )}
                      </div>
                      <div className="allscan-bridge-link-buttons">
                        <button
                          type="button"
                          className="allscan-action-button allscan-connect-button"
                          disabled={busy || cardBusy || !canConnect || !canChangeDestination || !card.controlReady || connectDestination === ''}
                          onClick={() => void connectReflectorNetCard(card)}
                        >
                          {cardBusy && bridgeControlAction === 'connect' ? 'Connecting…' : 'Connect'}
                        </button>
                        <button
                          type="button"
                          className="allscan-action-button allscan-disconnect-button"
                          disabled={busy || cardBusy || !canDisconnect || (
                            !card.controlReady
                            && !card.controlLinked
                            && !card.digitalLinked
                            && !card.allstarLinked
                          )}
                          onClick={() => void disconnectReflectorNetCard(card)}
                        >
                          {cardBusy && bridgeControlAction === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
                        </button>
                      </div>
                    </div>
                  ) : null}

                  {bridgeCardShowsClientDetails(card.cardType) ? (
                    <div className="allscan-bridge-detail-wrap">
                      <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={bridgeClientsOpen.has(card.id)} onClick={() => setBridgeClientsOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.clientsOpen', JSON.stringify([...next])); return next })}>
                        <span>{compactBridgeDetailTitle(card.detailTitle)}</span><b>{card.detailAvailable ? card.detailCount : '–'}</b><ChevronDown className={bridgeClientsOpen.has(card.id) ? 'is-open' : ''} aria-hidden="true" />
                      </button>
                      {bridgeClientsOpen.has(card.id) ? <div className="allscan-bridge-detail-box">
                        {card.detailRows.map((detail) => {
                          const identity = detail.label.replace(/\s+[A-Z]$/i, '').trim().toUpperCase()
                          const supportsKick = card.mode.toUpperCase() !== 'ZELLO' && bridgeConfig?.adminCapabilities?.clientAdmin.includes('kickClient') === true
                          const supportsBan = bridgeConfig?.adminCapabilities?.clientAdmin.includes('banClient') === true
                          const isZello = card.mode.toUpperCase() === 'ZELLO'
                          const kickAvailable = supportsKick && (!isZello || card.lastCaller.replace(/\s+[A-Z]$/i, '').trim().toUpperCase() === identity)
                          const banned = urfBlacklist.some((rule) => rule.endsWith('*') ? identity.startsWith(rule.slice(0, -1)) : identity === rule)
                          return <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}>
                            <div className="allscan-bridge-client-user"><span>{detail.label}</span></div>
                            {detail.meta && <div className="allscan-bridge-client-meta">{detail.meta}</div>}
                            {!detail.empty && authStatus.isAdmin && (supportsKick || supportsBan) ? <div className="allscan-bridge-inline-client-actions">{supportsKick ? <button type="button" title={kickAvailable ? 'Kick the active bridge session' : 'Kick is available only while this Zello identity is transmitting'} disabled={urfAccessBusy || !kickAvailable} onClick={() => void kickBridgeConnectedClient(identity, card.id, card.mode)}>Kick</button> : null}{supportsBan && !banned ? globalBanDurationSelect(identity) : null}</div> : null}
                          </div>
                        })}
                      </div> : null}
                    </div>
                  ) : null}

                  {card.cardType === 'standard' ? (
                    <div className="allscan-bridge-detail-wrap allscan-bridge-recent">
                      <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={bridgeHistoryOpen.has(card.id)} onClick={() => setBridgeHistoryOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.historyOpen', JSON.stringify([...next])); return next })}>
                        <span>Recent Activity</span><b>{card.recentRows.filter((detail) => !detail.empty).length}</b><ChevronDown className={bridgeHistoryOpen.has(card.id) ? 'is-open' : ''} aria-hidden="true" />
                      </button>
                      {bridgeHistoryOpen.has(card.id) ? <div className="allscan-bridge-detail-box">
                        {card.recentRows.map((detail) => (
                          <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}>
                            <div className="allscan-bridge-client-user"><span>{detail.label}</span></div>
                            {detail.meta && <div className="allscan-bridge-client-meta">{detail.meta}</div>}
                          </div>
                        ))}
                      </div> : null}
                    </div>
                  ) : null}
                </article>
                )
              })}
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

      {banDialog ? (
        <div className="allscan-drop-client-modal" onClick={() => !banBusy && setBanDialog(null)}>
          <div className="allscan-drop-client-box allscan-connection-ban-dialog" role="dialog" aria-modal="true" aria-label="Ban connection" onClick={(event) => event.stopPropagation()}>
            <h3>Global Ban</h3>
            {banDialog.row ? <p>{banDialog.row.node} · {banDialog.row.info}</p> : <p>Add a global ban for a disconnected callsign.</p>}
            <label>Callsign
              <input value={banDialog.value} maxLength={12} onChange={(event) => setBanDialog({ ...banDialog, value: event.target.value.toUpperCase() })} />
            </label>
            <label>Duration
              <select value={banDuration} onChange={(event) => setBanDuration(event.target.value as UrfBanDuration)}>
                {URF_BAN_DURATIONS.map((duration) => <option key={duration.value} value={duration.value}>{duration.label}</option>)}
              </select>
            </label>
            <p className="allscan-drop-client-help">Applies everywhere ASR can enforce it.{banDialog.row ? ' ASR will disconnect this connection after applying the ban.' : ''}</p>
            {banStatus ? <p role="status">{banStatus}</p> : null}
            <div className="allscan-drop-client-actions">
              <button type="button" className="allscan-action-button" disabled={banBusy || !banDialog.value.trim()} onClick={() => void submitParticipantBan()}>{banBusy ? 'Applying…' : 'Ban'}</button>
              <button type="button" className="allscan-action-button" disabled={banBusy} onClick={() => setBanDialog(null)}>Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
      {dropClientOpen ? (
        <div className="allscan-drop-client-modal" onClick={() => setDropClientOpen(false)}>
          <div className="allscan-drop-client-box" onClick={(event) => event.stopPropagation()}>
            <h3>Drop Client</h3>
            <div className="allscan-drop-client-help">
              This targets one live named IAX or Web client channel.
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

      {diagnosticsOpen ? (
        <div className="allscan-drop-client-modal" onClick={() => setDiagnosticsOpen(false)}>
          <div className="allscan-drop-client-box allscan-diagnostics-box" onClick={(event) => event.stopPropagation()}>
            <h3>Report a Bug</h3>
            <div className="allscan-drop-client-help">
              This creates an admin-only diagnostics report for KE7WIL. Review it before emailing.
            </div>
            <div className="allscan-drop-client-status">{diagnosticsStatus}</div>
            <textarea
              ref={diagnosticsTextRef}
              className="allscan-diagnostics-report"
              readOnly
              spellCheck={false}
              value={diagnosticsReport?.report || ''}
              aria-label="Diagnostics report"
            />
            <div className="allscan-drop-client-actions">
              <button type="button" className="allscan-action-button" disabled={busy} onClick={() => void loadDiagnosticsReport()}>
                Refresh
              </button>
              <button type="button" className="allscan-action-button" disabled={!diagnosticsReport?.report} onClick={() => void copyDiagnosticsReport()}>
                Copy
              </button>
              <button type="button" className="allscan-action-button" disabled={!diagnosticsReport?.report} onClick={emailDiagnosticsReport}>
                Email
              </button>
              <button type="button" className="allscan-action-button" onClick={() => setDiagnosticsOpen(false)}>
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
