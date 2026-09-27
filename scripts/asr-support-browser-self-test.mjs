#!/usr/bin/env node

import { spawn } from 'node:child_process'
import { access, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { constants } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const APP_URL = process.env.ASR_SUPPORT_TEST_URL || 'http://127.0.0.1:4173/asr/'
const SCREENSHOT_DIR = resolve(process.env.ASR_SUPPORT_SCREENSHOT_DIR || 'artifacts/support-asr')
const PORT = Number(process.env.ASR_SUPPORT_CDP_PORT || 9333)

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean)
  for (const candidate of candidates) {
    try {
      await access(candidate, constants.X_OK)
      return candidate
    } catch {
      // Try the next conventional browser path.
    }
  }
  throw new Error('Chrome or Chromium is required for the Support ASR browser self-test')
}

async function waitForJson(url, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return response.json()
    } catch {
      // Chrome is still starting.
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 100))
  }
  throw new Error(`Timed out waiting for ${url}`)
}

class CdpClient {
  constructor(url) {
    this.nextId = 1
    this.pending = new Map()
    this.listeners = new Map()
    this.socket = new WebSocket(url)
  }

  async connect() {
    await new Promise((resolveConnect, reject) => {
      this.socket.addEventListener('open', resolveConnect, { once: true })
      this.socket.addEventListener('error', reject, { once: true })
    })
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id) {
        const request = this.pending.get(message.id)
        if (!request) return
        this.pending.delete(message.id)
        if (message.error) request.reject(new Error(message.error.message))
        else request.resolve(message.result)
        return
      }
      for (const listener of this.listeners.get(message.method) || []) listener(message.params)
    })
  }

  send(method, params = {}) {
    const id = this.nextId++
    return new Promise((resolveRequest, reject) => {
      this.pending.set(id, { resolve: resolveRequest, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  waitFor(method, timeoutMs = 10000) {
    return new Promise((resolveEvent, reject) => {
      const listeners = this.listeners.get(method) || new Set()
      const timer = setTimeout(() => {
        listeners.delete(handler)
        reject(new Error(`Timed out waiting for ${method}`))
      }, timeoutMs)
      const handler = (params) => {
        clearTimeout(timer)
        listeners.delete(handler)
        resolveEvent(params)
      }
      listeners.add(handler)
      this.listeners.set(method, listeners)
    })
  }

  close() {
    this.socket.close()
  }
}

async function main() {
  const chrome = await findChrome()
  const profile = await mkdtemp(join(tmpdir(), 'asr-support-chrome-'))
  const browser = spawn(chrome, [
    '--headless=new',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' })

  let cdp
  try {
    const targets = await waitForJson(`http://127.0.0.1:${PORT}/json/list`)
    const pageTarget = targets.find((target) => target.type === 'page')
    assert(pageTarget?.webSocketDebuggerUrl, 'Chrome did not expose a page target')
    cdp = new CdpClient(pageTarget.webSocketDebuggerUrl)
    await cdp.connect()
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')

    const evaluate = async (expression) => {
      const result = await cdp.send('Runtime.evaluate', {
        expression,
        awaitPromise: true,
        returnByValue: true,
      })
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'Browser evaluation failed')
      return result.result.value
    }
    const delay = (milliseconds = 80) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds))
    const viewport = async (width, height, mobile = false) => {
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width, height, deviceScaleFactor: 1, mobile,
      })
    }
    const navigate = async () => {
      await cdp.send('Page.navigate', { url: APP_URL })
      const deadline = Date.now() + 10000
      while (Date.now() < deadline) {
        await delay(100)
        try {
          if (await evaluate(`document.readyState === 'complete' && location.href.startsWith(${JSON.stringify(APP_URL)})`)) {
            await delay(250)
            return
          }
        } catch {
          // The execution context can be replaced while navigation is in progress.
        }
      }
      throw new Error(`Timed out loading ${APP_URL}`)
    }
    const press = async (key, code, windowsVirtualKeyCode, modifiers = 0) => {
      const keyEvent = {
        key,
        code,
        windowsVirtualKeyCode,
        nativeVirtualKeyCode: windowsVirtualKeyCode,
        modifiers,
        ...(key === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}),
      }
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...keyEvent })
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...keyEvent })
      await delay()
    }
    const clickVisibleTextButton = async (text) => {
      const clicked = await evaluate(`(() => {
        const button = [...document.querySelectorAll('button')].find((item) =>
          item.textContent.trim() === ${JSON.stringify(text)} && item.getClientRects().length > 0)
        button?.click()
        return Boolean(button)
      })()`)
      assert(clicked, `Visible button not found: ${text}`)
      await delay()
    }
    const openMenu = async (theme = 'standard') => {
      if (theme === 'lcars-frame') {
        await evaluate("document.querySelector('.allscan-lcars-access-admin').click()")
      } else {
        await evaluate("document.querySelector('.allscan-menu-button').click()")
      }
      await delay()
    }
    const openSupport = async (theme = 'standard') => {
      await openMenu(theme)
      await clickVisibleTextButton('Support ASR')
    }
    const screenshot = async (filename) => {
      const capture = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true })
      await writeFile(join(SCREENSHOT_DIR, filename), Buffer.from(capture.data, 'base64'))
    }
    const modalFits = () => evaluate(`(() => {
      const dialog = document.querySelector('.asr-support-dialog')
      const actions = [...document.querySelectorAll('.asr-support-actions a, .asr-support-actions button')]
      if (!dialog || actions.length !== 2) return false
      const rects = [dialog, ...actions].map((element) => element.getBoundingClientRect())
      return rects.every((rect) => rect.top >= 0 && rect.left >= 0 &&
        rect.bottom <= window.innerHeight && rect.right <= window.innerWidth)
    })()`)

    await mkdir(SCREENSHOT_DIR, { recursive: true })
    await viewport(1440, 1000)
    await navigate()

    const storageBefore = await evaluate('JSON.stringify({ local: Object.keys(localStorage).sort(), session: Object.keys(sessionStorage).sort(), cookie: document.cookie })')
    await openMenu()
    const menuItems = await evaluate("[...document.querySelector('.allscan-menu-proxy-list').children].filter((item) => item.getClientRects().length > 0).map((item) => item.textContent.trim())")
    const supportIndex = menuItems.indexOf('Support ASR')
    const logoutIndex = menuItems.indexOf('Logout')
    assert(supportIndex >= 0 && supportIndex === logoutIndex - 1, 'Support ASR is not immediately above Logout')
    await screenshot('01-main-menu-support-asr.png')

    const urlBefore = await evaluate('location.href')
    await clickVisibleTextButton('Support ASR')
    assert(await evaluate("Boolean(document.querySelector('[role=dialog][aria-labelledby=asr-support-title]'))"), 'Support modal did not open')
    assert(await evaluate('location.href') === urlBefore, 'Opening Support ASR navigated away automatically')
    assert(await evaluate("document.querySelector('#asr-support-optional').textContent.includes('completely optional')"), 'Optional contribution wording is missing')
    const link = await evaluate(`(() => {
      const item = document.querySelector('.asr-support-actions a')
      return { href: item.href, target: item.target, rel: item.rel, text: item.innerText }
    })()`)
    assert(link.href === 'https://paypal.me/jdjcaz', 'PayPal destination is incorrect')
    assert(link.target === '_blank' && link.rel.includes('noopener') && link.rel.includes('noreferrer'), 'External link safety attributes are missing')
    assert(link.text.includes('Support ASR with PayPal') && link.text.includes('Opens in a new tab'), 'External-link action is not understandable')
    assert(await evaluate("document.activeElement.getAttribute('aria-label') === 'Close Support AllScan Reimagined'"), 'Initial modal focus is not on Close')
    assert(await modalFits(), 'Desktop modal or actions are clipped')
    await screenshot('02-support-asr-modal-desktop.png')

    await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))")
    assert(await evaluate("document.activeElement.textContent.trim() === 'Cancel'"), 'Shift+Tab did not wrap focus to Cancel')
    await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))")
    assert(await evaluate("document.activeElement.getAttribute('aria-label') === 'Close Support AllScan Reimagined'"), 'Tab did not wrap focus to Close')
    await press('Escape', 'Escape', 27)
    assert(!(await evaluate("Boolean(document.querySelector('.asr-support-dialog'))")), 'Escape did not close the modal')
    assert(await evaluate("document.activeElement.classList.contains('allscan-menu-button')"), 'Focus did not return to the menu button')

    await press('Enter', 'Enter', 13)
    await evaluate("document.querySelector('.allscan-menu-support-row').focus()")
    await press('Enter', 'Enter', 13)
    assert(await evaluate("Boolean(document.querySelector('.asr-support-dialog'))"), 'Keyboard activation did not open the modal')
    await clickVisibleTextButton('Cancel')
    assert(!(await evaluate("Boolean(document.querySelector('.asr-support-dialog'))")), 'Cancel did not close the modal')
    const storageAfter = await evaluate('JSON.stringify({ local: Object.keys(localStorage).sort(), session: Object.keys(sessionStorage).sort(), cookie: document.cookie })')
    assert(storageAfter === storageBefore, 'Support interaction changed browser storage or cookies')

    for (const [theme, mode] of [
      ['standard', 'dark'],
      ['standard', 'light'],
      ['deep-ocean-animated', 'dark'],
      ['matrix', 'dark'],
      ['lcars-frame', 'dark'],
    ]) {
      await evaluate(`localStorage.setItem('asrThemeSettings.v1', ${JSON.stringify(JSON.stringify({ theme, mode }))})`)
      await navigate()
      await openSupport(theme)
      const themeResult = await evaluate(`(() => {
        const dialog = document.querySelector('.asr-support-dialog')
        const style = getComputedStyle(dialog)
        return { theme: document.documentElement.dataset.asrTheme, background: style.backgroundColor, color: style.color }
      })()`)
      assert(themeResult.theme === theme, `Theme did not apply to Support ASR: ${theme}`)
      assert(themeResult.background !== 'rgba(0, 0, 0, 0)' && themeResult.color !== 'rgba(0, 0, 0, 0)', `Modal lost themed contrast: ${theme}`)
      assert(await modalFits(), `Modal is clipped under theme: ${theme}`)
      await press('Escape', 'Escape', 27)
    }

    for (const [name, width, height, mobile] of [
      ['tablet', 820, 1180, true],
      ['phone-portrait', 390, 844, true],
      ['phone-landscape', 844, 390, true],
    ]) {
      await viewport(width, height, mobile)
      await evaluate("localStorage.setItem('asrThemeSettings.v1', JSON.stringify({ theme: 'standard', mode: 'dark' }))")
      await navigate()
      await openSupport()
      assert(await modalFits(), `Modal or actions are clipped at ${name}`)
      if (name === 'phone-portrait') await screenshot('03-support-asr-modal-phone-390px.png')
      await press('Escape', 'Escape', 27)
    }

    console.log(`support ASR browser self-test: ok\nscreenshots: ${SCREENSHOT_DIR}`)
  } finally {
    cdp?.close()
    browser.kill('SIGTERM')
    if (browser.exitCode === null) {
      await Promise.race([
        new Promise((resolveExit) => browser.once('exit', resolveExit)),
        new Promise((resolveDelay) => setTimeout(resolveDelay, 2000)),
      ])
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  }
}

await main()
