#!/usr/bin/env node
// Read-only Chrome/CDP regression for #207 and #230. Runs against Vite mock data, never live nodes.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const chromePort = 9394, vitePort = 4194
const vite = spawn('./node_modules/.bin/vite', ['--host','127.0.0.1','--port',String(vitePort),'--strictPort'], {env:{...process.env,ASR_MOCK:'1'},stdio:'ignore'})
const profile = await mkdtemp(join(tmpdir(),'asr-johnny2-navigation-'))
const chrome = spawn(process.env.CHROME_BIN || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : '/usr/bin/chromium'), [
  '--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage',`--remote-debugging-port=${chromePort}`,`--user-data-dir=${profile}`,'about:blank'
], {stdio:'ignore'})
let ws
let nextId = 1
const pending = new Map()
const send = (method, params={}) => new Promise((resolve,reject) => {
  const id=nextId++; pending.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}))
})
const evaluate = async expression => {
  const response = await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true})
  if(response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails.exception?.description || response.exceptionDetails))
  return response.result?.value
}
const until = async (fn,message) => {
  for(let i=0;i<100;i++) {try {if(await fn()) return} catch {} await delay(100)}
  throw new Error(message)
}
try {
  let target
  await until(async () => {
    try {const response=await fetch(`http://127.0.0.1:${chromePort}/json/list`);if(!response.ok)return false;target=(await response.json()).find(item=>item.type==='page');return Boolean(target?.webSocketDebuggerUrl)} catch{return false}
  },'Chrome debugging endpoint unavailable')
  ws=new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve,reject) => {ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true})})
  ws.addEventListener('message', ({data}) => {
    const response=JSON.parse(data); if(!response.id)return
    const pendingCall=pending.get(response.id);if(!pendingCall)return
    pending.delete(response.id);response.error?pendingCall.reject(new Error(response.error.message)):pendingCall.resolve(response.result)
  })
  await send('Page.enable');await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride',{width:1280,height:540,deviceScaleFactor:1,mobile:false})
  await send('Page.addScriptToEvaluateOnNewDocument',{source:"window.localStorage.setItem('asrFavoritesOpen.v1','1')"})
  await send('Page.navigate',{url:`http://127.0.0.1:${vitePort}/asr/`})
  await until(() => evaluate("document.querySelectorAll('.allscan-connection-network-map-link').length>0 && document.querySelectorAll('.allscan-favorites-table tbody tr').length>=3"),'Dashboard chart and Favorites did not load')
  const launch=await evaluate(`(() => {
    const link=document.querySelector('.allscan-connection-network-map-link')
    const href=link.getAttribute('href')
    link.click()
    return {href,url:location.pathname}
  })()`)
  assert.equal(launch.href,'/asr/','Bubble link must not navigate to classic AllScan/Lookup on modified click')
  assert.equal(launch.url,'/asr/','Bubble launch navigated away from ASR')
  await until(() => evaluate("document.querySelector('.asr-bubble-dialog')?.open"),'Native bubble modal did not open')
  await delay(900)
  assert.equal(await evaluate("document.querySelector('.asr-bubble-dialog')?.open"),true,'Modal closed during dashboard rerender')
  const beforeReturn=await evaluate(`(() => {const d=document.querySelector('.asr-bubble-dialog');return {url:location.pathname,external:d.querySelector('.asr-bubble-footer a')?.target,returnText:d.querySelector('.asr-bubble-footer button')?.textContent}})()`)
  assert.equal(beforeReturn.url,'/asr/')
  assert.equal(beforeReturn.external,'_blank','External fallback must not replace ASR tab')
  assert.equal(beforeReturn.returnText,'Return to ASR')
  await evaluate("document.querySelector('.asr-bubble-footer-actions button').click()")
  await until(() => evaluate("!document.querySelector('.asr-bubble-dialog')"),'Bubble did not close')
  assert.equal(await evaluate('location.pathname'),'/asr/','Return to ASR changed to classic AllScan page')
  console.log('PASS #207: native dialog opens, survives rerender, return stays /asr/, fallback opens separate tab')

  const started=await evaluate(`(() => {
    window.scrollTo(0,Math.min(900,document.documentElement.scrollHeight-innerHeight))
    const rows=[...document.querySelectorAll('.allscan-favorites-table tbody tr')]
    const handle=rows.at(-1).querySelector('button.allscan-favorite-drag')
    window.__asrDragData=new DataTransfer()
    handle.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData,clientY:innerHeight-10}))
    return {scrollY:scrollY,scrollMax:document.documentElement.scrollHeight-innerHeight,lastNode:rows.at(-1).querySelector('.allscan-favorite-node-number')?.textContent}
  })()`)
  assert.ok(started.scrollY>150,'Test viewport was not scrollable '+JSON.stringify(started))
  await delay(100)
  const beforeUp=await evaluate('scrollY')
  await evaluate("window.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData,clientY:5}))")
  await delay(280)
  const afterUp=await evaluate('scrollY')
  assert.ok(afterUp<beforeUp-20,'Favorites drag must auto-scroll up while pointer is outside the rows '+JSON.stringify({started,beforeUp,afterUp}))
  await evaluate("window.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData,clientY:innerHeight-3}))")
  await delay(280)
  const afterDown=await evaluate('scrollY')
  assert.ok(afterDown>afterUp+20,'Favorites drag must auto-scroll down '+JSON.stringify({afterUp,afterDown}))
  await evaluate("document.querySelector('.allscan-favorites-table tbody tr button.allscan-favorite-drag').dispatchEvent(new DragEvent('dragend',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData}))")
  await delay(100)
  const stopped=await evaluate('scrollY')
  await delay(160)
  assert.equal(await evaluate('scrollY'),stopped,'Favorites must stop scrolling after drag ends')
  // Verify a bottom-to-top drop still reaches the existing reorder/persistence API.
  const sourceAndTarget=await evaluate(`(() => {
    window.__asrReorders=[]
    const oldFetch=window.fetch.bind(window)
    window.fetch=(url,options) => {
      if(String(url).includes('action=favorite-manage') && String(options?.body||'').includes('operation=reorder')) {
        window.__asrReorders.push(new URLSearchParams(options.body).get('value'))
        return Promise.resolve(new Response(JSON.stringify({ok:true}),{status:200,headers:{'Content-Type':'application/json'}}))
      }
      return oldFetch(url,options)
    }
    const rows=[...document.querySelectorAll('.allscan-favorites-table tbody tr')]
    const source=rows.at(-1).querySelector('.allscan-favorite-node-number').textContent
    const target=rows[0].querySelector('.allscan-favorite-node-number').textContent
    window.__asrDragData=new DataTransfer()
    rows.at(-1).querySelector('button.allscan-favorite-drag').dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData,clientY:innerHeight-8}))
    return {source,target}
  })()`)
  await delay(100)
  await evaluate("document.querySelector('.allscan-favorites-table tbody tr').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:window.__asrDragData,clientY:10}))")
  await until(() => evaluate('window.__asrReorders?.length>0'),'Dropping Favorites did not call the reorder persistence API')
  const writtenOrder=JSON.parse(await evaluate('window.__asrReorders[0]'))
  assert.equal(writtenOrder[0],sourceAndTarget.source,'Last Favorite was not moved to first persisted position')
  assert.ok(writtenOrder.includes(sourceAndTarget.target),'Existing Favorites were lost during drag reordering')
  console.log('PASS #230: Favorites scrolls both directions, stops after dragend, and persists bottom-to-top reorder')
} finally {
  ws?.close();chrome.kill('SIGTERM');vite.kill('SIGTERM')
  await delay(250)
  await rm(profile,{recursive:true,force:true})
}
