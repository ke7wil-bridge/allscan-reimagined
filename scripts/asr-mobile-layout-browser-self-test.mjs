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
  for (const [name,width,height] of [['galaxy-s25',360,780],['galaxy-s25-landscape',780,360],['portrait',390,844],['landscape',844,390],['narrow',360,740],['narrow-boundary',361,740],['narrow-middle',375,812],['narrow-upper',388,844],['compact-boundary',389,844],['tablet-portrait',768,1024],['tablet-landscape',1024,768],['desktop',1440,900]]) {
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
    if (width >= 361 && width <= 388 && height > width) {
      const manage = await page.locator('.allscan-kicks-bans-button').evaluate(button => {
        const rect=button.getBoundingClientRect();
        const row=button.closest('.allscan-controls-lower-row').getBoundingClientRect();
        const action=button.parentElement.querySelector('.allscan-controls-action-group').getBoundingClientRect();
        return {height:rect.height, width:rect.width, contained:rect.left>=row.left-1&&rect.right<=row.right+1, separateRow:rect.top>=action.bottom};
      });
      assert(manage.contained&&manage.separateRow&&manage.height>=29,name+': #329 management action overflow or lost touch target '+JSON.stringify(manage));
    }
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

    const dedicatedNetRows = await evaluate(`(() => {
      const existing=document.querySelector('.allscan-bridge-grid');
      if(!existing) return {ok:false,reason:'missing grid'};
      const widthForDesktop=()=>matchMedia('(min-width:1201px)').matches;
      const grid=document.createElement('div');
      grid.className='allscan-bridge-grid';
      existing.parentElement.append(grid);
      try {
        for(let count=0;count<=6;count++) {
          for(let slot=0;slot<=count;slot++) {
            grid.replaceChildren();
            const standards=Array.from({length:count},(_,i)=>{
              const el=document.createElement('article');el.className='allscan-bridge-card';
              el.textContent='Standard '+i;return el;
            });
            const net=document.createElement('article');
            net.className='allscan-bridge-card allscan-net-bridge-card';
            net.textContent='Net Bridge';
            const cards=[...standards];cards.splice(slot,0,net);grid.append(...cards);
            const r=net.getBoundingClientRect(),g=grid.getBoundingClientRect();
            const shared=standards.some(card=>{const c=card.getBoundingClientRect();return c.top<r.bottom-1&&c.bottom>r.top+1});
            const centered=Math.abs((r.left+r.right-g.left-g.right)/2)<=2;
            const contained=r.left>=g.left-1&&r.right<=g.right+1;
            if(widthForDesktop()) {
              const rows=[];
              for(const card of standards) {
                const c=card.getBoundingClientRect();
                let row=rows.find(x=>Math.abs(x.top-c.top)<=1);
                if(!row){row={top:c.top,left:c.left,right:c.right};rows.push(row)}
                else {row.left=Math.min(row.left,c.left);row.right=Math.max(row.right,c.right)}
              }
              if(rows.some(row=>Math.abs((row.left+row.right-g.left-g.right)/2)>2))
                return {ok:false,count,slot,reason:'uncentered Standard row'};
            }
            const equalWidth=standards.every(card=>Math.abs(card.getBoundingClientRect().width-r.width)<=2);
            if(shared||!centered||!contained||!equalWidth)
              return {ok:false,count,slot,shared,centered,contained,equalWidth,width:r.width,standardWidths:standards.map(c=>c.getBoundingClientRect().width),grid:g.width};
          }
        }
        // Render the actual control class structure rather than testing empty card boxes only.
        const base=existing.querySelector('.allscan-bridge-card:not(.allscan-net-bridge-card)');
        const standard=document.createElement('article');
        standard.className='allscan-bridge-card';
        standard.innerHTML=base.querySelector('.allscan-bridge-head').outerHTML+
          '<div class="allscan-bridge-row allscan-bridge-status-row allscan-standard-link-row"><span>Node 1005</span><i class="allscan-standard-link-dot"></i><b data-link-state="linked">Linked</b><button class="allscan-action-button">Disconnect</button></div>'+
          '<div class="allscan-bridge-body"><div class="allscan-bridge-row allscan-bridge-status-row"><span>Talking</span><b>–</b></div><div class="allscan-bridge-row allscan-bridge-status-row"><span>Last Talker</span><b>–</b></div></div>'+
          '<div class="allscan-bridge-detail-wrap"><button class="allscan-bridge-detail-title allscan-bridge-detail-toggle"><span>Connected Clients</span><b>0</b></button></div>'+
          '<div class="allscan-bridge-detail-wrap allscan-bridge-recent"><button class="allscan-bridge-detail-title allscan-bridge-detail-toggle"><span>Recent Activity</span><b>0</b></button></div>';
        const net=document.createElement('article');
        net.className='allscan-bridge-card allscan-net-bridge-card';
        for(const mode of ['dmr','ysf','p25','nxdn','m17']) {
          const m17=mode==='m17';
          const buttons='<div class="allscan-bridge-link-buttons'+(m17?' allscan-m17-link-buttons':'')+'"><button class="allscan-action-button">Connect</button><button class="allscan-action-button">Disconnect</button></div>';
          net.innerHTML=base.querySelector('.allscan-bridge-head').outerHTML+
            '<div class="allscan-bridge-body"><div class="allscan-bridge-row allscan-bridge-status-row"><span>Current Reflector</span><b>–</b></div><div class="allscan-bridge-row allscan-bridge-status-row"><span>Talking</span><b>–</b></div></div>'+
            '<div class="allscan-bridge-controls allscan-net-bridge-controls allscan-net-bridge-'+mode+'"><div class="allscan-bridge-tune allscan-net-bridge-mode-row"><label>Mode</label><select><option>'+mode.toUpperCase()+'</option></select>'+(!m17?buttons:'')+'</div>'+
            '<div class="allscan-bridge-tune allscan-net-bridge-destination-row'+(m17?' allscan-m17-net-tune':'')+'"><label>'+(m17?'Ref':mode==='ysf'?'Reflector':mode==='dmr'?'TG':'Target')+'</label><input value="'+(m17?'M17-WIL':'12345')+'">'+(m17?'<label>Mod</label><input class="allscan-m17-module-input" value="A">':'')+'</div>'+(m17?buttons:'')+'</div>';
          grid.replaceChildren(standard,net);
          const sr=standard.getBoundingClientRect(),nr=net.getBoundingClientRect();
          const fields=[...net.querySelectorAll('.allscan-net-bridge-controls :is(label,input,select)')].map(e=>e.getBoundingClientRect());
          const oneLine=fields.every(r=>Math.abs((r.top+r.bottom-fields[0].top-fields[0].bottom)/2)<=2);
          const controls=[...net.querySelectorAll('.allscan-net-bridge-controls :is(label,input,select,button)')].map(e=>e.getBoundingClientRect());
          const inside=controls.every(r=>r.left>=nr.left-1&&r.right<=nr.right+1&&r.width>0);
          if(Math.abs(sr.height-nr.height)>2||!oneLine||!inside) return {ok:false,mode,standardHeight:sr.height,netHeight:nr.height,oneLine,inside,width:nr.width,bands:[...standard.children].map(e=>[e.className,e.getBoundingClientRect().height]),netBands:[...net.children].map(e=>[e.className,e.getBoundingClientRect().height])};
        }
        return {ok:true};
      }finally{grid.remove()}
    })()`)
    assert(dedicatedNetRows.ok,name+': #343 dedicated Net Bridge row '+JSON.stringify(dedicatedNetRows))

    if (width < 900 && name !== 'tablet-portrait') {
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
        // #320 replaces the previous five-across desktop requirement.
        return {ok:cards.length===6&&Math.max(...widths)-Math.min(...widths)<=1&&Math.max(...tops)-Math.min(...tops)<=1&&rects.every(rect=>rect.right<=grid.getBoundingClientRect().right+1),count:cards.length,widths,tops};
      })()`)
      assert(bridgeLayout.ok,'desktop: bridge cards are not six uniform cards per row '+JSON.stringify(bridgeLayout))
    }
    console.log(name+': PASS '+result.viewport+'px, '+result.buttons+' visible controls')
  }
} finally {
  await browser?.close()
  vite.kill('SIGTERM')
  await delay(300)

}
