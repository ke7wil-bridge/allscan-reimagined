#!/usr/bin/env node

import { readFileSync } from 'node:fs'

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const dialog = readFileSync(new URL('../src/components/SupportAsrDialog.tsx', import.meta.url), 'utf8')
const supportConfig = readFileSync(new URL('../src/config/support.ts', import.meta.url), 'utf8')
const css = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')

const menuSupportIndex = app.indexOf('className="allscan-menu-proxy-row allscan-menu-support-row"')
const menuLogoutIndex = app.indexOf('className="allscan-menu-proxy-row allscan-menu-logout-row"')
assert(menuSupportIndex >= 0 && menuSupportIndex < menuLogoutIndex, 'Support ASR is not immediately before the standard Logout action')
assert(
  app.includes('Support ASR</button>\n                        <button type="button" role="menuitem" onClick={() => void logoutAllScan()}>Logout</button>'),
  'Support ASR is not immediately before the ST:ASL Logout action',
)
assert(
  (app.match(/authStatus\.loggedIn/g) || []).length >= 3 && app.includes('onClick={openSupportDialog}'),
  'Support ASR is not available through the authenticated menu architecture',
)
assert(
  app.includes('setSupportDialogOpen(true)')
    && !app.includes('window.open(SUPPORT_ASR_URL')
    && !app.includes('location.assign(SUPPORT_ASR_URL'),
  'The menu must open a confirmation modal without navigating to PayPal',
)

assert(
  supportConfig.trim() === "export const SUPPORT_ASR_URL = 'https://paypal.me/jdjcaz' as const",
  'The authoritative PayPal destination is incorrect or not isolated in project configuration',
)
assert(
  dialog.includes('href={SUPPORT_ASR_URL}')
    && dialog.includes('target="_blank"')
    && dialog.includes('rel="noopener noreferrer"'),
  'The PayPal action does not use the authoritative destination with safe new-tab behavior',
)
assert(
  dialog.includes('Contributions are completely optional')
    && dialog.includes('aren’t required to use any ASR features.'),
  'The modal does not clearly state that contributions are optional',
)
assert(
  dialog.includes("event.key === 'Escape'")
    && dialog.includes("event.key !== 'Tab'")
    && app.includes('window.requestAnimationFrame(() => supportReturnFocusRef.current?.focus())')
    && dialog.includes('onClick={onClose}>Cancel</button>'),
  'Escape, keyboard focus containment, focus return, or Cancel behavior is missing',
)
assert(
  css.includes('max-height: calc(100dvh')
    && css.includes('@media (max-width: 420px)')
    && css.includes('@media (max-height: 520px) and (orientation: landscape)')
    && css.includes('var(--theme-panel-solid)')
    && css.includes('var(--theme-text)')
    && css.includes(':focus-visible'),
  'Responsive, theme-token, or visible-focus support is missing',
)

const changedSources = [app, dialog, supportConfig, css].join('\n')
assert(
  !/(analytics|telemetry|donation.*(?:storage|cookie)|(?:local|session)Storage.*support|fetch\([^)]*paypal)/i.test(changedSources),
  'Donation tracking, storage, or PayPal polling was introduced',
)

console.log('support ASR self-test: ok')
