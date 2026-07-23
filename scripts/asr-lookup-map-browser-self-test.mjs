#!/usr/bin/env node
import fs from 'node:fs'
import vm from 'node:vm'

const lookupSource = fs.readFileSync(new URL('../compat/allscan-v1.01/lookup/index.php', import.meta.url), 'utf8')
const scriptMatch = lookupSource.match(/<script>\s*([\s\S]*?)\s*<\/script>/)
if (!scriptMatch) throw new Error('Lookup page script was not found')
const lookupScript = scriptMatch[1].replace(
  /<\?php\s+echo\s+json_encode\(rtrim\(\$urlbase,\s*'\/'\),\s*JSON_UNESCAPED_SLASHES\);\s*\?>/,
  '"/asr"',
)
if (lookupScript.includes('<?php')) throw new Error('Lookup page script still contains unevaluated PHP')

class MockElement {
  constructor(id) {
    this.id = id
    this.hidden = false
    this.disabled = false
    this.textContent = ''
    this.innerHTML = ''
    this.src = ''
    this.alt = ''
    this.complete = false
    this.naturalWidth = 0
    this.attributes = new Map()
    this.listeners = new Map()
    this.classNames = new Set()
    this.classList = {
      add: (...names) => names.forEach((name) => this.classNames.add(name)),
      remove: (...names) => names.forEach((name) => this.classNames.delete(name)),
    }
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value))
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback)
  }

  dispatch(name, event = {}) {
    const callback = this.listeners.get(name)
    if (callback) callback({ target: this, ...event })
  }
}

const ids = [
  'asrLookupList',
  'asrLookupCount',
  'asrLookupUpdated',
  'asrStationMapOpen',
  'asrStationMapClose',
  'asrStationMapPanel',
  'asrStationMapFrame',
  'asrStationMapCanvas',
  'asrStationMapCard',
  'asrStationMapSummary',
  'asrStationMapUnmapped',
  'asrNetworkMapOpen',
  'asrNetworkMapClose',
  'asrNetworkMapPanel',
  'asrNetworkMapFrame',
  'asrNetworkMapLoading',
  'asrNetworkMapImage',
  'asrNetworkMapFallback',
]
const elements = Object.fromEntries(ids.map((id) => [id, new MockElement(id)]))
elements.asrStationMapPanel.hidden = true
elements.asrStationMapCard.hidden = true
elements.asrNetworkMapPanel.hidden = true
elements.asrNetworkMapOpen.disabled = true

let lookupPayload = {
  ok: true,
  node: '641890',
  generatedAt: '2026-07-23T12:00:00Z',
  bridgeNodes: ['1883'],
  items: [{ source: 'Connection Status', label: 'N7YO', node: '2300', callsign: 'N7YO', locationHint: 'Phoenix, AZ' }],
}
let mapPayload = { ok: true, points: [], unmapped: [] }
let mapFetchMode = 'success'
let mapFetchDeferreds = []
const intervalCallbacks = []
const longTimeoutCallbacks = new Map()
const clearedTimeouts = []
const openedWindows = []
let nextTimeoutId = 1
let mobileViewport = false
const documentListeners = new Map()
const markerGroup = {
  markers: [],
  clearLayers() {
    this.markers = []
  },
  addTo() {
    return this
  },
}
const map = {
  fitBounds() {},
  setView() { return this },
  invalidateSize() {},
}

const document = {
  hidden: false,
  getElementById: (id) => elements[id],
  querySelector: () => null,
  createElement: () => new MockElement('created'),
  head: { appendChild() {} },
  addEventListener: (name, callback) => documentListeners.set(name, callback),
}
const window = {
  L: {
    map: () => map,
    tileLayer: () => ({ addTo() {} }),
    layerGroup: () => markerGroup,
    divIcon: (options) => options,
    marker: (coordinates) => ({
      addTo(group) {
        group.markers.push(coordinates)
        return this
      },
      on() { return this },
    }),
    DomEvent: { stopPropagation() {} },
  },
  setTimeout: (callback, delay = 0) => {
    const id = nextTimeoutId
    nextTimeoutId += 1
    if (delay >= 300000) longTimeoutCallbacks.set(id, callback)
    else callback()
    return id
  },
  clearTimeout: (id) => {
    clearedTimeouts.push(id)
    longTimeoutCallbacks.delete(id)
  },
  setInterval: (callback) => {
    intervalCallbacks.push(callback)
    return intervalCallbacks.length
  },
  matchMedia: () => ({ matches: mobileViewport }),
  open: (...args) => {
    openedWindows.push(args)
    return null
  },
}
window.window = window

const context = vm.createContext({
  console,
  document,
  window,
  fetch: async (url) => {
    if (!String(url).includes('action=station-map')) {
      return { ok: true, json: async () => lookupPayload }
    }
    if (mapFetchDeferreds.length) return mapFetchDeferreds.shift().promise
    if (mapFetchMode === 'network') throw new Error('network unavailable')
    if (mapFetchMode === 'http') return { ok: false, json: async () => ({ ok: false }) }
    if (mapFetchMode === 'json') {
      return { ok: true, json: async () => { throw new Error('invalid JSON') } }
    }
    return { ok: true, json: async () => mapPayload }
  },
  Promise,
  Date,
  Number,
  String,
  Array,
  Object,
  JSON,
  RegExp,
  Math,
  encodeURIComponent,
  isFinite,
})

vm.runInContext(lookupScript, context, { filename: 'lookup/index.php inline script' })

const flush = async () => {
  for (let index = 0; index < 20; index += 1) await Promise.resolve()
}
const assert = (condition, message) => {
  if (!condition) throw new Error(message)
}
const lastOpenedWindow = () => openedWindows[openedWindows.length - 1] || []
const refresh = async () => {
  intervalCallbacks[0]()
  await flush()
}
const deferredResponse = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
const mapResponse = (payload) => ({ ok: true, json: async () => payload })

await flush()
assert(elements.asrNetworkMapOpen.disabled === false, 'network-map button did not enable after the local node loaded')
elements.asrNetworkMapOpen.dispatch('click')
assert(elements.asrNetworkMapPanel.hidden === false, 'desktop network map did not open inline')
assert(elements.asrNetworkMapOpen.attributes.get('aria-expanded') === 'true', 'desktop network-map expanded state was not set')
assert(elements.asrNetworkMapImage.src.includes('/stats/641890/networkMap?nh_refresh='), 'desktop network map URL did not use the local node and cache buster')
assert(elements.asrNetworkMapImage.alt.includes('641890'), 'network map image alt text omitted the local node')
assert(longTimeoutCallbacks.size === 1, 'desktop network map did not schedule its five-minute refresh')

elements.asrNetworkMapImage.dispatch('error')
assert(elements.asrNetworkMapLoading.textContent === 'Map unavailable', 'network map image failure was not displayed')
elements.asrNetworkMapImage.complete = true
elements.asrNetworkMapImage.naturalWidth = 800
elements.asrNetworkMapImage.dispatch('load')
assert(elements.asrNetworkMapLoading.hidden === true, 'network map loader did not clear after image load')

const firstNetworkUrl = elements.asrNetworkMapImage.src
const firstNetworkTimer = [...longTimeoutCallbacks.keys()][0]
const firstNetworkRefresh = longTimeoutCallbacks.get(firstNetworkTimer)
longTimeoutCallbacks.delete(firstNetworkTimer)
await new Promise((resolve) => setTimeout(resolve, 2))
firstNetworkRefresh()
assert(elements.asrNetworkMapImage.src !== firstNetworkUrl, 'five-minute network refresh did not create a fresh image URL')
assert(longTimeoutCallbacks.size === 1, 'network map did not schedule its next five-minute refresh')

const fallbackUrl = elements.asrNetworkMapImage.src
elements.asrNetworkMapFallback.dispatch('click')
assert(lastOpenedWindow()[0] === fallbackUrl, 'Open Full Map did not use the current refreshed image URL')
assert(lastOpenedWindow()[1] === '_blank' && lastOpenedWindow()[2] === 'noopener', 'Open Full Map did not use a safe new tab')

const activeNetworkTimer = [...longTimeoutCallbacks.keys()][0]
elements.asrNetworkMapClose.dispatch('click')
assert(elements.asrNetworkMapPanel.hidden === true, 'network map close did not hide the inline panel')
assert(elements.asrNetworkMapOpen.attributes.get('aria-expanded') === 'false', 'network-map expanded state did not reset on close')
assert(clearedTimeouts.includes(activeNetworkTimer), 'network map close did not cancel the five-minute refresh')
assert(longTimeoutCallbacks.size === 0, 'network map timer remained scheduled after close')

mobileViewport = true
elements.asrNetworkMapOpen.dispatch('click')
assert(elements.asrNetworkMapPanel.hidden === true, 'mobile network map incorrectly opened inline')
assert(lastOpenedWindow()[0].includes('/stats/641890/networkMap?nh_refresh='), 'mobile network map did not open a fresh full-map URL')
assert(lastOpenedWindow()[1] === '_blank' && lastOpenedWindow()[2] === 'noopener', 'mobile network map did not use a safe new tab')
assert(longTimeoutCallbacks.size === 0, 'mobile network map incorrectly scheduled an inline refresh')
mobileViewport = false

elements.asrStationMapOpen.dispatch('click')
await flush()
assert(elements.asrStationMapOpen.disabled === true, 'station-map button did not disable while open')
assert(elements.asrStationMapOpen.attributes.get('aria-pressed') === 'true', 'station-map pressed state was not set')
assert(elements.asrStationMapSummary.textContent === 'No connected station locations are available right now.', 'empty first response left the map loading')

mapPayload = {
  ok: true,
  points: [{ callsign: 'N7YO', node: '2300', name: 'Jim', location: 'Phoenix, AZ', lat: 33.45, lng: -112.07 }],
  unmapped: [],
}
await refresh()
assert(markerGroup.markers.length === 1, 'new station marker was not added')
assert(elements.asrStationMapSummary.textContent === '1 connected station location mapped.', 'mapped-station summary was not updated')

mapPayload = { ok: false, error: 'temporary map failure' }
await refresh()
assert(markerGroup.markers.length === 1, 'ok:false response removed the last valid marker')
assert(elements.asrStationMapSummary.textContent.includes('showing last known map data'), 'ok:false response did not mark retained data stale')

for (const failureMode of ['http', 'network', 'json']) {
  mapFetchMode = failureMode
  await refresh()
  assert(markerGroup.markers.length === 1, `${failureMode} failure removed the last valid marker`)
  assert(elements.asrStationMapSummary.textContent.includes('showing last known map data'), `${failureMode} failure did not mark retained data stale`)
}
mapFetchMode = 'success'
mapPayload = {
  ok: true,
  points: [{ callsign: 'N7YO', node: '2300', name: 'Jim', location: 'Phoenix, AZ', lat: 33.45, lng: -112.07 }],
  unmapped: [],
}
await refresh()
assert(elements.asrStationMapSummary.textContent === '1 connected station location mapped.', 'successful unchanged response did not clear the stale label')

mapPayload = { ok: true, points: [], unmapped: [{ callsign: 'N7YO', node: '2300', label: 'N7YO' }] }
await refresh()
assert(markerGroup.markers.length === 0, 'disconnected station marker was not removed')
assert(elements.asrStationMapUnmapped.textContent.includes('N7YO'), 'unmapped station was not displayed')

mapPayload = { ok: true, points: [], unmapped: [{ callsign: 'W7TEST', node: '2301', label: 'W7TEST' }] }
await refresh()
assert(!elements.asrStationMapUnmapped.textContent.includes('N7YO'), 'stale unmapped station remained visible')
assert(elements.asrStationMapUnmapped.textContent.includes('W7TEST'), 'replacement unmapped station was not displayed')

const older = deferredResponse()
const newer = deferredResponse()
mapFetchDeferreds = [older, newer]
const olderRefresh = refresh()
await flush()
const newerRefresh = refresh()
await flush()
newer.resolve(mapResponse({
  ok: true,
  points: [{ callsign: 'NEWER', node: '2400', name: 'Newer', location: 'Newer place', lat: 40, lng: -100 }],
  unmapped: [],
}))
await newerRefresh
await flush()
older.resolve(mapResponse({
  ok: true,
  points: [{ callsign: 'OLDER', node: '2401', name: 'Older', location: 'Older place', lat: 10, lng: -20 }],
  unmapped: [],
}))
await olderRefresh
await flush()
assert(markerGroup.markers.length === 1, 'out-of-order responses produced duplicate markers')
assert(markerGroup.markers[0][0] === 40 && markerGroup.markers[0][1] === -100, 'older response overwrote newer map data')

elements.asrStationMapClose.dispatch('click')
assert(elements.asrStationMapPanel.hidden === true, 'station-map close did not hide the inline panel')
assert(elements.asrStationMapOpen.disabled === false, 'station-map button did not re-enable after close')
assert(elements.asrStationMapOpen.attributes.get('aria-pressed') === 'false', 'station-map pressed state did not reset after close')

assert(lookupSource.includes('>View Station Map</button>'), 'station-map button label does not match the status page')
assert(lookupSource.includes('>View Network Map</button>'), 'network-map button label does not match the status page')
assert(lookupSource.includes('@media (max-width:760px)'), 'lookup map does not include the mobile network-map layout')
assert(lookupSource.includes('Number(text) > 0 && Number(text) < 2000'), 'browser private-node rule does not preserve public four-digit nodes')
console.log('lookup/map browser self-test: ok')
