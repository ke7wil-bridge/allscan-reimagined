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
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { const response = await fetch(url); if (response.ok) return response } catch { /* starting */ }
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

class Cdp {
  constructor(url) {
    this.id = 0
    this.pending = new Map()
    this.socket = new WebSocket(url)
  }
  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true })
      this.socket.addEventListener('error', reject, { once: true })
    })
    this.socket.addEventListener('message', ({ data }) => {
      const message = JSON.parse(data)
      if (!message.id) return
      const pending = this.pending.get(message.id)
      this.pending.delete(message.id)
      message.error ? pending.reject(new Error(message.error.message)) : pending.resolve(message.result)
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }
  close() { this.socket.close() }
}

async function runBrowser(unauthorized = false) {
  const port = unauthorized ? 4192 : 4191
  const cdpPort = unauthorized ? 9392 : 9391
  const appUrl = `http://127.0.0.1:${port}/asr/`
  const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    env: { ...process.env, ASR_MOCK: '1', ASR_MOCK_UNAUTHORIZED: unauthorized ? '1' : '0' },
    stdio: 'ignore',
  })
  const profile = await mkdtemp(join(tmpdir(), 'asr-client-management-chrome-'))
  const chrome = spawn(await chromePath(), [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, 'about:blank',
  ], { stdio: 'ignore' })
  let cdp
  try {
    await waitFor(appUrl)
    const targets = await (await waitFor(`http://127.0.0.1:${cdpPort}/json/list`)).json()
    const target = targets.find((item) => item.type === 'page' && item.url === 'about:blank')
      || targets.find((item) => item.type === 'page' && !item.url.startsWith('chrome-extension://'))
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
    const waitUntil = async (expression, message, timeout = 8000) => {
      const deadline = Date.now() + timeout
      while (Date.now() < deadline) {
        try { if (await evaluate(expression)) return } catch { /* rendering */ }
        await delay(100)
      }
      throw new Error(message)
    }

    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, mobile: false, deviceScaleFactor: 1 })
    await cdp.send('Page.navigate', { url: appUrl })
    await waitUntil("document.readyState === 'complete' && document.body.textContent.includes('Connected Clients')", 'Dashboard did not load')
    await evaluate(`(() => {
      for (const card of document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')) {
        const button = [...card.querySelectorAll('.allscan-bridge-detail-toggle')].find((item) => item.textContent.includes('Connected Clients') || item.textContent.includes('Recent Talkers'))
        if (button?.getAttribute('aria-expanded') !== 'true') button?.click()
      }
    })()`)
    await delay(200)

    if (unauthorized) {
      assert(await evaluate("document.querySelectorAll('.allscan-bridge-inline-client-actions').length") === 0,
        'unauthorized browser received bridge client controls')
      return
    }

    const coverage = await evaluate(`(() => {
      const result = {};
      for (const mode of ['DMR','YSF','P25','NXDN','M17','Zello']) {
        const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
          .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === mode + ' Bridge');
        result[mode] = card ? {
          clients: card.querySelectorAll('.allscan-bridge-client:not(.is-empty)').length,
          kicks: [...card.querySelectorAll('.allscan-bridge-inline-client-actions button')].filter((item) => item.textContent.trim() === 'Kick').length,
          bans: card.querySelectorAll('.allscan-bridge-inline-client-actions select').length,
        } : null;
      }
      return result;
    })()`)
    for (const mode of ['DMR', 'YSF', 'P25', 'NXDN', 'M17', 'Zello']) {
      assert(coverage[mode]?.clients >= 1, `${mode} connected/recent client identification was not rendered`)
      assert(coverage[mode]?.kicks >= 1, `${mode} Kick was not available in the browser`)
      assert(coverage[mode]?.bans >= 1, `${mode} Ban was not available in the browser`)
    }

    const kicked = await evaluate(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'DMR Bridge');
      const row = [...(card?.querySelectorAll('.allscan-bridge-client') || [])].find((item) => item.textContent.includes('N1DMR'));
      row?.querySelector('button')?.click();
      return Boolean(row);
    })()`)
    assert(kicked, 'selected DMR client Kick could not be invoked')
    await waitUntil(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'DMR Bridge');
      return card && !card.textContent.includes('N1DMR');
    })()`, 'kicked DMR client remained in the selected roster')
    assert(await evaluate("document.body.textContent.includes('N2YSF') && document.body.textContent.includes('K7GLOBAL')"),
      'Kick removed an unrelated digital client')

    const banned = await evaluate(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'NXDN Bridge');
      const row = [...(card?.querySelectorAll('.allscan-bridge-client') || [])].find((item) => item.textContent.includes('K7GLOBAL'));
      const select = row?.querySelector('select');
      if (select) { select.value = '1h'; select.dispatchEvent(new Event('change', { bubbles: true })); }
      return Boolean(select);
    })()`)
    assert(banned, 'selected NXDN client Ban could not be invoked')
    await waitUntil("!document.body.textContent.includes('K7GLOBAL')", 'Global Ban did not clear the identity from every supported digital roster')
    assert(await evaluate("['N2YSF','N3P25','N4NXDN','N5M17'].every((identity) => document.body.textContent.includes(identity))"),
      'Global Ban removed an unrelated digital client')

    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, mobile: true, deviceScaleFactor: 1 })
    await delay(150)
    const mobile = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('.allscan-bridge-client:not(.is-empty)')].filter((row) => row.querySelector('.allscan-bridge-inline-client-actions'));
      return rows.length > 0 && rows.every((row) => {
        const rect = row.getBoundingClientRect();
        return rect.left >= -2 && rect.right <= innerWidth + 2 && row.querySelector('button') && row.querySelector('select');
      });
    })()`)
    assert(mobile, 'mobile Connected Clients Kick/Ban controls are missing or clipped')
  } finally {
    cdp?.close()
    chrome.kill('SIGTERM')
    vite.kill('SIGTERM')
    await delay(400)
    await rm(profile, { recursive: true, force: true })
  }
}

await runBrowser(false)
await delay(250)
await runBrowser(true)
console.log('Connected Clients/Kick/Ban browser self-test: ok')
