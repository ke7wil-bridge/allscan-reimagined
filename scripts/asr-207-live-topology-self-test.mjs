#!/usr/bin/env node
// Read-only live regression for the production condition that blanked Johnny2's graph.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const reportingNode = '623123'
const connectedNode = '637355'
const fetchStats = async node => {
  const response = await fetch(`https://stats.allstarlink.org/api/stats/${node}`, {
    headers: { Accept: 'application/json', 'User-Agent': 'ASR-Issue-207-Live-Regression/1.0' },
    signal: AbortSignal.timeout(15000),
  })
  assert.equal(response.ok, true, `AllStarLink stats request failed for ${node}: HTTP ${response.status}`)
  return response.json()
}

const [root, connected] = await Promise.all([fetchStats(reportingNode), fetchStats(connectedNode)])
const rootLinks = root?.stats?.data?.links?.map(String) || []
assert.ok(rootLinks.includes(connectedNode), `${reportingNode} no longer reports ${connectedNode} as a live connection`)
assert.equal(connected?.stats, null, `${connectedNode} now reports its own stats; update this regression fixture to another confirmed non-reporting connected node`)

const component = readFileSync('src/components/AllStarBubbleDialog.tsx', 'utf8')
const app = readFileSync('src/App.tsx', 'utf8')
assert.match(app, /localNode=\{config\.node\}/, 'Johnny2 reporting node is not passed to the bubble chart')
assert.match(component, /const seedEdge = safeNode\(node\) && safeNode\(localNode\)/, 'Verified live connection seed is missing')
assert.match(component, /new Set\(\[node, localNode\]/, 'Discovery does not query the clicked and reporting nodes')

console.log(`PASS #207 live topology: ${reportingNode} reports ${connectedNode}; non-reporting clicked-node fallback is wired without invented edges`)
