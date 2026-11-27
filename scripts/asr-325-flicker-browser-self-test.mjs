import assert from 'node:assert/strict'
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { readFileSync } from 'node:fs'
const listener = createServer()
await new Promise((resolve,reject)=>{listener.once('error',reject);listener.listen(0,'127.0.0.1',resolve)})
const port=listener.address().port
await new Promise(resolve=>listener.close(resolve))
const url=`http://127.0.0.1:${port}/asr/`
const vite=spawn('./node_modules/.bin/vite',['--host','127.0.0.1','--port',String(port),'--strictPort'],{env:{...process.env,ASR_MOCK:'1'},stdio:'ignore'})
let browser
try {
  let ready=false
  for(let i=0;i<100;i++){try{if((await fetch(url,{signal:AbortSignal.timeout(1000)})).ok){ready=true;break}}catch{} await new Promise(r=>setTimeout(r,100))}
  assert(ready,'Vite unavailable')
  browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})})
  const version=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8')).version
  for(const width of [1440,390]){
    const page=await browser.newPage({viewport:{width,height:900}})
    await page.addInitScript(version=>localStorage.setItem('asrWhatsNewSeenVersion.v1',version),version)
    let epoch=0, phase='initial', bridgeRequests=0, bridgeError=false, authError=false, authMode='admin', authRequests=0, holdBridge=false, activeBridgeRequests=0, maxOverlap=0
    const activity=Math.floor(Date.now()/1000)-60
    const log=[]
    await page.route('**/asr-api.php?*',async route=>{
      const action=new URL(route.request().url()).searchParams.get('action')
      if(action==='bridge-status'){
        bridgeRequests++
        log.push({action,phase,epoch,bridgeError,at:Date.now()})
        const responseEpoch=epoch
        if(holdBridge){
          activeBridgeRequests++
          maxOverlap=Math.max(maxOverlap,activeBridgeRequests)
          await new Promise(resolve=>setTimeout(resolve,400))
          activeBridgeRequests--
        }
        if(bridgeError)return route.fulfill({status:503,json:{ok:false,error:'fixture unavailable'}})
        return route.fulfill({json:{ok:true,bridge:responseEpoch?{dmr:{role:'idle',activity_epoch:responseEpoch}}:{},clients:{},controls:{}}})
      }
      if(action==='auth-status'){
        authRequests++
        log.push({action,authError,at:Date.now()})
        if(authError)return route.fulfill({status:503,json:{ok:false,error:'fixture unavailable'}})
        if(authMode==='malformed')return route.fulfill({json:{ok:true,canRead:'true',canModify:'true'}})
        if(authMode==='read-only')return route.fulfill({json:{ok:true,loggedIn:true,username:'mock-admin',permission:'2',publicPermission:0,canRead:true,canModify:false,canWrite:false,isAdmin:false}})
        if(authMode==='denied')return route.fulfill({json:{ok:true,loggedIn:false,username:'',permission:0,publicPermission:0,canRead:false,canModify:false,canWrite:false,isAdmin:false}})
      }
      return route.continue()
    })
    const activityLabel=page.locator('[data-dashboard-module="bridges"] .allscan-section-subtitle')
    const controls=page.locator('[data-dashboard-module="controls"]')
    await page.goto(url,{waitUntil:'domcontentloaded'})
    await controls.waitFor()
    await activityLabel.waitFor()
    assert.match(await activityLabel.innerText(),/--:--:--/)
    await page.evaluate(()=>{
      window.__flickerTrace=[]
      const sample=()=>{
        const activity=document.querySelector('[data-dashboard-module="bridges"] .allscan-section-subtitle')?.textContent
        const controls=document.querySelector('[data-dashboard-module="controls"]')
        const sample={activity,controls:!!controls,connectDisabled:controls?.querySelector('.allscan-connect-button')?.disabled}
        const value=JSON.stringify(sample)
        if(window.__flickerTrace.at(-1)?.value!==value)window.__flickerTrace.push({at:Date.now(),value})
      }
      new MutationObserver(sample).observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true})
      sample()
    })
    phase='valid';epoch=activity
    await page.waitForFunction(()=>!document.querySelector('[data-dashboard-module="bridges"] .allscan-section-subtitle')?.textContent.includes('--:--:--'))
    const verified=await activityLabel.innerText()
    if(process.env.ASR_EVIDENCE_DIR)await page.screenshot({path:`${process.env.ASR_EVIDENCE_DIR}/${width}-valid.png`})
    phase='missing';epoch=0
    async function pollSamples(samples=3){
      const count=bridgeRequests, deadline=Date.now()+20_000
      while(bridgeRequests<count+samples && Date.now()<deadline)await page.waitForTimeout(100)
      assert(bridgeRequests>=count+samples,'bridge polling did not recover')
      await page.waitForTimeout(250)
    }
    await pollSamples()
    const missing=await activityLabel.innerText()
    if(process.env.ASR_EVIDENCE_DIR)await page.screenshot({path:`${process.env.ASR_EVIDENCE_DIR}/${width}-missing.png`})
    assert.equal(missing,verified,'missing data erased verified activity')
    for(const [name,value] of [['invalid','not-an-epoch'],['future',activity+86400],['older',activity-60]]){
      phase=name;epoch=value
      await pollSamples(2)
      assert.equal(await activityLabel.innerText(),verified,name+' regressed activity')
    }
    phase='network-error';bridgeError=true
    await pollSamples(1)
    assert.equal(await activityLabel.innerText(),verified,'HTTP failure erased activity')
    bridgeError=false;phase='newer';epoch=activity+10
    const newer=await page.evaluate(value=>'LAST ACTIVITY: '+new Date(value*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}),epoch)
    await page.waitForFunction(expected=>document.querySelector('[data-dashboard-module="bridges"] .allscan-section-subtitle')?.textContent.trim()===expected,newer)
    phase='visibility-overlap';holdBridge=true
    const deadline=Date.now()+10000
    while(!activeBridgeRequests && Date.now()<deadline)await page.waitForTimeout(20)
    assert(activeBridgeRequests,'no delayed request observed')
    await page.evaluate(()=>{for(let i=0;i<5;i++)document.dispatchEvent(new Event('visibilitychange'))})
    await page.waitForTimeout(600)
    holdBridge=false
    assert.equal(maxOverlap,1,'visibility events started overlapping poll chains')
    await pollSamples(2)
    assert.equal(await activityLabel.innerText(),newer)
    // Identical verified auth snapshots must not remount controls or change
    // layout across normal polling and focus/visibility refreshes.
    const connect=controls.locator('.allscan-connect-button')
    const before=await controls.boundingBox()
    const element=await controls.elementHandle()
    const count=authRequests
    for(let i=0;i<5;i++){
      await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
      await page.waitForTimeout(300)
    }
    assert(authRequests>count,'auth refresh not observed')
    assert(await element.evaluate(el=>el.isConnected && el===document.querySelector('[data-dashboard-module="controls"]')))
    assert.deepEqual(await controls.boundingBox(),before,'verified auth polling moved Node Controls')
    assert(await connect.isEnabled())
    // A real permission downgrade must take effect immediately.
    authMode='read-only'
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
    await page.waitForFunction(()=>document.querySelector('.allscan-connect-button')?.disabled===true)
    assert.equal(await controls.getByRole('button',{name:'Manage Kicks & Bans'}).count(),0)
    authMode='admin'
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
    await page.waitForFunction(()=>document.querySelector('.allscan-connect-button')?.disabled===false)
    // Uncertainty still closes the whole gate immediately. First transient
    // failure automatically retries quickly, without focus/wake assistance.
    authError=true
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
    await page.getByText('Verifying dashboard access…').waitFor()
    assert.equal(await controls.count(),0,'protected controls must not remain rendered on auth error')
    if(process.env.ASR_EVIDENCE_DIR)await page.screenshot({path:`${process.env.ASR_EVIDENCE_DIR}/${width}-auth-error.png`})
    authError=false
    await controls.waitFor({timeout:4000})
    assert(await connect.isEnabled())
    authMode='malformed'
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
    await page.getByText('Verifying dashboard access…').waitFor()
    assert.equal(await controls.count(),0,'malformed authorization must fail closed')
    authMode='admin'
    await controls.waitFor({timeout:4000})
    assert(await connect.isEnabled())
    console.log(JSON.stringify({width,verified,missing,newer,maxOverlap,authRequests,network:log,dom:await page.evaluate(()=>window.__flickerTrace)}))
    authMode='denied'
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
    await page.waitForURL('**/asr/user/')
    assert.equal(await controls.count(),0,'explicit denial did not remove protected controls')
    await page.close()
  }
} finally {await browser?.close();vite.kill('SIGTERM')}
