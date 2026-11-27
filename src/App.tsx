import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { AlertTriangle, ArrowUpDown, ChevronDown, ChevronLeft, Menu, Pencil, RotateCcw, Search, Trash2, Palette } from 'lucide-react'
import { headerStats } from './mockData'
import UpdateAsrDialog from './components/UpdateAsrDialog'
import { ASR_WHATS_NEW } from './config/whatsNew'
import SupportFeedbackDialog from './components/SupportFeedbackDialog'
import SupportAsrDialog from './components/SupportAsrDialog'
import AllStarBubbleDialog from './components/AllStarBubbleDialog'
import { nextSort, hslToFavoriteHex, pickFavoriteHex, type SortDirection } from './lib/dashboardInteraction'
import { canPopulateNodeControl } from './lib/nodeNumbers'
import { canPreviewFavoriteBackup } from './lib/favoritesRecovery'
import { favoriteConnectionNames, connectionDisplayName } from './lib/favoriteConnectionNames'
import { matchingRecentTalker } from './lib/recentTalkerIdentity'
import { identityFromConnection, isBannableConnection } from './lib/participantIdentity'
import {
  actionOptions,
  activateNetBridgeMode,
  asrPath,
  bridgeCardShowsClientDetails,
  bridgeDashboardStatusLabel,
  bridgeTalkerFeed,
  relativeBridgeTime,
  disconnectBridge,
  dropClientChannel,
  fetchBridgeCards,
  bridgeActivityScope,
  retainBridgeActivity,
  type BridgeCardsSnapshot,
  fetchFastNextModeActivity,
  fetchFastStandardActivity,
  fetchFastYsfNetActivity,
  fetchZelloFastRelay,
  fetchAuthStatus,
  fetchTgifUserStatus,
  fetchCpuTemp,
  fetchCustomCommands,
  fetchDropClients,
  fetchFavorites,
  fetchFavoriteStats,
  manageFavorite,
  viewFavoritesFile,
  editFavoritesFile,
  recoverFavoritesFile,
  inspectFavoritesFiles,
  fetchReleaseStatus,
  isNodeMessageAuthNotice,
  normalizedBridgeMode,
  netBridgeTalkerDestinationLabel,
  orderedVisibleBridgeCards,
  fetchGlobalBans,
  exactZelloBanPolicy,
  type ExactZelloBan,
  fetchAslBans,
  changeAslBan,
  updateGlobalBan,
  globalBanPendingReason,
  globalBanScope,
  type GlobalBanList,
  updateM17Qualification,
  kickZelloClient,
  kickStandaloneClient,
  type TgifKickResult,
  provisionedNetBridgeModes,
  restartAsteriskCommand,
  repairStandardBridge,
  setStandardBridgeLink,
  saveCustomCommands,
  summarizeConnectionTotal,
  connectBridge,
  type FavoritesFileOption,
  type FavoriteStats,
  type FastNextModeActivity,
  sendNodeCommand,
  subscribeConnectionFeed,
  type BridgeCardView,
  type AuthStatus,
  type TgifUserStatus,
  type DropClientEntry,
  type CustomCommand,
  type FavoriteNode,
  type LiveConnectionRow,
  type TalkerFeedEntry,
  type RuntimeConfig,
  type ReleaseStatus,
  type GlobalBan,
  type GlobalBanAuditEvent,
  type AslBan,
  type AslBanKind,
} from './lib/allscanLive'

const FAST_ACTIVITY_POLL_MS = 250

const FAVORITES_DISPLAY_CACHE_KEY = 'asrFavoritesDisplayCache.v2'
const NODE_MESSAGE_LIVE_CACHE_KEY = 'asrNodeMessageState.liveOnly.v1'
const NODE_MESSAGE_LIVE_TTL = 300000
const BRIDGE_REFRESH_MS = 500
const BRIDGE_REFRESH_TIMEOUT_MS = 5000
const BRIDGE_REFRESH_ERROR_BACKOFF_MS = 5000
const TALKER_HISTORY_MAX_AGE_SECONDS = 24 * 60 * 60
const THEME_SETTINGS_KEY = 'asrThemeSettings.v1'
const AUTODISC_PREFERENCE_KEY = 'asrDisconnectBeforeConnect.v1'
const ACTION_PREFERENCE_KEY = 'asrNodeControlAction.v1'
const PERMANENT_PREFERENCE_KEY = 'asrPermanentConnection.v1'
const FAVORITES_PLACEMENT_KEY = 'asrFavoritesPlacement.v1'
const DASHBOARD_MODULE_ORDER_KEY = 'asrDashboardModuleOrder.v1'
const TOUCH_MODULE_DRAG_DELAY_MS = 350
const TOUCH_MODULE_DRAG_CANCEL_DISTANCE_PX = 8
const FAVORITES_OPEN_KEY = 'asrFavoritesOpen.v1'
const TALKERS_OPEN_KEY = 'asrTalkersOpen.v1'
const UPDATE_NOTICE_DISMISS_KEY = 'asrUpdateNoticeDismissedVersion.v1'
const WHATS_NEW_SEEN_KEY = 'asrWhatsNewSeenVersion.v1'
const FAVORITES_LOAD_ERROR = 'Favorites list could not be loaded.'
const GLOBAL_BAN_DURATIONS = [
  { value: '15m', label: '15 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '1w', label: '1 week' },
  { value: '30d', label: '30 days' },
  { value: 'permanent', label: 'Permanent' },
] as const

type GlobalBanDuration = (typeof GLOBAL_BAN_DURATIONS)[number]['value']

function formatGlobalBanRemaining(expiresAt: number, now: Date) {
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

function formatGlobalBanExpiration(expiresAt: number) {
  return expiresAt > 0 ? `until ${new Date(expiresAt * 1000).toLocaleString()}` : 'PERMANENT'
}

const loggedOutAuth: AuthStatus = {
  loggedIn: false,
  username: '',
  permission: 0,
  publicPermission: 2,
  canRead: false,
  canModify: false,
  canWrite: false,
  isAdmin: false,
}

type ThemeSettings = {
  theme?: string
  mode?: 'dark' | 'light'
  reduceBrowserActivity?: boolean
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
  document.documentElement.dataset.asrReduceActivity = normalized.reduceBrowserActivity ? 'true' : 'false'
}

function normalizeThemeSettings(settings: ThemeSettings): Required<ThemeSettings> {
  const allowedThemes = ['standard', 'deep-ocean-animated', 'matrix', 'lcars-frame']
  let theme = settings.theme || 'standard'
  if (theme === 'deep-ocean') theme = 'deep-ocean-animated'
  if (theme === 'matrix-static') theme = 'matrix'
  if (!allowedThemes.includes(theme)) theme = 'standard'
  const mode = settings.mode === 'light' ? 'light' : 'dark'
  const reduceBrowserActivity = settings.reduceBrowserActivity === true
  return { theme, mode, reduceBrowserActivity }
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

type NodeControlAction = (typeof actionOptions)[number]['value']

function readActionPreference(): NodeControlAction {
  if (typeof window === 'undefined') return 'dropclient'
  try {
    const saved = window.localStorage.getItem(ACTION_PREFERENCE_KEY)
    return actionOptions.some((option) => option.value === saved)
      ? saved as NodeControlAction
      : 'dropclient'
  } catch {
    return 'dropclient'
  }
}

function writeActionPreference(action: NodeControlAction) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(ACTION_PREFERENCE_KEY, action)
  } catch {
    // Ignore preference write failures.
  }
}

function readPermanentPreference() {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(PERMANENT_PREFERENCE_KEY) === 'true'
  } catch {
    return false
  }
}

function writePermanentPreference(checked: boolean) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(PERMANENT_PREFERENCE_KEY, checked ? 'true' : 'false')
  } catch {
    // Ignore preference write failures.
  }
}

function readDismissedUpdateVersion() {
  if (typeof window === 'undefined') return ''
  try {
    return window.localStorage.getItem(UPDATE_NOTICE_DISMISS_KEY) || ''
  } catch {
    return ''
  }
}

function writeDismissedUpdateVersion(version: string) {
  if (typeof window === 'undefined' || !version) return
  try {
    window.localStorage.setItem(UPDATE_NOTICE_DISMISS_KEY, version)
  } catch {
    // Ignore update-notice preference write failures.
  }
}

function readWhatsNewSeenVersion() {
  if (typeof window === 'undefined') return ''
  try {
    return window.localStorage.getItem(WHATS_NEW_SEEN_KEY) || ''
  } catch {
    return ''
  }
}

function writeWhatsNewSeenVersion(version: string) {
  if (typeof window === 'undefined' || !version) return
  try {
    window.localStorage.setItem(WHATS_NEW_SEEN_KEY, version)
  } catch {
    // Ignore browser preference write failures.
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
    if (isNodeMessageTransportNoise(line) || isNodeMessageAuthNotice(line)) {
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
  if (isNodeMessageTransportNoise(cleaned) || isNodeMessageAuthNotice(cleaned)) return

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
  unknown: 'bg-transparent text-[#f1f6fb]',
  talking: 'bg-[maroon] text-[yellow]',
  relay: 'bg-[#74560b] text-[#ffe37a]',
  both: 'bg-[#74560b] text-[#ffe37a]',
  normal: 'bg-transparent text-[#f1f6fb]',
  message: 'bg-transparent text-[#f1f6fb]',
}

const localRowStyles = {
  idle: { backgroundColor: 'hsl(150, 50%, 15%)', color: '#eaf4f8' },
  unknown: { backgroundColor: '#383838', color: '#f1f6fb' },
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
  const [bubbleNode, setBubbleNode] = useState<string | null>(null)
  const [rows, setRows] = useState<LiveConnectionRow[]>([])
  const [connectionFeedReady, setConnectionFeedReady] = useState(false)
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
  const [cpuValue, setCpuValue] = useState('--°F / --°C')
  const [cpuBgColor, setCpuBgColor] = useState('#59461c')
  const [bridgeState, setBridgeState] = useState<BridgeCardsSnapshot>(() => ({
    activityScope: bridgeActivityScope(config),
    activityEpoch: 0,
    updatedLabel: '--:--:--',
    cards: config.bridges.map((bridge): BridgeCardView => ({
      id: bridge.id,
      mode: normalizedBridgeMode(bridge.mode, bridge.id),
      node: bridge.node,
      title: bridge.title,
      cardType: bridge.cardType || 'standard',
      status: 'Idle',
      lastCaller: '-',
      warning: '-',
      healthSeverity: null,
      healthIssues: [],
      repairAvailable: false,
      currentTg: '',
      currentDestination: '',
      currentDestinationLabel: '',
      controlLinked: false,
      digitalLinked: false,
      allstarLinked: false,
      detailTitle: bridge.detailTitle,
      detailRows: [],
      detailCount: 0,
      detailAvailable: true,
      connectedClientCount: 0,
      runtimeOnline: null,
      reflector: '',
      module: bridge.m17Module || '',
      linkProtocol: '',
      lastTransmitter: '-',
      lastTxEpoch: 0,
      activeStartEpoch: 0,
      recentTalkers: [],
      relayEventEpochs: [],
      recentRows: [],
      audioMeter: null,
    })),
  }))
  const acceptBridgeSnapshot = useCallback((next: BridgeCardsSnapshot) => {
    setBridgeState((previous) => retainBridgeActivity(previous, next))
  }, [])
  const [bridgeStatusLoaded, setBridgeStatusLoaded] = useState(false)
  const [dmrTalkgroupInputs, setDmrTalkgroupInputs] = useState<Record<string, string>>({})
  const [bridgeDestinationInputs, setBridgeDestinationInputs] = useState<Record<string, string>>({})
  const [m17ModuleInputs, setM17ModuleInputs] = useState<Record<string, string>>({})
  const [netBridgeMode, setNetBridgeMode] = useState(() => config.netBridgeMode || 'dmr')
  const [netBridgeModeError, setNetBridgeModeError] = useState('')
  const activeNetBridgeModeRef = useRef(config.netBridgeMode || 'dmr')
  const pendingNetBridgeModeRef = useRef('')
  const modeSwitchPromiseRef = useRef<Promise<void> | null>(null)
  const pendingConnectBridgeRef = useRef('')
  const bridgeControlBusyRef = useRef('')
  const [bridgeControlBusy, setBridgeControlBusy] = useState('')
  const availableNetBridgeModes = useMemo(() => provisionedNetBridgeModes(config.bridges), [config.bridges])
  const m17NetConfig = config.bridges.find((bridge) => bridge.cardType === 'm17_net')
  const [bridgeControlAction, setBridgeControlAction] = useState<'connect' | 'disconnect' | ''>('')
  const [bridgeClientsOpen, setBridgeClientsOpen] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('asr.bridge.clientsOpen') || '[]')) } catch { return new Set() } })
  const [bridgeHistoryOpen, setBridgeHistoryOpen] = useState<Set<string>>(() => { try { return new Set(JSON.parse(localStorage.getItem('asr.bridge.historyOpen') || '[]')) } catch { return new Set() } })
  const [nodeMessage, setNodeMessage] = useState('Loading live status...')
  const [nodeMessageLatest, setNodeMessageLatest] = useState(() => readLiveNodeMessageCache() || 'No recent messages')
  const [nodeMessageRaw, setNodeMessageRaw] = useState('')
  const [messagesOpen, setMessagesOpen] = useState(false)
  const [favorites, setFavorites] = useState<FavoriteNode[]>([])
  const [favoriteFiles, setFavoriteFiles] = useState<FavoritesFileOption[]>([])
  const [favoriteInventory, setFavoriteInventory] = useState<Array<{ path: string; exists: boolean; symlink: boolean; readable: boolean; bytes: number | null }> | null>(null)
  const [favoriteFileView, setFavoriteFileView] = useState<{ name: string; path: string; content: string; hash: string } | null>(null)
  const [favoriteFileDraft, setFavoriteFileDraft] = useState('')
  const [favoriteFileEditing, setFavoriteFileEditing] = useState(false)
  const [favoriteFileInfo, setFavoriteFileInfo] = useState<{ exists?: boolean; readable?: boolean; path?: string; bytes?: number | null }>({})
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
  const favoriteDragPointerRef = useRef({ x: 0, y: 0 })
  const favoriteDragScrollFrameRef = useRef<number | null>(null)
  const favoriteDragActiveRef = useRef(false)
  const favoriteDragUpdateRef = useRef<((y: number) => void) | null>(null)
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
  const [aslBans, setAslBans] = useState<AslBan[]>([])
  const [banDialog, setBanDialog] = useState<{ row?: LiveConnectionRow; callsign: string; value: string; aslKind?: AslBanKind } | null>(null)
  const [banDuration, setBanDuration] = useState<GlobalBanDuration>('1h')
  const [banStatus, setBanStatus] = useState('')
  const [banBusy, setBanBusy] = useState(false)
  const [rowActions, setRowActions] = useState<{ row: LiveConnectionRow; left: number; top: number } | null>(null)
  const [dropClients, setDropClients] = useState<DropClientEntry[]>([])
  const [dropClientStatus, setDropClientStatus] = useState('No named client channels loaded yet.')
  const [menuOpen, setMenuOpen] = useState(false)
  const [openSubmenu, setOpenSubmenu] = useState<HeaderMenuKey | null>(null)
  const [themeSettings, setThemeSettings] = useState<ThemeSettings>(() => readThemeSettings())
  const [desktopThemeViewport, setDesktopThemeViewport] = useState(() => isDesktopThemeViewport())
  const [nodeValue, setNodeValue] = useState('')
  const [actionValue, setActionValue] = useState<NodeControlAction>(readActionPreference)
  const [permanent, setPermanent] = useState(readPermanentPreference)
  const [autodisc, setAutodisc] = useState(readAutodiscPreference)
  const [clock, setClock] = useState(() => new Date())
  const [lcarsNumbers, setLcarsNumbers] = useState(() => makeLcarsNumbers(12))
  const [lcarsHeaderNumbers, setLcarsHeaderNumbers] = useState(() => makeLcarsHeaderNumbers())
  const [busy, setBusy] = useState(false)
  const [authStatus, setAuthStatus] = useState<AuthStatus>(loggedOutAuth)
  const [authChecked, setAuthChecked] = useState(false)
  const [tgifUserStatus, setTgifUserStatus] = useState<TgifUserStatus | null>(null)
  const [banAdminOpen, setBanAdminOpen] = useState(false)
  const [globalBanEnforcement, setGlobalBanEnforcement] = useState<Pick<GlobalBanList, 'verified' | 'skippedBridges' | 'asl'> | null>(null)
  const [globalBanRules, setGlobalBanRules] = useState<string[]>([])
  const [zelloSavedBanRules, setZelloSavedBanRules] = useState<string[] | null>(null)
  const [exactZelloBans, setExactZelloBans] = useState<ExactZelloBan[] | null>(null)
  const [globalBans, setGlobalBans] = useState<GlobalBan[]>([])
  const [globalBanAudit, setGlobalBanAudit] = useState<GlobalBanAuditEvent[]>([])
  const [globalBanRule, setGlobalBanRule] = useState('')
  const [globalBanDuration, setGlobalBanDuration] = useState<GlobalBanDuration>('15m')
  const [banClientFilter, setBanClientFilter] = useState('')
  const [banAdminBusy, setBanAdminBusy] = useState(false)
  const [banAdminStatus, setBanAdminStatus] = useState('')
  const [releaseStatus, setReleaseStatus] = useState<ReleaseStatus | null>(null)
  const [dismissedUpdateVersion, setDismissedUpdateVersion] = useState(readDismissedUpdateVersion)
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false)
  const [updateDialogView, setUpdateDialogView] = useState<'update' | 'whats-new'>('update')
  const [supportFeedbackOpen, setSupportFeedbackOpen] = useState(false)
  const [donateAsrOpen, setDonateAsrOpen] = useState(false)
  const favoriteTxHistory = useRef<Record<string, { keyups: number; txtime: number; time: number; txPct: number }>>({})
  const connectionRowsRef = useRef<LiveConnectionRow[]>([])
  const fastStandardActivityRef = useRef<Record<string, { role: 'idle' | 'source' | 'relay'; callsign: string }>>({})
  const currentTalkerRef = useRef<TalkerFeedEntry | null>(null)
  const fastYsfNetActivityRef = useRef<Record<string, { role: 'idle' | 'source' | 'relay'; callsign: string }>>({})
  const fastNextModeActivityRef = useRef<Record<string, FastNextModeActivity>>({})
  const bridgeTalkerIdentityRef = useRef<Record<string, { callsign: string; source: string; description: string }>>({})
  const zelloFastRelayRef = useRef(false)
  const zelloFastRelayReadyRef = useRef(false)
  const nodeInputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const reportBugParamHandled = useRef(false)
  const updateAsrParamHandled = useRef(false)
  const nodeMessagesArmed = useRef(false)
  const lastNodeMessage = useRef('')
  const nodeMessagesBodyRef = useRef<HTMLDivElement>(null)
  const customCommandsRef = useRef<HTMLDivElement>(null)
  const dashboardModuleOverRef = useRef<DashboardModuleKey | null>(null)
  const dashboardDragPointerRef = useRef({ x: 0, y: 0 })
  const dashboardDragScrollFrameRef = useRef<number | null>(null)
  const dashboardModuleOrderRef = useRef<DashboardModuleKey[]>(dashboardModuleOrder)
  const dashboardDragStartOrderRef = useRef<DashboardModuleKey[] | null>(null)
  const pendingDashboardDragRef = useRef<{
    key: DashboardModuleKey
    pointerId: number
    startX: number
    startY: number
    handle: HTMLButtonElement
    timer: number
  } | null>(null)

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

  const enrichBridgeTalker = useCallback((talker: TalkerFeedEntry | null): TalkerFeedEntry | null => {
    if (!talker) return null
    const transportNode = String(talker.node || '')
    if (transportNode !== '1001' && transportNode !== '1999') return talker
    const isCurrentTalker = talker === currentTalker

    const modeCard = transportNode === '1999'
      ? bridgeState.cards.find((card) => card.node === '1999' && card.mode.toLowerCase() === netBridgeMode)
      : undefined
    const sourceCard = bridgeState.cards.find((card) => {
      if (transportNode === '1001') return card.cardType === 'standard' && card.status === 'Source/TX'
      return card.node === '1999' && card.mode.toLowerCase() === netBridgeMode && card.status === 'Source/TX'
    })
    const cacheKey = `${transportNode}:${transportNode === '1999' ? netBridgeMode : 'standard'}:${talker.startedEpoch || talker.eventEpoch}`
    if (transportNode === '1999' && modeCard
        && (modeCard.cardType === 'p25_net' || modeCard.cardType === 'nxdn_net')) {
      const fast = fastNextModeActivityRef.current[modeCard.id]
      const recentIdentity = matchingRecentTalker(fast?.recentTalkers, talker)
      const liveMatchesTransmission = fast?.role === 'source'
        && fast.eventEpoch > 0
        && talker.startedEpoch > 0
        && Math.abs(fast.eventEpoch - talker.startedEpoch) <= 3
      const liveCallsign = liveMatchesTransmission ? String(fast.callsign || '').trim() : ''
      const callsign = liveCallsign || String(recentIdentity?.callsign || '').trim()
      if (callsign && callsign !== '-') {
        bridgeTalkerIdentityRef.current[cacheKey] = {
          callsign,
          source: modeCard.mode.toUpperCase(),
          description: netBridgeTalkerDestinationLabel(
            modeCard.cardType,
            modeCard.currentDestinationLabel || modeCard.currentDestination || modeCard.reflector || talker.description,
          ),
        }
      }
    }
    if (transportNode === '1999' && modeCard?.cardType === 'm17_net') {
      const recentIdentity = matchingRecentTalker(modeCard.recentTalkers, talker)
      const liveCallsign = isCurrentTalker && sourceCard
        ? String(sourceCard.lastCaller || sourceCard.lastTransmitter || '').trim()
        : ''
      const callsign = liveCallsign || String(recentIdentity?.callsign || '').trim()
      if (callsign && callsign !== '-') {
        bridgeTalkerIdentityRef.current[cacheKey] = {
          callsign,
          source: 'M17',
          description: netBridgeTalkerDestinationLabel(
            modeCard.cardType,
            modeCard.currentDestinationLabel || modeCard.currentDestination || modeCard.reflector || talker.description,
          ),
        }
      }
    }
    // A live bridge card describes only the transmission happening now.  Do
    // not stamp that identity onto every historical row for the same transport
    // node; those rows retain the identity cached when they were current.
    if (isCurrentTalker && sourceCard && sourceCard.cardType !== 'p25_net' && sourceCard.cardType !== 'nxdn_net') {
      const fast = transportNode === '1001'
        ? fastStandardActivityRef.current[sourceCard.id]
        : sourceCard.mode.toLowerCase() === 'ysf'
          ? fastYsfNetActivityRef.current[sourceCard.id]
          : undefined
      const cardCallsign = String(sourceCard.lastCaller || sourceCard.lastTransmitter || '').trim()
      const fastCallsign = String(fast?.callsign || '').trim()
      const callsign = fastCallsign && fastCallsign !== '-' ? fastCallsign : cardCallsign
      if (callsign && callsign !== '-') {
        bridgeTalkerIdentityRef.current[cacheKey] = {
          callsign,
          source: sourceCard.mode.toUpperCase(),
          description: netBridgeTalkerDestinationLabel(
            sourceCard.cardType,
            sourceCard.currentDestinationLabel || sourceCard.currentDestination || sourceCard.reflector || talker.description,
          ),
        }
      }
    }
    // History rows already carry the identity the server resolved for that
    // transmission (e.g. node 1001 -> KN4EWT / DMR). Use it when this page did
    // not see the transmission live, so a refresh does not drop every past
    // bridge talker until someone keys up again (#168).
    const serverSource = String(talker.source || '').trim().toUpperCase()
    const serverCallsign = String(talker.info || '').trim().toUpperCase()
    if (!bridgeTalkerIdentityRef.current[cacheKey]
        && serverSource !== '' && serverSource !== 'ALLSTAR'
        && serverCallsign !== '' && serverCallsign !== transportNode
        && /^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/.test(serverCallsign)) {
      bridgeTalkerIdentityRef.current[cacheKey] = {
        callsign: serverCallsign,
        source: serverSource,
        description: String(talker.description || '').trim(),
      }
    }
    const identity = bridgeTalkerIdentityRef.current[cacheKey]
    if (!identity && transportNode === '1001') {
      if (!isCurrentTalker) return null
      const mode = String(sourceCard?.mode || '').trim().toUpperCase()
      return {
        ...talker,
        info: 'Identifying…',
        source: mode || 'DIGITAL',
        node: 'Bridge',
        description: `${mode || 'Digital'} traffic`,
      }
    }
    if (!identity && transportNode === '1999') {
      if (!isCurrentTalker) return null
      const mode = String(netBridgeMode || modeCard?.mode || '').trim().toUpperCase()
      const destination = modeCard
        ? netBridgeTalkerDestinationLabel(
          modeCard.cardType,
          modeCard.currentDestinationLabel || modeCard.currentDestination || modeCard.reflector || '',
        )
        : ''
      return {
        ...talker,
        info: 'Identifying…',
        source: mode || 'NET BRIDGE',
        node: 'Net Bridge',
        description: destination || `${mode || 'Digital'} traffic`,
      }
    }
    if (!identity) return talker
    return {
      ...talker,
      info: identity.callsign,
      source: identity.source,
      node: transportNode === '1999' ? 'Net Bridge' : 'Bridge',
      description: identity.description,
    }
  }, [bridgeState.cards, currentTalker, netBridgeMode])
  const talkerCards = useMemo(() => {
    const bridgeTalkers = bridgeTalkerFeed(bridgeState.cards)
    const cutoff = Math.floor(clock.getTime() / 1000) - TALKER_HISTORY_MAX_AGE_SECONDS
    // ASTAPI reports the AllStar/EchoLink side of a digital bridge relay as
    // a talker too. When its event timestamp matches a digital user's
    // incoming bridge TX edge, it is transport echo, not the originating
    // digital user. AllStar-originated audio relayed out stays visible.
    const relayEpochs = bridgeState.cards.flatMap((card) => card.relayEventEpochs || [])
    const isRelayEcho = (entry: TalkerFeedEntry | null) => Boolean(entry
      && String(entry.source || '').toUpperCase() === 'ALLSTAR'
      && relayEpochs.some((epoch) => Math.abs(epoch - entry.eventEpoch) <= 3))
    const rawActiveCandidate = currentTalker || bridgeTalkers.currentTalker
    const activeCandidate = isRelayEcho(rawActiveCandidate) ? null : rawActiveCandidate
    const active = activeCandidate && Math.max(activeCandidate.eventEpoch, activeCandidate.startedEpoch) >= cutoff
      ? activeCandidate
      : null
    const history = [...recentTalkers, ...bridgeTalkers.recentTalkers]
      .filter((entry) => !isRelayEcho(entry))
      .filter((entry) => entry.eventEpoch >= cutoff)
      .filter((entry) => (!active || entry.node !== active.node || entry.eventEpoch !== active.eventEpoch))
      .sort((left, right) => right.eventEpoch - left.eventEpoch)
    const enrichedActive = enrichBridgeTalker(active)
    const enrichedHistory = history.map(enrichBridgeTalker).filter((entry): entry is TalkerFeedEntry => entry !== null)
    const uniqueHistory = enrichedHistory.filter((entry, index, entries) => {
      if (enrichedActive
        && enrichedActive.info === entry.info
        && enrichedActive.source === entry.source
        && Math.abs(enrichedActive.eventEpoch - entry.eventEpoch) <= 3) return false
      return entries.findIndex((candidate) => (
      candidate.info === entry.info
      && candidate.source === entry.source
      && Math.abs(candidate.eventEpoch - entry.eventEpoch) <= 3
      )) === index
    })
    return [enrichedActive, ...uniqueHistory, null, null, null, null].slice(0, 5)
  }, [clock, currentTalker, recentTalkers, bridgeState.cards, enrichBridgeTalker])

  const talkerLastHeard = (talker: TalkerFeedEntry | null, index: number) => {
    if (!talker || index === 0 || talker.eventEpoch <= 0) return ''
    return new Date(talker.eventEpoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  }
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
    if (!banAdminOpen) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setBanAdminOpen(false)
    }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [banAdminOpen])

  const applyBridgeConnectionOverrides = useCallback((next: BridgeCardsSnapshot) => {
    const rowsByNode = new Map(
      connectionRowsRef.current
        .filter((row) => !row.configuredTransport)
        .map((row) => [row.node, row]),
    )
    const localRow = rowsByNode.get(config.node) || connectionRowsRef.current[0]
    const localIsTransmitting = localRow?.state === 'talking' || localRow?.state === 'both'
    const activeTalkerNode = currentTalkerRef.current?.node || ''
    const activeRelaySource = activeTalkerNode !== '' && connectionRowsRef.current.some((candidate) => (
      !candidate.configuredTransport
      && candidate.node !== config.node
      && candidate.node === activeTalkerNode
      && (candidate.state === 'talking' || candidate.state === 'both')
    ))
    const withStatus = (card: BridgeCardView, status: BridgeCardView['status']) => ({
      ...card,
      status,
      lastCaller: status === 'Relay' ? '-' : card.lastCaller,
    })
    return {
      ...next,
      cards: next.cards.map((card) => {
        const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id)
        const isTunableDigitalBridge = card.cardType !== 'standard'
        const row = isTunableDigitalBridge
          ? connectionRowsRef.current.find((candidate) => candidate.bridgeId === card.id && candidate.direction.toUpperCase() === 'OUT' && candidate.state !== 'message')
            || rowsByNode.get(bridgeConfig?.node || card.node)
          : rowsByNode.get(card.node)
        if (normalizedBridgeMode(bridgeConfig?.mode, bridgeConfig?.id || card.id) === 'zello') {
          if (card.status === 'Source/TX') return card
          // Zello's own feed is authoritative for inbound Source/TX. Outbound
          // relay follows the live AllStar transport immediately; the optional
          // fast hint may only accelerate that signal, never suppress it.
          // Once the fast producer has observed Zello USRP activity it is
          // authoritative for both key and unkey.  Falling back to ASTAPI after
          // the fast unkey reintroduces its ~1.5-2s release lag.
          const relayActive = zelloFastRelayReadyRef.current
            ? zelloFastRelayRef.current
            : Boolean(row && localIsTransmitting)
          return withStatus(card, relayActive ? 'Relay' : 'Idle')
        }
        const fastYsfNet = fastYsfNetActivityRef.current[card.id]
        const fastNextMode = fastNextModeActivityRef.current[card.id]
        if ((card.cardType === 'p25_net' || card.cardType === 'nxdn_net') && fastNextMode?.ready) {
          return withStatus(card, fastNextMode.role === 'source'
            ? 'Source/TX'
            : fastNextMode.role === 'relay' ? 'Relay' : 'Idle')
        }
        // The physical Net Bridge node wins for inbound direction.  The fast
        // protocol feed may observe the digital-side relay edge first, but do
        // not paint Relay while node 1999 is still idle: that creates a false
        // Relay -> Source/TX transition at the start of an inbound transmission.
        if (isTunableDigitalBridge && row?.state === 'talking') {
          return withStatus(card, 'Source/TX')
        }
        if (card.cardType === 'ysf_net' && fastYsfNet) {
          if (fastYsfNet.role === 'source') return withStatus(card, 'Source/TX')
          if (fastYsfNet.role === 'relay') return withStatus(card, localIsTransmitting ? 'Relay' : 'Idle')
          return withStatus(card, 'Idle')
        }
        // Otherwise a bridge-specific Source/TX or Relay role is more
        // authoritative than its Asterisk transport row.
        if (card.status !== 'Idle') {
          return card
        }
        if (card.cardType !== 'standard' && !card.controlLinked) {
          return withStatus(card, 'Idle')
        }
        if (row?.state === 'talking') {
          return withStatus(card, 'Source/TX')
        }
        // A concrete keyed remote row means the node is forwarding that audio
        // to every connected bridge transport. This is bridge-safe RELAY
        // evidence and does not rely on the pseudo hub's sticky RPT_TXKEYED bit.
        if (row && activeRelaySource) {
          return withStatus(card, 'Relay')
        }
        if (row) {
          // A pseudo hub can report RPT_TXKEYED continuously even when every
          // bridge transport is idle.  Never promote that node-wide bit into
          // a bridge-specific Relay indication; the bridge activity producer
          // above remains authoritative for Relay.
          return withStatus(card, 'Idle')
        }
        if (isTunableDigitalBridge) {
          return withStatus(card, 'Idle')
        }
        return withStatus(card, card.status)
      }),
    }
  }, [config.bridges, config.node])

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
    const cachedMessage = readLiveNodeMessageCache()
    const cachedFallback = cleanNodeMessageLine(cachedMessage) === target ? '' : cachedMessage
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
    setNodeMessage((current) => removeTarget(current) || cachedFallback || 'No recent messages')
    setNodeMessageLatest((current) => (
      cleanNodeMessageLine(current) === target
        ? cachedFallback || 'No recent messages'
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
  const favoriteNames = useMemo(() => favoriteConnectionNames(favorites), [favorites])
  const bridgeTransportNodes = useMemo(() => new Set(config.bridges.flatMap((bridge) => [bridge.node, bridge.linkAlias || ''])), [config.bridges])
  const connectionName = useCallback((row: LiveConnectionRow) => connectionDisplayName(row, config.node, bridgeTransportNodes, favoriteNames), [config.node, bridgeTransportNodes, favoriteNames])

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

      const result = compareText(key === 'info' ? connectionName(a.row) : String(a.row[key] || ''), key === 'info' ? connectionName(b.row) : String(b.row[key] || ''), connectionSort.direction)
      return result || a.index - b.index
    })

    return [...pinnedRows, ...items].map((item) => item.row)
  }, [rows, connectionSort, config.node, connectionName])

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
    applyThemeSettings(themeSettings)
  }, [themeSettings])

  useEffect(() => {
    const handleResize = () => setDesktopThemeViewport(isDesktopThemeViewport())
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => () => {
    if (favoriteDragScrollFrameRef.current !== null) window.cancelAnimationFrame(favoriteDragScrollFrameRef.current)
  }, [])

  useEffect(() => {
    if (!favoriteDragNode) return
    const track = (event: DragEvent) => {
      if (favoriteDragActiveRef.current) {
        event.preventDefault()
        favoriteDragUpdateRef.current?.(event.clientY)
      }
    }
    const stop = () => stopFavoriteDragScroll()
    window.addEventListener('dragover', track, { capture: true })
    window.addEventListener('drop', stop, { capture: true })
    window.addEventListener('dragend', stop, { capture: true })
    window.addEventListener('blur', stop)
    return () => {
      window.removeEventListener('dragover', track, { capture: true })
      window.removeEventListener('drop', stop, { capture: true })
      window.removeEventListener('dragend', stop, { capture: true })
      window.removeEventListener('blur', stop)
    }
  }, [favoriteDragNode])

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
    if (reportBugParamHandled.current) return
    const params = new URLSearchParams(window.location.search)
    if (params.get('reportBug') !== '1' && params.get('supportFeedback') !== '1') return
    reportBugParamHandled.current = true
    params.delete('reportBug')
    params.delete('supportFeedback')
    const query = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSupportFeedbackOpen(true)
  }, [])

  useEffect(() => {
    if (updateAsrParamHandled.current || !authStatus.isAdmin) return
    const params = new URLSearchParams(window.location.search)
    if (params.get('updateAsr') !== '1') return
    updateAsrParamHandled.current = true
    params.delete('updateAsr')
    const query = params.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`)
    // Opening the requested admin dialog is the purpose of this URL-driven effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUpdateDialogView('update')
    setUpdateDialogOpen(true)
  }, [authStatus.isAdmin])

  useEffect(() => {
    if (
      !authStatus.isAdmin ||
      updateDialogOpen ||
      updateAsrParamHandled.current ||
      readWhatsNewSeenVersion() === ASR_WHATS_NEW.version
    ) return
    writeWhatsNewSeenVersion(ASR_WHATS_NEW.version)
    // A newly installed version opens its plain-language summary once per browser.
    setUpdateDialogView('whats-new')
    setUpdateDialogOpen(true)
  }, [authStatus.isAdmin, updateDialogOpen])

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
    let retryTimer: number | undefined
    let inFlight = false
    let consecutiveFailures = 0

    const refreshAuthStatus = async () => {
      // Polling, focus and wake events share one request. Previously each event
      // invalidated the preceding response, even when that response succeeded.
      if (cancelled || inFlight) return
      inFlight = true
      window.clearTimeout(retryTimer)
      try {
        const next = await fetchAuthStatus()
        if (!cancelled) {
          // A repeated, identical verified permission snapshot should not
          // invalidate the controls or any auth-dependent effects.
          setAuthStatus((current) => (
            current.loggedIn === next.loggedIn && current.username === next.username
            && current.permission === next.permission && current.publicPermission === next.publicPermission
            && current.canRead === next.canRead && current.canModify === next.canModify
            && current.canWrite === next.canWrite && current.isAdmin === next.isAdmin
              ? current : next
          ))
          consecutiveFailures = 0
          setAuthChecked(true)
          if (!next.canRead) window.location.assign(asrPath('user/'))
        }
      } catch {
        if (!cancelled) {
          // Fail closed on uncertain authentication, but recover automatically.
          setAuthStatus(loggedOutAuth)
          setAuthChecked(false)
          consecutiveFailures += 1
          // Keep the gate closed throughout uncertainty. Recover quickly
          // from one transient failure, then back off on repeated failures.
          retryTimer = window.setTimeout(() => void refreshAuthStatus(), consecutiveFailures === 1 ? 1000 : 10000)
        }
      } finally {
        inFlight = false
      }
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') void refreshAuthStatus()
    }
    void refreshAuthStatus()
    window.addEventListener('focus', refreshAuthStatus)
    window.addEventListener('online', refreshAuthStatus)
    document.addEventListener('visibilitychange', onVisibilityChange)
    const timer = window.setInterval(refreshAuthStatus, 120000)
    return () => {
      cancelled = true
      window.clearTimeout(retryTimer)
      window.removeEventListener('focus', refreshAuthStatus)
      window.removeEventListener('online', refreshAuthStatus)
      document.removeEventListener('visibilitychange', onVisibilityChange)
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
    if (!authStatus.loggedIn || !config.tgifSessionRequired) return
    let cancelled = false
    const refreshTgifStatus = async () => {
      try {
        const next = await fetchTgifUserStatus()
        if (!cancelled) setTgifUserStatus(next)
      } catch {
        // A transient status request failure is not proof that another login is required.
      }
    }
    void refreshTgifStatus()
    window.addEventListener('focus', refreshTgifStatus)
    const timer = window.setInterval(refreshTgifStatus, 60000)
    return () => {
      cancelled = true
      window.removeEventListener('focus', refreshTgifStatus)
      window.clearInterval(timer)
    }
  }, [authStatus.loggedIn, config.tgifSessionRequired])

  useEffect(() => {
    const warnings = [config.storageWarning, config.phpSecurityWarning, ...(config.resourceWarnings || [])].filter(Boolean) as string[]
    if (!warnings.length) return
    const timer = window.setTimeout(() => warnings.forEach((warning) => appendNodeMessage(warning)), 0)
    return () => window.clearTimeout(timer)
  }, [config.phpSecurityWarning, config.resourceWarnings, config.storageWarning])

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
      netBridgeMode,
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
        setConnectionFeedReady(true)
        setConnectedCount(snapshot.connectedCount)
        setDirectCount(snapshot.directCount)
        setAdjacentCount(snapshot.adjacentCount)
        setLinkedNodes(snapshot.linkedNodes)
        setLinkedNodeCounts(snapshot.linkedNodeCounts)
        currentTalkerRef.current = snapshot.currentTalker
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
  }, [config.node, netBridgeMode])

  useEffect(() => {
    let cancelled = false
    let failureNotified = false

    const refreshCpu = async () => {
      try {
        const next = await fetchCpuTemp()
        if (!cancelled && next) {
          setCpuValue(next.value)
          setCpuBgColor(next.bgColor)
          clearNodeMessage('CPU temperature request failed.')
          failureNotified = false
        }
      } catch {
        if (!cancelled) {
          setCpuValue('--°F / --°C')
          setCpuBgColor('#59461c')
          if (!failureNotified) appendNodeMessage('CPU temperature request failed.')
          failureNotified = true
        }
      }
    }

    void refreshCpu()
    const timer = window.setInterval(refreshCpu, effectiveThemeSettings.reduceBrowserActivity ? 120000 : 60000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [effectiveThemeSettings.reduceBrowserActivity])

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    let failureNotified = false
    let activeController: AbortController | undefined

    if (config.bridges.length === 0) {
      const resetTimer = window.setTimeout(() => {
        setBridgeState({ activityScope: bridgeActivityScope(config), activityEpoch: 0, updatedLabel: '--:--:--', cards: [] })
      }, 0)
      return () => window.clearTimeout(resetTimer)
    }

    const refreshBridgeCards = async () => {
      if (document.hidden || cancelled || activeController) return
      const controller = new AbortController()
      activeController = controller
      const timeout = window.setTimeout(() => controller.abort(), BRIDGE_REFRESH_TIMEOUT_MS)
      try {
        const next = await fetchBridgeCards(config, controller.signal)
        if (!cancelled) {
          acceptBridgeSnapshot(applyBridgeConnectionOverrides(next))
          setBridgeStatusLoaded(true)
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
              audioMeter: null,
            })),
          }))
        }
      } finally {
        window.clearTimeout(timeout)
        activeController = undefined
        if (!cancelled) {
          timer = window.setTimeout(
            refreshBridgeCards,
            failureNotified ? BRIDGE_REFRESH_ERROR_BACKOFF_MS : (effectiveThemeSettings.reduceBrowserActivity ? 5000 : BRIDGE_REFRESH_MS),
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
      activeController?.abort()
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [config, applyBridgeConnectionOverrides, acceptBridgeSnapshot, effectiveThemeSettings.reduceBrowserActivity])

  useEffect(() => {
    let cancelled = false
    let previous = ''
    let requestSequence = 0
    let appliedSequence = 0
    let timer: number | undefined
    const tick = async () => {
      const started = performance.now()
      const sequence = ++requestSequence
      try {
        const cards = await fetchFastNextModeActivity()
        if (cancelled || sequence < appliedSequence) return
        appliedSequence = sequence
        const fingerprint = JSON.stringify(cards)
        fastNextModeActivityRef.current = cards
        if (fingerprint === previous) return
        previous = fingerprint
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      } catch { /* full bridge and connection status remain the fallback */ }
      finally {
        if (!cancelled) timer = window.setTimeout(() => void tick(), document.hidden ? 1000 : Math.max(0, FAST_ACTIVITY_POLL_MS - (performance.now() - started)))
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [config, applyBridgeConnectionOverrides])

  useEffect(() => {
    let cancelled = false
    let previous = ''
    let requestSequence = 0
    let appliedSequence = 0
    let timer: number | undefined
    const tick = async () => {
      const started = performance.now()
      const sequence = ++requestSequence
      try {
        const cards = await fetchFastYsfNetActivity()
        // The 100 ms poll may overlap when a request is slow. Never let an
        // older Relay response arrive after and overwrite a newer direct EOT.
        // A real re-key is a newer request and is therefore applied at once.
        if (cancelled || sequence < appliedSequence) return
        appliedSequence = sequence
        const fingerprint = JSON.stringify(cards)
        fastYsfNetActivityRef.current = cards
        if (fingerprint === previous) return
        previous = fingerprint
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      } catch { /* full bridge status remains available */ }
      finally {
        if (!cancelled) timer = window.setTimeout(() => void tick(), document.hidden ? 1000 : Math.max(0, FAST_ACTIVITY_POLL_MS - (performance.now() - started)))
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [config, applyBridgeConnectionOverrides])

  useEffect(() => {
    let cancelled = false
    let previous = false
    let appliedEpochMs = 0
    let timer: number | undefined
    const tick = async () => {
      const started = performance.now()
      try {
        const relay = await fetchZelloFastRelay()
        // This poll is single-flight because the producer timestamp has
        // millisecond precision. Sequential observations disambiguate two
        // legitimate opposite edges that happen within the same millisecond.
        if (cancelled || relay.updatedEpochMs <= 0 || relay.updatedEpochMs < appliedEpochMs) return
        appliedEpochMs = relay.updatedEpochMs
        const localRows = connectionRowsRef.current.filter((row) => !row.configuredTransport)
        const localRow = localRows.find((row) => row.node === config.node) || localRows[0]
        const asteriskRelayActive = localRow?.state === 'talking' || localRow?.state === 'both'
        // A producer edge leads the slower Asterisk snapshot briefly. After
        // that grace period, require live transport evidence so an old true
        // file cannot survive a Zello process restart indefinitely.
        const edgeAgeMs = Date.now() - relay.updatedEpochMs
        const active = relay.active && (asteriskRelayActive || (edgeAgeMs >= 0 && edgeAgeMs <= 3000))
        const wasReady = zelloFastRelayReadyRef.current
        zelloFastRelayReadyRef.current = true
        if (active === previous && wasReady) return
        previous = active
        zelloFastRelayRef.current = active
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      } catch { /* full bridge status remains available */ }
      finally {
        if (!cancelled) timer = window.setTimeout(() => void tick(), document.hidden ? 1000 : Math.max(0, FAST_ACTIVITY_POLL_MS - (performance.now() - started)))
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [config, applyBridgeConnectionOverrides])

  useEffect(() => {
    let cancelled = false
    let previous = ''
    let requestSequence = 0
    let appliedSequence = 0
    let timer: number | undefined
    const tick = async () => {
      const started = performance.now()
      const sequence = ++requestSequence
      try {
        const cards = await fetchFastStandardActivity()
        if (cancelled || sequence < appliedSequence) return
        appliedSequence = sequence
        const fingerprint = JSON.stringify(cards)
        fastStandardActivityRef.current = cards
        if (fingerprint === previous) return
        previous = fingerprint
        setBridgeState((current) => applyBridgeConnectionOverrides(current))
      } catch { /* full bridge status remains available if the fast feed fails */ }
      finally {
        if (!cancelled) timer = window.setTimeout(() => void tick(), document.hidden ? 1000 : Math.max(0, FAST_ACTIVITY_POLL_MS - (performance.now() - started)))
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [config, applyBridgeConnectionOverrides])


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
          setFavoriteFileInfo({ exists: next.fileExists, readable: next.fileReadable, path: next.filePath, bytes: next.fileBytes })
          attempts = 0
          failureNotified = false
          if (next.selectedFile !== selectedFavoriteFile) {
            setSelectedFavoriteFile(next.selectedFile)
          }
        }
      } catch (error) {
        if (cancelled) return
        attempts += 1
        if (!failureNotified) {
          appendNodeMessage(error instanceof Error ? error.message : FAVORITES_LOAD_ERROR)
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

  // #317: reading saved policy must not trigger native bridge reconciliation.
  const zelloRosterExpanded = bridgeClientsOpen.has('zello')
  useEffect(() => {
    if (!authStatus.isAdmin || !zelloRosterExpanded) return
    let active = true
    const refresh = async () => {
      try {
        const response = await fetch(asrPath('asr-api.php') + '?action=global-ban-saved-rules', { credentials: 'same-origin', cache: 'no-store' })
        const payload = await response.json()
        if (!response.ok || payload.ok !== true || !Array.isArray(payload.rules)) throw new Error('Saved policy unavailable')
        if (active) setZelloSavedBanRules(payload.rules)
      } catch { if (active) setZelloSavedBanRules(null) }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [authStatus.isAdmin, zelloRosterExpanded])

  useEffect(() => {
    if (!authStatus.isAdmin || (!zelloRosterExpanded && !banAdminOpen)) return
    let active = true
    const refresh = async () => {
      try {
        const bans = await exactZelloBanPolicy()
        if (active) setExactZelloBans(bans)
      } catch { if (active) setExactZelloBans(null) }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [authStatus.isAdmin, zelloRosterExpanded, banAdminOpen])

  useEffect(() => {
    if (!banAdminOpen || !authStatus.isAdmin) return
    const refresh = async () => {
      try {
        const result = await fetchGlobalBans()
        setGlobalBanEnforcement(result)
        setGlobalBanRules(result.rules)
        setGlobalBans(result.bans)
        setGlobalBanAudit(result.audit)
      } catch { setGlobalBanEnforcement(null) /* Keep rules, but do not claim stale enforcement. */ }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => window.clearInterval(timer)
  }, [banAdminOpen, authStatus.isAdmin])

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
    setFavoriteSort((current) => nextSort(current, key))
  }

  async function reloadFavorites() {
    const next = await fetchFavorites(selectedFavoriteFile)
    setFavorites(next.rows)
    setFavoriteFiles(next.files)
    setFavoriteFileInfo({ exists: next.fileExists, readable: next.fileReadable, path: next.filePath, bytes: next.fileBytes })
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

  useEffect(() => {
    if (!favoriteStatus) return
    const timer = window.setTimeout(() => setFavoriteStatus(''), 3000)
    return () => window.clearTimeout(timer)
  }, [favoriteStatus])

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

  function activateDashboardModuleDrag(
    key: DashboardModuleKey,
    handle: HTMLButtonElement,
    pointerId: number,
    x: number,
    y: number,
  ) {
    handle.setPointerCapture(pointerId)
    dashboardModuleOverRef.current = key
    dashboardModuleOrderRef.current = dashboardModuleOrder
    dashboardDragStartOrderRef.current = [...dashboardModuleOrder]
    dashboardDragPointerRef.current = { x, y }
    setDashboardModuleDragging(key)
    setDashboardModuleOver(key)
  }

  function clearPendingDashboardDrag() {
    const pending = pendingDashboardDragRef.current
    if (!pending) return
    window.clearTimeout(pending.timer)
    pendingDashboardDragRef.current = null
  }

  function beginDashboardModuleDrag(key: DashboardModuleKey, event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return
    clearPendingDashboardDrag()
    if (event.pointerType === 'touch' && window.innerWidth < 1200) {
      const handle = event.currentTarget
      const pointerId = event.pointerId
      const startX = event.clientX
      const startY = event.clientY
      const timer = window.setTimeout(() => {
        const pending = pendingDashboardDragRef.current
        if (!pending || pending.pointerId !== pointerId || pending.handle !== handle) return
        pendingDashboardDragRef.current = null
        activateDashboardModuleDrag(key, handle, pointerId, startX, startY)
      }, TOUCH_MODULE_DRAG_DELAY_MS)
      pendingDashboardDragRef.current = { key, pointerId, startX, startY, handle, timer }
      return
    }
    event.preventDefault()
    activateDashboardModuleDrag(key, event.currentTarget, event.pointerId, event.clientX, event.clientY)
  }

  function updateDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    const pending = pendingDashboardDragRef.current
    if (pending?.pointerId === event.pointerId) {
      const distance = Math.hypot(event.clientX - pending.startX, event.clientY - pending.startY)
      if (distance > TOUCH_MODULE_DRAG_CANCEL_DISTANCE_PX) clearPendingDashboardDrag()
      return
    }
    if (!dashboardModuleDragging) return
    event.preventDefault()
    dashboardDragPointerRef.current = { x: event.clientX, y: event.clientY }
    updateDashboardDropTarget(event.clientX, event.clientY)
    scheduleDashboardDragAutoScroll()
  }

  function clearDashboardModuleDrag() {
    clearPendingDashboardDrag()
    stopDashboardDragAutoScroll()
    dashboardModuleOverRef.current = null
    setDashboardModuleDragging(null)
    setDashboardModuleOver(null)
  }

  function endDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (pendingDashboardDragRef.current?.pointerId === event.pointerId) {
      clearPendingDashboardDrag()
      return
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    if (dashboardModuleDragging) writeDashboardModuleOrder(dashboardModuleOrderRef.current)
    dashboardDragStartOrderRef.current = null
    clearDashboardModuleDrag()
  }

  function cancelDashboardModuleDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (pendingDashboardDragRef.current?.pointerId === event.pointerId) {
      clearPendingDashboardDrag()
      return
    }
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
        <span className="allscan-grip-dots" aria-hidden="true">
          <i /><i /><i /><i /><i /><i />
        </span>
      </button>
    )
  }

  function closeFavoritesDetails() {
    setFavoriteInventory(null)
    setFavoriteFileView(null)
    setFavoriteFileEditing(false)
  }

  async function inspectFavoritesSources() {
    closeFavoritesDetails()
    try {
      const result = await inspectFavoritesFiles()
      setFavoriteInventory(result.files)
    } catch (error) {
      setFavoriteStatus(error instanceof Error ? error.message : 'Unable to inspect Favorites sources.')
    }
  }

  async function showFavoritesSource() {
    closeFavoritesDetails()
    try {
      const file = await viewFavoritesFile(selectedFavoriteFile)
      setFavoriteFileView(file)
      setFavoriteFileDraft(file.content)
      setFavoriteFileEditing(false)
    } catch (error) {
      setFavoriteStatus(error instanceof Error ? error.message : 'Unable to view Favorites source.')
    }
  }

  async function restoreFavoritesBackup(source: string) {
    if (!authStatus.isAdmin || busy) return
    try {
      setBusy(true)
      const preview = await recoverFavoritesFile({ operation: 'preview-recovery', favsfile: selectedFavoriteFile || 'favorites.ini', source })
      if (!window.confirm(`Restore Favorites from ${source}?\n\nBackup: ${preview.bytes ?? '?'} bytes\nCurrent file: ${preview.existingBytes ?? '?'} bytes\n\nThe current file will be backed up first. This replaces its contents.`)) return
      const result = await recoverFavoritesFile({ operation: 'restore-backup', favsfile: selectedFavoriteFile || 'favorites.ini', source, token: preview.token })
      await reloadFavorites()
      await inspectFavoritesSources()
      setFavoriteStatus(result.changed ? `Favorites restored; previous file saved at ${result.backup || 'the Favorites directory'}.` : 'The selected backup already matches the current file.')
    } catch (error) {
      setFavoriteStatus(error instanceof Error ? error.message : 'Unable to restore Favorites backup.')
    } finally { setBusy(false) }
  }

  async function saveFavoritesSource() {
    if (!favoriteFileView || !authStatus.isAdmin || busy) return
    if (!window.confirm(`Save changes to ${favoriteFileView.name}?\n\nThe existing file will be backed up first. Any concurrent edits will cause the save to fail.`)) return
    try {
      setBusy(true)
      const result = await editFavoritesFile(favoriteFileView.name, favoriteFileDraft, favoriteFileView.hash)
      await reloadFavorites()
      await showFavoritesSource()
      setFavoriteFileEditing(false)
      setFavoriteStatus(result.changed ? `Favorites saved; original backed up at ${result.backup || 'the Favorites directory'}.` : 'No Favorites changes needed.')
    } catch (error) {
      setFavoriteStatus(error instanceof Error ? error.message : 'Unable to save Favorites file.')
    } finally { setBusy(false) }
  }

  function stopFavoriteDragScroll() {
    favoriteDragActiveRef.current = false
    if (favoriteDragScrollFrameRef.current !== null) {
      window.cancelAnimationFrame(favoriteDragScrollFrameRef.current)
      favoriteDragScrollFrameRef.current = null
    }
  }

  function runFavoriteDragScroll() {
    favoriteDragScrollFrameRef.current = null
    if (!favoriteDragActiveRef.current) return
    const y = favoriteDragPointerRef.current.y
    const edge = Math.min(120, Math.max(70, window.innerHeight * .14))
    // Native HTML drag events often stop near the browser's top chrome, so the
    // upward pointer cannot reach the same edge depth as the downward pointer.
    // Apply equal full-speed scrolling within the edge bands in either direction.
    const delta = y < edge ? -26 : y > window.innerHeight - edge ? 26 : 0
    if (delta) {
      const before = window.scrollY
      window.scrollBy(0, delta)
      if (window.scrollY !== before) {
        favoriteDragScrollFrameRef.current = window.requestAnimationFrame(runFavoriteDragScroll)
      }
    }
  }

  function updateFavoriteDragPointer(y: number) {
    if (!favoriteDragActiveRef.current) return
    favoriteDragPointerRef.current.y = y
    if (favoriteDragScrollFrameRef.current === null) {
      favoriteDragScrollFrameRef.current = window.requestAnimationFrame(runFavoriteDragScroll)
    }
  }

  favoriteDragUpdateRef.current = updateFavoriteDragPointer

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
    setConnectionSort((current) => nextSort(current, key))
  }

  function isProtectedBanConnection(row: LiveConnectionRow) {
    const protectedIdentities = new Set(config.protectedBanIdentities.map((value) => value.toUpperCase()))
    const rowNode = row.node.trim().toUpperCase()
    const infoTokens = row.info.toUpperCase().split(/[^A-Z0-9_./-]+/).filter(Boolean)
    return protectedIdentities.has(rowNode) || infoTokens.some((value) => protectedIdentities.has(value))
  }

  // A display-only name has no verified cross-protocol identity mapping.
  // Offer only explicitly scoped, saved policy, never a claimed global ban.
  function exactZelloBanSelect(identity: string) {
    if (exactZelloBans === null || !/^[A-Z0-9 @._#/+:-]{1,48}$/.test(identity)) return null
    if (exactZelloBans.some(ban => ban.rule === identity && (!ban.expiresAt || ban.expiresAt > clock.getTime() / 1000))) {
      return <span className="asr-zello-moderation-status" data-status="banned" title="Zello-only policy saved; live enforcement unverified; no linked radio callsign">Ban saved</span>
    }
    return <select value="" aria-label={`Ban ${identity} (Zello only)`} title="Zello-only saved policy; no linked radio callsign" disabled={banAdminBusy} onChange={(event) => {
      const duration = event.target.value as GlobalBanDuration
      if (duration) void changeExactZelloBan('ban', identity, duration)
    }}>
      <option value="" disabled>Ban</option>
      {GLOBAL_BAN_DURATIONS.map(option => <option key={option.value} value={option.value}>{option.label} (Zello only)</option>)}
    </select>
  }

  async function changeExactZelloBan(verb: 'ban' | 'unban', identity: string, duration: GlobalBanDuration = 'permanent') {
    if (!authStatus.isAdmin || banAdminBusy) return
    try {
      setBanAdminBusy(true)
      setExactZelloBans(await exactZelloBanPolicy(verb, identity, duration))
      setBanAdminStatus(`${identity}: Zello-only policy ${verb === 'ban' ? 'saved' : 'removed'}. Live enforcement not verified; no linked radio callsign.`)
    } catch (error) {
      setBanAdminStatus(error instanceof Error ? error.message : 'Exact Zello policy update failed.')
    } finally { setBanAdminBusy(false) }
  }

  function globalBanDurationSelect(identity: string, exactZello = false) {
    // A Global Ban rule is any ASR identity (callsign, M17 base call, Zello
    // username up to 15 characters). M17/YSF suffixes such as KE7WIL-02 or
    // KE7WIL/P ban the base callsign, which the reflector proxies match.
    const shown = (exactZello ? identity : identity.replace(/\s+[A-Z]$/i, '')).trim().toUpperCase()
    const suffixed = /^([A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7})[-/][A-Z0-9]{1,4}$/.exec(shown)
    const callsign = !exactZello && suffixed ? suffixed[1] : shown
    if (!/^[A-Z0-9][A-Z0-9_./-]{0,14}$/.test(callsign)) return exactZello ? exactZelloBanSelect(callsign) : null
    const banned = globalBanRules.some((rule) => rule.endsWith('*') ? callsign.startsWith(rule.slice(0, -1)) : callsign === rule)
    if (banned) return null
    return <select value="" aria-label={`Ban ${callsign}`} disabled={banAdminBusy} onChange={(event) => {
      const duration = event.target.value as GlobalBanDuration
      if (duration) void changeGlobalBan('ban', callsign, duration, exactZello)
    }}>
      <option value="" disabled>Ban</option>
      {GLOBAL_BAN_DURATIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  }

  function openParticipantBan(row?: LiveConnectionRow) {
    const identity = row ? identityFromConnection(row) : undefined
    const callsign = identity?.callsign || ''
    const aslKind: AslBanKind | undefined = identity?.network === 'allstar' && identity.nodeNumber
      ? 'node'
      : identity?.network === 'echolink' && callsign
        ? 'echolink-call'
        : identity?.network === 'iax' && callsign
          ? 'allstar-call'
          : undefined
    const value = aslKind === 'node' ? identity?.nodeNumber || '' : callsign
    if (row && isProtectedBanConnection(row)) {
      setBanStatus('This installed service bridge is protected and cannot be banned.')
      setRowActions(null)
      return
    }
    if (row && !value) {
      setBanStatus('This connection does not expose a verified AllStar or EchoLink identity.')
      setRowActions(null)
      return
    }
    setBanDialog({ row, callsign, value, aslKind })
    setBanDuration('1h')
    setBanStatus('')
    setRowActions(null)
  }

  async function loadAslBans() {
    if (!authStatus.isAdmin) return
    try {
      const [result, native] = await Promise.all([fetchGlobalBans(), fetchAslBans()])
      setGlobalBanEnforcement(result)
      setGlobalBanRules(result.rules)
      setZelloSavedBanRules(result.rules)
      setGlobalBans(result.bans)
      setGlobalBanAudit(result.audit)
      setAslBans(native.bans)
      const echoPending = native.applied.pending?.echolink
      setAslEnforcementStatus(echoPending ? `AllStar applied; EchoLink pending: ${echoPending}` : 'AllStar / EchoLink policy loaded')
      setAslExternal({
        allstar: native.applied.externalAst || [],
        echolink: native.applied.externalEcho || [],
      })
      setBanStatus('')
    } catch (error) {
      setGlobalBanEnforcement(null)
      setBanStatus(error instanceof Error ? error.message : 'Global Ban list could not be loaded.')
    }
  }

  async function submitParticipantBan() {
    if (!authStatus.isAdmin || !banDialog || banBusy) return
    const value = banDialog.value.trim().toUpperCase()
    try {
      setBanBusy(true)
      setBanStatus(banDialog.aslKind ? 'Applying native connection ban…' : 'Applying Global Ban…')
      if (banDialog.aslKind) {
        const result = await changeAslBan('ban', banDialog.aslKind, value, banDuration)
        setAslBans(result.bans)
        setAslExternal({ allstar: result.applied.externalAst || [], echolink: result.applied.externalEcho || [] })
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
                entry.label.toUpperCase().includes(banDialog.row!.node.toUpperCase())
                || entry.label.toUpperCase().includes(banDialog.callsign))
              if (!client) throw new Error('Active named client was not found.')
              await dropClientChannel(client.channel)
            }
            disconnectStatus = ' Current connection was disconnected.'
          } catch {
            disconnectStatus = ' The native deny is active; the session may already be gone.'
          }
        }
        setBanDialog(null)
        setBanStatus(`Banned ${banDialog.aslKind === 'node' ? 'AllStar node' : banDialog.aslKind === 'echolink-call' ? 'EchoLink callsign' : 'AllStar callsign'} ${value}.${disconnectStatus}`)
        return
      }
      if (!/^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/.test(value)) throw new Error('Enter a valid callsign. Every ASR ban is global.')
      if (banDialog.row && isProtectedBanConnection(banDialog.row)) throw new Error('This installed service bridge is protected and cannot be banned.')
      const result = await updateGlobalBan('ban', value, banDuration)
      setGlobalBanEnforcement(result)
      setGlobalBanRules(result.rules)
      setZelloSavedBanRules(result.rules)
      setGlobalBans(result.bans)
      setGlobalBanAudit(result.audit)
      setAslEnforcementStatus(result.asl?.status === 'applied' ? 'AllStar / EchoLink policy applied' : `AllStar / EchoLink policy pending: ${result.asl?.error || 'Adapter unavailable'}`)
      if (!result.verified || result.asl?.applied?.unmappedGlobalRules?.includes(value)) {
        throw new Error(`Global Ban was saved, but full enforcement is pending: ${globalBanPendingReason(result, 'The callsign could not be mapped.')}`)
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
      setBanStatus(`Saved Global Ban for ${value}; supported backend enforcement verified for ${GLOBAL_BAN_DURATIONS.find((option) => option.value === banDuration)?.label || banDuration}.${disconnectStatus}`)
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
      const result = await updateGlobalBan('unban', rule)
      setGlobalBanEnforcement(result)
      setGlobalBanRules(result.rules)
      setZelloSavedBanRules(result.rules)
      setGlobalBans(result.bans)
      setGlobalBanAudit(result.audit)
      setBanStatus(result.verified ? `Removed global ban for ${rule}.` : `Global Ban removal is pending full enforcement: ${globalBanPendingReason(result, 'Retrying.')}`)
    } catch (error) {
      setBanStatus(error instanceof Error ? error.message : 'Global unban failed.')
    } finally { setBanBusy(false) }
  }

  async function removeAslBan(ban: AslBan) {
    if (!authStatus.isAdmin || banBusy) return
    try {
      setBanBusy(true)
      const result = await changeAslBan('unban', ban.kind, ban.value)
      setAslBans(result.bans)
      setAslExternal({ allstar: result.applied.externalAst || [], echolink: result.applied.externalEcho || [] })
      setBanStatus(`Removed ${ban.kind === 'node' ? 'AllStar node' : ban.kind === 'echolink-call' ? 'EchoLink callsign' : 'AllStar callsign'} ban for ${ban.value}.`)
    } catch (error) {
      setBanStatus(error instanceof Error ? error.message : 'Native connection unban failed.')
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
    // A configured Standard doorway must use its dedicated persistent link
    // control rather than a generic node command from Favorites or shortcuts.
    if (action === 'connect' || action === 'disconnect') {
      const configured = config.bridges.filter((bridge) => bridge.node === node && (bridge.cardType ?? 'standard') === 'standard')
      if (configured.length) {
        const card = configured.length === 1 ? bridgeState.cards.find((item) => item.id === configured[0].id) : undefined
        if (card?.standardLinkControl) {
          await controlStandardLink(card, action)
          return
        }
        if (configured.length !== 1 || configured.some((bridge) => bridge.manualDisconnect) || card?.manualDisconnect) {
          appendNodeMessage('Use the dedicated Standard Bridge link control to change a saved manual disconnect.')
          return
        }
        if (action === 'disconnect') {
          appendNodeMessage('Persistent manual disconnect is unavailable for this Standard Bridge. Review Bridge Diagnostics.')
          return
        }
      }
    }
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
    const deadline = Date.now() + timeoutMs
    let last = applyBridgeConnectionOverrides(await fetchBridgeCards(
      config,
      AbortSignal.timeout(Math.min(5_000, timeoutMs)),
    ))
    while (true) {
      const card = last.cards.find((item) => item.id === bridgeId)
      const confirmed = card
        && card.controlLinked === linked
        && (!linked || !destination || card.currentDestination === destination)
      if (confirmed) {
        acceptBridgeSnapshot(last)
        return card
      }
      const remaining = deadline - Date.now()
      if (remaining <= 0) break
      await new Promise<void>((resolve) => window.setTimeout(resolve, Math.min(250, remaining)))
      const requestBudget = Math.max(250, Math.min(5_000, deadline - Date.now()))
      last = applyBridgeConnectionOverrides(await fetchBridgeCards(
        config,
        AbortSignal.timeout(requestBudget),
      ))
    }
    acceptBridgeSnapshot(last)
    throw new Error('The bridge command completed, but canonical status did not confirm it in time.')
  }

  function beginBridgeControl(owner: string) {
    if (bridgeControlBusyRef.current) return false
    bridgeControlBusyRef.current = owner
    setBridgeControlBusy(owner)
    return true
  }

  function endBridgeControl(owner: string) {
    if (bridgeControlBusyRef.current !== owner) return
    bridgeControlBusyRef.current = ''
    setBridgeControlBusy('')
  }

  async function controlStandardLink(card: BridgeCardView, verb: 'connect' | 'disconnect') {
    const owner = `standard-link-${card.id}`
    if (!authStatus.canModify || !card.standardLinkControl || !beginBridgeControl(owner)) return
    try {
      const result = await setStandardBridgeLink(card.id, verb)
      appendNodeMessage(result.message)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'Standard Bridge link control failed.')
    } finally {
      try { acceptBridgeSnapshot(applyBridgeConnectionOverrides(await fetchBridgeCards(config, AbortSignal.timeout(10_000)))) }
      catch { /* Regular polling will refresh status. */ }
      endBridgeControl(owner)
    }
  }

  async function repairBridge(card: BridgeCardView) {
    const owner = `repair-${card.id}`
    if (!card.repairAvailable || !beginBridgeControl(owner)) return
    if (!window.confirm(`Restart only the approved ${card.title} service chain? Other bridges will not be restarted.`)) {
      endBridgeControl(owner)
      return
    }
    try {
      const result = await repairStandardBridge(card.id)
      appendNodeMessage(result.message)
      await new Promise<void>((resolve) => window.setTimeout(resolve, 1500))
      acceptBridgeSnapshot(applyBridgeConnectionOverrides(await fetchBridgeCards(config, AbortSignal.timeout(10_000))))
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'Bridge repair failed.')
    } finally {
      endBridgeControl(owner)
    }
  }

  async function manageM17Qualification(
    bridgeId: string,
    verb: 'start' | 'complete' | 'cancel',
  ) {
    const owner = `m17-qualification-${bridgeId}`
    if (!authStatus.canModify || !beginBridgeControl(owner)) return
    if (verb === 'start' && !window.confirm(
      'Start a bounded M17 qualification session? Send and receive real M17 audio, then select Complete qualification.',
    )) {
      endBridgeControl(owner)
      return
    }
    if (verb === 'cancel' && !window.confirm('Cancel M17 qualification and stop its unqualified runtime?')) {
      endBridgeControl(owner)
      return
    }
    try {
      const result = await updateM17Qualification(bridgeId, verb)
      appendNodeMessage(
        result.qualificationState === 'qualified'
          ? 'M17 qualification verified fresh reflector, AllStar, RX audio, and TX audio evidence.'
          : result.qualificationState === 'qualifying'
            ? 'M17 qualification is active. Verify live audio in both directions, then complete qualification.'
            : 'M17 qualification was cancelled.',
      )
      window.setTimeout(() => window.location.reload(), 700)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'M17 qualification action failed.')
    } finally {
      endBridgeControl(owner)
    }
  }

  async function runNetBridgeModeSwitch(initialMode: string, owner: string) {
    let targetMode = initialMode
    setNetBridgeModeError('')
    try {
      while (targetMode) {
        const result = await activateNetBridgeMode(targetMode)
        const selected = String(result.mode || '').toLowerCase()
        if (selected !== targetMode || result.node !== 1999) throw new Error('Net Bridge mode control returned an invalid result.')
        activeNetBridgeModeRef.current = selected
        acceptBridgeSnapshot(applyBridgeConnectionOverrides(await fetchBridgeCards(
          config,
          AbortSignal.timeout(10_000),
        )))
        const pendingMode = pendingNetBridgeModeRef.current
        pendingNetBridgeModeRef.current = ''
        if (pendingMode && pendingMode !== selected) {
          targetMode = pendingMode
          continue
        }
        setNetBridgeMode(selected)
        setNetBridgeModeError('')
        appendNodeMessage(`Net Bridge switched to ${selected.toUpperCase()}.`)
        break
      }
    } catch (error) {
      setNetBridgeMode(activeNetBridgeModeRef.current)
      const message = error instanceof Error ? error.message : 'Net Bridge mode switch failed.'
      setNetBridgeModeError(message)
      appendNodeMessage(message)
      throw error
    } finally {
      pendingNetBridgeModeRef.current = ''
      endBridgeControl(owner)
    }
  }

  async function selectNetBridgeMode(mode: string) {
    if (!authStatus.canModify || mode === netBridgeMode) return
    setNetBridgeModeError('')
    // Qualification must be reachable before the guarded M17 mode switch.
    if (mode === 'm17' && m17NetConfig && !m17NetConfig.m17AudioQualified
        && m17NetConfig.m17QualificationState !== 'qualifying') {
      await manageM17Qualification(m17NetConfig.id, 'start')
      return
    }
    setNetBridgeMode(mode)
    const owner = 'net-bridge-mode'
    if (bridgeControlBusyRef.current === owner) {
      pendingNetBridgeModeRef.current = mode
      return
    }
    if (!beginBridgeControl(owner)) {
      setNetBridgeMode(activeNetBridgeModeRef.current)
      return
    }
    try {
      const operation = runNetBridgeModeSwitch(mode, owner)
      modeSwitchPromiseRef.current = operation
      await operation
    } catch {
      // runNetBridgeModeSwitch reports the actionable error.
    } finally {
      modeSwitchPromiseRef.current = null
    }
  }

  async function waitForNetBridgeOwner(card: BridgeCardView) {
    if (modeSwitchPromiseRef.current) await modeSwitchPromiseRef.current
    if (activeNetBridgeModeRef.current !== card.mode.toLowerCase()) {
      throw new Error(`${card.mode.toUpperCase()} did not become the confirmed Net Bridge owner.`)
    }
  }

  async function connectDmrNetCard(card: BridgeCardView) {
    if (!authStatus.canModify) return
    const talkgroup = String(dmrTalkgroupInputs[card.id] || '').trim()
    if (!/^\d{1,8}$/.test(talkgroup) || Number(talkgroup) < 1 || Number(talkgroup) > 16777215) {
      appendNodeMessage('Enter a valid DMR talkgroup.')
      return
    }
    if (Number(talkgroup) === 4000) {
      appendNodeMessage('Use Disconnect instead of entering TG 4000.')
      return
    }
    if (pendingConnectBridgeRef.current) return
    pendingConnectBridgeRef.current = card.id
    setBridgeControlAction('connect')
    let acquired = false
    try {
      await waitForNetBridgeOwner(card)
      acquired = beginBridgeControl(card.id)
      if (!acquired) throw new Error('Bridge control is busy. Try Connect again in a moment.')
      await connectBridge(card.id, talkgroup)
      const canonical = await confirmBridgeControlState(card.id, true, talkgroup)
      setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: '' }))
      appendNodeMessage(`DMR Net Bridge connected to ${canonical.currentDestinationLabel || `TG ${canonical.currentDestination}`}.`)
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'DMR Net Bridge connection failed.')
    } finally {
      if (acquired) endBridgeControl(card.id)
      if (pendingConnectBridgeRef.current === card.id) pendingConnectBridgeRef.current = ''
      setBridgeControlAction('')
    }
  }

  async function disconnectDmrNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || !beginBridgeControl(card.id)) return

    try {
      setBridgeControlAction('disconnect')
      await disconnectBridge(card.id)
      await confirmBridgeControlState(card.id, false)
      setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: '' }))
      appendNodeMessage('DMR Net Bridge disconnected.')
    } catch (error) {
      appendNodeMessage(error instanceof Error ? error.message : 'DMR Net Bridge disconnect failed.')
    } finally {
      endBridgeControl(card.id)
      setBridgeControlAction('')
    }
  }

  async function connectReflectorNetCard(card: BridgeCardView) {
    if (!authStatus.canModify) return
    const reflectorInput = String(bridgeDestinationInputs[card.id] || '').trim().toUpperCase()
    const reflector = card.cardType === 'm17_net' && /^[A-Z0-9]{3}$/.test(reflectorInput)
      ? `M17-${reflectorInput}`
      : reflectorInput
    const module = String(m17ModuleInputs[card.id] || config.bridges.find((bridge) => bridge.id === card.id)?.m17Module || 'A').trim().toUpperCase()
    const destination = card.cardType === 'm17_net' ? `${reflector} ${module}` : reflector
    if (!destination) {
      appendNodeMessage(card.cardType === 'ysf_net'
        ? 'Enter an exact YSF reflector name or ID.'
        : `Enter a valid ${card.mode.toUpperCase()} destination.`)
      return
    }
    if ((card.cardType === 'p25_net' || card.cardType === 'nxdn_net') && !/^\d{1,8}$/.test(reflector)) {
      appendNodeMessage(`Enter a numeric ${card.mode.toUpperCase()} destination.`)
      return
    }
    if (card.cardType === 'm17_net' && (!/^M17-[A-Z0-9]{3}$/.test(reflector) || !/^[A-Z]$/.test(module))) {
      appendNodeMessage('Enter an M17 reflector as XXX or M17-XXX and a module A-Z.')
      return
    }
    if (pendingConnectBridgeRef.current) return
    pendingConnectBridgeRef.current = card.id
    setBridgeControlAction('connect')
    let acquired = false
    try {
      await waitForNetBridgeOwner(card)
      acquired = beginBridgeControl(card.id)
      if (!acquired) throw new Error('Bridge control is busy. Try Connect again in a moment.')
      const result = await connectBridge(card.id, destination)
      const canonicalId = String(result.currentDestination || '').trim()
      if (!canonicalId || canonicalId.length > 80) {
        throw new Error(`${card.title} returned an invalid canonical destination.`)
      }
      if (card.cardType === 'p25_net' || card.cardType === 'nxdn_net') {
        setBridgeDestinationInputs((current) => ({ ...current, [card.id]: '' }))
        appendNodeMessage(card.cardType === 'p25_net'
          ? `${card.title} selected Talkgroup ${canonicalId}.`
          : `${card.title} selected ${canonicalId}.`)
        // The controller response already confirms that the destination was
        // selected and the AllStar transport was handled.  Do not keep the
        // Connect button busy while a separate status refresh waits on every
        // bridge backend; the regular single-flight poll will reconcile the
        // card, and this bounded refresh merely makes that happen sooner.
        void fetchBridgeCards(config, AbortSignal.timeout(BRIDGE_REFRESH_TIMEOUT_MS))
          .then((next) => acceptBridgeSnapshot(applyBridgeConnectionOverrides(next)))
          .catch(() => undefined)
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
      if (acquired) endBridgeControl(card.id)
      if (pendingConnectBridgeRef.current === card.id) pendingConnectBridgeRef.current = ''
      setBridgeControlAction('')
    }
  }

  async function disconnectReflectorNetCard(card: BridgeCardView) {
    if (!authStatus.canModify || !beginBridgeControl(card.id)) return

    try {
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
      endBridgeControl(card.id)
      setBridgeControlAction('')
    }
  }

  async function changeGlobalBan(verb: 'ban' | 'unban', rule: string, duration: GlobalBanDuration = 'permanent', exactZello = false) {
    if (!authStatus.isAdmin || banAdminBusy) return
    const normalized = rule.trim().toUpperCase()
    if (!(exactZello ? /^[A-Z0-9][A-Z0-9_./-]{0,14}$/ : /^[A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}$/).test(normalized)) { setBanAdminStatus('Enter a valid identity. Every ASR ban is global.'); return }
    try {
      setBanAdminBusy(true)
      const result = await updateGlobalBan(verb, normalized, duration)
      setGlobalBanEnforcement(result)
      setGlobalBanRules(result.rules)
      setZelloSavedBanRules(result.rules)
      setGlobalBans(result.bans)
      setGlobalBanAudit(result.audit)
      setGlobalBanRule('')
      const action = verb === 'ban' ? 'added to' : 'removed from'
      const durationLabel = verb === 'ban' ? ` for ${GLOBAL_BAN_DURATIONS.find((option) => option.value === duration)?.label || duration}` : ''
      const removal = verb === 'ban' && result.removed > 0 ? ` ${result.removed} active backend session${result.removed === 1 ? ' was' : 's were'} removed immediately.` : ''
      const aslLimit = !result.verified ? ` Full enforcement pending: ${globalBanPendingReason(result, 'full enforcement not verified')}.` : ''
      const tgif = result.tgif
      const tgifNote = !tgif || tgif.status === 'unavailable' || tgif.status === 'not-configured' ? ''
        : tgif.status !== 'applied' ? ` TGIF talkgroup ban list was not updated: ${(tgif.errors || [])[0] || tgif.error || 'TGIF did not answer'}.`
        : tgif.noDmrId?.includes(normalized) ? ' TGIF has no DMR ID for this callsign, so the TGIF talkgroup ban list was not changed.'
        : verb === 'ban' ? ` Also banned on TGIF talkgroup ${tgif.talkgroup || ''}.`.replace(' .', '.')
        : ` Removed from the TGIF talkgroup ${tgif.talkgroup || ''} ban list.`.replace('  ', ' ')
      setBanAdminStatus(`${normalized} was ${action} the ASR Global Ban List${durationLabel}${result.verified ? ' and the saved logical/backend state was verified' : ''}.${removal}${aslLimit}${tgifNote}`)
    } catch (error) {
      setBanAdminStatus(error instanceof Error ? error.message : 'Global Ban update failed.')
    } finally { setBanAdminBusy(false) }
  }

  async function kickBridgeConnectedClient(callsign: string, bridgeId: string, mode: string, clientId = '') {
    if (!authStatus.isAdmin || banAdminBusy) return
    const bridge = config.bridges.find((item) => item.id === bridgeId)
    if (!bridge) { setBanAdminStatus('Bridge configuration is unavailable.'); return }
    try {
      setBanAdminBusy(true)
      setBanAdminStatus(`Kicking ${callsign} from ${mode.toUpperCase()}…`)
      const result: { verified: boolean; removed: number; tgif?: TgifKickResult } = mode.toUpperCase() === 'ZELLO'
        ? await kickZelloClient(callsign)
        : await kickStandaloneClient(bridgeId, callsign, clientId)
      if (!result.verified || result.removed < 1) throw new Error('Kick was not verified by the active backend.')
      const tgif = result.tgif
      if (tgif?.ok) {
        // #134: DMR Kick also disconnects the station from the TGIF talkgroup.
        // It is not a ban: the station may reconnect at once.
        const blocked = !tgif.released && tgif.seconds ? ` TGIF still holds it out for up to ${Math.max(1, Math.round(tgif.seconds / 60))} minutes.` : ' It can reconnect right away.'
        setBanAdminStatus(`${callsign} was disconnected from TGIF${tgif.talkgroup ? ` talkgroup ${tgif.talkgroup}` : ''}.${blocked}`)
      } else if (tgif) {
        setBanAdminStatus(`${callsign}'s audio is blocked on this node, but TGIF did not disconnect the station: ${tgif.error || 'unknown TGIF error'}`)
      } else if (mode.toUpperCase() === 'ZELLO') {
        setBanAdminStatus(`${callsign} is muted on this Zello bridge for one minute. Rekeying does not end the mute.`)
      } else {
        setBanAdminStatus(`${callsign} was kicked from ${mode.toUpperCase()} and backend enforcement was verified. The client may reconnect immediately unless banned.`)
      }
    } catch (error) {
      setBanAdminStatus(error instanceof Error ? error.message : 'Bridge client Kick failed.')
    } finally { setBanAdminBusy(false) }
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
              onChange={(event) => { setFavorites([]); setSelectedFavoriteFile(event.target.value) }}
            >
              {favoriteFiles.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </form>
          <div className="allscan-favorites-tools">
            {authStatus.canModify ? <button type="button" onClick={() => void inspectFavoritesSources()}>Find backups</button> : null}
            <button type="button" disabled={busy || !selectedFavoriteFile} onClick={() => void showFavoritesSource()}>View file</button>
            <a href={`${asrPath('asr-api.php')}?action=favorites-download&favsfile=${encodeURIComponent(selectedFavoriteFile)}`} onClick={closeFavoritesDetails} download={selectedFavoriteFile || 'favorites.ini'}>Download file</a>
            <button type="button" disabled={busy || !authStatus.canModify} onClick={() => {
              closeFavoritesDetails()
              if (window.confirm('WARNING: Reset the saved custom order for this Favorites list?\n\nThis cannot be undone. Descriptions and colors will stay unchanged.')) void favoriteOperation('reset-order')
            }}><RotateCcw /> Reset order</button>
            <button type="button" disabled={busy || !authStatus.canModify} onClick={() => {
              closeFavoritesDetails()
              if (window.confirm('WARNING: Reset all Favorite colors for this list?\n\nThis cannot be undone. Descriptions and order will stay unchanged.')) void favoriteOperation('reset-appearance')
            }}><RotateCcw /> Reset colors</button>
          </div>
        </div>
        {favoriteInventory ? <div className="allscan-favorites-source-view">
          <div className="allscan-favorites-detail-heading"><strong>Available Favorites files and backups</strong><button type="button" onClick={() => setFavoriteInventory(null)}>Close</button></div>
          <p>Selected source: <code>{favoriteFileInfo.path || 'Not located'}</code> — {favoriteFileInfo.exists === false ? 'Missing' : favoriteFileInfo.readable === false ? 'Unreadable' : favoriteFileInfo.bytes != null ? `${favoriteFileInfo.bytes} bytes` : 'Size unavailable'}.</p>
          <p>These files are listed for recovery only. Nothing is changed unless you choose Restore.</p>
          {favoriteInventory.length ? <ul className="allscan-favorites-inventory-list">{favoriteInventory.map((item) => <li key={item.path}><code>{item.path}</code><span>{item.exists ? item.readable ? `${item.bytes ?? '?'} bytes` : 'Unreadable' : 'Missing'}{item.symlink ? ' · Link' : ''}</span>{authStatus.isAdmin && canPreviewFavoriteBackup(item) && (!selectedFavoriteFile || favoriteFileInfo.path?.startsWith('/etc/allscan/')) ? <button type="button" disabled={busy} onClick={() => void restoreFavoritesBackup(item.path)}>Restore…</button> : null}</li>)}</ul> : <p>No matching files found.</p>}
        </div> : null}
        {favoriteFileView ? <div className="allscan-favorites-source-view">
          <div className="allscan-favorites-detail-heading"><strong>Viewing {favoriteFileView.name}</strong><button type="button" onClick={() => { setFavoriteFileView(null); setFavoriteFileEditing(false) }}>Close</button></div>
          {favoriteFileEditing && authStatus.isAdmin && favoriteFileView.path.startsWith('/etc/allscan/') ? <>
            <p>Editing Favorites file — changes are not applied until you save.</p>
            <textarea aria-label="Edit Favorites file contents" value={favoriteFileDraft} onChange={(event) => setFavoriteFileDraft(event.target.value)} rows={14} className="allscan-favorites-file-editor" />
            <div className="allscan-favorites-editor-actions">
              <button type="button" disabled={busy || favoriteFileDraft === favoriteFileView.content} onClick={() => void saveFavoritesSource()}>Save Favorites file (creates backup)</button>
              <button type="button" onClick={() => { setFavoriteFileDraft(favoriteFileView.content); setFavoriteFileEditing(false) }}>Cancel editing</button>
            </div>
          </> : <>
            <p>File contents (read-only). Select Edit to make changes.</p>
            <pre tabIndex={0} role="region" aria-label="Favorites file contents (read only)" className="allscan-favorites-file-preview">{favoriteFileView.content}</pre>
            {authStatus.isAdmin && favoriteFileView.path.startsWith('/etc/allscan/') ?
              <button type="button" className="allscan-favorites-edit-button" onClick={() => { setFavoriteFileDraft(favoriteFileView.content); setFavoriteFileEditing(true) }}>Edit Favorites file</button> : null}
          </>}
        </div> : null}
        {favoriteStatus ? <p className="allscan-favorites-status" role="status">{favoriteStatus}</p> : null}

        <div className="allscan-favorites-table-wrap" onDragOver={(event) => { if (favoriteDragActiveRef.current) { event.preventDefault(); updateFavoriteDragPointer(event.clientY) } }} onDrop={() => stopFavoriteDragScroll()}>
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
              const displayColor = favoriteColorOpen === favorite.node
                ? (favoriteColorDrafts[favorite.node] || favorite.color)
                : favorite.color
              return (
              <tr
                key={favorite.node}
                draggable={false}
                onDragOver={(event) => { event.preventDefault(); updateFavoriteDragPointer(event.clientY) }}
                onDrop={(event) => {
                  stopFavoriteDragScroll()
                  event.preventDefault()
                  const sourceNode = event.dataTransfer.getData('text/plain') || favoriteDragNode
                  if (sourceNode) void reorderFavorite(sourceNode, favorite.node)
                  setFavoriteDragNode(null)
                }}
                style={displayColor ? { '--asr-favorite-tab-color': displayColor, borderInlineStartColor: displayColor } as React.CSSProperties : undefined}
              >
                <td className={[favCellClass, scanning ? 'allscan-fav-scanning' : ''].filter(Boolean).join(' ') || undefined}>
                  <button type="button" className="allscan-favorite-drag" draggable={authStatus.canModify} aria-label={`Move Favorite ${favorite.node}`} title="Drag to reorder"
                    onDragStart={(event) => {
                      event.stopPropagation()
                      setFavoriteDragNode(favorite.node)
                      favoriteDragActiveRef.current = true
                      updateFavoriteDragPointer(event.clientY)
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
                    onDragEnd={() => { stopFavoriteDragScroll(); setFavoriteDragNode(null) }}><span className="allscan-grip-dots" aria-hidden="true"><i /><i /><i /><i /><i /><i /></span></button>
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
                          <a href={favorite.href} target="_blank" rel="noreferrer" onClick={(event) => {
                            // Favorites supplied by AllScan may link to an external node-status
                            // page. Open the same native bubble chart as Connection Status.
                            if (/^https?:\/\/stats\.allstarlink\.org\/stats\/\d+(?:[/?#]|$)/i.test(favorite.href) && /^\d{3,7}$/.test(favorite.node)) {
                              event.preventDefault()
                              setBubbleNode(favorite.node)
                            }
                          }}>{favorite.description || favorite.name || <em>No description</em>}</a>
                        ) : (favorite.description || favorite.name || <em>No description</em>)}
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
                        <div className="allscan-favorite-color-hex">
                          <span>Hex</span>
                          <code>{(favoriteColorDrafts[favorite.node] || favorite.color || '#4aa3df').toUpperCase()}</code>
                        </div>
                        <span className="allscan-favorite-color-field" aria-label="Color palette"
                          onPointerDown={(event) => {
                            event.preventDefault()
                            event.stopPropagation()
                            event.currentTarget.setPointerCapture(event.pointerId)
                            const hex = pickFavoriteHex(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect(), favoriteColorHue)
                            setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: hex }))
                          }}
                          onPointerMove={(event) => {
                            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
                            event.preventDefault()
                            event.stopPropagation()
                            const hex = pickFavoriteHex(event.clientX, event.clientY, event.currentTarget.getBoundingClientRect(), favoriteColorHue)
                            setFavoriteColorDrafts((current) => ({ ...current, [favorite.node]: hex }))
                          }}
                          onPointerUp={(event) => { event.preventDefault(); event.stopPropagation(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
                          onPointerCancel={(event) => { event.stopPropagation(); if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) }}
                        style={{ backgroundColor: `hsl(${favoriteColorHue} 100% 50%)` }}><span className="allscan-favorite-color-field-white" /><span className="allscan-favorite-color-field-black" /></span>
                        <input className="allscan-favorite-hue" aria-label="Hue" type="range" min="0" max="360" value={(360 - favoriteColorHue) % 360} onChange={(event) => {
                          const slider = Number(event.target.value)
                          const hue = (360 - slider) % 360
                          setFavoriteColorHue(hue)
                          const hex = hslToFavoriteHex(hue, 1, 0.5)
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

  // Do not render protected dashboard content while authentication is unknown
  // or a login redirect is pending (issue #111).
  if (!authChecked || !authStatus.canRead) {
    return (
      <main className="min-h-screen bg-[#151515] text-[#eaf4f8] flex items-center justify-center px-6"
        role="status" aria-live="polite" aria-busy="true">
        <p>{authChecked ? 'Redirecting to login…' : 'Verifying dashboard access…'}</p>
      </main>
    )
  }


  return (
    <div className="allscan-app min-h-screen bg-[#151515] text-[#eaf4f8]">
      <div className="w-full px-0 pb-6">
        <header className="allscan-header">
          <div className="allscan-brand">
            <a className="allscan-brand-main" href={asrPath()} aria-label="Return to main ASR dashboard">
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
                    const isLcarsDesktop = effectiveThemeSettings.theme === 'lcars-frame' && desktopThemeViewport
                    if (isLcarsDesktop && key === 'admin') {
                      const closeCurrentMenu = menuOpen && openSubmenu === null
                      setMenuOpen(!closeCurrentMenu)
                      setOpenSubmenu(null)
                      return
                    }
                    const closeCurrentMenu = menuOpen && openSubmenu === key
                    setMenuOpen(!closeCurrentMenu)
                    setOpenSubmenu(closeCurrentMenu ? null : key)
                  }}
                >
                  {key === 'admin' && effectiveThemeSettings.theme === 'lcars-frame' && desktopThemeViewport ? 'Menu' : label} <ChevronDown className="h-3 w-3" />
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
                  <button
                    type="button"
                    role="menuitem"
                    className="allscan-menu-proxy-row allscan-menu-report-row"
                    onClick={() => { setMenuOpen(false); setOpenSubmenu(null); setSupportFeedbackOpen(true) }}
                  >
                    <span>Support &amp; Feedback</span>
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="allscan-menu-proxy-row allscan-menu-support-row"
                    onClick={() => { setMenuOpen(false); setOpenSubmenu(null); setDonateAsrOpen(true) }}
                  >
                    <span>Donate to ASR</span>
                  </button>
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
                  {authStatus.loggedIn ? <a role="menuitem" href={asrPath('asr-settings/?section=account')} onClick={() => setMenuOpen(false)}>My Account</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('asr-settings/')} onClick={() => setMenuOpen(false)}>Settings</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('cfg/')} onClick={() => setMenuOpen(false)}>Manage Favorites</a> : null}
                  {authStatus.isAdmin ? <a role="menuitem" href={asrPath('asr-instructions/')} onClick={() => setMenuOpen(false)}>Help &amp; Instructions</a> : null}
                  {authStatus.isAdmin ? <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setOpenSubmenu(null); setUpdateDialogView('update'); setUpdateDialogOpen(true) }}>Update ASR</button> : null}
                  <a role="menuitem" href={`http://stats.allstarlink.org/stats/${config.node}`} onClick={() => setMenuOpen(false)}>Node Status</a>
                  {authStatus.canWrite ? <button type="button" role="menuitem" onClick={restartAsterisk}>Restart Asterisk</button> : null}
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
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    aria-checked={effectiveThemeSettings.reduceBrowserActivity ? 'true' : 'false'}
                    className={effectiveThemeSettings.reduceBrowserActivity ? 'is-selected' : undefined}
                    onClick={() => updateTheme({ reduceBrowserActivity: !effectiveThemeSettings.reduceBrowserActivity })}
                  >
                    Reduce browser activity
                  </button>
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
          {config.resourceWarnings?.length ? (
            <aside className="allscan-resource-notice" role="alert" aria-live="polite" aria-label="Node resource warning">
              <div><strong>Node resource warning</strong>{config.resourceWarnings.map((warning) => <span key={warning}>{warning}</span>)}</div>
            </aside>
          ) : null}
          {authStatus.loggedIn && config.tgifSessionRequired && tgifUserStatus && (!tgifUserStatus.configured || tgifUserStatus.reauthRequired) ? (
            <aside className="allscan-tgif-notice" role="alert" aria-live="polite" aria-label="Node-wide TGIF sign-in required">
              <div className="allscan-tgif-notice-copy">
                <strong>Node-wide TGIF sign-in required</strong>
                <span>{authStatus.isAdmin
                  ? 'The DMR bridge can remain connected, but ASR cannot show TGIF connected users until an administrator signs this node in again. Enter the TGIF callsign, website password, and session talkgroup, then complete the CAPTCHA if TGIF requests one.'
                  : 'The DMR bridge can remain connected, but ASR cannot show TGIF connected users until a node administrator signs this node in again.'}</span>
              </div>
              {authStatus.isAdmin ? <a href={asrPath('asr-settings/?section=bridges&tgif=login')}>Sign in this node</a> : null}
            </aside>
          ) : null}
          {releaseStatus?.updateAvailable && releaseStatus.availableVersion !== dismissedUpdateVersion ? (
            <aside
              className="allscan-update-notice"
              role="status"
              aria-live="polite"
              aria-label="AllScan Reimagined update available"
            >
              <div className="allscan-update-notice-copy">
                <strong>ASR update available: {releaseStatus.availableLabel}</strong>
                <span>
                  This node has {releaseStatus.installedLabel}. Administrators can install this update here in the browser; nothing will install automatically.
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
                {authStatus.isAdmin ? (
                  <button type="button" onClick={() => { setUpdateDialogView('update'); setUpdateDialogOpen(true) }}>Update in Browser</button>
                ) : null}
                <button type="button" onClick={() => {
                  writeDismissedUpdateVersion(releaseStatus.availableVersion)
                  setDismissedUpdateVersion(releaseStatus.availableVersion)
                }}>Remind me with the next version</button>
                {releaseStatus.releaseUrl ? (
                  <a href={releaseStatus.releaseUrl} target="_blank" rel="noreferrer">Release details</a>
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
                  <div className="allscan-talker-card-title">{index === 0 ? 'Current Talker' : `Last Talker ${index} · ${talker ? talkerLastHeard(talker, index) : '--:--'}`}</div>
                  <div className="allscan-talker-callsign">{talker ? (talker.info || talker.node) : '\u00a0'}</div>
                  <div className="allscan-talker-source">{talker ? `${talker.source}${talker.node ? ` · ${talker.networkNode || talker.node}` : ''}` : (index === 0 ? 'No one talking' : 'No recent talker')}</div>
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
                      onChange={(event) => {
                        const action = event.target.value as NodeControlAction
                        setActionValue(action)
                        writeActionPreference(action)
                      }}
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
                {authStatus.isAdmin ? <button type="button" className="allscan-action-button allscan-kicks-bans-button" onClick={() => { setManagementTab('clients'); setBanAdminOpen(true); void loadAslBans() }}>Manage Kicks & Bans</button> : null}
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
                    onChange={(event) => {
                      const checked = event.target.checked
                      setPermanent(checked)
                      writePermanentPreference(checked)
                    }}
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
                      className="allscan-action-button allscan-custom-commands-button"
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
                                  disabled={busy || !authStatus.canModify}
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
                      const displayName = connectionName(row)
                      const isLocalRow = row.node === config.node
                      const configuredBridgeState = row.bridgeId
                        ? bridgeConnectionStates.byId.get(row.bridgeId)
                        : bridgeConnectionStates.byNode.get(row.node)
                      const statusUnknown = rows.some((item) => item.node === config.node && item.state === 'unknown')
                      const displayState = isLocalRow
                        ? row.state
                        : statusUnknown
                          ? 'unknown'
                        : row.state === 'talking'
                          ? row.state
                          : configuredBridgeState || row.state
                      const canUseRowNode = !row.configuredTransport && canPopulateNodeControl(row.node)
                      const canViewNetworkMap = canUseRowNode && !row.bridgeId && !config.bridges.some((bridge) => bridge.node === row.node)
                        && /^[0-9]{3,7}$/.test(row.node) && !/^100[1-6]$/.test(row.node)
                        && !/^3\d{6}$/.test(row.node) && !/echolink|bridge|broadcastify|digital|transport/i.test(`${row.mode} ${row.info}`)
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
                                {canViewNetworkMap ? (
                                  <a
                                    className="allscan-connection-network-map-link"
                                    href={asrPath()}
                                    onClick={(event) => { event.preventDefault(); setBubbleNode(row.node) }}
                                    title={displayName !== row.info ? `Original node info: ${row.info} — View network bubble chart for AllStar node ${row.node}` : `View network bubble chart for AllStar node ${row.node}`}
                                    aria-label={`View network bubble chart for AllStar node ${row.node}${row.info ? `: ${row.info}` : ''}${displayName !== row.info ? ` (Favorite: ${displayName})` : ''}`}
                                  >{displayName || `View network map for ${row.node}`}</a>
                                ) : displayName}
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

          {rowActions ? createPortal(
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
                {isBannableConnection(rowActions.row) && !isProtectedBanConnection(rowActions.row) ? <button type="button" role="menuitem" onClick={() => openParticipantBan(rowActions.row)}>Ban…</button> : null}
              </div>
            </>, document.body
          ) : null}

          {bridgeState.cards.length || banAdminOpen ? <section
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
              const managedCards = bridgeState.cards.filter((card) => config.bridges.find((bridge) => bridge.id === card.id)?.adminCapabilities?.clientAdmin.includes('listBans'))
              const managedConnectedCards = bridgeState.cards.filter((card) => config.bridges.find((bridge) => bridge.id === card.id)?.adminCapabilities?.clientAdmin.includes('listClients'))
              const zelloTalkers = managedCards
                .filter((card) => card.mode.toUpperCase() === 'ZELLO')
                .flatMap((card) => card.detailRows
                  .filter((detail) => !detail.empty)
                  .map((detail) => ({ card, identity: detail.label.trim().toUpperCase(), active: card.status === 'Source/TX' && card.lastCaller.trim().toUpperCase() === detail.label.trim().toUpperCase() })))
              if (!banAdminOpen || !authStatus.isAdmin) return null
              return <>
                {banAdminOpen && authStatus.isAdmin ? createPortal(<div className="allscan-ban-admin-modal" role="dialog" aria-modal="true" aria-label="Manage Kicks & Bans" onMouseDown={(event) => { if (event.target === event.currentTarget) setBanAdminOpen(false) }}><div className="allscan-ban-admin-panel"><div className="allscan-ban-admin-modal-head"><strong>Manage Kicks & Bans</strong><button type="button" className="allscan-ban-admin-close" aria-label="Close Manage Kicks & Bans" onClick={() => setBanAdminOpen(false)}>×</button></div><div className="allscan-management-tabs" role="tablist" aria-label="Connection management"><button type="button" role="tab" aria-selected={managementTab === 'clients'} onClick={() => setManagementTab('clients')}>Connected clients</button><button type="button" role="tab" aria-selected={managementTab === 'bans'} onClick={() => { setManagementTab('bans'); void loadAslBans() }}>Bans</button></div>{managementTab === 'clients' ? <>
                  <div className="allscan-ban-admin-scope"><strong>Client administration</strong><span>Global Ban saves a callsign policy; only supported backends can enforce it. Protected service and health-probe identities cannot be banned. Kick affects only the selected current bridge session. DMR Kick stops local forwarding until reconnect; it does not remove the upstream TGIF subscriber.</span></div>
                  <div className="allscan-ban-admin-tools">
                    <label>Find connected client<input value={banClientFilter} onChange={(event) => setBanClientFilter(event.target.value.toUpperCase())} placeholder="Callsign or mode" maxLength={32} /></label>
                    <label>Ban callsign<input value={globalBanRule} onChange={(event) => setGlobalBanRule(event.target.value.toUpperCase())} placeholder="Callsign" maxLength={12} disabled={banAdminBusy} /></label>
                    <label>Ban duration<select value={globalBanDuration} disabled={banAdminBusy} onChange={(event) => setGlobalBanDuration(event.target.value as GlobalBanDuration)}>{GLOBAL_BAN_DURATIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                    <button type="button" disabled={banAdminBusy || !globalBanRule.trim()} onClick={() => void changeGlobalBan('ban', globalBanRule, globalBanDuration)}>Ban</button>
                  </div>
                  <div className="allscan-ban-admin-clients"><b>AllStar / EchoLink connections</b>{sortedConnectionRows.filter((row) => row.state !== 'message' && isBannableConnection(row) && !isProtectedBanConnection(row) && row.node !== config.node && !row.bridgeId && !config.bridges.some((bridge) => bridge.node === row.node) && (!banClientFilter.trim() || `${row.node} ${row.info} ${row.mode}`.toUpperCase().includes(banClientFilter.trim().toUpperCase()))).map((row, index) => <span key={`${row.node}-${row.mode}-${index}`}><code>{row.node}</code><small>{row.info} · {row.mode}</small><span className="allscan-bridge-client-admin-actions"><button type="button" onClick={() => { if (/^[0-9]{3,10}$/.test(row.node)) void runCommandForNode('disconnect', row.node); else { setBanAdminOpen(false); setDropClientOpen(true); void loadDropClients() } }}>{/^[0-9]{3,10}$/.test(row.node) ? 'Disconnect' : 'Drop Client…'}</button>{isBannableConnection(row) && !isProtectedBanConnection(row) ? <button type="button" onClick={() => { setBanAdminOpen(false); openParticipantBan(row) }}>Ban…</button> : null}</span></span>)}</div>
                  <div className="allscan-ban-admin-clients"><b>Digital bridge clients</b>{managedConnectedCards.filter((managedCard) => managedCard.mode.toUpperCase() !== 'ZELLO').flatMap((managedCard) => { const bridgeConfig = config.bridges.find((bridge) => bridge.id === managedCard.id); const clientAdmin = bridgeConfig?.adminCapabilities?.clientAdmin || []; const canKick = clientAdmin.includes('kickClient'); const canBan = clientAdmin.includes('banClient'); return managedCard.detailRows.filter((detail) => !detail.empty).map((detail) => ({ ...detail, mode: managedCard.mode, bridgeId: managedCard.id, canKick, canBan })) }).filter((detail) => !banClientFilter.trim() || `${detail.label} ${detail.mode}`.toUpperCase().includes(banClientFilter.trim().toUpperCase())).map((detail) => { const callsign = detail.label.replace(/\s+[A-Z]$/i, '').trim().toUpperCase(); const banned = globalBanRules.some((rule) => rule.endsWith('*') ? callsign.startsWith(rule.slice(0, -1)) : callsign === rule); return <span key={`${detail.mode}-${detail.key}`}><code>{detail.label}</code><small>{detail.mode.toUpperCase()}{banned ? ` · ${globalBanScope(globalBanEnforcement)}` : ''}</small><span className="allscan-bridge-client-admin-actions">{detail.canKick ? <button type="button" disabled={banAdminBusy} onClick={() => void kickBridgeConnectedClient(callsign, detail.bridgeId, detail.mode, detail.clientId)}>Kick</button> : null}{detail.canBan && !banned ? globalBanDurationSelect(callsign) : null}</span></span> })}</div>
                  {zelloTalkers.length ? <div className="allscan-ban-admin-clients"><b>Recent Zello Talkers</b>{zelloTalkers.filter(({ identity }) => !banClientFilter.trim() || `${identity} ZELLO`.includes(banClientFilter.trim().toUpperCase())).map(({ card, identity, active }) => { const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id); const canKick = bridgeConfig?.adminCapabilities?.clientAdmin.includes('kickClient') === true; const banned = globalBanRules.some((rule) => rule.endsWith('*') ? identity.startsWith(rule.slice(0, -1)) : identity === rule); return <span key={`zello-${card.id}-${identity}`}><code>{identity}</code><small>ZELLO · {active ? 'CURRENT TRANSMISSION' : 'RECENT TALKER'}{banned ? ` · ${globalBanScope(globalBanEnforcement)}` : ''}</small><span className="allscan-bridge-client-admin-actions">{canKick ? <button type="button" title="Mute this Zello talker on this bridge for one minute" disabled={banAdminBusy || !/^[A-Z0-9 @._#/+:-]{1,48}$/.test(identity)} onClick={() => void kickBridgeConnectedClient(identity, card.id, card.mode)}>Kick</button> : null}{banned ? null : globalBanDurationSelect(identity, true)}</span></span> })}</div> : null}
                  </> : <div className="allscan-management-bans">
                    <button type="button" className="allscan-action-button" onClick={() => { setBanAdminOpen(false); openParticipantBan() }}>Add ban…</button>
                    {aslEnforcementStatus ? <p role="status">{aslEnforcementStatus}</p> : null}
            <div className="allscan-connection-ban-list">
              <strong>Global callsigns</strong>
              {globalBans.length ? globalBans.map((ban) => <div key={`global-${ban.rule}`}>
                <strong>{ban.rule}</strong><span>{globalBanScope(globalBanEnforcement)} · {formatGlobalBanRemaining(ban.expiresAt, clock)} · {formatGlobalBanExpiration(ban.expiresAt)}</span>
                <button type="button" disabled={banBusy} onClick={() => void removeGlobalBan(ban.rule)}>Unban</button>
              </div>) : <p>No global bans.</p>}
              {exactZelloBans !== null ? <>
                <strong>Zello-only exact-name policies (no linked callsign)</strong>
                {exactZelloBans.length ? exactZelloBans.map(ban => <div key={`zello-exact-${ban.rule}`}>
                  <strong>{ban.rule}</strong><span>Saved policy; live enforcement not verified · {formatGlobalBanRemaining(ban.expiresAt, clock)} · {formatGlobalBanExpiration(ban.expiresAt)}</span>
                  <button type="button" disabled={banAdminBusy} onClick={() => void changeExactZelloBan('unban', ban.rule)}>Unban</button>
                </div>) : <p>No exact Zello policies.</p>}
              </> : null}
              <strong>AllStar / EchoLink native bans</strong>
              {aslBans.length ? aslBans.map((ban) => <div key={`asl-${ban.kind}-${ban.value}`}>
                <strong>{ban.value}</strong><span>{ban.kind === 'node' ? 'AllStar node' : ban.kind === 'echolink-call' ? 'EchoLink callsign' : 'AllStar callsign'} · {formatGlobalBanRemaining(ban.expiresAt, clock)} · {formatGlobalBanExpiration(ban.expiresAt)}</span>
                <button type="button" disabled={banBusy} onClick={() => void removeAslBan(ban)}>Unban</button>
              </div>) : <p>No native connection bans.</p>}
              {aslExternal.allstar.length || aslExternal.echolink.length ? <>
                <strong>Linux / manual restrictions (read only)</strong>
                {aslExternal.allstar.map((value) => <div key={`external-node-${value}`}><strong>{value}</strong><span>AllStar · manual</span></div>)}
                {aslExternal.echolink.map((value) => <div key={`external-echo-${value}`}><strong>{value}</strong><span>EchoLink · manual</span></div>)}
              </> : null}
            </div>
                    {globalBanAudit.length ? <div className="allscan-management-history"><strong>Recent bridge actions</strong>{[...globalBanAudit].reverse().slice(0, 12).map((event, index) => <p key={`${event.timestamp}-${index}`}>{event.action.toUpperCase()} · {event.rule || event.callsign || "—"} · {new Date(event.timestamp * 1000).toLocaleString()}</p>)}</div> : null}
                    {banStatus ? <p role="status">{banStatus}</p> : null}
                    <button type="button" className="allscan-action-button" onClick={() => void loadAslBans()}>Refresh</button>
                  </div>}
                  {banAdminStatus ? <div className="allscan-ban-admin-status" role="status">{banAdminStatus}</div> : null}
                </div></div>, document.body) : null}
              </>
            })()}

            <div className="allscan-bridge-grid">
              {orderedVisibleBridgeCards(config.bridges, bridgeState.cards, netBridgeMode, config.bridgeOrderVersion).map((card) => {
                const bridgeConfig = config.bridges.find((bridge) => bridge.id === card.id)
                const bridgeCapabilities = bridgeConfig?.adminCapabilities?.bridgeControl || []
                const canConnect = bridgeCapabilities.includes('connect')
                const canDisconnect = bridgeCapabilities.includes('disconnect')
                const canChangeDestination = bridgeCapabilities.includes('changeDestination')
                // Standard cards use the AllStar transport independently of
                // external digital-network reachability. IN and OUT are both
                // established transports; direction only identifies who linked.
                const allstarTransportLinked = card.cardType === 'standard' && (
                  card.allstarLinked || rows.some(
                    (row) => (row.bridgeId === card.id || row.node === card.node)
                      && row.state !== 'message',
                  )
                )
                const bridgeLinked = card.cardType !== 'standard'
                  ? card.controlLinked
                  : allstarTransportLinked
                const standardLinkMissing = card.cardType === 'standard'
                  && /^\d{3,10}$/.test(card.node)
                  && card.node !== config.node
                  && connectionFeedReady
                  && !bridgeLinked
                const dashboardStatusLabel = bridgeDashboardStatusLabel(card)
                const dashboardStatusClass = bridgeStatusClasses[card.status]
                // Node 1999 has one control-plane owner. Draft fields remain
                // editable during an operation; only actions are serialized.
                const modeSwitchBusy = bridgeControlBusy === 'net-bridge-mode'
                const bridgeActionBusy = Boolean(bridgeControlBusy && !modeSwitchBusy)
                const connectPending = bridgeControlAction === 'connect'
                const destinationInput = bridgeDestinationInputs[card.id] || ''
                const dmrTalkgroupCandidate = dmrTalkgroupInputs[card.id] || ''
                const validDmrTalkgroup = /^\d{1,8}$/.test(dmrTalkgroupCandidate)
                  && Number(dmrTalkgroupCandidate) >= 1
                  && Number(dmrTalkgroupCandidate) <= 16777215
                  && Number(dmrTalkgroupCandidate) !== 4000
                const m17ModuleInput = m17ModuleInputs[card.id] ?? String(bridgeConfig?.m17Module || 'A')
                const m17QualificationState = bridgeConfig?.m17QualificationState || 'not_qualified'
                const connectDestination = destinationInput.trim()
                return (
                <article
                  key={card.id}
                  data-bridge-mode={card.mode.toLowerCase()}
                  className={`allscan-bridge-card${card.cardType !== 'standard' ? ' allscan-net-bridge-card' : ''}${bridgeClientsOpen.has(card.id) || bridgeHistoryOpen.has(card.id) ? ' is-detail-expanded' : ''} ${bridgeRoleClasses[card.status]}`}
                >
                  <div className="allscan-bridge-head">
                    <span className="allscan-bridge-head-title">{card.cardType !== 'standard' ? 'Net Bridge' : config.node === '674982' ? ({ '1001': 'DMR TGIF Bridge', '1002': 'YSF Bridge', '1003': 'P25 Bridge', '1004': 'M17 Bridge', '1005': 'NXDN Bridge', '1006': 'Zello Bridge' } as Record<string, string>)[card.node] || card.title : card.title}</span>
                    <span className="allscan-bridge-head-actions">
                      <button
                        type="button"
                        className={`allscan-bridge-status-control${card.healthSeverity ? ' has-health-issue' : ''}`}
                        aria-label={standardLinkMissing ? `Warning: AllStar node ${card.node} is not linked` : card.healthSeverity ? `${bridgeHealthLabels[card.healthSeverity]}: ${card.healthIssues.join(' ')}` : `Status: ${dashboardStatusLabel}`}
                        title={standardLinkMissing ? `AllStar node ${card.node} is not linked` : card.healthSeverity ? card.healthIssues.join('\n') : dashboardStatusLabel}
                        onClick={() => {
                          if (card.healthSeverity && card.healthIssues.length) window.alert(card.healthIssues.join('\n'))
                        }}
                      >
                        {standardLinkMissing ? <AlertTriangle className="allscan-bridge-health-triangle" aria-hidden="true" /> : card.healthSeverity ? <AlertTriangle className={`allscan-bridge-health-triangle ${bridgeHealthTriangleClasses[card.healthSeverity]}`} aria-hidden="true" /> : null}
                        <span className={`allscan-bridge-status rounded-full border ${standardLinkMissing || card.healthSeverity ? pillClasses.neutral : dashboardStatusClass}`}>
                          {standardLinkMissing ? 'NOT LINKED' : card.healthSeverity ? bridgeHealthLabels[card.healthSeverity] : dashboardStatusLabel}
                        </span>
                      </button>
                    </span>
                  </div>

                  {card.cardType === 'standard' && card.standardLinkControl && authStatus.canModify && connectionFeedReady ? (
                    <div className="allscan-bridge-row allscan-bridge-status-row allscan-standard-link-row">
                      <span title={`AllStar link · Node ${card.node}`}>Node {card.node}</span>
                      <i className="allscan-standard-link-dot" aria-hidden="true" />
                      <b data-link-state={bridgeLinked ? 'linked' : 'unlinked'} title={card.manualDisconnect ? (bridgeLinked ? 'Hold conflict · linked' : 'Manually disconnected') : (bridgeLinked ? 'Linked' : 'Unlinked')}>{bridgeLinked ? 'Linked' : 'Unlinked'}</b>
                      <button type="button" className="allscan-action-button" disabled={busy || Boolean(bridgeControlBusy)}
                        onClick={() => void controlStandardLink(card, bridgeLinked ? 'disconnect' : 'connect')}>
                        {bridgeControlBusy === `standard-link-${card.id}` ? 'Working…' : bridgeLinked ? 'Disconnect' : 'Reconnect'}
                      </button>
                    </div>
                  ) : standardLinkMissing && card.manualDisconnect ? (
                    <div className="allscan-bridge-row allscan-bridge-status-row allscan-standard-link-row">
                      <span title={`AllStar link · Node ${card.node}`}>Node {card.node}</span>
                      <i className="allscan-standard-link-dot" aria-hidden="true" />
                      <b data-link-state="unlinked" title="Manually disconnected · control unavailable">Unlinked</b>
                    </div>
                  ) : standardLinkMissing && authStatus.canModify ? (
                    <div className="allscan-bridge-row allscan-bridge-status-row allscan-standard-link-row">
                      <span title={`AllStar link · Node ${card.node}`}>Node {card.node}</span>
                      <i className="allscan-standard-link-dot" aria-hidden="true" />
                      <b data-link-state="unlinked" title="Unlinked">Unlinked</b>
                      <button type="button" className="allscan-action-button" disabled={busy}
                        title={`Reconnect ${card.title} using AllStar node ${card.node}`}
                        onClick={() => void runCommandForNode('connect', card.node, false, false)}>
                        {busy ? 'Working…' : 'Reconnect'}
                      </button>
                    </div>
                  ) : null}

                  <div className="allscan-bridge-body">
                    {card.cardType === 'dmr_net' ? (
                      <div className="allscan-bridge-row allscan-bridge-status-row allscan-bridge-current-row">
                        <span>Current TG</span>
                        <b title={bridgeLinked ? (card.currentDestinationLabel || card.currentTg || undefined) : undefined}>{bridgeLinked ? (card.currentDestinationLabel || card.currentTg || '–') : '–'}</b>
                      </div>
                    ) : null}
                    {card.cardType !== 'standard' && card.cardType !== 'dmr_net' ? (
                      <div className="allscan-bridge-row allscan-bridge-status-row allscan-bridge-current-row">
                        <span>{card.cardType === 'nxdn_net' ? 'Current TG' : 'Current Reflector'}</span>
                        <b title={bridgeLinked ? (card.currentDestinationLabel || card.currentDestination || undefined) : undefined}>{bridgeLinked ? (card.currentDestinationLabel || card.currentDestination || '–') : '–'}</b>
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

                  {card.healthSeverity && card.repairAvailable && authStatus.canModify ? (
                    <div className="allscan-bridge-repair-alert" role="alert">
                      <span>{card.healthIssues[0] || `${card.title} needs attention.`}</span>
                      <button type="button" className="allscan-action-button" disabled={Boolean(bridgeControlBusy)} onClick={() => void repairBridge(card)}>
                        {bridgeControlBusy === `repair-${card.id}` ? 'Repairing…' : 'Repair Bridge'}
                      </button>
                    </div>
                  ) : null}

                  {card.mode.toLowerCase() === 'm17' && bridgeConfig?.backendMode === 'managed' && !bridgeConfig.m17AudioQualified ? (
                    <div className="allscan-bridge-repair-alert" role="status">
                      <span>M17 audio qualification: {m17QualificationState === 'qualifying' ? 'In progress — send and receive live audio' : 'Required'}</span>
                      {authStatus.canModify ? (
                        <span className="allscan-bridge-link-buttons">
                          {m17QualificationState === 'qualifying' ? <>
                            <button type="button" className="allscan-action-button" disabled={Boolean(bridgeControlBusy)}
                              onClick={() => void manageM17Qualification(card.id, 'complete')}>Complete qualification</button>
                            <button type="button" className="allscan-action-button" disabled={Boolean(bridgeControlBusy)}
                              onClick={() => void manageM17Qualification(card.id, 'cancel')}>Cancel</button>
                          </> : (
                            <button type="button" className="allscan-action-button" disabled={Boolean(bridgeControlBusy)}
                              onClick={() => void manageM17Qualification(card.id, 'start')}>Start qualification</button>
                          )}
                        </span>
                      ) : null}
                    </div>
                  ) : null}

                  {card.cardType === 'dmr_net' && authStatus.canModify && (canConnect || canDisconnect || canChangeDestination) ? (
                    <div className="allscan-bridge-controls allscan-net-bridge-controls allscan-net-bridge-dmr">
                      <div className="allscan-bridge-tune allscan-net-bridge-mode-row">
                        <label htmlFor={`net-mode-${card.id}`}>Mode</label>
                        <select id={`net-mode-${card.id}`} value={netBridgeMode} disabled={busy || bridgeActionBusy || connectPending} onChange={(event) => void selectNetBridgeMode(event.target.value)}>
                          {['dmr','ysf','p25','nxdn','m17'].filter((mode) => availableNetBridgeModes.has(mode)).map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
                        </select>
                        <div className="allscan-bridge-link-buttons">
                          <button
                            type="button"
                            className="allscan-action-button allscan-connect-button"
                            disabled={busy || bridgeActionBusy || connectPending || !canConnect || !canChangeDestination || !validDmrTalkgroup}
                            onClick={() => void connectDmrNetCard(card)}
                          >
                            {connectPending ? (modeSwitchBusy ? 'Waiting…' : 'Connecting…') : 'Connect'}
                          </button>
                          <button
                            type="button"
                            className="allscan-action-button allscan-disconnect-button"
                            disabled={busy || Boolean(bridgeControlBusy) || !canDisconnect}
                            onClick={() => void disconnectDmrNetCard(card)}
                          >
                            {bridgeActionBusy && bridgeControlAction === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
                          </button>
                        </div>
                      </div>
                      <div className="allscan-bridge-tune allscan-net-bridge-destination-row">
                        <label htmlFor={`dmr-net-tg-${card.id}`}>TG</label>
                        <input
                          id={`dmr-net-tg-${card.id}`}
                          type="text"
                          inputMode="numeric"
                          autoComplete="off"
                          spellCheck={false}
                          maxLength={8}
                          value={dmrTalkgroupCandidate}
                          placeholder=""
                          onChange={(event) => {
                            const talkgroup = event.target.value.replace(/\D/g, '').slice(0, 8)
                            setDmrTalkgroupInputs((current) => ({ ...current, [card.id]: talkgroup }))
                          }}
                        />
                      </div>
                    </div>
                  ) : null}

                  {card.cardType !== 'standard' && card.cardType !== 'dmr_net' && authStatus.canModify && (canConnect || canDisconnect || canChangeDestination) ? (
                    <div className={`allscan-bridge-controls allscan-net-bridge-controls allscan-net-bridge-${card.mode.toLowerCase()}`}>
                      <div className="allscan-bridge-tune allscan-net-bridge-mode-row">
                        <label htmlFor={`net-mode-${card.id}`}>Mode</label>
                        <select id={`net-mode-${card.id}`} value={netBridgeMode} disabled={busy || bridgeActionBusy || connectPending} onChange={(event) => void selectNetBridgeMode(event.target.value)}>
                          {['dmr','ysf','p25','nxdn','m17'].filter((mode) => availableNetBridgeModes.has(mode)).map((mode) => <option key={mode} value={mode}>{mode.toUpperCase()}</option>)}
                        </select>
                        {card.cardType !== 'm17_net' ? <div className="allscan-bridge-link-buttons">
                          <button
                            type="button"
                            className="allscan-action-button allscan-connect-button"
                            disabled={busy || bridgeActionBusy || connectPending || !canConnect || !canChangeDestination || connectDestination === ''}
                            onClick={() => void connectReflectorNetCard(card)}
                          >
                            {connectPending ? (modeSwitchBusy ? 'Waiting…' : 'Connecting…') : 'Connect'}
                          </button>
                          <button
                            type="button"
                            className="allscan-action-button allscan-disconnect-button"
                            disabled={busy || Boolean(bridgeControlBusy) || !canDisconnect}
                            onClick={() => void disconnectReflectorNetCard(card)}
                          >
                            {bridgeActionBusy && bridgeControlAction === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
                          </button>
                        </div> : null}
                      </div>
                      <div className={`allscan-bridge-tune allscan-net-bridge-destination-row${card.cardType === 'm17_net' ? ' allscan-m17-net-tune' : ''}`}>
                        <label htmlFor={`digital-net-destination-${card.id}`}>{card.cardType === 'm17_net' ? 'Ref' : card.cardType === 'ysf_net' ? 'Reflector' : 'Target'}</label>
                        {card.cardType === 'ysf_net' || card.cardType === 'm17_net' ? (
                          <>
                          <input
                            id={`digital-net-destination-${card.id}`}
                            type="text"
                            autoComplete="off"
                            spellCheck={false}
                            maxLength={card.cardType === 'm17_net' ? 7 : 80}
                            value={destinationInput}
                            placeholder=""
                            onChange={(event) => {
                              setBridgeDestinationInputs((current) => ({
                                ...current,
                                [card.id]: (card.cardType === 'm17_net'
                                  ? event.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 7)
                                  : event.target.value.slice(0, 80)),
                              }))
                            }}
                          />
                          {card.cardType === 'm17_net' ? <>
                            <label htmlFor={`m17-net-module-${card.id}`}>Mod</label>
                            <input
                              id={`m17-net-module-${card.id}`}
                              className="allscan-m17-module-input"
                              type="text"
                              autoComplete="off"
                              spellCheck={false}
                              maxLength={1}
                              value={m17ModuleInput}
                              onChange={(event) => setM17ModuleInputs((current) => ({
                                ...current,
                                [card.id]: event.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 1),
                              }))}
                            />
                          </> : null}
                          </>
                        ) : (
                          <input
                            id={`digital-net-destination-${card.id}`}
                            type="text"
                            inputMode="numeric"
                            autoComplete="off"
                            spellCheck={false}
                            maxLength={8}
                            value={destinationInput}
                            placeholder=""
                            onChange={(event) => {
                              const destination = event.target.value.replace(/\D/g, '').slice(0, 8)
                              setBridgeDestinationInputs((current) => ({
                                ...current,
                                [card.id]: destination,
                              }))
                            }}
                          />
                        )}
                      </div>
                      {card.cardType === 'm17_net' ? <div className="allscan-bridge-link-buttons allscan-m17-link-buttons">
                        <button
                          type="button"
                          className="allscan-action-button allscan-connect-button"
                          disabled={busy || bridgeActionBusy || connectPending || !canConnect || !canChangeDestination || connectDestination === ''}
                          onClick={() => void connectReflectorNetCard(card)}
                        >
                          {connectPending ? (modeSwitchBusy ? 'Waiting…' : 'Connecting…') : 'Connect'}
                        </button>
                        <button
                          type="button"
                          className="allscan-action-button allscan-disconnect-button"
                          disabled={busy || Boolean(bridgeControlBusy) || !canDisconnect}
                          onClick={() => void disconnectReflectorNetCard(card)}
                        >
                          {bridgeActionBusy && bridgeControlAction === 'disconnect' ? 'Disconnecting…' : 'Disconnect'}
                        </button>
                      </div> : null}
                    </div>
                  ) : null}

                  {bridgeCardShowsClientDetails(card.cardType) ? (
                    <div className="allscan-bridge-detail-wrap">
                      <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={bridgeClientsOpen.has(card.id)} onClick={() => setBridgeClientsOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.clientsOpen', JSON.stringify([...next])); return next })}>
                        <span>{compactBridgeDetailTitle(card.detailTitle)}</span><b>{!bridgeStatusLoaded ? '…' : card.detailAvailable ? card.detailCount : '–'}</b><ChevronDown className={bridgeClientsOpen.has(card.id) ? 'is-open' : ''} aria-hidden="true" />
                      </button>
                      {bridgeClientsOpen.has(card.id) ? <div className="allscan-bridge-detail-box asr-bridge-roster">
                        {card.detailRows.map((detail) => {
                          const identity = (card.mode.toUpperCase() === 'ZELLO' ? detail.label : detail.label.replace(/\s+[A-Z]$/i, '')).trim().toUpperCase()
                          const supportsKick = bridgeConfig?.adminCapabilities?.clientAdmin.includes('kickClient') === true
                          const supportsBan = bridgeConfig?.adminCapabilities?.clientAdmin.includes('banClient') === true
                          const isZello = card.mode.toUpperCase() === 'ZELLO'
                          // Zello display names can contain characters the moderation API cannot accept.
                          // Never present an actionable control for an unsupported identity.
                          const zelloBanSupported = !isZello || /^[A-Z0-9][A-Z0-9_./-]{0,14}$/.test(identity)
                          const kickAvailable = supportsKick && (!isZello || /^[A-Z0-9 @._#/+:-]{1,48}$/.test(identity))
                          const banned = (isZello ? zelloSavedBanRules ?? globalBanRules : globalBanRules).some((rule) => rule.endsWith('*') ? identity.startsWith(rule.slice(0, -1)) : identity === rule)
                          return <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}>
                            <div className="allscan-bridge-client-user asr-client-duration-line"><span>{detail.label}</span>{detail.connectedSince && detail.connectionTimeSource ? <span className="asr-client-duration" title={detail.connectionTimeSource === 'native' ? 'Time connected reported by the bridge' : 'Approximate time since ASR first observed this connection'}>{formatConnectionDuration(detail.connectedSince, detail.connectionTimeSource)}</span> : null}</div>
                            {detail.meta && !isZello && compactBridgeDetailTitle(card.detailTitle) !== 'Connected Clients' && <div className="allscan-bridge-client-meta">{detail.meta}</div>}
                            {!detail.empty && authStatus.isAdmin && (supportsKick || supportsBan) ? <div className="allscan-bridge-inline-client-actions">{supportsKick ? <button type="button" title={card.mode.toUpperCase() === 'DMR' ? 'Disconnect this station from TGIF and mute it on this bridge for one minute' : (isZello ? 'Mute this Zello talker on this bridge for one minute' : 'Disconnect this station and refuse it for one minute')} disabled={banAdminBusy || !kickAvailable} aria-label={isZello && !kickAvailable ? 'Kick unavailable for this Zello name' : undefined} onClick={() => void kickBridgeConnectedClient(identity, card.id, card.mode, detail.clientId)}>Kick</button> : null}{supportsBan ? (isZello && banned ? <span className="asr-zello-moderation-status" data-status="banned" title="Saved Global Ban policy; live enforcement is not verified by this read-only status">Ban saved</span> : isZello && !zelloBanSupported ? (exactZelloBanSelect(identity) ?? <span className="asr-zello-moderation-status" data-status="unavailable" title="Exact-name policy unavailable; no linked radio callsign">N/A</span>) : !banned ? globalBanDurationSelect(identity, isZello) : null) : null}</div> : null}
                          </div>
                        })}
                      </div> : null}
                    </div>
                  ) : null}

                  {card.cardType !== 'standard' && card.cardType !== 'm17_net'
                      && m17NetConfig && !m17NetConfig.m17AudioQualified && authStatus.canModify ? (
                    <div className="allscan-bridge-repair-alert" role="status">
                      <span>M17 Net requires live RX/TX qualification before use.</span>
                      <button type="button" className="allscan-action-button" disabled={Boolean(bridgeControlBusy)}
                        onClick={() => void manageM17Qualification(m17NetConfig.id, 'start')}>Qualify M17 Net</button>
                    </div>
                  ) : null}

                  {card.cardType !== 'standard' && netBridgeModeError ? (
                    <div className="allscan-net-bridge-error" role="alert">
                      <strong>Mode change failed.</strong> {netBridgeModeError} The previous mode remains active.
                    </div>
                  ) : null}

                  {card.cardType === 'standard' ? (
                    <div className="allscan-bridge-detail-wrap allscan-bridge-recent">
                      <button type="button" className="allscan-bridge-detail-title allscan-bridge-detail-toggle" aria-expanded={bridgeHistoryOpen.has(card.id)} onClick={() => setBridgeHistoryOpen((value) => { const next = new Set(value); if (next.has(card.id)) next.delete(card.id); else next.add(card.id); localStorage.setItem('asr.bridge.historyOpen', JSON.stringify([...next])); return next })}>
                        <span>Recent Activity</span><b>{card.recentRows.filter((detail) => !detail.empty).length}</b><ChevronDown className={bridgeHistoryOpen.has(card.id) ? 'is-open' : ''} aria-hidden="true" />
                      </button>
                      {bridgeHistoryOpen.has(card.id) ? <div className="allscan-bridge-detail-box asr-bridge-activity-list">
                        {card.recentRows.map((detail) => (
                          <div key={detail.key} className={`allscan-bridge-client${detail.empty ? ' is-empty' : ''}`}>
                            {detail.activityType ? <>
                              <span className="asr-bridge-activity-time">{detail.activityTime}</span>
                              <span className="asr-bridge-activity-label" data-event={detail.activityType}>{detail.activityType}</span>
                              <span className="asr-bridge-activity-detail">{detail.label}{detail.activityDuration ? ` — ${detail.activityDuration}` : ''}</span>
                            </> : <div className="allscan-bridge-client-user"><span>{detail.label}</span></div>}
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
            <h3>{banDialog.aslKind === 'node' ? 'AllStar Node Ban' : banDialog.aslKind === 'echolink-call' ? 'EchoLink Ban' : banDialog.aslKind === 'allstar-call' ? 'AllStar Callsign Ban' : 'Global Ban'}</h3>
            {banDialog.row ? <p>{banDialog.row.node} · {banDialog.row.info}</p> : <p>Add a global ban for a disconnected callsign.</p>}
            <label>{banDialog.aslKind === 'node' ? 'Node' : 'Callsign'}
              <input value={banDialog.value} maxLength={banDialog.aslKind === 'node' ? 10 : 12} readOnly={Boolean(banDialog.row && banDialog.aslKind)} onChange={(event) => setBanDialog({ ...banDialog, value: event.target.value.toUpperCase() })} />
            </label>
            <label>Duration
              <select value={banDuration} onChange={(event) => setBanDuration(event.target.value as GlobalBanDuration)}>
                {GLOBAL_BAN_DURATIONS.map((duration) => <option key={duration.value} value={duration.value}>{duration.label}</option>)}
              </select>
            </label>
            <p className="allscan-drop-client-help">{banDialog.aslKind === 'node' ? 'Blocks this AllStar node in Asterisk.' : banDialog.aslKind ? 'Applies to this native connection type.' : 'Applies everywhere ASR can enforce it.'}{banDialog.row ? ' ASR will disconnect this connection after applying the ban.' : ''}</p>
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

      {updateDialogOpen && authStatus.isAdmin ? <UpdateAsrDialog initialView={updateDialogView} onClose={() => setUpdateDialogOpen(false)} initialUpdate={releaseStatus ? { ok: true, installedVersion: releaseStatus.installedVersion, availableVersion: releaseStatus.availableVersion, updateAvailable: releaseStatus.updateAvailable } : null} /> : null}

      {bubbleNode ? <AllStarBubbleDialog node={bubbleNode} localNode={config.node} onClose={() => setBubbleNode(null)} /> : null}

      {supportFeedbackOpen ? <SupportFeedbackDialog config={config} isAdmin={authStatus.isAdmin} onClose={() => setSupportFeedbackOpen(false)} /> : null}
      {donateAsrOpen ? <SupportAsrDialog onClose={() => setDonateAsrOpen(false)} /> : null}
    </div>
  )
}

export default App


function formatConnectionDuration(epoch: number, source: 'native' | 'observed') {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - epoch) / 60))
  const prefix = source === 'observed' ? '~' : ''
  if (minutes < 1) return `${prefix}<1m`
  if (minutes < 60) return `${prefix}${minutes}m`
  if (minutes < 1440) return `${prefix}${Math.floor(minutes / 60)}h ${minutes % 60}m`
  return `${prefix}${Math.floor(minutes / 1440)}d ${Math.floor(minutes / 60) % 24}h`
}
