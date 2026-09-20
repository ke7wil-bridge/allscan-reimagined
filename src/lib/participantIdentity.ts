import type { LiveConnectionRow } from './allscanLive'

export type ParticipantIdentity = {
  network: 'allstar' | 'echolink' | 'iax'
  nodeNumber?: string
  callsign?: string
  presentedIdentity: string
  active: boolean
}

// EchoLink nodes arrive through app_rpt as 3 + six-digit EchoLink identifiers.
export function isEchoLinkConnection(row: LiveConnectionRow) {
  return /^3[0-9]{6}$/.test(row.node) && /\[EchoLink [0-9]+\]/i.test(row.info)
}

export function connectionCallsign(row: LiveConnectionRow) {
  const text = /^[0-9]+$/.test(row.node) ? row.info.trim() : row.node.trim()
  const match = /^([A-Z0-9]{1,3}[0-9][A-Z0-9]{1,7}(?:-[LR])?)(?:\s|$)/i.exec(text)
  return match ? match[1].replace(/-(?:L|R)$/i, '').toUpperCase() : ''
}

export function identityFromConnection(row: LiveConnectionRow): ParticipantIdentity {
  const network = isEchoLinkConnection(row) ? 'echolink'
    : /^[0-9]{3,10}$/.test(row.node) ? 'allstar' : 'iax'
  const callsign = connectionCallsign(row)
  return {
    network,
    nodeNumber: network === 'allstar' ? row.node : undefined,
    callsign: callsign || undefined,
    presentedIdentity: row.node,
    active: row.state !== 'message',
  }
}
