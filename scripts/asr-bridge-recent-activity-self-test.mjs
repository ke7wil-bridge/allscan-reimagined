#!/usr/bin/env node
// Bridge card Recent Activity shows CONN / DISC / TX (#134, live-accepted on
// Netoholics 674982, Nov 23 2026). Runs the real dashboard code against a
// bridge-status payload; never weaken these checks to make a change pass.

import { createServer } from 'vite'

// The dashboard module expects a browser DOMParser (as in the dashboard self-test).
globalThis.DOMParser = class {
  parseFromString(value) {
    return { body: { textContent: String(value).replace(/<[^>]*>/g, '') } }
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const server = await createServer({ appType: 'custom', configLoader: 'runner', logLevel: 'silent', server: { middlewareMode: true } })
const priorFetch = globalThis.fetch
try {
  const { fetchBridgeCards } = await server.ssrLoadModule('/src/lib/allscanLive.ts')
  const now = Math.floor(Date.now() / 1000)
  const modes = ['dmr', 'ysf', 'p25', 'nxdn', 'm17', 'zello']
  const bridges = modes.map((mode, index) => ({
    id: `${mode}_audit`, mode, node: String(1001 + index), title: mode.toUpperCase(),
    detailTitle: 'Clients', cardType: 'standard',
  }))
  const entries = Object.fromEntries(modes.map((mode, index) => {
    const identity = mode === 'm17' ? 'N5M17-02' : mode === 'zello' ? 'ZelloUser' : `${mode.toUpperCase()}-USER`
    return [`${mode}_audit`, {
      role: 'idle',
      client_events: [
        { callsign: identity, event: 'connect', epoch: now - 300 - index },
        { callsign: identity, event: 'disconnect', epoch: now - 120 - index },
        { callsign: 'N0NOISE', event: 'refresh', epoch: now - 50 },
        { callsign: 'OLD1', event: 'connect', epoch: now - 86401 },
      ],
      tx_events: [
        { callsign: identity, epoch: now - 200 - index, duration_seconds: mode === 'zello' ? 0.4 : 12.4, direction: 'incoming', source_protocol: mode.toUpperCase() },
        { callsign: 'CROSS-BRIDGE', epoch: now - 40, duration_seconds: 8, direction: 'incoming', source_protocol: mode === 'dmr' ? 'YSF' : 'DMR' },
        { callsign: identity, epoch: now - 30, duration_seconds: 30, direction: 'outgoing', source_protocol: mode.toUpperCase() },
        { callsign: identity, epoch: now - 20, duration_seconds: 8, direction: '', source_protocol: mode.toUpperCase() },
      ],
    }]
  }))
  // Only DMR's sub-second registration/keepalive frame is false activity.
  entries.dmr_audit.tx_events.push({ callsign: 'DMR-NOISE', epoch: now - 10, duration_seconds: 0.2, direction: 'incoming', source_protocol: 'DMR' })
  // An authentic event at the far edge of the requested history remains visible.
  entries.ysf_audit.client_events.push({ callsign: 'YSF-24H', event: 'connect', epoch: now - 86399 })

  globalThis.fetch = async () => ({ json: async () => ({ bridge: entries }) })
  const { cards } = await fetchBridgeCards({ node: '674982', bridges })
  for (const card of cards) {
    const rows = card.recentRows
    const actions = rows.map((row) => row.meta.split(' · ')[0])
    assert(actions.every((action) => ['CONN', 'DISC', 'TX'].includes(action)), `${card.mode} has an invalid activity label: ${JSON.stringify(actions)}`)
    assert(rows.every((row) => !/\bCON\b|\bDIS\b/.test(row.meta)), `${card.mode} restored abbreviated labels`)
    assert(rows.every((row) => row.label !== 'CROSS-BRIDGE' && row.label !== 'OLD1'), `${card.mode} leaked false or stale activity`)
    assert(actions.filter((action) => action === 'TX').length === 1, `${card.mode} did not retain exactly its authentic TX`)
    assert(rows.some((row) => row.label === (card.mode === 'm17' ? 'N5M17' : card.mode === 'zello' ? 'ZELLOUSER' : `${card.mode.toUpperCase()}-USER`)), `${card.mode} identity is wrong`)
    if (card.mode === 'zello') assert(actions.length === 1 && actions[0] === 'TX', 'Zello Recent Activity must be TX-only')
  }
  assert(cards.find((card) => card.mode === 'ysf').recentRows.some((row) => row.label === 'YSF-24H'), '24-hour activity retention regressed')
  assert(/^TX · .+ · 12s$/.test(cards.find((card) => card.mode === 'p25').recentRows.find((row) => row.meta.startsWith('TX')).meta), 'TX row must show its duration')

  globalThis.fetch = async () => ({ json: async () => ({ bridge: { ysf_audit: { role: 'idle', tx_events: [], client_events: [] } } }) })
  const empty = (await fetchBridgeCards({ node: '674982', bridges: [bridges[1]] })).cards[0].recentRows
  assert(empty.length === 1 && empty[0].empty, 'an idle card must show the empty Recent Activity placeholder')
  console.log('bridge Recent Activity CONN/DISC/TX self-test: ok')
} finally {
  globalThis.fetch = priorFetch
  await server.close()
}
