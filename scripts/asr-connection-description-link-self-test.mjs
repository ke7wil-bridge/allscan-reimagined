import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const app=readFileSync('src/App.tsx','utf8')
const css=readFileSync('src/styles/asr/04-bridge-cards.css','utf8')
assert.match(app,/canViewNetworkMap && row.info \? \(\s*<a/)
assert.match(app,/className="allscan-connection-network-map-link"/)
assert.match(app,/<span>\{row.node\}<\/span>/)
assert.match(css,/td:nth-child\(2\) \.allscan-connection-network-map-link:visited/)
assert.match(css,/font-weight: 850/)
assert.match(css,/\.allscan-connection-network-map-link:hover,[\s\S]*?text-decoration: underline/)
assert.doesNotMatch(css,/\.allscan-connection-network-map-link:hover \{ opacity:/)
console.log('ASR Connection Status description link parity: ok')
