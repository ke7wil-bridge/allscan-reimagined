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

async function runBrowser(unauthorized = false) {
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
      if (action === 'bridge-status') {
        const response = await route.fetch()
        const payload = await response.json()
        for (const [id, state] of Object.entries(linkState)) {
          payload.controls[id] = { ...payload.controls[id], allstarLinked:state.linked,
            manualDisconnect:state.held, standardLinkControl:id !== 'p25' }
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
    // #307: check the actual control geometry under each supported theme.
    for (const [theme,mode] of [['standard','dark'],['standard','light'],['deep-ocean-animated','dark'],['matrix','dark'],['lcars-frame','dark']]) {
      for (const width of [1920,1440,1024,768,390,320]) {
        if (theme === 'lcars-frame' && width < 1200) continue
        await page.setViewportSize({width,height:1000})
        await page.evaluate(([theme,mode]) => {
          for (const el of [document.documentElement,document.body]) { el.dataset.asrTheme=theme; el.dataset.asrMode=mode }
        },[theme,mode])
        await delay(150)
        const row = dmrLink.filter({has:page.getByRole('button',{name:'Disconnect',exact:true})})
        const geometry = await row.evaluate(el => {
          const r=el.getBoundingClientRect(), b=el.querySelector('button').getBoundingClientRect()
          const dot=el.querySelector('.allscan-standard-link-dot').getBoundingClientRect(), node=el.querySelector('span').getBoundingClientRect(), status=el.querySelector('b').getBoundingClientRect()
          const peers=[...el.closest('.allscan-bridge-card').querySelectorAll('.allscan-bridge-body .allscan-bridge-status-row')].map(row=>row.getBoundingClientRect().height)
          return {height:r.height, peers, fits:b.left>=r.left-1 && b.right<=r.right+1 && b.bottom<=r.bottom+1, buttonHeight:b.height, width:r.width, dotFits:dot.width===3 && dot.height===3 && dot.left-node.right>=0 && dot.left-node.right<=6 && status.left-dot.right>=0 && status.left-dot.right<=6, placement:el.nextElementSibling?.classList.contains('allscan-bridge-body') && el.previousElementSibling?.classList.contains('allscan-bridge-head'), noWrap:[...el.children].every(x=>getComputedStyle(x).whiteSpace==='nowrap'), labelFits:el.querySelector('b').scrollWidth<=el.querySelector('b').clientWidth+1, children:[...el.children].map(x=>({text:x.textContent,width:x.getBoundingClientRect().width,height:x.getBoundingClientRect().height,font:getComputedStyle(x).fontSize}))}
        })
        assert(geometry.dotFits && geometry.placement && geometry.fits && geometry.noWrap && geometry.labelFits && geometry.buttonHeight>=24 && (width===320 || geometry.peers.every(h=>Math.abs(h-geometry.height)<=1)), `#307 control overflow or small target: ${theme}/${mode}/${width}: ${JSON.stringify(geometry)}`)
        console.log('LAYOUT',theme,mode,width,JSON.stringify(geometry))
        if (process.env.ASR_LAYOUT_EVIDENCE) await standardCard('DMR').screenshot({path:`${process.env.ASR_LAYOUT_EVIDENCE}-${theme}-${mode}-${width}.png`})
      }
    }
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
        longHandled: Boolean(longLabel && getComputedStyle(longLabel).textOverflow === 'ellipsis' && getComputedStyle(longLabel).whiteSpace === 'nowrap'),
        contained: Boolean(long && long.getBoundingClientRect().right <= long.closest('.allscan-bridge-card').getBoundingClientRect().right + 1),
        rightAligned: Boolean(observed && Math.abs(observed.parentElement.getBoundingClientRect().right - observed.getBoundingClientRect().right) <= 1),
        callsignCentered: Boolean(observed && Math.abs((observed.parentElement.querySelector('span:first-child').getBoundingClientRect().left + observed.parentElement.querySelector('span:first-child').getBoundingClientRect().right) / 2 - (observed.parentElement.getBoundingClientRect().left + observed.parentElement.getBoundingClientRect().right) / 2) <= 1),
        inherited,
      };
    })()`)
    assert(durations.observed.startsWith('~'), 'ASR-observed connection duration lacks the approximation prefix')
    assert(!durations.native.startsWith('~') && durations.native.length > 0, 'native connection duration was marked approximate or omitted')
    assert(durations.observedTitle.includes('ASR first observed') && durations.nativeTitle.includes('reported by the bridge'), 'duration provenance tooltips are missing')
    assert(durations.zelloDurations === 0, 'activity-only Zello feed received a connection duration')
    assert(durations.longHandled && durations.contained, 'long callsign and duration do not fit the existing card geometry')
    assert(durations.rightAligned, 'connection duration is not aligned to the right edge of the callsign row')
    assert(durations.callsignCentered, 'callsign is not centered independently of the duration')
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
        return rect.left >= -2 && rect.right <= innerWidth + 2 && row.querySelector('button') && row.querySelector('select');
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
await delay(250)
await runBrowser(true)
console.log('Connected Clients/Kick/Ban browser self-test: ok')
