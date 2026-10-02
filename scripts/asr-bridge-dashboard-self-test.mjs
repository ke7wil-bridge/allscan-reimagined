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
    bridgeConnectionIdentities,
    bridgeCardShowsClientDetails,
    bridgeCardWarningText,
    normalizedBridgeMode,
    provisionedNetBridgeModes,
    resolveBridgeLastCaller,
    summarizeBridgeClientCounts,
    summarizeConnectionTotal,
  } = await server.ssrLoadModule('/src/lib/allscanLive.ts')

  const netBridgeTypes = ['dmr_net', 'ysf_net', 'p25_net', 'nxdn_net', 'm17_net']
  const netBridges = netBridgeTypes.map((cardType, index) => ({
    id: `qa_${cardType}`,
    node: String(1101 + index),
    linkAlias: String(999100001 + index),
    title: `${cardType} bridge`,
    detailTitle: 'Connected Clients',
    cardType,
  }))
  const identities = bridgeConnectionIdentities(netBridges)
  for (const bridge of netBridges) {
    assert(identities.get(bridge.node)?.id === bridge.id, `${bridge.cardType} physical node identity was not mapped`)
    assert(identities.get(bridge.linkAlias)?.id === bridge.id, `${bridge.cardType} link alias identity was not mapped`)
  }

  const unifiedModes = provisionedNetBridgeModes(netBridgeTypes.map((cardType) => ({
    id: `qa_${cardType}`,
    mode: 'display metadata is not the provisioning authority',
    node: '1999',
    title: cardType,
    detailTitle: 'Connected Clients',
    cardType,
  })))
  assert(
    ['dmr', 'ysf', 'p25', 'nxdn', 'm17'].every((mode) => unifiedModes.has(mode)),
    'the unified mode inventory did not expose all five provisioned Net Bridge card types',
  )
  assert(
    provisionedNetBridgeModes([{ ...netBridges[0], node: '1004' }]).size === 0,
    'a bridge outside the unified node 1999 transport was treated as a unified mode',
  )

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
  const indexCssSource = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
  const allscanLiveSource = readFileSync(new URL('../src/lib/allscanLive.ts', import.meta.url), 'utf8')
  assert(
    allscanLiveSource.includes("|| (mode === 'dstar'")
      && !allscanLiveSource.includes("healthSeverity: entry?.health_severity\n        || (entry?.online === false"),
    'unknown DMR/YSF link state still creates a false bridge warning',
  )
  const dmrControls = appSource.match(
    /\{card\.cardType === 'dmr_net' && authStatus\.canModify[\s\S]+?\{card\.cardType !== 'standard'/,
  )?.[0] || ''
  assert(dmrControls.includes('<input'), 'DMR Net talkgroup is not a typeable input')
  assert(dmrControls.includes('inputMode="numeric"'), 'DMR Net talkgroup lost its numeric keyboard hint')
  assert(dmrControls.includes('placeholder=""'), 'empty DMR Net input shows placeholder text')
  assert(dmrControls.includes('id={`net-mode-${card.id}`}'), 'unified Net Bridge mode selector is missing')
  assert(
    appSource.includes("['dmr','ysf','p25','nxdn','m17'].map")
      && appSource.includes('disabled={!availableNetBridgeModes.has(mode)}')
      && appSource.includes("card.mode.toLowerCase() === (selectedModeAvailable ? netBridgeMode : fallbackMode)"),
    'the dashboard does not constrain the five modes to one visible Net Bridge card',
  )
  assert(
    appSource.includes('void selectNetBridgeMode(event.target.value)')
      && appSource.includes('await activateNetBridgeMode(targetMode)')
      && appSource.includes('netBridgeQueuedModeRef.current = mode')
      && !appSource.includes('on private node 1999'),
    'mode selection does not use the unified lifecycle or exposes its private node',
  )
  assert(!dmrControls.includes('<select id={`dmr-net-tg-${card.id}`}'), 'DMR Net talkgroup regressed to a dropdown')
  assert(!dmrControls.includes('approvedDestinations.length'), 'DMR Net input still depends on an approved list')
  assert(
    dmrControls.includes("event.target.value.replace(/\\D/g, '').slice(0, 8)"),
    'DMR Net talkgroup input is not restricted to eight digits',
  )
  assert(
    appSource.includes("card.cardType === 'ysf_net' || card.cardType === 'm17_net' ? (")
      && appSource.match(/card\.cardType === 'ysf_net' \|\| card\.cardType === 'm17_net' \? \([\s\S]+?placeholder=""/)
      && appSource.includes('event.target.value.slice(0, 80)'),
    'YSF/M17 Net reflector is not a typeable bounded input',
  )
  assert(
    appSource.includes('id={`m17-net-module-${card.id}`}')
      && appSource.includes("Enter an M17 reflector in M17-XXX format and a module A-Z.")
      && appSource.includes("/^M17-[A-Z0-9]{3}$/.test(reflector)")
      && appSource.includes("event.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 1)"),
    'M17 Net reflector/module controls are missing or insufficiently validated',
  )
  assert(
    !appSource.match(/card\.cardType === 'm17_net'[\s\S]{0,300}approvedDestinations\.map/),
    'M17 Net destination is still restricted to the provisioned dropdown',
  )
  assert(
    appSource.includes("? 'Current Reflector' : 'Current Destination'")
      && appSource.includes("card.currentDestination || '–'")
      && !appSource.includes("card.currentTg || '-'"),
    'Net Bridge destination labels or idle en dashes are not standardized',
  )
  assert(
    (appSource.match(/allscan-bridge-row allscan-bridge-status-row/g) || []).length >= 5
      && (appSource.match(/allscan-bridge-current-row/g) || []).length === 2
      && indexCssSource.includes('.allscan-bridge-current-row > span:first-child')
      && indexCssSource.includes('white-space: nowrap;'),
    'Net Bridge current destination rows do not share standard alignment or nowrap behavior',
  )
  assert(
    indexCssSource.includes('justify-content:space-evenly;')
      && !indexCssSource.includes('column-gap:calc((100% - 24px) / 10)'),
    'desktop bridge grid does not preserve five-across spacing',
  )
  assert(
    indexCssSource.includes('@media (min-width: 1201px)')
      && indexCssSource.includes('.allscan-bridge-grid .allscan-bridge-controls')
      && indexCssSource.includes('grid-template-columns: 130px minmax(0, 1fr);')
      && indexCssSource.includes('min-height: 38px;')
      && indexCssSource.includes('height: 38px;')
      && indexCssSource.includes('border-top: 1px solid rgba(255, 255, 255, 0.16);')
      && indexCssSource.includes('min-height: 28px;')
      && indexCssSource.includes('font-size: 13px;')
      && indexCssSource.includes('font-weight: 700;')
      && indexCssSource.includes('font-size: 12px;'),
    'desktop Net Bridge controls do not use equal fields and the compact standard type scale',
  )
  assert(
  (appSource.match(/allscan-bridge-controls allscan-net-bridge-controls/g) || []).length === 2
      && (appSource.match(/allscan-bridge-tune allscan-net-bridge-mode-row/g) || []).length === 2
      && (appSource.match(/allscan-net-bridge-destination-row/g) || []).length === 2
      && indexCssSource.includes('grid-template-columns: auto minmax(52px, .72fr) auto minmax(62px, 1.28fr) auto 30px;')
      && indexCssSource.includes('grid-column: 1 / -1;')
      && indexCssSource.includes('grid-row: 2;')
      && indexCssSource.includes('flex: 1 1 0;'),
    'unified Net Bridge controls do not keep mode/destination on top and actions on the bottom row',
  )
  assert(
    appSource.includes('allscan-controls-lower-row')
      && appSource.includes('Manage Kicks & Bans')
      && indexCssSource.includes('.allscan-controls-lower-row'),
    'current Node Controls management layout regressed',
  )
  assert(
    !appSource.includes('fetchBridgeDestinations')
      && !appSource.includes('approvedDestinations')
      && !appSource.includes('approvedDestinationInput'),
    'Net Bridge controls still depend on approved destination lists',
  )
  assert(
    appSource.includes('inputMode="numeric"')
      && !appSource.includes('Choose approved destination'),
    'P25/NXDN Net Bridge destinations are not free numeric entry fields',
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
  assert(
    appSource.includes('allscan-menu-proxy-row allscan-menu-logout-row'),
    'the theme-independent Logout action was removed',
  )
  assert(
    (appSource.match(/onClick=\{\(\) => void logoutAllScan\(\)\}/g) || []).length === 1,
    'Logout must have exactly one shared render site',
  )
  assert(
    appSource.includes('bridgeLastTalker(card)')
      && appSource.includes('relativeBridgeTime(card.lastTxEpoch)')
      && !appSource.includes("new Date(card.lastTxEpoch * 1000).toLocaleTimeString"),
    'Last Talker is not using the standard relative-time presentation',
  )
  const seedConfigSource = readFileSync(new URL('../personalization/config.seed.json', import.meta.url), 'utf8')
  assert(
    seedConfigSource.includes('"id": "zello"') && seedConfigSource.includes('"detailTitle": "Recent Talkers"')
      && (seedConfigSource.match(/"detailTitle": "Connected Clients"/g) || []).length >= 6,
    'seed bridge labels do not follow semantic roster/talker capabilities',
  )
  assert(
    allscanLiveSource.includes("bridgeConfig.id === 'zello' ? liveZelloRecentTalkers(bridge.zello) : []")
      && allscanLiveSource.includes("detailAvailable")
      && allscanLiveSource.includes("clientMeta"),
    'bridge detail semantics do not preserve Zello talker history and roster provenance',
  )
  assert(
    appSource.includes('allscan-urf-mini-card')
      && appSource.includes('allscan-urf-mini-grid')
      && appSource.includes('<span>Connected Clients</span>')
      && appSource.includes('<span>Recent Activity</span>')
      && appSource.includes('<span>Last Talker</span>'),
    'URF modes are not rendered as compact standard bridge cards',
  )
  assert(
    appSource.includes('allscan-controls-lower-row')
      && appSource.includes('Manage Kicks & Bans'),
    'Node Controls lost centralized client administration access',
  )
  assert(
    !appSource.includes('allscan-urf-mode-stack')
      && !appSource.includes('allscan-urf-mode-dropdown'),
    'legacy bespoke URF mode-card rendering is still active',
  )
  assert(
    !appSource.includes('allscan-bridge-warning-row')
      && !appSource.includes('<span>Warning / Error</span>'),
    'permanent warning/error body row returned instead of header warning treatment',
  )

  const settingsSource = readFileSync('compat/allscan-v1.01/asr-settings/index.php', 'utf8')
  assert(
    (settingsSource.match(/<option value="net_bridge">Net Bridge<\/option>/g) || []).length === 1
      && !settingsSource.includes('<option value="net">Net Bridge</option>'),
    'Settings does not expose exactly one unified Net Bridge setup choice',
  )
  for (const mode of ['DMR', 'YSF', 'P25', 'NXDN', 'M17']) {
    assert(settingsSource.includes(`>${mode}`) || settingsSource.includes(`'${mode.toLowerCase()}'`), `Settings unified setup is missing ${mode}`)
  }
  assert(
    settingsSource.includes('asrSettingsUnifiedNetBridgePanel')
      && settingsSource.includes("'net-bridge-plan'")
      && settingsSource.includes("'net-bridge-install'")
      && !settingsSource.includes('m17.example.net'),
    'Settings unified rendering/provisioning wiring is incomplete or retains the fake M17 host',
  )

  console.log('bridge dashboard self-test: ok')
} finally {
  await server.close()
}
