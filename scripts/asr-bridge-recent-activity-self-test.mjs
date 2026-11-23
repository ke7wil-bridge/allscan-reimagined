#!/usr/bin/env node
// Bridge card Recent Activity shows CON / DIS / TX (#134, live-accepted on
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

const server = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
const priorFetch = globalThis.fetch
try {
  const { fetchBridgeCards } = await server.ssrLoadModule('/src/lib/allscanLive.ts')
  const now = Math.floor(Date.now() / 1000)
  const bridge = { id: 'ysf_net', mode: 'ysf', node: '1002', title: 'YSF', detailTitle: 'Clients', cardType: 'standard' }
  const entry = {
    role: 'idle',
    client_events: [
      { callsign: 'KE7WIL', event: 'connect', epoch: now - 300 },
      { callsign: 'KE7WIL', event: 'disconnect', epoch: now - 120 },
      { callsign: 'KF8DRJ', event: 'connect', epoch: now - 60, source: 'baseline_observation' },
      { callsign: 'N0NOISE', event: 'refresh', epoch: now - 50 },          // not a connection change
      { callsign: 'OLD1', event: 'connect', epoch: now - 3 * 86400 },     // outside the window
    ],
    tx_events: [
      { callsign: 'KE7WIL', epoch: now - 200, duration_seconds: 12.4, direction: 'incoming' },
      { callsign: '', epoch: now - 190, duration_seconds: 30, direction: 'outgoing' },   // AllStar relayed out
      { callsign: 'W1AW', epoch: now - 180, duration_seconds: 0.2, direction: 'incoming' }, // DMR keepalive noise
      { callsign: 'W1AW', epoch: now - 170, duration_seconds: 8, direction: '' },          // unknown direction
    ],
  }
  globalThis.fetch = async () => ({ json: async () => ({ bridge: { ysf_net: entry } }) })
  const { cards } = await fetchBridgeCards({ node: '674982', bridges: [bridge] })
  const rows = cards[0].recentRows
  const shown = rows.map((row) => `${row.label} ${row.meta.split(' · ')[0]}`)
  assert(JSON.stringify(shown) === JSON.stringify(['KF8DRJ CON', 'KE7WIL DIS', 'KE7WIL TX', 'KE7WIL CON']),
    `Recent Activity rows are wrong: ${JSON.stringify(shown)}`)
  assert(/^TX · .+ · 12s$/.test(rows[2].meta), `TX row must show its duration: ${rows[2].meta}`)
  assert(rows.every((row) => !/CONN|DISC/.test(row.meta)), 'old CONN/DISC labels are back')

  globalThis.fetch = async () => ({ json: async () => ({ bridge: { ysf_net: { role: 'idle', tx_events: [], client_events: [] } } }) })
  const empty = (await fetchBridgeCards({ node: '674982', bridges: [bridge] })).cards[0].recentRows
  assert(empty.length === 1 && empty[0].empty, 'an idle card must show the empty Recent Activity placeholder')
  console.log('bridge Recent Activity CON/DIS/TX self-test: ok')
} finally {
  globalThis.fetch = priorFetch
  await server.close()
}
