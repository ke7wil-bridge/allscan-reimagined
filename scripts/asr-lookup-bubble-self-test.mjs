#!/usr/bin/env node
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { execFileSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'

const page = 'compat/allscan-v1.05/lookup/index.php'
const backend = 'compat/allscan-v1.05/lookup/network-graph.php'
const details = 'compat/allscan-v1.05/lookup/node-info.php'
const source = fs.readFileSync(page, 'utf8')
const php = fs.readFileSync(backend, 'utf8')
const match = source.match(/<script>\s*([\s\S]*?)\s*<\/script>/)
assert.ok(match, 'Lookup inline JS missing')
const js = match[1].replace(/<\?php echo json_encode\(rtrim\(\$urlbase, '\/'\), JSON_UNESCAPED_SLASHES\); \?>/, '"/asr"')
assert.ok(!js.includes('<?php'), 'Unexpanded PHP in JS')
const temp = path.join(os.tmpdir(), 'asr-207-lookup-check-' + process.pid + '.js')
try {
  fs.writeFileSync(temp, js)
  execFileSync('node', ['--check', temp])
} finally {
  fs.rmSync(temp, { force: true })
}
for (const id of ['asrNetworkGraph','asrNetworkGraphStatus','asrNetworkZoomIn','asrNetworkZoomOut','asrNetworkFit']) {
  assert.ok(source.includes('id="' + id + '"'), 'Missing graph control: ' + id)
}
for (const token of ['pointerdown','pointermove','pointerup','pointercancel','touchstart','touchmove','wheel','keydown','MutationObserver','networkGraphRequest++','networkGraphUnavailable']) {
  assert.ok(js.includes(token), 'Missing interaction/lifecycle: ' + token)
}
assert.ok(source.includes(':hover text') && source.includes(':focus text'), 'Hover/focus underlining missing')
assert.ok(js.includes('getComputedStyle(document.documentElement)'), 'Theme tokens not read')
assert.ok(js.includes(String.raw`/^\\d{3,7}$/`) === false, 'Double-escaped node regex would discard all topology links')
assert.ok(js.includes(String.raw`/^\d{3,7}$/`), 'Valid numeric node matcher missing')
assert.ok(js.includes("credentials:'same-origin'"), 'Authenticated topology fetch missing')
assert.ok(js.includes("networkGraphRequest++"), 'Async cancellation on close missing')
assert.ok(js.includes("networkGraph.removeAttribute('hidden');networkMapImage.hidden=true"), 'Interactive graph must remain visible when upstream is not reporting')
assert.ok(php.includes("asrInitAuthenticatedUser($msg)"), 'Authentication missing')
assert.ok(php.includes('LOCK_EX | LOCK_NB'), 'Upstream throttle missing')
assert.ok(php.includes('time() - filemtime($cache) < 300'), 'Cache TTL missing')
assert.ok(php.includes("preg_match('/^[0-9]{3,7}$/D'"), 'Node validation missing')
assert.ok(source.includes('<dialog id="asrNetworkMapPanel"'), 'Native popout dialog missing')
assert.ok(js.includes('showNetworkNodeInfo(id)'), 'Station details handler missing')
assert.ok(js.includes('networkGraphChildren'), 'Directed collapse relationships missing')
assert.ok(js.includes('function removeEdge(node,linked)'), 'Stale connection removal missing')
assert.ok(js.includes("item.source==='Connection Status'"), 'Local active AllStar connections must seed graph when root API stats are absent')
assert.ok(js.includes('networkGraphSeeds.forEach'), 'Live connected nodes must be reconciled into root topology')
assert.ok(js.includes("cache:'no-store'"), 'Browser topology cache bypass missing')
assert.ok(js.includes('currentLinks.has(old)'), 'Topology refresh must reconcile removed links')
assert.ok(js.includes("observe(document.body"), 'Body theme changes must trigger graph refresh')
assert.ok(js.includes("css('--accent-color'"), 'Station bubbles must use ASR theme accent')
assert.ok(js.includes("css('--surface'"), 'Graph background must use ASR theme surface')
assert.ok(js.includes('networkMapPanel.showModal()'), 'Modal launch missing')
assert.ok(fs.readFileSync(details,'utf8').includes('asrInitAuthenticatedUser($msg)'), 'Details endpoint auth missing')
const graphBackend=fs.readFileSync(backend,'utf8')
assert.ok(graphBackend.includes('age > 900'), 'Expired topology must not be served indefinitely')
assert.ok(graphBackend.includes("$data['status'] = 'stale'"), 'Stale fallback must be marked stale')
assert.ok(!fs.readFileSync(details,'utf8').includes('mb_substr('), 'mbstring must not be required')
const integrity = fs.readFileSync('scripts/asr-integrity-check.sh','utf8')
for(const endpoint of ['lookup/network-graph.php','lookup/node-info.php']) assert.ok(integrity.includes(endpoint),'Integrity coverage missing: '+endpoint)
for (const file of [page,backend,details]) execFileSync('php',['-l',file])
console.log('ASR #207 bubble integration checks: ok')
