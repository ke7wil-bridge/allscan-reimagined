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
    configuredUnifiedNetBridgeRow,
    bridgeCardShowsClientDetails,
    bridgeCardWarningText,
    bridgeLastActivityEpoch,
    bridgeTalkerFeed,
    normalizedBridgeMode,
    netBridgeTalkerDestinationLabel,
    provisionedNetBridgeModes,
    resolveBridgeLastCaller,
    summarizeBridgeClientCounts,
    summarizeConnectionTotal,
    talkerInfoParts,
  } = await server.ssrLoadModule('/src/lib/allscanLive.ts')

  const iaxTalker = talkerInfoParts('IaxRpt / Web Transceiver client\u00a0\u00a0Goodyear, AZ', 'KM7GHS')
  assert(iaxTalker.callsign === 'KM7GHS', 'IaxRpt transport label replaced the Web Transceiver callsign')
  assert(iaxTalker.sourceOverride === 'VoIP App', 'Web Transceiver source label was not normalized')
  assert(
    iaxTalker.description === 'Web Transceiver client' && iaxTalker.location === 'Goodyear, AZ',
    'Web Transceiver description or location ordering regressed',
  )
  const allstarTalker = talkerInfoParts('NY7S Full-Duplex 4 Life\u00a0\u00a0Phoenix, Arizona', '53430')
  assert(
    allstarTalker.callsign === 'NY7S'
      && allstarTalker.description === 'Full-Duplex 4 Life'
      && allstarTalker.location === 'Phoenix, Arizona'
      && allstarTalker.sourceOverride === '',
    'ordinary AllStar talker identity regressed',
  )

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
  const sharedTransports = ['p25', 'ysf'].map((mode) => ({
    ...netBridges[0], id: `${mode}_net`, mode, node: '1999', cardType: `${mode}_net`,
  }))
  assert(
    bridgeConnectionIdentities(sharedTransports, 'p25').get('1999')?.id === 'p25_net',
    'node 1999 was not bound exclusively to the active Unified Net Bridge mode',
  )
  const configuredTransportRow = configuredUnifiedNetBridgeRow(sharedTransports[0])
  assert(
    configuredTransportRow.node === '1999'
      && configuredTransportRow.configuredTransport === true
      && configuredTransportRow.connected === '',
    'disconnected Unified Net Bridge transport row was not presentation-only',
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
  assert(
    bridgeLastActivityEpoch({ activity_epoch: 2_000_000_123, last_source_epoch: 0 }) === 2_000_000_123,
    'real outbound activity was discarded when no inbound talker existed',
  )
  assert(
    bridgeLastActivityEpoch({ last_time_epoch: 2_000_000_124 }) === 2_000_000_124,
    'collector-persisted last activity was not consumed by the production UI path',
  )
  assert(bridgeCardShowsClientDetails('standard'), 'standard bridge client details were hidden')
  assert(!bridgeCardShowsClientDetails('dmr_net'), 'DMR Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('ysf_net'), 'YSF Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('p25_net'), 'P25 Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('nxdn_net'), 'NXDN Net Bridge client details were shown')
  assert(!bridgeCardShowsClientDetails('m17_net'), 'M17 Net Bridge client details were shown')
  assert(
    netBridgeTalkerDestinationLabel('p25_net', '707') === 'Talkgroup 707',
    'P25 Talker Card destination was not labeled as a talkgroup',
  )
  assert(
    netBridgeTalkerDestinationLabel('p25_net', 'Talkgroup 707') === 'Talkgroup 707',
    'P25 Talker Card duplicated its talkgroup label',
  )
  const dmrTalkers = bridgeTalkerFeed([{
    id: 'dmr', mode: 'dmr', node: '1883', title: 'DMR Bridge', cardType: 'standard',
    status: 'Source/TX', lastCaller: 'KB2MPX', warning: '-', healthSeverity: null,
    healthIssues: [], currentTg: '91', currentDestination: '91',
    currentDestinationLabel: 'Talkgroup 91', controlLinked: true, digitalLinked: true,
    allstarLinked: true, detailTitle: 'Connected Clients', detailRows: [], detailCount: 0,
    detailAvailable: true, connectedClientCount: 19, runtimeOnline: true, reflector: '',
    module: '', linkProtocol: '', lastTransmitter: 'KB2MPX', lastTxEpoch: 2_000_000_003,
    activeStartEpoch: 2_000_000_000,
    recentTalkers: [{ callsign: 'KB2MPX', startEpoch: 2_000_000_000, eventEpoch: 2_000_000_003, duration: 3 }],
    recentRows: [],
  }])
  assert(
    dmrTalkers.currentTalker?.info === 'KB2MPX'
      && dmrTalkers.currentTalker?.source === 'DMR'
      && dmrTalkers.currentTalker?.node === '1883'
      && dmrTalkers.currentTalker?.description === 'Talkgroup 91',
    'bridge-only DMR caller was not promoted into the main Talkers feed',
  )
  assert(dmrTalkers.recentTalkers.length === 0, 'active DMR caller was duplicated in Talkers history')
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
  const mainSource = readFileSync(new URL('../src/main.tsx', import.meta.url), 'utf8')
  const indexCssSource = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8')
  const allscanLiveSource = readFileSync(new URL('../src/lib/allscanLive.ts', import.meta.url), 'utf8')
  const asrApiSource = readFileSync(new URL('../asr-api.php', import.meta.url), 'utf8')
  const runtimeV102 = readFileSync(new URL('../compat/allscan-v1.02/include/asrRuntime.php', import.meta.url), 'utf8')
  const runtimeV103 = readFileSync(new URL('../compat/allscan-v1.03/include/asrRuntime.php', import.meta.url), 'utf8')
  assert(
    asrApiSource.includes("['connected', 'selected-unverified', 'connected-unverified', 'partial-digital-only']"),
    'Protocol-confirmed P25/NXDN connections must remain linked in the production API',
  )
  assert(
    asrApiSource.includes('$zelloConfigured')
      && asrApiSource.includes("'activity_epoch' => $activityEpoch")
      && asrApiSource.includes("$role === 'relay' ? 'RELAY'"),
    'Zello USRP relay/EOT state is absent from the production bridge-status payload',
  )
  assert(
    !allscanLiveSource.includes("healthSeverity: entry?.health_severity\n        || (entry?.online === false"),
    'unknown DMR/YSF link state still creates a false bridge warning',
  )
  assert(
    asrApiSource.includes("'health_severity' => $ready ? null : 'offline'")
      && asrApiSource.includes(". ' is not active.'"),
    'authoritative P25, NXDN, or M17 readiness failures are not exposed as card health',
  )
  assert(
    asrApiSource.includes('function asr_standard_urf_runtime_paths()')
      && asrApiSource.includes("'/opt/allscan-reimagined-bridges/urf/' . $id . '/data'"),
    'managed Standard URF cards still depend on the legacy global runtime path',
  )
  const dmrControls = appSource.match(
    /\{card\.cardType === 'dmr_net' && authStatus\.canModify[\s\S]+?\{card\.cardType !== 'standard'/,
  )?.[0] || ''
  assert(dmrControls.includes('<input'), 'DMR Net talkgroup is not a typeable input')
  assert(dmrControls.includes('inputMode="numeric"'), 'DMR Net talkgroup lost its numeric keyboard hint')
  assert(dmrControls.includes('placeholder=""'), 'empty DMR Net input shows placeholder text')
  assert(dmrControls.includes('id={`net-mode-${card.id}`}'), 'unified Net Bridge mode selector is missing')
  assert(
    appSource.includes("['dmr','ysf','p25','nxdn','m17'].filter((mode) => availableNetBridgeModes.has(mode)).map")
      && appSource.includes("card.mode.toLowerCase() === (selectedModeAvailable ? netBridgeMode : fallbackMode)"),
    'the dashboard does not constrain installed modes to one visible Net Bridge card',
  )
  assert(
    appSource.includes('void selectNetBridgeMode(event.target.value)')
      && appSource.includes("const owner = 'net-bridge-mode'")
      && appSource.includes("if (bridgeControlBusyRef.current === owner)")
      && appSource.includes('pendingNetBridgeModeRef.current = mode')
      && appSource.includes('await activateNetBridgeMode(targetMode)')
      && appSource.includes('setNetBridgeMode(selected)')
      && appSource.includes('await waitForNetBridgeOwner(card)')
      && appSource.includes("connectPending ? (modeSwitchBusy ? 'Waiting…' : 'Connecting…') : 'Connect'")
      && !appSource.includes('Connect when ready')
      && !appSource.includes('netBridgeQueuedModeRef')
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
      && appSource.includes("Enter an M17 reflector as XXX or M17-XXX and a module A-Z.")
      && appSource.includes("? `M17-${reflectorInput}`")
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
  const narrowControlsStart = indexCssSource.indexOf('/* Beta 8 locked narrow/tablet Node Controls: keep')
  const narrowControlsEnd = indexCssSource.indexOf('/* Beta 8 locked mobile-landscape Node Controls geometry. */', narrowControlsStart)
  const narrowControls = indexCssSource.slice(narrowControlsStart, narrowControlsEnd)
  const authoritativeNarrowStart = indexCssSource.indexOf('/* Beta 8 locked narrow/tablet Node Controls: qualified')
  const authoritativeNarrowEnd = indexCssSource.indexOf('/* Beta 8 locked non-desktop Connection Status readability. */', authoritativeNarrowStart)
  const authoritativeNarrowControls = indexCssSource.slice(authoritativeNarrowStart, authoritativeNarrowEnd)
  assert(
    narrowControlsStart >= 0
      && authoritativeNarrowStart >= 0
      && narrowControls.includes('@media (min-width: 521px) and (max-width: 1199px)')
      && !narrowControls.includes('(orientation: portrait)')
      && narrowControls.includes('display: flex !important;')
      && narrowControls.includes('flex-wrap: nowrap;')
      && authoritativeNarrowControls.includes('@media (min-width: 521px) and (max-width: 1199px)')
      && !authoritativeNarrowControls.includes('(orientation: portrait)')
      && authoritativeNarrowControls.includes('width: 407px !important;')
      && authoritativeNarrowControls.includes('padding-right: 102px;'),
    'Node Controls does not preserve its compact accepted geometry across the 521-1199px resize range',
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
      && (seedConfigSource.match(/"detailTitle": "Connected Clients"/g) || []).length >= 5,
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

  const settingsSource = readFileSync('compat/allscan-v1.02/asr-settings/index.php', 'utf8') + '\n' + readFileSync('compat/allscan-v1.02/asr-settings/settings-controller.php', 'utf8')
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

  assert(
    appSource.includes('const relayActive = zelloFastRelayReadyRef.current')
      && appSource.includes('? zelloFastRelayRef.current')
      && appSource.includes(': Boolean(row && localIsTransmitting)'),
    'Zello relay must use the fast producer when present and retain legacy Asterisk fallback when absent',
  )
  assert(
    appSource.includes('const wasReady = zelloFastRelayReadyRef.current')
      && appSource.includes('zelloFastRelayReadyRef.current = true')
      && appSource.includes('relay.active === previous && wasReady'),
    'Zello fast relay readiness must be established before equal idle samples may suppress the legacy fallback',
  )
  assert(
    appSource.includes("!bridgeStatusLoaded ? '…' : card.detailAvailable ? card.detailCount : '–'"),
    'Bridge client counts must not display a false zero before the first live bridge response',
  )
  assert(allscanLiveSource.includes('action=zello-fast-relay'), 'Zello fast relay endpoint fetch missing')
  assert(
    asrApiSource.includes("'updatedEpochMs' => max(0, (int) ($payload['updated_epoch_ms'] ?? 0))"),
    'Zello fast relay endpoint must preserve the producer event timestamp',
  )
  assert(
    runtimeV102.includes("'zello-relay.json'") && runtimeV103.includes("'zello-relay.json'"),
    'Zello fast relay runtime file must be allowed by every supported ASTAPI runtime',
  )
  assert(
    allscanLiveSource.includes('action=fast-next-mode-activity')
      && asrApiSource.includes('function asr_fast_next_mode_activity_payload()')
      && asrApiSource.includes("$action === 'fast-next-mode-activity'"),
    'P25/NXDN fast activity producer and API route must be deployed together',
  )
  assert(
    allscanLiveSource.includes('updatedEpochMs')
      && appSource.includes('relay.updatedEpochMs < appliedEpochMs'),
    'Zello fast relay polling must reject responses older than the producer event',
  )
  assert(
    appSource.includes('Math.max(0, 100 - (performance.now() - started))'),
    'Zello fast relay polling must remain single-flight while preserving its cadence',
  )
  assert(
    appSource.includes('netBridgeModeError')
      && appSource.includes('Mode change failed.')
      && appSource.includes('The previous mode remains active.'),
    'Net Bridge mode failures are not persistent beside the mode control',
  )
  assert(
    mainSource.includes("window.addEventListener('focus'")
      && mainSource.includes("window.addEventListener('pageshow'")
      && mainSource.includes("document.addEventListener('visibilitychange'")
      && mainSource.includes('installedVersion !== ASR_BUILD_VERSION_LABEL')
      && mainSource.includes('Refresh now')
      && allscanLiveSource.includes("versionLabel: 'Version temporarily unavailable'"),
    'Restored browser tabs do not recheck the installed version or clearly request a refresh',
  )

  console.log('bridge dashboard self-test: ok')
} finally {
  await server.close()
}
