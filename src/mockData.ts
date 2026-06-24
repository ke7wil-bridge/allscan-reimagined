export type HeaderStat = {
  label: string
  value: string
  tone: 'idle' | 'source' | 'relay' | 'neutral'
}

export type ConnectionRow = {
  node: string
  info: string
  received: string
  direction: 'IN' | 'OUT' | 'LOCAL'
  connected: string
  mode: string
  state: 'idle' | 'talking' | 'relay' | 'normal'
}

export type BridgeCard = {
  id: string
  title: string
  status: 'Idle' | 'Source/TX' | 'Relay'
  lastCaller: string
  warning: string
  detailTitle: string
  detailRows: string[]
}

export const headerStats: HeaderStat[] = [
  { label: 'Green = Idle', value: 'Idle', tone: 'idle' },
  { label: 'Red = Source/TX', value: 'Source/TX', tone: 'source' },
  { label: 'Amber = Relay', value: 'Relay', tone: 'relay' },
]

export const connections: ConnectionRow[] = [
  {
    node: '641890',
    info: 'KE7WIL Full-Duplex 4 Life Phoenix, Arizona',
    received: '00:10:04',
    direction: 'LOCAL',
    connected: '03:40:46',
    mode: 'Transceive',
    state: 'idle',
  },
  {
    node: '674982',
    info: 'KN4EWT Netoholics HUB Carthage, TN',
    received: '00:03:41',
    direction: 'OUT',
    connected: '00:10:20',
    mode: 'Transceive',
    state: 'talking',
  },
  {
    node: 'KE7WILiax',
    info: 'KE7WIL Mobile App IAX connection',
    received: '00:10:15',
    direction: 'IN',
    connected: '04:54:25',
    mode: 'Transceive',
    state: 'normal',
  },
  {
    node: '1999',
    info: 'Zello ASL Bridge',
    received: '50:49:21',
    direction: 'OUT',
    connected: '93:50:42',
    mode: 'Transceive',
    state: 'relay',
  },
  {
    node: '1998',
    info: 'DMR TGIF 86753 Bridge',
    received: '75:46:10',
    direction: 'OUT',
    connected: '93:50:38',
    mode: 'Transceive',
    state: 'relay',
  },
  {
    node: '1997',
    info: 'YSF Reflector 64189 Bridge',
    received: 'Never',
    direction: 'OUT',
    connected: '93:50:36',
    mode: 'Transceive',
    state: 'normal',
  },
]

export const bridgeCards: BridgeCard[] = [
  {
    id: 'dmr',
    title: 'DMR Bridge',
    status: 'Idle',
    lastCaller: 'W3WIN',
    warning: '-',
    detailTitle: 'Connected DMR Clients',
    detailRows: ['KE7WIL · 3224939', 'W3WIN · 320608402'],
  },
  {
    id: 'ysf',
    title: 'YSF Bridge',
    status: 'Idle',
    lastCaller: '-',
    warning: '-',
    detailTitle: 'Linked YSF Gateways',
    detailRows: ['No linked YSF gateways'],
  },
  {
    id: 'zello',
    title: 'Zello Bridge',
    status: 'Relay',
    lastCaller: 'ASL Bridge',
    warning: '-',
    detailTitle: 'Recent Talkers',
    detailRows: ['KF8S', 'KJ7RNB'],
  },
  {
    id: 'dstar',
    title: 'D-Star Bridge',
    status: 'Idle',
    lastCaller: '-',
    warning: '-',
    detailTitle: 'Linked D-Star Gateways',
    detailRows: ['No linked D-Star gateways'],
  },
]
