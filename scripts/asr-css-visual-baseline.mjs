#!/usr/bin/env node
// Read-only deterministic mock dashboard screenshots for CSS regression checks.
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { resolve, join } from 'node:path'
const require = createRequire(import.meta.url)
let chromium
for (const path of [
  'playwright-core',
  '/home/josh/worktrees/asr-st-asl-theme/node_modules/playwright-core',
]) {
  try { chromium = require(path).chromium; break } catch { /* try next installed location */ }
}
if (!chromium) throw new Error('Install playwright-core to run CSS visual regression checks')
const root = resolve(process.argv[2] || '/tmp/asr-css-visual-candidate')
const url = process.env.ASR_CSS_VISUAL_URL || 'http://127.0.0.1:4197/asr/'
const themes = [['dark','standard','dark'],['light','standard','light'],['ocean','deep-ocean-animated','dark'],['matrix','matrix','dark'],['star-trek','lcars-frame','dark']]
const sizes = [['desktop',1440,900],['tablet',768,1024],['mobile',390,844],['galaxy-s25',360,780]]
await mkdir(root,{recursive:true})
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']})
try {
  for (const [label,width,height] of sizes) {
    const page=await browser.newPage({viewport:{width,height},deviceScaleFactor:1,reducedMotion:'reduce'})
    await page.goto(url,{waitUntil:'domcontentloaded',timeout:15000})
    await page.locator('.allscan-app').waitFor({timeout:15000})
    for (const [theme,slug,mode] of themes) {
      await page.evaluate(([t,m])=>{document.documentElement.dataset.asrTheme=t;document.documentElement.dataset.asrMode=m;document.body.dataset.asrTheme=t;document.body.dataset.asrMode=m},[slug,mode])
      await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important} html{scroll-behavior:auto!important}'})
      await page.screenshot({path:join(root,`${label}-${theme}.png`),animations:'disabled',fullPage:true})
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth)
      if (overflow>2) throw new Error(`${label}/${theme}: horizontal overflow ${overflow}px`)
      console.log(`${label}/${theme}: captured`)
    }
    await page.close()
  }
} finally { await browser.close() }
console.log('CSS visual capture: 20 screenshots at '+root)
