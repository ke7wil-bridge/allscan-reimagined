#!/usr/bin/env node
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
const listener = createServer()
await new Promise((resolve,reject) => {listener.once('error',reject);listener.listen(0,'127.0.0.1',resolve)})
const port = listener.address().port
await new Promise(resolve => listener.close(resolve))
const url = `http://127.0.0.1:${port}/asr/`
const vite = spawn('./node_modules/.bin/vite',['--host','127.0.0.1','--port',String(port),'--strictPort'],{env:{...process.env,ASR_MOCK:'1'},stdio:'ignore'})
let browser
try {
  let ready=false
  for(let i=0;i<100;i++){try{if((await fetch(url,{signal:AbortSignal.timeout(1000)})).ok){ready=true;break}}catch{} await new Promise(r=>setTimeout(r,100))}
  assert(ready,'Vite unavailable')
  browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})})
  const page=await browser.newPage({viewport:{width:1440,height:900}})
  let saved='First custom name';const requests=[]
  const version = JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8')).version
  await page.addInitScript(version=>{localStorage.setItem('asrFavoritesOpen.v1','1');localStorage.setItem('asrWhatsNewSeenVersion.v1',version)},version)
  page.setDefaultTimeout(10000)
  await page.route('**/asr-api.php?*', async route=>{
    const req=route.request(), query=new URL(req.url()).searchParams
    if(query.get('action')==='favorites') {
      const file=query.get('favsfile') || 'favorites.ini'
      const custom=file==='favorites.ini'?saved:'Other list name'
      const row=(node,name,description)=>({node,index:node,label:name,name,description:description||name,customDescription:description,desc:description||name,frequency:'',referenceDesc:'',location:'',color:'',rx:'',lcnt:'',href:''})
      return route.fulfill({json:{ok:true,selectedFile:file,files:['favorites.ini','favorites-other.ini'].map(value=>({value,label:value,selected:value===file})),filePath:'/etc/allscan/'+file,fileExists:true,fileReadable:true,rows:[row('100010','Public Test Node',custom),row('100011','Private Bridge Node','Must not replace bridge'),row('123456','External AllStar Test',''),row('100000','Local','Must not replace local'),row('3M6PBY','EchoLink','Must not replace EchoLink')]}})
    }
    if(query.get('action')==='favorite-manage') {
      const form=new URLSearchParams(req.postData()||'');requests.push(Object.fromEntries(form));assert.equal(form.get('node'),'100010');assert.equal(form.get('favsfile'),'favorites.ini');
      if(form.get('operation')==='update-description') saved=form.get('value')
      else if(form.get('operation')==='reset-favorite') saved=''
      else throw new Error('unexpected favorite operation')
      return route.fulfill({json:{ok:true}})
    }
    return route.continue()
  })
  const row=node=>page.locator('.allscan-connection-table tbody tr').filter({has:page.locator('td:first-child span',{hasText:new RegExp('^'+node+'$')})})
  const info=node=>row(node).locator('td').nth(1)
  async function expectText(locator,expected){await locator.waitFor({state:'visible'});await page.waitForFunction(({selector,text})=>[...document.querySelectorAll(selector)].some(e=>e.textContent.trim()===text),{selector:'.allscan-connection-table tbody tr td:nth-child(2)',text:expected});assert.equal((await locator.innerText()).trim(),expected)}
  await page.goto(url,{waitUntil:'domcontentloaded'})
  await expectText(info('100010'),'First custom name')
  assert.match(await info('100010').locator('a').getAttribute('title'),/Original node info: Public Test Node/)
  await expectText(info('100011'),'Private Bridge Node')
  await expectText(info('123456'),'External AllStar Test')
  const favorite=page.locator('.allscan-favorites-table tbody tr').filter({has:page.locator('.allscan-favorite-node-number',{hasText:/^100010$/})})
  await favorite.getByTitle('Edit friendly description').click()
  await page.getByLabel('Friendly description for 100010').fill('Renamed without changing node')
  await favorite.getByRole('button',{name:'Save',exact:true}).click()
  await expectText(info('100010'),'Renamed without changing node')
  assert.equal(requests[0].operation,'update-description')
  await page.reload({waitUntil:'domcontentloaded'})
  await expectText(info('100010'),'Renamed without changing node')
  await page.selectOption('#allscan-favsfile','favorites-other.ini')
  await expectText(info('100010'),'Other list name')
  await page.selectOption('#allscan-favsfile','favorites.ini')
  await expectText(info('100010'),'Renamed without changing node')
  await page.setViewportSize({width:390,height:844})
  await expectText(info('100010'),'Renamed without changing node')
  page.once('dialog',d=>d.accept())
  await favorite.getByTitle("Reset this Favorite's description and color").click()
  await expectText(info('100010'),'Public Test Node')
  assert.equal(requests.at(-1).operation,'reset-favorite')
  assert.equal(await info('100010').locator('a').getAttribute('href'),'/asr/')
  assert.equal(await row('100010').locator('td:first-child span').innerText(),'100010')
  await page.reload({waitUntil:'domcontentloaded'})
  await expectText(info('100010'),'Public Test Node')
  console.log('PASS #135 desktop/mobile browser rename, reset, reload, file selection, protected bridge and stable node link (fixture)')
} finally { await browser?.close();vite.kill('SIGTERM') }
