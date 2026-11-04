#!/usr/bin/env node
import { spawn } from 'node:child_process'
import { access, mkdtemp, rm } from 'node:fs/promises'
import { constants } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const assert = (value, message) => { if (!value) throw new Error(message) }
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function chromePath() {
  for (const candidate of [process.env.CHROME_BIN, '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].filter(Boolean)) {
    try { await access(candidate, constants.X_OK); return candidate } catch { /* continue */ }
  }
  throw new Error('Chrome or Chromium is required')
}

async function waitFor(url, timeout = 15000) {
  const end = Date.now() + timeout
  while (Date.now() < end) {
    try { const response = await fetch(url); if (response.ok) return response } catch { /* starting */ }
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

class Cdp {
  constructor(url) { this.id = 0; this.pending = new Map(); this.socket = new WebSocket(url) }
  async connect() {
    await new Promise((resolve, reject) => { this.socket.addEventListener('open', resolve, { once: true }); this.socket.addEventListener('error', reject, { once: true }) })
    this.socket.addEventListener('message', ({ data }) => { const message = JSON.parse(data); if (!message.id) return; const item = this.pending.get(message.id); this.pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result) })
  }
  send(method, params = {}) { const id = ++this.id; return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.socket.send(JSON.stringify({ id, method, params })) }) }
  close() { this.socket.close() }
}

async function runBrowser(unauthorized = false) {
  const port = unauthorized ? 4184 : 4183
  const cdpPort = unauthorized ? 9344 : 9343
  const appUrl = `http://127.0.0.1:${port}/asr/`
  const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    env: { ...process.env, ASR_MOCK: '1', ASR_MOCK_UNAUTHORIZED: unauthorized ? '1' : '0' }, stdio: 'ignore',
  })
  const profile = await mkdtemp(join(tmpdir(), 'asr-urf-client-chrome-'))
  const chrome = spawn(await chromePath(), ['--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' })
  let cdp
  try {
    await waitFor(appUrl)
    const targets = await (await waitFor(`http://127.0.0.1:${cdpPort}/json/list`)).json()
    const target = targets.find((item) => item.type === 'page' && item.url === 'about:blank') || targets.find((item) => item.type === 'page' && !item.url.startsWith('chrome-extension://'))
    assert(target?.webSocketDebuggerUrl, 'Chrome did not expose the application page target')
    cdp = new Cdp(target.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    const evaluate = async (expression) => {
      const result = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
      return result.result.value
    }
    const viewport = (width, height, mobile) => cdp.send('Emulation.setDeviceMetricsOverride', { width, height, mobile, deviceScaleFactor: 1 })
    const navigate = async () => { await cdp.send('Page.navigate', { url: appUrl }); for (let i = 0; i < 100; i += 1) { await delay(100); try { if (await evaluate("document.readyState === 'complete' && document.body.textContent.includes('Connected Clients')")) return } catch { /* navigating */ } } throw new Error(`Dashboard did not load: ${await evaluate("location.href + ' :: ' + document.body.innerText.slice(0, 2000)")}`) }
    const openClients = async () => {
      const clicked = await evaluate(`(() => { const button = [...document.querySelectorAll('.allscan-bridge-detail-toggle')].find((item) => item.textContent.includes('Connected Clients')); if (button?.getAttribute('aria-expanded') !== 'true') button?.click(); return Boolean(button) })()`)
      assert(clicked, 'Connected Clients toggle was not rendered')
      await delay(150)
    }
    await viewport(1440, 1000, false)
    await navigate()
    await openClients()
    if (unauthorized) {
      assert(await evaluate("document.querySelectorAll('.allscan-bridge-inline-client-actions').length") === 0, 'unauthorized browser received URF client controls')
      return
    }
    assert(await evaluate("document.querySelectorAll('.allscan-bridge-client:not(.is-empty)').length >= 4"), 'four connected DMR clients were not rendered')
    assert(await evaluate("document.querySelectorAll('.allscan-bridge-inline-client-actions button').length >= 4"), 'desktop Kick controls were not rendered')
    assert(await evaluate("[...document.querySelectorAll('.allscan-bridge-inline-client-actions button')].every((item) => item.textContent.trim() === 'Disconnect')"), 'URF DMR session actions are not labeled Disconnect')
    assert(await evaluate("document.querySelectorAll('.allscan-bridge-inline-client-actions select').length >= 4"), 'desktop Ban controls were not rendered')
    const kicked = await evaluate(`(() => { const row = [...document.querySelectorAll('.allscan-bridge-client')].find((item) => item.textContent.includes('N1TEST')); row?.querySelector('button')?.click(); return Boolean(row) })()`)
    assert(kicked, 'selected client Kick could not be invoked')
    await delay(900)
    assert(!(await evaluate("document.body.textContent.includes('N1TEST')")), 'kicked client remained after refresh')
    assert(await evaluate("document.body.textContent.includes('N0TEST') && document.body.textContent.includes('N2TEST') && document.body.textContent.includes('N3TEST')"), 'Kick removed an unrelated client')
    const banned = await evaluate(`(() => { const row = [...document.querySelectorAll('.allscan-bridge-client')].find((item) => item.textContent.includes('N2TEST')); const select = row?.querySelector('select'); if (select) { select.value = '1h'; select.dispatchEvent(new Event('change', { bubbles: true })) } return Boolean(select) })()`)
    assert(banned, 'selected client Ban could not be invoked')
    await delay(900)
    assert(!(await evaluate("document.body.textContent.includes('N2TEST')")), 'banned client remained after refresh')
    assert(await evaluate("document.body.textContent.includes('N0TEST') && document.body.textContent.includes('N3TEST')"), 'Ban removed an unrelated client')
    await viewport(390, 844, true)
    await delay(150)
    assert(await evaluate(`(() => { const row = [...document.querySelectorAll('.allscan-bridge-client')].find((item) => item.textContent.includes('N3TEST')); if (!row) return false; const rect = row.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && Boolean(row.querySelector('button')) && Boolean(row.querySelector('select')) })()`), 'mobile Connected Clients controls are missing or clipped')
  } finally {
    cdp?.close(); chrome.kill('SIGTERM'); vite.kill('SIGTERM'); await delay(500); await rm(profile, { recursive: true, force: true })
  }
}

await runBrowser(false)
await delay(300)
await runBrowser(true)
console.log('URF client-management browser self-test: ok')
