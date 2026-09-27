import test from 'node:test'
import assert from 'node:assert/strict'
import { LABELS, redact, validate } from '../src/worker.js'

test('label mapping is exact', () => {
  assert.deepEqual(LABELS.bug, ['bug','asr-report'])
  assert.deepEqual(LABELS.question, ['question','asr-support'])
  assert.deepEqual(LABELS.feature, ['enhancement','asr-feedback'])
})
test('redacts authentication secrets', () => {
  const value = redact('password=hunter2 github_token=abc TGIF_password=nope Authorization: Bearer xyz')
  assert.equal(value.includes('hunter2'), false)
  assert.equal(value.includes('abc'), false)
  assert.equal(value.includes('nope'), false)
  assert.equal(value.includes('xyz'), false)
})
test('validates request and screenshots', () => {
  assert.equal(validate({kind:'bug',content:'broken',website:''}), '')
  assert.notEqual(validate({kind:'other',content:'broken'}), '')
  assert.notEqual(validate({kind:'question',content:'ok',screenshots:[{type:'text/plain',data:'x'}]}), '')
})
test('diagnostics are content-only and service never gathers them', () => {
  assert.equal(validate({kind:'question',content:'question only'}), '')
  assert.equal(validate({kind:'feature',content:'feature only'}), '')
})

test('worker handles CORS and rate limiting without GitHub calls', async () => {
  const worker = (await import('../src/worker.js')).default
  const kv = new Map()
  const env = { RATE_LIMIT: { get: async (k) => kv.get(k), put: async (k,v) => kv.set(k,v) } }
  const preflight = await worker.fetch(new Request('https://support.test/', {method:'OPTIONS'}), env)
  assert.equal(preflight.status, 204)
  kv.set('rate:1.2.3.4:' + Math.floor(Date.now()/3600000), '5')
  const limited = await worker.fetch(new Request('https://support.test/', {method:'POST',headers:{'CF-Connecting-IP':'1.2.3.4','Content-Type':'application/json'},body:JSON.stringify({kind:'bug',content:'broken'})}), env)
  assert.equal(limited.status, 429)
})
