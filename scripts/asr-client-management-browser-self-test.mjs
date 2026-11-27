#!/usr/bin/env node
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'

const assert = (value, message) => { if (!value) throw new Error(message) }
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function freePort() {
  const server = createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const port = server.address().port
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  return port
}

async function waitFor(url, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { const response = await fetch(url, { signal: AbortSignal.timeout(2000) }); if (response.ok) return response } catch { /* starting */ }
    await delay(100)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

async function runBrowser(unauthorized = false, netMode = 'dmr', layoutOnly = false) {
  const port = await freePort()
  const appUrl = `http://127.0.0.1:${port}/asr/`
  const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    env: { ...process.env, ASR_MOCK: '1', ASR_MOCK_UNAUTHORIZED: unauthorized ? '1' : '0' },
    stdio: 'ignore',
  })
  let browser
  try {
    await waitFor(appUrl)
    browser = await chromium.launch({headless:true, ...(process.env.CHROME_BIN ? {executablePath:process.env.CHROME_BIN} : {})})
    const page = await browser.newPage({viewport:{width:1440,height:1000}})
    const evaluate = expression => page.evaluate(expression)
    const waitUntil = async (expression, message, timeout = 8000) => {
      const deadline = Date.now() + timeout
      while (Date.now() < deadline) {
        try { if (await evaluate(expression)) return } catch { /* rendering */ }
        await delay(100)
      }
      throw new Error(message)
    }

    // #130: exercise real frontend control requests with synthetic backend
    // responses. This does not assert an actual Asterisk link operation.
    const linkState = { dmr: { linked: true, held: false }, ysf: { linked: true, held: false }, p25: { linked: false, held: true } }
    const linkRequests = []
    let rejectLink = false
    await page.route('**/asr-api.php?*', async route => {
      const request = route.request()
      const action = new URL(request.url()).searchParams.get('action')
      if (action === 'runtime-config') {
        const response=await route.fetch()
        const payload=await response.json()
        for (const bridge of payload.bridges) if (bridge.cardType?.endsWith('_net')) bridge.node='1999'
        payload.netBridgeMode=netMode
        return route.fulfill({response,json:payload})
      }
      if (action === 'global-ban-saved-rules') {
        return route.fulfill({json:{ok:true,rules:['TESTBANNED']}})
      }
      if (action === 'bridge-status') {
        const response = await route.fetch()
        const payload = await response.json()
        for (const [id, state] of Object.entries(linkState)) {
          payload.controls[id] = { ...payload.controls[id], allstarLinked:state.linked,
            manualDisconnect:state.held, standardLinkControl:id !== 'p25' }
        }
        // #317: known authoritative events, independent of moderation roster identities.
        const now = Math.floor(Date.now()/1000)
        payload.clientMeta ||= {}
        payload.bridge.zello.recent_users = [
          {name:'K6ZELLO',last_tx_epoch:now-10},
          {name:'TESTBANNED',last_tx_epoch:now-20},
          {name:'Taurus 78',last_tx_epoch:now-30}
        ]

        for (const id of ['dmr','ysf','p25','nxdn','m17','zello']) {
          if (!payload.bridge[id]) continue
          payload.bridge[id].tx_events = [{callsign:id==='zello'?'TAURUS 78':'TEST-TX',epoch:now-10,duration_seconds:12,direction:'incoming',source_protocol:id.toUpperCase()}]
          payload.bridge[id].client_events = [{callsign:'TEST-CONN',epoch:now-30,event:'connect'},{callsign:'TEST-DISC',epoch:now-20,event:'disconnect'}]
          if (id !== 'zello') payload.clientMeta[id] = {kind:'current'}
        }
        return route.fulfill({response, json:payload})
      }
      if (action === 'standard-bridge-link') {
        const form = new URLSearchParams(request.postData() || '')
        const id = form.get('bridgeId'), verb = form.get('verb')
        linkRequests.push({id,verb,method:request.method(),header:request.headers()['x-asr-requested-with']})
        if (unauthorized || rejectLink || !['dmr','ysf'].includes(id) || !['connect','disconnect'].includes(verb)) {
          return route.fulfill({status:409,json:{ok:false,error:'Fixture link operation rejected'}})
        }
        linkState[id] = {linked:verb==='connect',held:verb==='disconnect'}
        return route.fulfill({json:{ok:true,message:'Fixture link operation completed'}})
      }
      return route.fallback()
    })
    await page.goto(appUrl, {waitUntil:'domcontentloaded',timeout:20000})
    try {
      await waitUntil("document.readyState === 'complete' && document.body.textContent.includes('Connected Clients')", 'Dashboard did not load', 30000)
    } catch (error) {
      console.error('Dashboard diagnostic:', await evaluate("JSON.stringify({url:location.href,state:document.readyState,text:document.body?.innerText?.slice(0,1000)})"))
      throw error
    }
    await evaluate(`(() => {
      for (const card of document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')) {
        const button = [...card.querySelectorAll('.allscan-bridge-detail-toggle')].find((item) => item.textContent.includes('Connected Clients') || item.textContent.includes('Recent Talkers'))
        if (button?.getAttribute('aria-expanded') !== 'true') button?.click()
      }
    })()`)
    await delay(200)

    if (unauthorized) {
      assert(await page.locator('.allscan-bridge-status-row button').count() === 0, 'read-only browser received Standard Bridge link controls')
      assert(linkRequests.length === 0, 'read-only browser issued a Standard Bridge mutation')
      assert(await evaluate("document.querySelectorAll('.allscan-bridge-inline-client-actions').length") === 0,
        'unauthorized browser received bridge client controls')
      return
    }

    const standardCard = mode => page.locator('.allscan-bridge-card:not(.allscan-net-bridge-card)').filter({has:page.locator('.allscan-bridge-head-title',{hasText:new RegExp('^'+mode+' Bridge$')})})
    const dmrLink = standardCard('DMR').locator('.allscan-bridge-status-row')
    const ysfLink = standardCard('YSF').locator('.allscan-bridge-status-row')
    const p25Link = standardCard('P25').locator('.allscan-bridge-status-row')
    const closeUpdate = page.getByRole('button',{name:'Close Update ASR'})
    if (await closeUpdate.count()) await closeUpdate.click()
    await page.evaluate(() => {
      for (const button of document.querySelectorAll('.allscan-bridge-recent > button[aria-expanded="false"]')) button.click()
    })
    await page.locator('.asr-bridge-activity-label[data-event="CONN"]').first().waitFor()
    const zelloRoster = standardCard('Zello').locator('.asr-bridge-roster')
    await zelloRoster.locator('[data-status="banned"]').waitFor()
    const zelloRow = name => zelloRoster.locator('.allscan-bridge-client').filter({hasText:name})
    assert(await zelloRow('K6ZELLO').getByRole('button',{name:'Kick',exact:true}).isEnabled(), 'Valid Zello station lost Kick')
    assert(await zelloRow('K6ZELLO').locator('select').count()===1, 'Valid Zello station lost Ban')
    assert(await zelloRow('TESTBANNED').locator('[data-status="banned"]').count()===1 && await zelloRow('TESTBANNED').locator('select').count()===0, 'Banned Zello station status missing')
    assert(await zelloRow('Taurus 78').locator('button').isDisabled() && await zelloRow('Taurus 78').locator('[data-status="unavailable"]').count()===1, 'Unsupported Zello identity presents misleading action')
    assert(!/Last TX|No recent TX/.test(await zelloRoster.innerText()), 'Redundant Zello TX timestamp remains')
    // #307: check the actual control geometry under each supported theme.
    for (const [theme,mode] of [['standard','dark'],['standard','light'],['deep-ocean-animated','dark'],['matrix','dark'],['lcars-frame','dark']]) {
      for (const width of [1920,1440,1024,768,390,320]) {
        if (theme === 'lcars-frame' && width < 1200) continue
        await page.setViewportSize({width,height:1000})
        await page.evaluate(([theme,mode]) => {
          for (const el of [document.documentElement,document.body]) { el.dataset.asrTheme=theme; el.dataset.asrMode=mode }
        },[theme,mode])
        await delay(150)
        const grid = await page.locator('.allscan-bridge-grid').evaluate(el => {
          const cards = [...el.querySelectorAll(':scope > .allscan-bridge-card')]
          const first = cards[0].getBoundingClientRect()
          return {count: cards.filter(card=>Math.abs(card.getBoundingClientRect().top-first.top)<2).length,
            width: first.width, gridWidth: el.clientWidth, gap: parseFloat(getComputedStyle(el).columnGap),
            contained: cards.every(card=>card.getBoundingClientRect().right<=el.getBoundingClientRect().right+1)}
        })
        assert(grid.contained, '#320 bridge grid overflow')
        if (width >= 1440 && grid.gridWidth >= 1350) assert(grid.count===6, '#320 expected six desktop cards: '+JSON.stringify(grid))
        console.log('GRID WIDTH', netMode,theme,mode,width,JSON.stringify(grid))
        const row = dmrLink.filter({has:page.getByRole('button',{name:'Disconnect',exact:true})})
        const geometry = await row.evaluate(el => {
          const r=el.getBoundingClientRect(), b=el.querySelector('button').getBoundingClientRect()
          const dot=el.querySelector('.allscan-standard-link-dot').getBoundingClientRect(), node=el.querySelector('span').getBoundingClientRect(), status=el.querySelector('b').getBoundingClientRect()
          const label=el.querySelector('b'), button=el.querySelector('button')
          const oldLabel=label.textContent, oldButton=button.textContent, oldState=label.dataset.linkState
          const green=getComputedStyle(label).color
          label.textContent='Unlinked'; label.dataset.linkState='unlinked'; button.textContent='Reconnect'
          const red=getComputedStyle(label).color
          const unlinkedWidths=[label.scrollWidth,label.clientWidth,button.getBoundingClientRect().width]
          const unlinkedFits=label.scrollWidth<=label.clientWidth+1 && button.getBoundingClientRect().right<=r.right+1
          label.textContent=oldLabel; label.dataset.linkState=oldState; button.textContent=oldButton
          const colorOK=green=== (document.documentElement.dataset.asrMode==='light'?'rgb(21, 128, 61)':'rgb(74, 222, 128)') && red===(document.documentElement.dataset.asrMode==='light'?'rgb(185, 28, 28)':'rgb(248, 113, 113)')
          const peerRows=[...el.closest('.allscan-bridge-card').querySelectorAll('.allscan-bridge-body .allscan-bridge-status-row')]
          const peers=peerRows.map(row=>row.getBoundingClientRect().height)
          const fonts=[...el.querySelectorAll('span,b,button'),...peerRows.flatMap(row=>[...row.querySelectorAll('span,b')])].map(x=>getComputedStyle(x).fontSize)
          const equalFonts=fonts.every(f=>f===fonts[0])
          return {height:r.height, peers, fonts, equalFonts, unlinkedFits, unlinkedWidths, colorOK, green, red, fits:b.left>=r.left-1 && b.right<=r.right+1 && b.bottom<=r.bottom+1, buttonHeight:b.height, width:r.width, dotFits:dot.width===4 && dot.height===4 && dot.left-node.right>=4 && dot.left-node.right<=7 && status.left-dot.right>=4 && status.left-dot.right<=7, placement:el.nextElementSibling?.classList.contains('allscan-bridge-body') && el.previousElementSibling?.classList.contains('allscan-bridge-head'), noWrap:[...el.children].every(x=>getComputedStyle(x).whiteSpace==='nowrap'), labelFits:el.querySelector('b').scrollWidth<=el.querySelector('b').clientWidth+1, children:[...el.children].map(x=>({text:x.textContent,width:x.getBoundingClientRect().width,height:x.getBoundingClientRect().height,font:getComputedStyle(x).fontSize}))}
        })
        assert(geometry.equalFonts && geometry.unlinkedFits && geometry.colorOK && geometry.dotFits && geometry.placement && geometry.fits && geometry.noWrap && geometry.labelFits && geometry.buttonHeight>=24 && geometry.peers.every(h=>h===27) && (width===320 || geometry.height===27), `#307 control overflow or small target: ${theme}/${mode}/${width}: ${JSON.stringify(geometry)}`)
        console.log('LAYOUT',theme,mode,width,JSON.stringify(geometry))
        const netRows=await page.locator('.allscan-net-bridge-card').evaluateAll(cards=>cards.map(card=>{
          const rows=[...card.querySelectorAll('.allscan-bridge-body > .allscan-bridge-status-row')]
          return {rows:rows.map(row=>({label:row.querySelector('span')?.textContent,height:row.getBoundingClientRect().height,fonts:[...row.querySelectorAll('span,b')].map(x=>getComputedStyle(x).fontSize)}))}
        }))
        assert(netRows.length>0 && netRows.every(card=>card.rows.length===2 && card.rows[0].label===(netMode==='ysf'?'Current Reflector':'Current TG') && card.rows[1].label==='Talking' && card.rows.every(row=>row.height===27 && row.fonts.every(f=>f===geometry.fonts[0]))), `#307 Net Bridge information row mismatch: ${theme}/${mode}/${width}: ${JSON.stringify(netRows)}`)
        console.log('NET LAYOUT',netMode,theme,mode,width,JSON.stringify(netRows))
        const detailBands = await page.locator('.allscan-bridge-card > .allscan-bridge-detail-wrap').evaluateAll(wraps=>wraps.map(wrap=>{
          const button=wrap.querySelector('.allscan-bridge-detail-toggle'), r=button.getBoundingClientRect(), w=wrap.getBoundingClientRect()
          const border=parseFloat(getComputedStyle(wrap).borderTopWidth)
          return {label:button.querySelector('span').textContent, height:r.height+border, expanded:button.getAttribute('aria-expanded')==='true', wrapHeight:w.height, fonts:[button,...button.querySelectorAll('span,b')].map(x=>getComputedStyle(x).fontSize), fits:[...button.children].every(x=>{const box=x.getBoundingClientRect();return box.left>=w.left-1&&box.right<=w.right+1&&box.top>=r.top-1&&box.bottom<=r.bottom+1&&x.scrollWidth<=x.clientWidth+1})}
        }))
        assert(detailBands.length>0 && detailBands.some(row=>row.label==='Connected Clients') && detailBands.some(row=>row.label==='Recent Activity') && detailBands.every(row=>row.height===27 && row.fonts.every(f=>f===geometry.fonts[0]) && row.fits && (row.expanded || row.wrapHeight===27)), `#307 detail header mismatch: ${theme}/${mode}/${width}: ${JSON.stringify(detailBands)}`)
        console.log('DETAIL LAYOUT',netMode,theme,mode,width,JSON.stringify(detailBands))
        const entries = await page.evaluate(() => {
          const palette = document.documentElement.dataset.asrMode==='light'
            ? {CONN:'rgb(66, 105, 103)',DISC:'rgb(135, 90, 85)',TX:'rgb(103, 92, 128)'}
            : {CONN:'rgb(145, 181, 178)',DISC:'rgb(195, 160, 155)',TX:'rgb(181, 174, 203)'}
          const cards=[...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
          return cards.map(card=>{
            const mode=card.querySelector('.allscan-bridge-head-title').textContent
            const rows=[...card.querySelectorAll('.asr-bridge-activity-list .allscan-bridge-client:not(.is-empty)')]
            const types=rows.map(row=>row.querySelector('[data-event]')?.dataset.event)
            const roster=card.querySelector('.asr-bridge-roster')
            const current=roster?.previousElementSibling?.textContent.includes('Connected Clients')
            const fits=(row,child)=>{const r=row.getBoundingClientRect(),c=child.getBoundingClientRect();return c.left>=r.left-1 && c.right<=r.right+1 && c.bottom<=r.bottom+1}
            return {mode,types,colors:rows.every(row=>{const label=row.querySelector('[data-event]');return label&&getComputedStyle(label).color===palette[label.dataset.event]}),
              detail:rows.every(row=>row.querySelector('.asr-bridge-activity-time')?.textContent && (row.querySelector('.asr-bridge-activity-detail')?.textContent.startsWith('TEST-') || (mode==='Zello Bridge' && row.querySelector('.asr-bridge-activity-detail')?.textContent.startsWith('TAURUS 78'))) && (row.querySelector('[data-event]').dataset.event!=='TX'||row.querySelector('.asr-bridge-activity-detail').textContent.endsWith('12s'))),
              fit:rows.every(row=>[...row.children].every(c=>fits(row,c))),
              noLastTx:!current||!/(Last TX|No recent TX)/.test(roster.textContent),
              rosterFit:[...(roster?.querySelectorAll('.allscan-bridge-client:not(.is-empty)')||[])].every(row=>[...row.querySelectorAll('.asr-client-duration-line > span, .allscan-bridge-inline-client-actions > *')].every(c=>fits(row,c))),
              compactDesktop: [...(roster?.querySelectorAll('.allscan-bridge-client:not(.is-empty)')||[])].filter(row=>row.querySelector('.allscan-bridge-client-user > span:first-child')?.textContent.length<=9).every(row=>row.getBoundingClientRect().height<=29),
              zelloActivitySingleLine:mode!=='Zello Bridge'||rows.every(row=>row.getBoundingClientRect().height<=21),
              rosterHeights:current?[...roster.querySelectorAll('.allscan-bridge-client:not(.is-empty)')].map(row=>row.getBoundingClientRect().height):[]}
          })
        })
        assert(entries.length===6 && entries.every(card=>card.colors && card.detail && card.fit && card.noLastTx && card.rosterFit && card.rosterHeights.every(h=>h<=70) && card.types.join(',')===(card.mode==='Zello Bridge'?'TX':'TX,DISC,CONN')), `#317 entry mismatch ${theme}/${mode}/${width}: ${JSON.stringify(entries)}`)
        if (width>=1440) assert(entries.every(card=>card.compactDesktop&&card.zelloActivitySingleLine), '#320 desktop entries must fit one line: '+JSON.stringify(entries))
        console.log('ENTRY LAYOUT',netMode,theme,mode,width,JSON.stringify(entries))


        if (process.env.ASR_LAYOUT_EVIDENCE) await standardCard('DMR').screenshot({path:`${process.env.ASR_LAYOUT_EVIDENCE}-${theme}-${mode}-${width}.png`})
      }
    }
    if (layoutOnly) return
    await page.evaluate(() => { for (const button of document.querySelectorAll('.allscan-bridge-recent > button[aria-expanded="true"]')) button.click() })
    await page.setViewportSize({width:1440,height:1000})
    await page.evaluate(() => { for (const el of [document.documentElement,document.body]) { el.dataset.asrTheme='standard'; el.dataset.asrMode='dark' } })
    await dmrLink.getByRole('button',{name:'Disconnect',exact:true}).click()
    await dmrLink.locator('b[title="Manually disconnected"]').filter({hasText:/^Unlinked$/}).waitFor()
    assert(await ysfLink.getByText('Linked',{exact:true}).isVisible(), 'DMR hold changed an unrelated YSF bridge')
    assert(await p25Link.locator('b[title="Manually disconnected · control unavailable"]').filter({hasText:/^Unlinked$/}).isVisible(), 'unavailable persistent hold is not visible')
    assert(await p25Link.getByRole('button').count() === 0, 'unavailable held bridge exposes generic reconnect')
    rejectLink = true
    await dmrLink.getByRole('button',{name:'Reconnect',exact:true}).click()
    await page.getByText('Fixture link operation rejected',{exact:false}).first().waitFor()
    await dmrLink.getByRole('button',{name:'Reconnect',exact:true}).waitFor()
    assert(await dmrLink.locator('b[title="Manually disconnected"]').filter({hasText:/^Unlinked$/}).isVisible(), 'failed reconnect cleared the saved hold')
    rejectLink = false
    await page.setViewportSize({width:390,height:844})
    await dmrLink.getByRole('button',{name:'Reconnect',exact:true}).click()
    await dmrLink.getByText('Linked',{exact:true}).waitFor()
    assert(linkRequests.length===3 && linkRequests.every(r=>r.id==='dmr' && r.method==='POST' && r.header==='asr-standard-link')
      && linkRequests.map(r=>r.verb).join(',')==='disconnect,connect,connect', 'Standard Bridge requests were not scoped or authenticated-header POSTs')
    await page.setViewportSize({width:1440,height:1000})
    console.log('PASS: Standard Bridge controls, independent holds, failed reconnect and mobile reconnect (fixture API)')

    const coverage = await evaluate(`(() => {
      const result = {};
      for (const mode of ['DMR','YSF','P25','NXDN','M17','Zello']) {
        const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
          .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === mode + ' Bridge');
        result[mode] = card ? {
          clients: card.querySelectorAll('.allscan-bridge-client:not(.is-empty)').length,
          kicks: [...card.querySelectorAll('.allscan-bridge-inline-client-actions button')].filter((item) => item.textContent.trim() === 'Kick').length,
          bans: card.querySelectorAll('.allscan-bridge-inline-client-actions select').length,
        } : null;
      }
      return result;
    })()`)
    for (const mode of ['DMR', 'YSF', 'P25', 'NXDN', 'M17', 'Zello']) {
      assert(coverage[mode]?.clients >= 1, `${mode} connected/recent client identification was not rendered`)
      assert(coverage[mode]?.kicks >= 1, `${mode} Kick was not available in the browser`)
      assert(coverage[mode]?.bans >= 1, `${mode} Ban was not available in the browser`)
    }

    const durations = await evaluate(`(() => {
      const row = (mode, callsign) => {
        const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
          .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === mode + ' Bridge');
        return [...(card?.querySelectorAll('.allscan-bridge-client') || [])].find((item) => item.textContent.includes(callsign));
      };
      const observed = row('DMR', 'N1DMR')?.querySelector('.asr-client-duration');
      const native = row('YSF', 'N2YSF')?.querySelector('.asr-client-duration');
      const long = row('YSF', 'LONGCALLSIGN-12');
      const longLabel = long?.querySelector('.asr-client-duration-line > span:first-child');
      const zelloCard = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'Zello Bridge');
      const inherited = ['standard', 'deep-ocean-animated', 'matrix', 'lcars-frame'].every((theme) => {
        document.documentElement.dataset.asrTheme = theme;
        document.body.dataset.asrTheme = theme;
        return observed && getComputedStyle(observed).color === getComputedStyle(observed.parentElement).color
          && getComputedStyle(observed).backgroundColor === 'rgba(0, 0, 0, 0)';
      });
      document.documentElement.dataset.asrTheme = 'standard';
      document.body.dataset.asrTheme = 'standard';
      return {
        observed: observed?.textContent || '', observedTitle: observed?.getAttribute('title') || '',
        native: native?.textContent || '', nativeTitle: native?.getAttribute('title') || '',
        zelloDurations: zelloCard?.querySelectorAll('.asr-client-duration').length || 0,
        longHandled: Boolean(longLabel && getComputedStyle(longLabel).overflowWrap === 'anywhere' && getComputedStyle(longLabel).whiteSpace === 'normal'),
        contained: Boolean(long && long.getBoundingClientRect().right <= long.closest('.allscan-bridge-card').getBoundingClientRect().right + 1),
        rightAligned: Boolean(observed && Math.abs(observed.parentElement.getBoundingClientRect().right - observed.getBoundingClientRect().right) <= 1),
        callsignLeftAligned: Boolean(observed && Math.abs(observed.parentElement.querySelector('span:first-child').getBoundingClientRect().left - observed.parentElement.getBoundingClientRect().left) <= 1),
        inherited,
      };
    })()`)
    assert(durations.observed.startsWith('~'), 'ASR-observed connection duration lacks the approximation prefix')
    assert(!durations.native.startsWith('~') && durations.native.length > 0, 'native connection duration was marked approximate or omitted')
    assert(durations.observedTitle.includes('ASR first observed') && durations.nativeTitle.includes('reported by the bridge'), 'duration provenance tooltips are missing')
    assert(durations.zelloDurations === 0, 'activity-only Zello feed received a connection duration')
    assert(durations.longHandled && durations.contained, 'long callsign and duration do not fit the existing card geometry')
    assert(durations.rightAligned, 'connection duration is not aligned to the right edge of the callsign row')
    assert(durations.callsignLeftAligned, 'ASRPlus-style callsign is not left aligned')
    assert(durations.inherited, 'duration text does not inherit the selected theme')

    const kicked = await evaluate(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'DMR Bridge');
      const row = [...(card?.querySelectorAll('.allscan-bridge-client') || [])].find((item) => item.textContent.includes('N1DMR'));
      row?.querySelector('button')?.click();
      return Boolean(row);
    })()`)
    assert(kicked, 'selected DMR client Kick could not be invoked')
    await waitUntil(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'DMR Bridge');
      return card && !card.textContent.includes('N1DMR');
    })()`, 'kicked DMR client remained in the selected roster')
    assert(await evaluate("document.body.textContent.includes('N2YSF') && document.body.textContent.includes('K7GLOBAL')"),
      'Kick removed an unrelated digital client')

    const banned = await evaluate(`(() => {
      const card = [...document.querySelectorAll('.allscan-bridge-card:not(.allscan-net-bridge-card)')]
        .find((item) => item.querySelector('.allscan-bridge-head-title')?.textContent.trim() === 'NXDN Bridge');
      const row = [...(card?.querySelectorAll('.allscan-bridge-client') || [])].find((item) => item.textContent.includes('K7GLOBAL'));
      const select = row?.querySelector('select');
      if (select) { select.value = '1h'; select.dispatchEvent(new Event('change', { bubbles: true })); }
      return Boolean(select);
    })()`)
    assert(banned, 'selected NXDN client Ban could not be invoked')
    await waitUntil("!document.body.textContent.includes('K7GLOBAL')", 'Global Ban did not clear the identity from every supported digital roster')
    assert(await evaluate("['N2YSF','N3P25','N4NXDN','N5M17'].every((identity) => document.body.textContent.includes(identity))"),
      'Global Ban removed an unrelated digital client')

    await (await page.context().newCDPSession(page)).send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, mobile: true, deviceScaleFactor: 1 })
    await delay(150)
    const mobile = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('.allscan-bridge-client:not(.is-empty)')].filter((row) => row.querySelector('.allscan-bridge-inline-client-actions'));
      const durations = [...document.querySelectorAll('.asr-client-duration')];
      const longLabel = [...document.querySelectorAll('.asr-client-duration-line > span:first-child')].find((label) => label.textContent.includes('LONGCALLSIGN-12'));
      return rows.length > 0 && durations.length > 0 && rows.every((row) => {
        const rect = row.getBoundingClientRect();
        const actions = row.querySelector('.allscan-bridge-inline-client-actions')
        const control = actions.querySelector('select, [data-status="banned"], [data-status="unavailable"]')
        return rect.left >= -2 && rect.right <= innerWidth + 2 && row.querySelector('button') && control
          && [...actions.children].every(child => { const r = child.getBoundingClientRect(); return r.left >= rect.left-1 && r.right <= rect.right+1 && r.bottom <= rect.bottom+1 });
      }) && durations.every((duration) => duration.getBoundingClientRect().right <= duration.closest('.allscan-bridge-card').getBoundingClientRect().right + 1)
        && Boolean(longLabel && longLabel.getBoundingClientRect().right <= longLabel.closest('.allscan-bridge-card').getBoundingClientRect().right + 1);
    })()`)
    assert(mobile, 'mobile Connected Clients Kick/Ban controls are missing or clipped')
    await (await page.context().newCDPSession(page)).send('Emulation.setDeviceMetricsOverride', { width:1440,height:1000,mobile:false,deviceScaleFactor:1 })
    // #129: partial enforcement must remain visible after list refresh/reload,
    // including when a ban is saved from the client-management dialog.
    let saved = ['N2YSF']
    const partial = () => ({ ok:true, verified:false, enforcement:'partial_pending_enforcement',
      standalone:{skipped:['nxdn','p25','ysf']}, asl:{status:'applied'}, rules:saved,
      bans:saved.map(rule=>({rule,createdAt:1,expiresAt:0,remainingSeconds:0})), audit:[], serverEpoch:1 })
    await page.route('**/asr-api.php?*', async route => {
      const action=new URL(route.request().url()).searchParams.get('action')
      if(action==='global-ban-list') return route.fulfill({json:partial()})
      if(action==='global-ban-control') {
        const form=new URLSearchParams(route.request().postData() || '')
        const rule=form.get('rule'); saved=form.get('verb')==='unban' ? saved.filter(x=>x!==rule) : [...new Set([...saved,rule])]
        return route.fulfill({json:partial()})
      }
      if(action==='asl-ban-list') return route.fulfill({json:{ok:true,bans:[],applied:{}}})
      return route.continue()
    })
    const dialog=page.getByRole('dialog',{name:'Manage Kicks & Bans'})
    const releaseClose=page.getByRole('button',{name:'Close Update ASR'})
    if(await releaseClose.count()) await releaseClose.click()
    await page.locator('.allscan-kicks-bans-button').click()
    await dialog.getByRole('tab',{name:'Bans'}).click()
    await waitUntil("document.querySelector('.allscan-connection-ban-list')?.innerText.includes('not enforced on NXDN, P25, YSF')", 'saved list failed to name unsupported bridges')
    assert(!(await dialog.innerText()).includes('Everywhere'), 'saved list falsely claimed universal enforcement')
    await dialog.getByRole('tab',{name:'Connected clients'}).click()
    await dialog.getByLabel('Ban callsign').fill('Q9QA129')
    await dialog.getByRole('button',{name:'Ban',exact:true}).click()
    await waitUntil("document.querySelector('.allscan-ban-admin-status')?.innerText.includes('not enforced on NXDN, P25, YSF')", 'client-management mutation omitted partial enforcement')
    await dialog.getByRole('tab',{name:'Bans'}).click()
    await waitUntil("document.querySelector('.allscan-connection-ban-list')?.innerText.includes('Q9QA129')", 'saved partial rule missing from list')
    await dialog.getByRole('button',{name:'Close Manage Kicks & Bans'}).click()
    console.log('PASS: partial Global Ban mutation and saved-list scope')

  } finally {
    await browser?.close()
    vite.kill('SIGTERM')
    await delay(400)

  }
}

await runBrowser(false)
await runBrowser(false,'ysf',true)
await delay(250)
await runBrowser(true)
console.log('Connected Clients/Kick/Ban browser self-test: ok')
