#!/usr/bin/env node
// Read-only browser qualification for #159, against a local mock ASR app.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'
import { createServer } from 'node:net'
import { parseReleaseCategories } from '../src/lib/releaseNotes.js'

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const version = JSON.parse(read('../package.json')).version
const actualChanges = parseReleaseCategories(read(`../release-notes/v${version}.md`)).reduce((n, c) => n + c.items.length, 0)
const listener = createServer()
await new Promise((resolve, reject) => {
  listener.once('error', reject)
  listener.listen(0, '127.0.0.1', resolve)
})
const vitePort = listener.address().port
await new Promise(resolve => listener.close(resolve))
const url = `http://127.0.0.1:${vitePort}/asr/`
const vite = spawn('./node_modules/.bin/vite', ['--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'],
  { env: { ...process.env, ASR_MOCK: '1' }, stdio: 'ignore' })
let browser
try {
  let serverReady = false
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(2000) })).ok) { serverReady = true; break }
    } catch { /* Vite is starting. */ }
    await delay(100)
  }
  assert.ok(serverReady, 'Mock ASR server unavailable')
  browser = await chromium.launch({ headless: true, ...(process.env.CHROME_BIN ? { executablePath: process.env.CHROME_BIN } : {}) })
  const themes = [
    ['standard', 'dark'], ['standard', 'light'], ['deep-ocean-animated', 'dark'],
    ['matrix', 'dark'], ['lcars-frame', 'dark'],
  ]
  const viewports = [['desktop', 1280, 800], ['phone-portrait', 390, 844], ['phone-landscape', 844, 390]]
  for (const [theme, mode] of themes) {
    for (const [viewport, width, height] of viewports) {
      if (theme === 'lcars-frame' && width < 1200) continue // ASR falls back to standard on mobile.
      const name = `${theme}/${mode}/${viewport}`
      const context = await browser.newContext({ viewport: { width, height }, isMobile: width < 1000 })
      await context.addInitScript(({ theme, mode }) => {
        localStorage.setItem('asrThemeSettings.v1', JSON.stringify({ theme, mode }))
        localStorage.removeItem('asrWhatsNewSeenVersion.v1')
      }, { theme, mode })
      const page = await context.newPage()
      const evalJs = expression => page.evaluate(expression)
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 })
      await page.locator('.asr-whats-new').waitFor({ timeout: 15000 })
      const ready = await evalJs(`document.documentElement.dataset.asrTheme === ${JSON.stringify(theme)}
        && document.documentElement.dataset.asrMode === ${JSON.stringify(mode)}`)
      assert.ok(ready, `${name}: post-update popup did not open automatically in mock ASR`)
      // Check the same renderer when switching away and reopening the What's New tab.
      await evalJs("document.querySelector('.asr-update-view-tabs button:first-child').click()")
      await evalJs("document.querySelector('.asr-update-view-tabs button:last-child').click()")
      const result = await evalJs(`(() => {
        const dialog = document.querySelector('.asr-update-dialog--whats-new');
        const scroller = dialog.querySelector('.asr-whats-new');
        const list = scroller.querySelector('.asr-whats-new-category:last-of-type ul');
        const close = dialog.querySelector('.asr-update-head button');
        const footer = dialog.querySelector('footer');
        const original = scroller.querySelectorAll('.asr-whats-new-category li').length;
        const sections = [...scroller.querySelectorAll('.asr-whats-new-category, .asr-contributors-callout')];
        const typography = sections.map(section => {
          const heading = section.querySelector('h4');
          const body = heading.nextElementSibling.matches('ul') ? heading.nextElementSibling.firstElementChild : heading.nextElementSibling;
          const previous = section.previousElementSibling;
          const previousText = previous.querySelector('li:last-child') || previous;
          const titleStyle = getComputedStyle(heading);
          const bodyStyle = getComputedStyle(body);
          return {
            title: heading.textContent,
            sectionGap: heading.getBoundingClientRect().top - previousText.getBoundingClientRect().bottom,
            bodyGap: body.getBoundingClientRect().top - heading.getBoundingClientRect().bottom,
            titleSize: parseFloat(titleStyle.fontSize), bodySize: parseFloat(bodyStyle.fontSize),
            titleWeight: parseInt(titleStyle.fontWeight, 10), bodyWeight: parseInt(bodyStyle.fontWeight, 10),
          };
        });
        list.innerHTML = Array.from({length:43}, (_, i) => '<li>Released change ' + (i + 1) + ' with a short explanation.</li>').join('');
        list.lastElementChild.scrollIntoView({block: 'end'});
        const bounds = dialog.getBoundingClientRect();
        const area = scroller.getBoundingClientRect();
        const last = list.lastElementChild.getBoundingClientRect();
        const btn = close.getBoundingClientRect();
        const foot = footer.getBoundingClientRect();
        return {
          typography, original, total: list.children.length, scrollTop: scroller.scrollTop,
          canScroll: scroller.scrollHeight > scroller.clientHeight,
          lastAccessible: last.bottom <= area.bottom + 2 && last.top >= area.top - 2,
          closeAccessible: btn.top >= -1 && btn.bottom <= innerHeight + 1,
          footerAccessible: foot.top >= -1 && foot.bottom <= innerHeight + 1,
          dialogFits: bounds.top >= -1 && bounds.bottom <= innerHeight + 1,
          overflow: getComputedStyle(scroller).overflowY, tabIndex: scroller.tabIndex,
        };
      })()`)
      assert.ok(result.typography.length > 1, `${name}: expected multiple sections`)
      for (const style of result.typography) {
        assert.ok(style.sectionGap >= 16 && style.bodyGap >= 0 && style.bodyGap < style.sectionGap,
          `${name}: insufficient section separation ${JSON.stringify(style)}`)
        assert.ok(style.titleSize >= style.bodySize * 1.1 && style.titleWeight >= 700 && style.titleWeight > style.bodyWeight,
          `${name}: heading is not larger and bolder ${JSON.stringify(style)}`)
      }
      assert.equal(result.original, actualChanges, `${name}: rendered changes differ from the release file`)
      assert.equal(result.total, 43, `${name}: long release fixture not rendered`)
      assert.ok(result.canScroll && result.scrollTop > 0, `${name}: long list cannot scroll ${JSON.stringify(result)}`)
      assert.ok(result.lastAccessible, `${name}: final change clipped ${JSON.stringify(result)}`)
      assert.ok(result.dialogFits && result.closeAccessible && result.footerAccessible,
        `${name}: fixed controls offscreen ${JSON.stringify(result)}`)
      assert.equal(result.overflow, 'scroll')
      assert.equal(result.tabIndex, 0)
      await evalJs("(() => {const el = document.querySelector('.asr-whats-new'); el.scrollTop = 0; el.focus()})()")
      await page.keyboard.press('End')
      await delay(250)
      const keyboardPosition = await evalJs("document.querySelector('.asr-whats-new').scrollTop")
      assert.ok(keyboardPosition > 0, `${name}: End key did not scroll the focused release notes`)
      console.log(`${name}: PASS ${width}x${height}, section spacing, heading hierarchy, 43 changes, final entry, fixed controls and keyboard scrolling`)
      await context.close()
    }
  }
} finally {
  await browser?.close()
  vite.kill('SIGTERM')
}
