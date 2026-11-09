#!/usr/bin/env node
// Headless local UI regression test. Does not create a real GitHub issue.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const port = Number(process.env.ASR_155_CDP_PORT || 9345)
const url = process.env.ASR_155_TEST_URL || 'http://127.0.0.1:4175/asr/?reportBug=1'
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const profile = await mkdtemp(join(tmpdir(), 'asr-155-browser-'))
const browser = spawn(process.env.CHROME_BIN || '/usr/bin/chromium', [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })
let socket
let nextId = 0
const requests = new Map()

function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId
    requests.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })
}

async function evaluate(expression) {
  const result = await command('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'Browser script failed')
  return result.result.value
}

async function until(expression, hint) {
  for (let i = 0; i < 70; i += 1) {
    if (await evaluate(expression)) return
    await sleep(100)
  }
  throw new Error(`Timed out: ${hint}`)
}

async function clickButton(name) {
  const found = await evaluate(`(() => {
    const b = [...document.querySelectorAll('.asr-feedback-box button')]
      .find(b => b.textContent.trim() === ${JSON.stringify(name)} && !b.disabled)
    b?.click()
    return !!b
  })()`)
  assert.ok(found, `Button not available: ${name}`)
}

try {
  let target
  for (let i = 0; i < 100 && !target; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      if (res.ok) target = (await res.json()).find((item) => item.type === 'page')
    } catch { /* Chrome starting */ }
    if (!target) await sleep(100)
  }
  assert.ok(target?.webSocketDebuggerUrl, 'Chrome debugging connection not ready')
  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', (event) => {
    const data = JSON.parse(event.data)
    if (!data.id || !requests.has(data.id)) return
    const waiter = requests.get(data.id)
    requests.delete(data.id)
    if (data.error) waiter.reject(new Error(data.error.message))
    else waiter.resolve(data.result)
  })
  await command('Page.enable')
  await command('Runtime.enable')
  await command('Page.navigate', { url })
  await until("!!document.querySelector('.asr-feedback-box textarea')", 'report modal')
  assert.match(await evaluate("document.querySelector('.asr-feedback-count').textContent"), /No screenshots selected/)

  const setMessage = `(() => {
    const field = document.querySelector('.asr-feedback-box textarea')
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set
    set.call(field, 'Regression test report')
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })()`
  await evaluate(setMessage)
  await evaluate(`(() => {
    const file = new File([Uint8Array.from([137,80,78,71,13,10,26,10,0])], 'example.png', { type: 'image/png' })
    const input = document.querySelector('.asr-feedback-file-input')
    const transfer = new DataTransfer()
    transfer.items.add(file)
    input.files = transfer.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  })()`)
  await until("document.querySelector('.asr-feedback-count')?.textContent.includes('1 of 3')", 'screenshot selected')
  assert.match(await evaluate("document.querySelector('.asr-feedback-files')?.textContent"), /example\.png/)
  await clickButton('Review')
  await until("!!document.querySelector('.asr-feedback-review-images img')", 'screenshot review thumbnail')
  assert.match(await evaluate("document.querySelector('.asr-feedback-count').textContent"), /1 screenshot attached/)
  await clickButton('Back')
  await until("document.querySelector('.asr-feedback-count')?.textContent.includes('1 of 3')", 'screenshot retained after Back')
  await clickButton('Review')
  await until("!!document.querySelector('.asr-feedback-review-images')", 'second review')
  await evaluate(`(() => {
    window.__supportPosts = []
    const realFetch = window.fetch.bind(window)
    window.fetch = async (url, init) => {
      if (String(url).includes('127.0.0.1:8999')) {
        window.__supportPosts.push(JSON.parse(init.body))
        return new Response(JSON.stringify({ ok: true, issueNumber: 1555 }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      }
      return realFetch(url, init)
    }
  })()`)
  await clickButton('Submit Public Issue')
  await until("!!document.querySelector('.asr-feedback-success')", 'submission confirmation')
  assert.match(await evaluate("document.querySelector('.asr-feedback-success').textContent"), /1555/)
  assert.equal(await evaluate("window.__supportPosts.length"), 1)
  assert.equal(await evaluate("window.__supportPosts[0].screenshots.length"), 1)
  assert.equal(await evaluate("document.querySelector('.asr-feedback-box button')?.textContent.includes('Submit Public Issue')"), false)
  console.log('PASS: file selection, Review, Back, submit confirmation, one request')

  await command('Page.navigate', { url })
  await until("!!document.querySelector('.asr-feedback-box textarea')", 'new report modal')
  await evaluate(setMessage)
  await clickButton('Review')
  await until("!!document.querySelector('.asr-feedback-preview')", 'text-only review')
  await evaluate(`(() => {
    const realFetch = window.fetch.bind(window)
    window.fetch = async (url, init) => {
      if (String(url).includes('127.0.0.1:8999')) return new Response(
        JSON.stringify({ ok: false, error: 'Too many submissions. Please try again later.' }),
        { status: 429, headers: { 'Content-Type': 'application/json' } },
      )
      return realFetch(url, init)
    }
  })()`)
  await clickButton('Submit Public Issue')
  await until("document.querySelector('.allscan-drop-client-status')?.textContent.includes('Too many submissions')", '429 error')
  assert.equal(await evaluate("!!document.querySelector('.asr-feedback-success')"), false)
  assert.match(await evaluate("document.querySelector('.asr-feedback-count').textContent"), /No screenshots attached/)
  console.log('PASS: text-only review and rate-limit error are visible; no false success')
} finally {
  socket?.close()
  browser.kill('SIGTERM')
  await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
}
