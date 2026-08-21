#!/usr/bin/env node

import { readFileSync } from 'node:fs'
import { createServer } from 'vite'

globalThis.DOMParser = class {
  parseFromString(value) {
    return { body: { textContent: String(value).replace(/<[^>]*>/g, '') } }
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

const server = await createServer({
  appType: 'custom',
  logLevel: 'silent',
  server: { middlewareMode: true },
})

try {
  const {
    bridgeCardShowsClientDetails,
    bridgeCardWarningText,
    normalizedBridgeMode,
    resolveBridgeLastCaller,
    summarizeBridgeClientCounts,
    summarizeConnectionTotal,
  } = await server.ssrLoadModule('/src/lib/allscanLive.ts')

  const modeFixtures = {
    dmr_home: 'dmr',
    ysf_netbridge: 'ysf',
    zello_primary: 'zello',
    p25_main: 'p25',
    nxdn_main: 'nxdn',
    m17_main: 'm17',
    custom_shared: 'custom',
  }
  for (const [id, expected] of Object.entries(modeFixtures)) {
    assert(normalizedBridgeMode(undefined, id) === expected, `${id} mode was not normalized`)
  }

  const counts = summarizeBridgeClientCounts([
    { mode: 'dmr', connectedClientCount: 1 },
    { mode: 'dmr', connectedClientCount: 1 },
    { mode: 'ysf', connectedClientCount: 3 },
    { mode: 'zello', connectedClientCount: 0 },
    { mode: 'p25', connectedClientCount: 0 },
    { mode: 'nxdn', connectedClientCount: 0 },
    { mode: 'm17', connectedClientCount: 0 },
    { mode: 'custom', connectedClientCount: 0 },
  ])
  assert(JSON.stringify(counts) === JSON.stringify([
    { mode: 'dmr', label: 'DMR', count: 2 },
    { mode: 'ysf', label: 'YSF', count: 3 },
  ]), 'bridge instance aggregation or zero-category omission failed')
  const linkedNetBridgeCounts = summarizeBridgeClientCounts([
    { mode: 'ysf', connectedClientCount: 6, cardType: 'standard' },
    {
      mode: 'ysf', connectedClientCount: 0, cardType: 'ysf_net',
      controlLinked: true, digitalLinked: true,
    },
    {
      mode: 'ysf', connectedClientCount: 0, cardType: 'ysf_net',
      controlLinked: false, digitalLinked: false,
    },
    { mode: 'dmr', connectedClientCount: 2, cardType: 'standard' },
    {
      mode: 'dmr', connectedClientCount: 0, cardType: 'dmr_net',
      controlLinked: true, digitalLinked: false,
    },
    {
      mode: 'p25', connectedClientCount: 0, cardType: 'p25_net',
      controlLinked: false, digitalLinked: false, allstarLinked: true,
      currentDestination: '10200',
    },
    {
      mode: 'm17', connectedClientCount: 0, cardType: 'm17_net',
      controlLinked: true, digitalLinked: true,
    },
  ])
  assert(JSON.stringify(linkedNetBridgeCounts) === JSON.stringify([
    { mode: 'ysf', label: 'YSF', count: 7 },
    { mode: 'dmr', label: 'DMR', count: 3 },
    { mode: 'p25', label: 'P25', count: 1 },
    { mode: 'm17', label: 'M17', count: 1 },
  ]), 'connected Net Bridge links were not included once in their mode totals')
  const summary = summarizeConnectionTotal(3, 2, [
    { mode: 'dmr', connectedClientCount: 2 },
    { mode: 'ysf', connectedClientCount: 3 },
  ])
  assert(summary.total === 10, 'combined ASL/DMR/YSF/adjacent total failed')
  assert(
    summary.parts.join(', ') === '3 ASL, 2 DMR, 3 YSF, 2 adjacent',
    'combined ASL/bridge/adjacent label failed',
  )
  const reclassifiedNetSummary = summarizeConnectionTotal(3, 0, [
    { mode: 'ysf', connectedClientCount: 6, cardType: 'standard' },
    {
      mode: 'ysf', connectedClientCount: 0, cardType: 'ysf_net',
      controlLinked: true, digitalLinked: true,
    },
  ])
  assert(reclassifiedNetSummary.total === 9, 'Net Bridge transport was double-counted')
  assert(
    reclassifiedNetSummary.parts.join(', ') === '2 ASL, 7 YSF, 0 adjacent',
    'Net Bridge transport was not reclassified from ASL into its digital mode',
  )

  const config = { node: '100000' }
  assert(resolveBridgeLastCaller(
    { current_user: 'SOURCE', last_user: 'OLD' }, 'Source/TX', config,
  ) === 'SOURCE', 'active source caller was not shown')
  assert(resolveBridgeLastCaller(
    { current_user: '', caller: '', last_user: 'OLD' }, 'Source/TX', config,
  ) === '-', 'timestamp-free generic last_user was revived for Source/TX')
  assert(resolveBridgeLastCaller(
    { last_source_user: 'COMPLETED', last_source_epoch: 2_000_000_000 }, 'Idle', config,
  ) === '-', 'idle bridge retained a completed caller')
  assert(resolveBridgeLastCaller(
    { current_user: 'OUTBOUND', last_source_user: 'SOURCE' }, 'Relay', config,
  ) === '-', 'relay displayed a source caller')
  assert(bridgeCardShowsClientDetails('standard'), 'standard bridge client details were hidden')
  assert(!bridgeCardShowsClientDetails('dmr_net'), 'DMR Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('ysf_net'), 'YSF Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('p25_net'), 'P25 Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('nxdn_net'), 'NXDN Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('m17_net'), 'M17 Net Bridge client details were shown')
  assert(
    bridgeCardWarningText('-') === '-',
    'healthy backend readiness text was shown as a warning',
  )
  assert(
    bridgeCardWarningText('') === '-',
    'empty warning was not normalized',
  )
  assert(
    bridgeCardWarningText('Bridge status needs attention. Review Bridge Settings.')
      === 'Bridge status needs attention. Review Bridge Settings.',
    'generic Net Bridge warning was hidden',
  )
  assert(
    bridgeCardWarningText('Audio path unavailable.') === 'Audio path unavailable.',
    'live bridge warning was hidden',
  )
  const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  const dmrControls = appSource.match(
    /\{card\.cardType === 'dmr_net' && authStatus\.canModify[\s\S]+?\{card\.cardType !== 'standard'/,
  )?.[0] || ''
  assert(dmrControls.includes('<input'), 'DMR Net talkgroup is not a typeable input')
  assert(dmrControls.includes('inputMode="numeric"'), 'DMR Net talkgroup lost its numeric keyboard hint')
  assert(dmrControls.includes('placeholder=""'), 'empty DMR Net input shows placeholder text')
  assert(!dmrControls.includes('<select'), 'DMR Net talkgroup regressed to a dropdown')
  assert(!dmrControls.includes('approvedDestinations.length'), 'DMR Net input still depends on an approved list')
  assert(
    dmrControls.includes("event.target.value.replace(/\\D/g, '').slice(0, 8)"),
    'DMR Net talkgroup input is not restricted to eight digits',
  )
  assert(
    appSource.includes("card.cardType === 'ysf_net' ? (")
      && appSource.match(/card\.cardType === 'ysf_net' \? \([\s\S]+?placeholder=""/)
      && appSource.includes('event.target.value.slice(0, 80)'),
    'YSF Net reflector is not a typeable bounded input',
  )
  assert(
    appSource.includes("!['standard', 'dmr_net', 'ysf_net'].includes(bridge.cardType)"),
    'manual DMR/YSF controls still poll approved destination lists',
  )
  assert(
    appSource.includes('<option value=""></option>')
      && !appSource.includes('Choose approved destination'),
    'a Net Bridge dropdown still shows placeholder text',
  )
  assert(
    appSource.includes("const dmrTalkgroupCandidate = dmrTalkgroupInputs[card.id] || ''")
      && !appSource.includes('bridgeLinked ? card.currentTg')
      && !appSource.includes('next[card.id] = card.currentDestination'),
    'a Net Bridge destination field is still prefilled from the current connection',
  )
  assert(
    !appSource.includes('[card.id]: canonicalId')
      && !appSource.includes('[card.id]: canonical.currentDestination'),
    'a Net Bridge destination field is not cleared after connecting',
  )
  assert(
    (appSource.match(/\[card\.id\]: ''/g) || []).length >= 5,
    'a Net Bridge destination field is not cleared after connect and disconnect actions',
  )
  const adminSubmenu = appSource.match(
    /allscan-submenu-admin[\s\S]+?allscan-submenu-theme/,
  )?.[0] || ''
  assert(
    adminSubmenu.includes("effectiveThemeSettings.theme === 'lcars-frame'")
      && adminSubmenu.includes('&& desktopThemeViewport')
      && adminSubmenu.includes('authStatus.loggedIn')
      && adminSubmenu.includes('onClick={() => void logoutAllScan()}>Logout</button>'),
    'ST:ASL desktop Admin menu does not expose authenticated Logout',
  )
  assert(
    appSource.includes('allscan-menu-proxy-row allscan-menu-logout-row'),
    'the existing standard-theme and mobile Logout action was removed',
  )
  assert(
    (appSource.match(/onClick=\{\(\) => void logoutAllScan\(\)\}/g) || []).length === 2,
    'Logout must have exactly one standard render site and one ST:ASL desktop render site',
  )

  console.log('bridge dashboard self-test: ok')
} finally {
  await server.close()
}
