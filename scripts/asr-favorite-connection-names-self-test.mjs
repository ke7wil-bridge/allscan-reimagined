import assert from 'node:assert/strict'
import { favoriteConnectionNames, connectionDisplayName } from '../src/lib/favoriteConnectionNames.ts'
import { identityFromConnection } from '../src/lib/participantIdentity.ts'

const favorites = [{ node: '623123', customDescription: 'Johnny Main' }, { node: '641890', customDescription: '  ' }, { node: '641893', customDescription: 'Test A' }, { node: '641893', customDescription: 'Test B' }, { node: '623124', description: 'Directory label, not custom' }]
const names = favoriteConnectionNames(favorites)
assert.deepEqual([...names], [['623123', 'Johnny Main']])
const row = { node: '623123', info: 'N1YKT Verified directory description', state: 'normal', mode: 'Transceive', direction: 'OUT', received: '', connected: '' }
const original = structuredClone(row)
const display = (r, local = '674982', bridges = new Set(), aliases = names) => connectionDisplayName(r, local, bridges, aliases)
assert.equal(display(row), 'Johnny Main')
assert.deepEqual(row, original)
assert.equal(identityFromConnection(row).callsign, 'N1YKT')
assert.equal(identityFromConnection(row).nodeNumber, '623123')
assert.equal(display(row, row.node), row.info)
assert.equal(display(row, '674982', new Set([row.node])), row.info)
for (const extra of [{ state: 'message' }, { configuredTransport: true }, { bridgeId: 'dmr' }, { info: 'Bridge transport' }, { info: 'Broadcastify feed' }, { info: 'EchoLink connection' }]) {
  const candidate = { ...row, ...extra }; assert.equal(display(candidate), candidate.info)
}
for (const node of ['1999','1001','3069556','3M6PBY','N1YKT']) {
  const candidate = { ...row, node }; assert.equal(display(candidate, '674982', new Set(), new Map([[node, 'Wrong alias']])), candidate.info)
}
assert.equal(display(row, '674982', new Set(), favoriteConnectionNames([])), row.info)
assert.equal(display(row, '674982', new Set(), favoriteConnectionNames([{node: row.node, customDescription:'<b>Text only</b>'}])), '<b>Text only</b>')
console.log('PASS #135 presentation-only Favorites naming and protected identity precedence')
