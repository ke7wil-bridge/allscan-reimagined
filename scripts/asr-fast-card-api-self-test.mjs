import assert from 'node:assert/strict'
import { createServer } from 'vite'

globalThis.DOMParser = class {
  parseFromString(value) {
    return { body: { textContent: String(value).replace(/<[^>]*>/g, '') } }
  }
}
const server = await createServer({ appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
const saved = globalThis.fetch
try {
  const { fetchFastStandardActivity, fetchFastYsfNetActivity } = await server.ssrLoadModule('/src/lib/allscanLive.ts')
  const cases = [
    {}, { cards: null }, { cards: {} },
    { cards: { a: { role: 'source', callsign: 'N0CALL' }, b: { role: 'relay', callsign: 'N1TEST' }, c: { role: 'unknown', callsign: 123 }, d: null, e: { role: 'idle', callsign: false }, f: { role: 'SOURCE', callsign: '  abc-2  ' } } },
    { cards: [{ role: 'relay', callsign: 'TEST' }, undefined, { callsign: ['A','B'] }] },
    { cards: { a: { role: 'source', callsign: 0 }, b: { role: null, callsign: {} } } },
  ]
  const oracle = (payload) => {
    const cards = {}
    for (const [id, raw] of Object.entries(payload.cards || {})) {
      const role = raw?.role === 'source' || raw?.role === 'relay' ? raw.role : 'idle'
      cards[id] = { role, callsign: String(raw?.callsign || '') }
    }
    return cards
  }
  let count = 0
  for (const [fn, action, message] of [
    [fetchFastStandardActivity, 'fast-standard-activity', 'Fast standard bridge activity unavailable'],
    [fetchFastYsfNetActivity, 'fast-ysf-net-activity', 'Fast YSF Net Bridge activity unavailable'],
  ]) {
    for (const signal of [undefined, new AbortController().signal]) for (const payload of cases) {
      let calls = 0
      globalThis.fetch = async (url, options) => {
        assert.equal(url, '/asr/asr-api.php?action=' + action)
        assert.deepEqual(options, { credentials: 'same-origin', cache: 'no-store', signal })
        calls++
        return { ok: true, json: async () => payload }
      }
      assert.deepEqual(await fn(signal), oracle(payload))
      assert.equal(calls, 1)
      count++
    }
    globalThis.fetch = async () => ({ ok: false, json: async () => { throw Error('unexpected JSON read') } })
    await assert.rejects(fn(), { message })
    const networkError = new Error('network')
    globalThis.fetch = async () => { throw networkError }
    await assert.rejects(fn(), error => error === networkError)
    const parseError = new SyntaxError('bad JSON')
    globalThis.fetch = async () => ({ ok: true, json: async () => { throw parseError } })
    await assert.rejects(fn(), error => error === parseError)
    count += 3
  }
  console.log('PASS', count, 'fast feed request, response, signal and failure equivalence cases')
} finally { globalThis.fetch = saved; await server.close() }
