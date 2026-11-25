#!/usr/bin/env node
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
const assert = (v,m) => { if (!v) throw new Error(m) }
const delay = ms => new Promise(r => setTimeout(r,ms))
const listener = createServer()
await new Promise((resolve,reject) => {listener.once('error',reject);listener.listen(0,'127.0.0.1',resolve)})
const port = listener.address().port
await new Promise(resolve => listener.close(resolve))
const url = `http://127.0.0.1:${port}/asr/`
const vite = spawn('./node_modules/.bin/vite',['--host','127.0.0.1','--port',String(port),'--strictPort'],{env:{...process.env,ASR_MOCK:'1'},stdio:'ignore'})
let browser
try {
  let ready=false
  for(let i=0;i<120;i++){try{if((await fetch(url,{signal:AbortSignal.timeout(2000)})).ok){ready=true;break}}catch{} await delay(100)}
  assert(ready,'Vite unavailable')
  browser=await chromium.launch({headless:true,...(process.env.CHROME_BIN?{executablePath:process.env.CHROME_BIN}:{})})
  const page=await browser.newPage()
  const cdp=await page.context().newCDPSession(page)
  const evaluate=expression=>page.evaluate(expression)
  for (const [name,width,height] of [['galaxy-s25',360,780],['galaxy-s25-landscape',780,360],['portrait',390,844],['landscape',844,390],['narrow',360,740],['desktop',1440,900]]) {
    await cdp.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<1000})
    await page.goto(url,{waitUntil:'domcontentloaded',timeout:20000})
    let loaded=false
    for (let i=0;i<100;i++) { await delay(100); try { if (await evaluate("document.readyState === 'complete' && document.body.innerText.includes('Connection Status')")) { loaded=true; break } } catch {} }
    assert(loaded, name + ': dashboard failed to load')
    await evaluate(`(() => {
      const button=[...document.querySelectorAll('button')].find(e=>e.textContent.includes('Connected Clients'));
      if (button && !document.querySelector('.allscan-bridge-detail-box')) button.click();
    })()`)
    await delay(50)
    const result=await evaluate(`(() => {
      const viewport=document.documentElement.clientWidth;
      const visible=[...document.querySelectorAll('button')].filter(e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'});
      const clientLists=[...document.querySelectorAll('.allscan-bridge-detail-box')].map(e=>getComputedStyle(e).overflowY);
      return {viewport,scrollWidth:document.documentElement.scrollWidth,buttons:visible.length,clientLists,clipped:visible.filter(e=>{const r=e.getBoundingClientRect();return r.left < -2 || r.right > viewport+2}).slice(0,8).map(e=>e.textContent.trim().slice(0,60))};
    })()`)
    assert(result.buttons>0,name+': no visible controls')
    assert(result.scrollWidth<=result.viewport+2,name+': horizontal page overflow '+JSON.stringify(result))
    assert(result.clipped.length===0,name+': clipped controls '+JSON.stringify(result.clipped))
    assert(result.clientLists.every(value=>value!=='auto'&&value!=='scroll'),name+': bridge client list gained an internal scrollbar '+JSON.stringify(result.clientLists))

    assert(await evaluate("(() => { const button=document.querySelector('.allscan-menu-button'); button?.click(); return Boolean(button) })()"),name+': menu button missing')
    await delay(100)
    const menu = await evaluate(`(() => {
      const button=document.querySelector('.allscan-menu-button');
      const panel=document.querySelector('.allscan-menu-panel');
      if(!button||!panel) return {ok:false,reason:'missing'};
      const rect=panel.getBoundingClientRect();
      const visible=panel.getClientRects().length>0&&getComputedStyle(panel).visibility!=='hidden';
      const items=[...panel.querySelectorAll('a,button')].filter(item=>item.getClientRects().length>0).length;
      return {ok:visible&&items>0&&rect.left>=-2&&rect.right<=innerWidth+2&&rect.top>=-2&&rect.bottom<=innerHeight+2,rect:{left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom},items};
    })()`)
    assert(menu.ok,name+': menu missing, empty, or clipped '+JSON.stringify(menu))

    if (width < 900) {
      const gridFixture = await evaluate(`(() => {
        const existing=document.querySelector('.allscan-bridge-grid');
        if(!existing) return {ok:false,reason:'missing grid'};
        const grid=document.createElement('div');
        grid.className='allscan-bridge-grid';
        existing.parentElement.append(grid);
        try {
          const make=(isNet=false)=>{const el=document.createElement('article');el.className='allscan-bridge-card'+(isNet?' allscan-net-bridge-card':'');el.textContent='Bridge';grid.append(el);return el};
          const cards=Array.from({length:6},()=>make());
          const tops=cards.map(x=>x.getBoundingClientRect().top);
          const pairs=[0,2,4].every(i=>Math.abs(tops[i]-tops[i+1])<=1)&&tops[2]>tops[0]+1&&tops[4]>tops[2]+1;
          cards[5].classList.add('allscan-net-bridge-card');
          const penultimate=getComputedStyle(cards[4]).gridColumn;
          const net=getComputedStyle(cards[5]).gridColumn;
          return {ok:pairs&&penultimate==='1 / -1'&&net==='1 / -1',tops,penultimate,net};
        }finally{grid.remove()}
      })()`)
      assert(gridFixture.ok,name+': six Standard Bridge cards must pair without breaking the centered trailing Net Bridge '+JSON.stringify(gridFixture))
    }
    if(name==='desktop') {
      const bridgeLayout=await evaluate(`(() => {
        const grid=document.querySelector('.allscan-bridge-grid');
        if(!grid) return {ok:false,reason:'missing grid'};
        const existing=[...grid.querySelectorAll(':scope > .allscan-bridge-card')];
        if(!existing.length) return {ok:false,reason:'missing cards'};
        while(grid.querySelectorAll(':scope > .allscan-bridge-card').length<6) grid.append(existing[0].cloneNode(true));
        const cards=[...grid.querySelectorAll(':scope > .allscan-bridge-card')].slice(0,6);
        const rects=cards.map(card=>card.getBoundingClientRect());
        const widths=rects.map(rect=>rect.width);
        const tops=rects.map(rect=>rect.top);
        // At most five cards per row (#134): five share the first row and the
        // sixth wraps to a second row at the same width.
        const firstRow=tops.slice(0,5);
        return {ok:cards.length===6&&Math.max(...widths)-Math.min(...widths)<=1&&Math.max(...firstRow)-Math.min(...firstRow)<=1&&tops[5]>tops[0]+1,count:cards.length,widths,tops};
      })()`)
      assert(bridgeLayout.ok,'desktop: bridge cards are not five uniform cards per row '+JSON.stringify(bridgeLayout))
    }
    console.log(name+': PASS '+result.viewport+'px, '+result.buttons+' visible controls')
  }
} finally {
  await browser?.close()
  vite.kill('SIGTERM')
  await delay(300)

}
