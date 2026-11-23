#!/usr/bin/env node
// Real-Chrome interaction regression for Issue #207. Uses only Vite synthetic data.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const chromePort = 9395, vitePort = 4195
const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'], { env: { ...process.env, ASR_MOCK: '1' }, stdio: 'ignore' })
const profile = await mkdtemp(join(tmpdir(), 'asr-207-bubble-'))
const chrome = spawn(process.env.CHROME_BIN || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : '/usr/bin/chromium'), [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', `--remote-debugging-port=${chromePort}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: 'ignore' })
let ws
let nextId = 1
const pending = new Map()
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = nextId++; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params }))
})
const evaluate = async expression => {
  const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (response.exceptionDetails) throw new Error(String(response.exceptionDetails.exception?.description || JSON.stringify(response.exceptionDetails)))
  return response.result?.value
}
const until = async (fn, message, attempts = 120) => {
  for (let i = 0; i < attempts; i += 1) { try { if (await fn()) return } catch {} await delay(100) }
  throw new Error(message)
}
const center = async elementExpression => {
  const value = await evaluate(`(() => { const element=${elementExpression}; if(!element)return null; const r=element.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2,width:r.width,height:r.height,left:r.left,top:r.top,right:r.right,bottom:r.bottom}; })()`)
  assert.ok(value, `Element missing: ${elementExpression}`)
  return value
}
const click = async (elementExpression, clickCount = 1) => {
  await evaluate(`(() => { const element=${elementExpression}; element?.scrollIntoView({block:'center',inline:'center'}); })()`)
  await delay(40)
  const point = await center(elementExpression)
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y })
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount })
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount })
}
const doubleClick = async elementExpression => {
  await click(elementExpression, 1)
  await delay(60)
  await click(elementExpression, 2)
}
const drag = async (elementExpression, dx, dy) => {
  await evaluate(`(() => { const element=${elementExpression}; element?.scrollIntoView({block:'center',inline:'center'}); })()`)
  await delay(40)
  const point = await center(elementExpression)
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y })
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 })
  for (let step = 1; step <= 5; step += 1) await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x + dx * step / 5, y: point.y + dy * step / 5, button: 'left', buttons: 1 })
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x + dx, y: point.y + dy, button: 'left', buttons: 0, clickCount: 1 })
}
const chartLink = `[...document.querySelectorAll('.allscan-connection-network-map-link')].find(element=>element.getAttribute('aria-label')?.includes('123456'))`
const nodeGroup = id => `document.querySelector('.asr-bubble-frame [data-node="${id}"]')`
const actionButton = label => `[...document.querySelectorAll('.asr-bubble-actions button')].find(element=>element.textContent.trim()==='${label}')`
const saveScreenshot = async name => {
  const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true })
  const directory = join(process.cwd(), 'artifacts', 'issue-207')
  await mkdir(directory, { recursive: true })
  await writeFile(join(directory, name), Buffer.from(shot.data, 'base64'))
}
const openChart = async () => {
  await evaluate("document.addEventListener('click',event=>window.__asrLastClick={tag:event.target.tagName,cls:event.target.className,text:event.target.textContent},{capture:true,once:true})")
  if (await evaluate('innerWidth<=650')) {
    await evaluate(`(${chartLink})?.focus({preventScroll:false})`)
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
  } else {
    await click(chartLink)
  }
  try {
    await until(() => evaluate("document.querySelector('.asr-bubble-dialog')?.open && document.querySelectorAll('.asr-bubble-frame [data-node]').length===6"), 'Bubble topology did not open with six fixture stations')
  } catch (error) {
    const state = await evaluate(`(() => {const link=${chartLink},r=link?.getBoundingClientRect();return {open:document.querySelector('.asr-bubble-dialog')?.open||false,nodes:[...document.querySelectorAll('.asr-bubble-frame [data-node]')].map(n=>n.dataset.node),status:document.querySelector('.asr-bubble-footer [role="status"]')?.textContent||'',links:[...document.querySelectorAll('.allscan-connection-network-map-link')].map(a=>a.getAttribute('aria-label')),linkRect:r&&{left:r.left,top:r.top,width:r.width,height:r.height},hit:r&&document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)?.outerHTML,lastClick:window.__asrLastClick||null}})()`)
    throw new Error(`${error.message}: ${JSON.stringify(state)}`)
  }
}

try {
  let target
  await until(async () => {
    try { const response = await fetch(`http://127.0.0.1:${chromePort}/json/list`); if (!response.ok) return false; target = (await response.json()).find(item => item.type === 'page'); return Boolean(target?.webSocketDebuggerUrl) } catch { return false }
  }, 'Chrome debugging endpoint unavailable')
  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, { once: true }); ws.addEventListener('error', reject, { once: true }) })
  ws.addEventListener('message', ({ data }) => {
    const response = JSON.parse(data); if (!response.id) return
    const call = pending.get(response.id); if (!call) return
    pending.delete(response.id); response.error ? call.reject(new Error(response.error.message)) : call.resolve(response.result)
  })
  await send('Page.enable'); await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', { width: 1365, height: 900, deviceScaleFactor: 1, mobile: false })
  await send('Page.addScriptToEvaluateOnNewDocument', { source: "localStorage.setItem('asrFavoritesOpen.v1','1');localStorage.setItem('asrWhatsNewSeenVersion.v1','1.0.0-beta.8.6')" })
  await send('Page.navigate', { url: `http://127.0.0.1:${vitePort}/asr/` })
  await until(() => evaluate(`${chartLink} && document.querySelectorAll('.allscan-favorites-table tbody tr').length>=3`), 'Dashboard fixtures did not load')

  await openChart()
  await delay(1200)
  assert.equal(await evaluate("document.querySelector('.asr-bubble-dialog')?.open"), true, 'Chart closed during live dashboard updates')
  const layout = await evaluate(`(() => {
    const dialog=document.querySelector('.asr-bubble-dialog'),frame=document.querySelector('.asr-bubble-frame'),svg=frame.querySelector('svg'),root=frame.querySelector('.asr-bubble-root circle'),node=frame.querySelector('[data-node="234567"] circle');
    const d=dialog.getBoundingClientRect(),f=frame.getBoundingClientRect(),s=svg.getBoundingClientRect();
    return {dialog:{left:d.left,top:d.top,right:d.right,bottom:d.bottom},frame:{width:f.width,height:f.height},svg:{width:s.width,height:s.height},rootRadius:root.getAttribute('r'),nodeRadius:node.getAttribute('r'),path:location.pathname};
  })()`)
  assert.ok(layout.dialog.left >= 0 && layout.dialog.top >= 0 && layout.dialog.right <= 1365 && layout.dialog.bottom <= 900, 'Dialog is outside desktop viewport')
  assert.ok(layout.svg.width <= layout.frame.width && layout.svg.height <= layout.frame.height, `Initial Fill must fit the graph: ${JSON.stringify(layout)}`)
  assert.equal(layout.rootRadius, '26'); assert.equal(layout.nodeRadius, '16'); assert.equal(layout.path, '/asr/')

  await click(nodeGroup('234567'))
  await until(() => evaluate("document.querySelector('[data-station-panel]')?.textContent.includes('W7ALP')"), 'Station details did not load')
  const station = await evaluate(`(() => {
    const p=document.querySelector('[data-station-panel]'),b=document.querySelector('.asr-bubble-body'),r=p.getBoundingClientRect(),br=b.getBoundingClientRect();
    return {top:r.top-br.top,right:br.right-r.right,text:p.textContent,position:getComputedStyle(p).position};
  })()`)
  assert.ok(station.top <= 16 && station.right <= 20, `Station panel is not upper-right: ${JSON.stringify(station)}`)
  for (const value of ['Callsign', 'Description', 'Location', 'Connections', 'W7ALP', 'Mesa, Arizona', '456789']) assert.ok(station.text.includes(value), `Station panel missing ${value}`)
  assert.equal(station.position, 'absolute')
  await saveScreenshot('bubble-desktop-standard.png')

  const beforeCollapse = await evaluate("document.querySelectorAll('.asr-bubble-frame [data-node]').length")
  await doubleClick(nodeGroup('234567'))
  await until(() => evaluate("document.querySelectorAll('.asr-bubble-frame [data-node]').length===5"), 'Double-click did not collapse child stations')
  await doubleClick(nodeGroup('234567'))
  await until(() => evaluate(`document.querySelectorAll('.asr-bubble-frame [data-node]').length===${beforeCollapse}`), 'Double-click did not expand child stations')

  const beforeDrag = await evaluate(`(() => {const n=${nodeGroup('345678')};return {x:+n.dataset.x,y:+n.dataset.y}})()`)
  await drag(nodeGroup('345678'), 80, 55)
  await until(() => evaluate(`(() => {const n=${nodeGroup('345678')};return Math.abs(+n.dataset.x-${beforeDrag.x})>20&&Math.abs(+n.dataset.y-${beforeDrag.y})>20})()`), 'Pointer drag did not reposition the station')

  const panelBeforeScroll = await center("document.querySelector('[data-station-panel]')")
  for (let i = 0; i < 6; i += 1) await click(actionButton('+ Zoom'))
  await until(() => evaluate("document.querySelector('.asr-bubble-frame').scrollWidth>document.querySelector('.asr-bubble-frame').clientWidth && document.querySelector('.asr-bubble-frame').scrollHeight>document.querySelector('.asr-bubble-frame').clientHeight"), 'Zoom did not create horizontal and vertical scroll ranges')
  const frameCenter = await center("document.querySelector('.asr-bubble-frame')")
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: frameCenter.x, y: frameCenter.y })
  await send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: frameCenter.x, y: frameCenter.y, deltaX: 360, deltaY: 320 })
  await until(() => evaluate("document.querySelector('.asr-bubble-frame').scrollLeft>0 && document.querySelector('.asr-bubble-frame').scrollTop>0"), 'Actual wheel input did not scroll both chart axes')
  const panelAfterScroll = await center("document.querySelector('[data-station-panel]')")
  assert.ok(Math.abs(panelBeforeScroll.left - panelAfterScroll.left) < 2 && Math.abs(panelBeforeScroll.top - panelAfterScroll.top) < 2, 'Station panel moved with graph scrolling')
  const zoomed = Number(await evaluate("document.querySelector('.asr-bubble-frame').dataset.zoom"))
  await click(actionButton('− Zoom'))
  assert.ok(Number(await evaluate("document.querySelector('.asr-bubble-frame').dataset.zoom")) < zoomed, 'Zoom Out did not reduce zoom')
  await click(actionButton('Fill'))
  await until(() => evaluate("document.querySelector('.asr-bubble-frame').dataset.zoom==='1.000' && document.querySelector('.asr-bubble-frame').scrollLeft===0 && document.querySelector('.asr-bubble-frame').scrollTop===0"), 'Fill did not restore fit and origin')
  const standardColors = await evaluate(`(() => {const f=document.querySelector('.asr-bubble-frame'),c=document.querySelector('.asr-bubble-frame [data-node="234567"] circle');return {background:getComputedStyle(f).backgroundColor,fill:getComputedStyle(c).fill}})()`)
  assert.equal(await evaluate("document.querySelectorAll('.asr-bubble-footer a, .asr-bubble-footer button').length"), 0, 'Removed footer controls must be absent')
  await click(actionButton('Close'))
  await until(() => evaluate("!document.querySelector('.asr-bubble-dialog')"), 'Header Close did not dismiss chart')
  assert.equal(await evaluate('location.pathname'), '/asr/', 'Close navigated away from ASR')

  await click("document.querySelector('.allscan-menu-button')")
  await click("[...document.querySelectorAll('.allscan-menu-proxy-row')].find(element=>element.textContent.includes('Theme'))")
  await click("[...document.querySelectorAll('.allscan-submenu-theme button')].find(element=>element.textContent.trim()==='Matrix')")
  await until(() => evaluate("document.documentElement.dataset.asrTheme==='matrix'"), 'Theme UI did not switch to Matrix')
  await openChart()
  const matrixColors = await evaluate(`(() => {const f=document.querySelector('.asr-bubble-frame'),c=document.querySelector('.asr-bubble-frame [data-node="234567"] circle');return {background:getComputedStyle(f).backgroundColor,fill:getComputedStyle(c).fill}})()`)
  assert.notDeepEqual(matrixColors, standardColors, 'Chart colors did not follow active ASR theme')
  await click(actionButton('Close'))
  await until(() => evaluate("!document.querySelector('.asr-bubble-dialog')"), 'Close did not dismiss themed chart')
  assert.equal(await evaluate('location.pathname'), '/asr/', 'Close navigated to original AllScan')

  for (let i = 0; i < 3; i += 1) {
    await openChart()
    await click(actionButton('Close'))
    await until(() => evaluate("!document.querySelector('.asr-bubble-dialog')"), `Repeated close ${i + 1} failed`)
  }
  assert.ok(await evaluate("document.querySelectorAll('.allscan-favorites-table tbody tr').length>=3"), 'Favorites disappeared after chart interactions')

  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  await send('Page.navigate', { url: `http://127.0.0.1:${vitePort}/asr/` })
  await until(() => evaluate(`Boolean(${chartLink})`), 'Mobile dashboard chart link did not load')
  await openChart()
  await click(nodeGroup('234567'))
  await until(() => evaluate("document.querySelector('[data-station-panel]')?.textContent.includes('W7ALP')"), 'Mobile station panel did not load')
  const mobile = await evaluate(`(() => {const d=document.querySelector('.asr-bubble-dialog').getBoundingClientRect(),b=document.querySelector('.asr-bubble-body').getBoundingClientRect(),p=document.querySelector('[data-station-panel]').getBoundingClientRect();return {dialog:{left:d.left,top:d.top,right:d.right,bottom:d.bottom},panel:{top:p.top-b.top,right:b.right-p.right,width:p.width},viewport:{width:innerWidth,height:innerHeight}}})()`)
  assert.ok(mobile.dialog.left >= 0 && mobile.dialog.top >= 0 && mobile.dialog.right <= mobile.viewport.width && mobile.dialog.bottom <= mobile.viewport.height, `Mobile dialog overflow: ${JSON.stringify(mobile)}`)
  assert.ok(mobile.panel.top <= 14 && mobile.panel.right <= 14 && mobile.panel.width <= 370, `Mobile station panel is not upper-right: ${JSON.stringify(mobile)}`)
  await saveScreenshot('bubble-mobile-matrix.png')
  console.log('PASS #207: desktop/mobile layout, fixed upper-right details, click, double-click, drag, zoom, Fill, two-axis scroll, theme, repeat open/close, and ASR-safe navigation')
} finally {
  ws?.close(); chrome.kill('SIGTERM'); vite.kill('SIGTERM')
  await delay(250)
  await rm(profile, { recursive: true, force: true })
}
