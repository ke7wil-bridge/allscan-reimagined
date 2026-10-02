#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8')
const exists = (relative) => fs.existsSync(path.join(root, relative))
const check = (condition, message) => { if (!condition) throw new Error(message) }
const sourceTree = exists('asr-api.php')

const selector = read('compat/allscan-v1.01/include/asrCpuTemperature.php')
const common = read('compat/allscan-v1.01/include/common.php')
const api = read(sourceTree ? 'asr-api.php' : 'server/asr-api.php')
const serverApi = read('server/asr-api.php')
const performance = read('compat/allscan-v1.01/performance/index.php')
const astapi = read('compat/allscan-v1.01/astapi/server.php')

check(common.includes("require_once(__DIR__ . '/asrCpuTemperature.php')"), 'ASR common header does not load the shared CPU selector.')
check(common.includes('$cpu = asr_cpu_temp_payload();') && common.includes("$cpu['value']") && common.includes("$cpu['bgColor']"), 'Server-rendered ASR header does not use the shared value and color contract.')
check(!/\bcpuTemp\s*\(/.test(common), 'Modern ASR common/header calls legacy cpuTemp() directly.')

for (const [label, source] of [['web API', api], ['packaged API', serverApi]]) {
  check(source.includes("require_once __DIR__ . '/include/common.php'"), `${label} does not load common shared includes.`)
  check(source.includes('asr_json(asr_cpu_temp_payload())'), `${label} CPU endpoint does not use the shared selector.`)
  check(source.includes('$cpu = asr_cpu_temp_payload();'), `${label} Performance Stats does not use the shared selector.`)
  check(!source.includes('function asr_cpu_temperature_reading'), `${label} embeds an independent CPU selector.`)
}

check(performance.includes("action=performance-stats") && performance.includes('data.cpuTemp'), 'Performance Stats UI does not consume the API temperature contract.')
check(astapi.includes('$cpu = asr_cpu_temp_payload();') && !astapi.includes("glob('/sys/class/thermal"), 'AST status-load throttle does not use the shared selector.')
if (sourceTree) {
  const client = read('src/lib/allscanLive.ts')
  const app = read('src/App.tsx')
  check(client.includes('?action=cpu-temp') && app.includes('setCpuValue(next.value)') && app.includes('setCpuBgColor(next.bgColor)'), 'React dashboard does not consume the authoritative API value/color contract.')
}

const headerPages = [
  ['Settings', 'compat/allscan-v1.01/asr-settings/settings-controller.php'],
  ['Help', 'compat/allscan-v1.01/asr-instructions/index.php'],
  ['Lookup & Map', 'compat/allscan-v1.01/lookup/index.php'],
  ['Performance Stats', 'compat/allscan-v1.01/performance/index.php'],
  ['TGIF', 'compat/allscan-v1.01/tgif/index.php'],
  ['EchoLink Lookup', 'compat/allscan-v1.01/echolink-lookup/index.php'],
]
for (const [label, relative] of headerPages) {
  check(/\bpageInit\s*\(/.test(read(relative)), `${label} does not render the shared ASR header.`)
}
const account = read('compat/allscan-v1.01/user/settings/index.php')
check(account.includes('/asr-settings/?section=account'), 'My Account does not route to the Settings shell and its shared header.')

const modernSources = [
  ...(sourceTree ? ['asr-api.php'] : []),
  'server/asr-api.php',
  ...fs.readdirSync(path.join(root, 'compat/allscan-v1.01'), { recursive: true })
    .filter((relative) => typeof relative === 'string' && /\.(?:php|js)$/.test(relative))
    .map((relative) => `compat/allscan-v1.01/${relative}`)
    .filter((relative) => relative !== 'compat/allscan-v1.01/include/asrCpuTemperature.php'),
]
for (const relative of modernSources) {
  const source = read(relative)
  check(!/\/sys\/class\/(?:thermal|hwmon)/.test(source), `${relative} added an independent direct CPU sensor read.`)
  check(!/\bcpuTemp\s*\(/.test(source), `${relative} calls legacy cpuTemp() directly.`)
  check(!/function\s+asr_cpu_(?:temperature_reading|temp_payload)\s*\(/.test(source), `${relative} duplicates the authoritative selector.`)
}

check(selector.includes('ASR_CPU_TEMP_SELECTOR_VERSION = 3'), 'Selector cache version 3 was not preserved.')
check(selector.includes("'coretemp'") && selector.includes("'package id'"), 'Intel package selection is missing.')
check(selector.includes("'k10temp'") && selector.includes("'zenpower'"), 'AMD selection is missing.')
check(selector.includes("'bcm2835_thermal'"), 'SBC selection is missing.')
if (sourceTree) {
  const build = read('build-release.sh')
  check(build.includes('allscan-v1.01/include/asrCpuTemperature.php'), 'Shared selector is absent from the release compatibility manifest.')
  check(build.includes('asr-cpu-temperature-consumers-self-test.mjs'), 'ASR-wide consumer regression is not run and packaged.')
}

console.log('ASR-wide CPU temperature consumer self-test: ok (one selector for API, headers, Performance Stats, React, and AST load control)')
