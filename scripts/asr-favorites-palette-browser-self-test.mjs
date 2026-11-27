#!/usr/bin/env node
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { chromium, firefox, webkit } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'

const listener = createServer()
await new Promise((resolve,reject) => { listener.once('error',reject); listener.listen(0,'127.0.0.1',resolve) })
const port = listener.address().port
await new Promise(resolve => listener.close(resolve))
const url = `http://127.0.0.1:${port}/asr/`
const origin = new URL(url).origin
const vite = spawn('./node_modules/.bin/vite',['--host','127.0.0.1','--port',String(port),'--strictPort'],{env:{...process.env,ASR_MOCK:'1'},stdio:'ignore'})
const version = JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8')).version
const cases = [
  ['light-390',390,844,'standard','light'],
  ['light-320',320,740,'standard','light'],
  ['light-360',360,780,'standard','light'],
  ['dark-390',390,844,'standard','dark'],
  ['matrix-390',390,844,'matrix','dark'],
  ['ocean-390',390,844,'deep-ocean-animated','dark'],
  ['lcars-390',390,844,'lcars-frame','dark'],
  ['light-600',600,850,'standard','light'],
  ['light-620',620,850,'standard','light'],
  ['light-621',621,850,'standard','light'],
  ['light-720',720,850,'standard','light'],
  ['light-721',721,850,'standard','light'],
  ['landscape',844,390,'standard','light'],
  ['desktop',1440,900,'standard','dark'],
]
const engine=process.env.ASR_PALETTE_BROWSER||'chromium'
const browserType={chromium,firefox,webkit}[engine]
assert(browserType,'Unsupported test browser')
let browser
try {
  let ready=false
  for(let i=0;i<120;i++){try{if((await fetch(url,{signal:AbortSignal.timeout(1000)})).ok){ready=true;break}}catch{} await new Promise(r=>setTimeout(r,100))}
  assert(ready,'Vite unavailable')
  const executablePath=process.env.ASR_PALETTE_BROWSER_BIN||(engine==='chromium'?process.env.CHROME_BIN:undefined)
  browser=await browserType.launch({headless:true,...(executablePath?{executablePath}:{})})
  for(const [name,width,height,theme,mode] of cases){
    const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1,hasTouch:width<900,reducedMotion:'reduce'})
    const mutations=[]
    const unexpected=[]
    try {
      await page.addInitScript(({version,theme,mode})=>{
        localStorage.setItem('asrThemeSettings.v1',JSON.stringify({theme,mode,reduceBrowserActivity:true}))
        localStorage.setItem('asrFavoritesOpen.v1','1')
        localStorage.setItem('asrWhatsNewSeenVersion.v1',version)
      },{version,theme,mode})
      await page.route('**/*',async route=>{
        const req=route.request(),u=new URL(req.url())
        if(u.origin!==origin)return route.abort()
        if(u.pathname.endsWith('/asr-api.php')&&u.searchParams.get('action')==='favorite-manage'&&req.method()==='POST'){
          mutations.push(Object.fromEntries(new URLSearchParams(req.postData()||'')))
          return route.fulfill({json:{ok:true}})
        }
        // This POST retrieves read-only statistics from the local mock.
        if(u.pathname==='/asr/stats/stats.php'&&req.method()==='POST')return route.continue()
        if(!['GET','HEAD','OPTIONS'].includes(req.method())){
          unexpected.push(req.method()+':'+u.pathname)
          return route.fulfill({status:403,json:{ok:false,error:'Isolated browser fixture'}})
        }
        if(u.pathname.endsWith('/asr-api.php')&&u.searchParams.get('action')==='runtime-config'){
          const response=await route.fetch(),j=await response.json()
          j.versionLabel='v'+version.replace('-beta.',' Beta ')
          return route.fulfill({json:j})
        }
        return route.continue()
      })
      await page.goto(url,{waitUntil:'domcontentloaded'})
      const trigger=page.getByRole('button',{name:/Choose color for node/}).first()
      await trigger.waitFor({timeout:30000})
      await page.evaluate(()=>document.fonts.ready)
      await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important} html{scroll-behavior:auto!important}'})
      const checkGeometry=async state=>{
        const result=await page.evaluate(()=>{
          const panel=document.querySelector('.allscan-favorites-inner').getBoundingClientRect()
          const bounds=e=>{const r=e.getBoundingClientRect();return [r.left,r.right,r.width,r.height]}
          const tools=[...document.querySelectorAll('.allscan-favorites-tools > button,.allscan-favorites-tools > a')]
            .map(e=>({label:e.textContent.trim(),bounds:bounds(e)}))
          const palette=document.querySelector('.allscan-favorite-color-popover')
          return {viewport:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,panel:[panel.left,panel.right],tools,palette:palette?bounds(palette):null}
        })
        assert(result.scrollWidth<=result.viewport+1,name+' '+state+': horizontal overflow '+JSON.stringify(result))
        assert.equal(result.tools.length,5,name+' '+state+': missing Favorites tools')
        assert(result.tools.every(({bounds:[left,right,w,h]})=>left>=result.panel[0]-1&&right<=result.panel[1]+1&&w>0&&h>=25),name+' '+state+': clipped or misplaced Favorites tools '+JSON.stringify(result))
        if(result.palette)assert(result.palette[0]>=0&&result.palette[1]<=result.viewport+1&&result.palette[2]>=200,name+' '+state+': palette outside viewport')
      }
      await checkGeometry('closed')
      const node=(await trigger.getAttribute('aria-label')).replace('Choose color for node ','')
      await trigger.click()
      const dialog=page.getByRole('dialog',{name:/Favorite color for node/})
      await dialog.waitFor()
      await checkGeometry('open')
      const hex=dialog.locator('code')
      const original=await hex.innerText()
      const hue=dialog.getByRole('slider',{name:'Hue'})
      await hue.fill('120')
      assert.match(await hex.innerText(),/^#[0-9A-F]{6}$/)
      const field=dialog.locator('.allscan-favorite-color-field')
      await field.scrollIntoViewIfNeeded()
      const r=await field.boundingBox()
      assert(r&&r.width>0&&r.height>0)
      await page.mouse.move(r.x+r.width*.2,r.y+r.height*.2)
      await page.mouse.down()
      await page.mouse.move(r.x+r.width*.8,r.y+r.height*.7,{steps:3})
      await page.mouse.up()
      assert.match(await hex.innerText(),/^#[0-9A-F]{6}$/)
      await dialog.getByRole('button',{name:'Select #22c55e',exact:true}).click()
      assert.equal(await hex.innerText(),'#22C55E')
      await dialog.getByRole('button',{name:'Cancel',exact:true}).click()
      await dialog.waitFor({state:'hidden'})
      assert.equal(mutations.length,0,name+': Cancel must not save')
      await checkGeometry('cancel')
      await trigger.click()
      await dialog.waitFor()
      assert.equal(await hex.innerText(),original,name+': Cancel must restore original draft')
      await dialog.getByRole('button',{name:'Select #22c55e',exact:true}).click()
      await dialog.getByRole('button',{name:'Apply',exact:true}).click()
      await dialog.waitFor({state:'hidden',timeout:15000})
      assert.equal(mutations.length,1,name+': Apply should save exactly once')
      assert.equal(mutations[0].operation,'set-color')
      assert.equal(mutations[0].node,node)
      assert.equal(mutations[0].value,'#22c55e')
      await checkGeometry('applied')
      assert.deepEqual(unexpected,[])
      console.log('PASS',engine,name,'Favorites tools, palette, hue/drag, Cancel and Apply')
    }finally{await page.close()}
  }
}finally{await browser?.close();vite.kill('SIGTERM')}
