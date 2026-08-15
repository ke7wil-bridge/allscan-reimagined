#!/usr/bin/env node

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
    bridgeDestinationPlaceholder,
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
  assert(
    bridgeDestinationPlaceholder(0, 'Choose approved destination') === '',
    'empty Net Bridge dropdown showed placeholder text',
  )
  assert(
    bridgeDestinationPlaceholder(2, 'Choose approved destination') === 'Choose approved destination',
    'populated Net Bridge dropdown lost its selection prompt',
  )

  console.log('bridge dashboard self-test: ok')
} finally {
  await server.close()
}
