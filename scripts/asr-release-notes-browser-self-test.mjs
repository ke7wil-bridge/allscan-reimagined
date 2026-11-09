#!/usr/bin/env node
// Read-only browser qualification for #159, against a local mock ASR app.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { readFileSync } from 'node:fs'
import { parseReleaseChanges } from '../src/lib/releaseNotes.js'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const version = JSON.parse(read('../package.json')).version
const actualChanges = parseReleaseChanges(read(`../release-notes/v${version}.md`)).length
const vitePort = 49159
const cdpPort = 49160
const profile = await mkdtemp(join(tmpdir(), 'asr-release-notes-'))
const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'],
  { env: { ...process.env, ASR_MOCK: '1' }, stdio: 'ignore' })
const chrome = spawn('/usr/bin/chromium', [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })
let socket
const pending = new Map()
let sequence = 0
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence
  pending.set(id, { resolve, reject })
  socket.send(JSON.stringify({ id, method, params }))
})
const evalJs = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
  return result.result.value
}
try {
  let target
  for (let i = 0; i < 100; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${cdpPort}/json/list`)
      if (response.ok) target = (await response.json()).find((item) => item.type === 'page')
      if (target?.webSocketDebuggerUrl) break
    } catch { /* Chromium is starting. */ }
    await delay(100)
  }
  assert.ok(target?.webSocketDebuggerUrl, 'Chromium CDP endpoint unavailable')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data)
    if (!message.id) return
    const request = pending.get(message.id)
    if (!request) return
    pending.delete(message.id)
    message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result)
  })
  await send('Page.enable')
  await send('Runtime.enable')
  for (const [name, width, height] of [
    ['desktop', 1280, 800], ['phone-portrait', 390, 844], ['phone-landscape', 844, 390],
  ]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 1000 })
    await send('Page.navigate', { url: `http://127.0.0.1:${vitePort}/asr/?updateAsr=1` })
    let ready = false
    for (let i = 0; i < 100; i++) {
      await delay(100)
      try {
        ready = await evalJs(`(() => {
          const tab = document.querySelector('.asr-update-view-tabs button:last-child');
          if (!tab) return false;
          tab.click();
          return Boolean(document.querySelector('.asr-whats-new'));
        })()`)
        if (ready) break
      } catch { /* ASR is still rendering. */ }
    }
    assert.ok(ready, `${name}: popup did not open in mock ASR`)
    const result = await evalJs(`(() => {
      const dialog = document.querySelector('.asr-update-dialog--whats-new');
      const scroller = dialog.querySelector('.asr-whats-new');
      const list = scroller.querySelector('ul');
      const close = dialog.querySelector('.asr-update-head button');
      const footer = dialog.querySelector('footer');
      const original = list.children.length;
      list.innerHTML = Array.from({length:43}, (_, i) => '<li>Released change ' + (i + 1) + ' with a short explanation.</li>').join('');
      scroller.scrollTop = scroller.scrollHeight;
      const bounds = dialog.getBoundingClientRect();
      const area = scroller.getBoundingClientRect();
      const last = list.lastElementChild.getBoundingClientRect();
      const btn = close.getBoundingClientRect();
      const foot = footer.getBoundingClientRect();
      return {
        original, total: list.children.length, scrollTop: scroller.scrollTop,
        canScroll: scroller.scrollHeight > scroller.clientHeight,
        lastAccessible: last.bottom <= area.bottom + 2 && last.top >= area.top - 2,
        closeAccessible: btn.top >= -1 && btn.bottom <= innerHeight + 1,
        footerAccessible: foot.top >= -1 && foot.bottom <= innerHeight + 1,
        dialogFits: bounds.top >= -1 && bounds.bottom <= innerHeight + 1,
        overflow: getComputedStyle(scroller).overflowY, tabIndex: scroller.tabIndex,
      };
    })()`)
    assert.equal(result.original, actualChanges, `${name}: rendered changes differ from the release file`)
    assert.equal(result.total, 43, `${name}: long release fixture not rendered`)
    assert.ok(result.canScroll && result.scrollTop > 0, `${name}: long list cannot scroll ${JSON.stringify(result)}`)
    assert.ok(result.lastAccessible, `${name}: final change clipped ${JSON.stringify(result)}`)
    assert.ok(result.dialogFits && result.closeAccessible && result.footerAccessible,
      `${name}: fixed controls offscreen ${JSON.stringify(result)}`)
    assert.equal(result.overflow, 'scroll')
    assert.equal(result.tabIndex, 0)
    await evalJs("(() => {const el = document.querySelector('.asr-whats-new'); el.scrollTop = 0; el.focus()})()")
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'End', code: 'End', windowsVirtualKeyCode: 35 })
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'End', code: 'End', windowsVirtualKeyCode: 35 })
    await delay(250)
    const keyboardPosition = await evalJs("document.querySelector('.asr-whats-new').scrollTop")
    assert.ok(keyboardPosition > 0, `${name}: End key did not scroll the focused release notes`)
    console.log(`${name}: PASS ${width}x${height}, 43 changes, final entry, fixed controls and keyboard scrolling`)
  }
} finally {
  socket?.close()
  chrome.kill('SIGTERM')
  vite.kill('SIGTERM')
  await delay(250)
  await rm(profile, { recursive: true, force: true })
}
