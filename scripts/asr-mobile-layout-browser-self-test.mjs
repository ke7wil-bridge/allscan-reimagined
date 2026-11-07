#!/usr/bin/env node
import { existsSync } from 'node:fs'
/** Read-only responsive dashboard smoke test (#128); no bridge actions. */
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const assert = (v, m) => { if (!v) throw new Error(m) }
const delay = ms => new Promise(r => setTimeout(r, ms))
const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', '4188', '--strictPort'], { env: { ...process.env, ASR_MOCK: '1' }, stdio: 'ignore' })
const profile = await mkdtemp(join(tmpdir(), 'asr-mobile-layout-'))
const browser = spawn(process.env.CHROME_BIN || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : '/usr/bin/chromium-browser'), ['--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--remote-debugging-port=9388', '--user-data-dir=' + profile, 'about:blank'], { stdio: 'ignore' })
let ws
const pending = new Map()
let nextId = 0
const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++nextId; pending.set(id, {resolve,reject}); ws.send(JSON.stringify({id,method,params})) })
const evaluate = async expression => { const x = await send('Runtime.evaluate', {expression,returnByValue:true,awaitPromise:true}); if (x.exceptionDetails) throw new Error(x.exceptionDetails.text); return x.result.value }
try {
  let target
  for (let i = 0; i < 120; i++) {
    try { const x = await fetch('http://127.0.0.1:9388/json/list'); if (x.ok) { target = (await x.json()).find(t => t.type === 'page'); if (target?.webSocketDebuggerUrl) break } } catch {}
    await delay(100)
  }
  assert(target?.webSocketDebuggerUrl, 'Chromium CDP unavailable')
  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve,reject) => { ws.addEventListener('open',resolve,{once:true}); ws.addEventListener('error',reject,{once:true}) })
  ws.addEventListener('message', ({data}) => { const x=JSON.parse(data); if (!x.id) return; const p=pending.get(x.id); if (!p) return; pending.delete(x.id); x.error ? p.reject(new Error(x.error.message)) : p.resolve(x.result) })
  await send('Page.enable')
  await send('Runtime.enable')
  for (const [name,width,height] of [['portrait',390,844],['landscape',844,390],['narrow',360,740],['desktop',1440,900]]) {
    await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<1000})
    await send('Page.navigate',{url:'http://127.0.0.1:4188/asr/'})
    let loaded=false
    for (let i=0;i<100;i++) { await delay(100); try { if (await evaluate("document.readyState === 'complete' && document.body.innerText.includes('Connection Status')")) { loaded=true; break } } catch {} }
    assert(loaded, name + ': dashboard failed to load')
    const result=await evaluate(`(() => {
      const viewport=document.documentElement.clientWidth;
      const visible=[...document.querySelectorAll('button')].filter(e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'});
      return {viewport,scrollWidth:document.documentElement.scrollWidth,buttons:visible.length,clipped:visible.filter(e=>{const r=e.getBoundingClientRect();return r.left < -2 || r.right > viewport+2}).slice(0,8).map(e=>e.textContent.trim().slice(0,60))};
    })()`)
    assert(result.buttons>0,name+': no visible controls')
    assert(result.scrollWidth<=result.viewport+2,name+': horizontal page overflow '+JSON.stringify(result))
    assert(result.clipped.length===0,name+': clipped controls '+JSON.stringify(result.clipped))
    console.log(name+': PASS '+result.viewport+'px, '+result.buttons+' visible controls')
  }
} finally {
  ws?.close()
  browser.kill('SIGTERM')
  vite.kill('SIGTERM')
  await delay(300)
  await rm(profile,{recursive:true,force:true})
}
