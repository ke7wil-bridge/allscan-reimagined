import assert from 'node:assert/strict'
import { matchingRecentTalker } from '../src/lib/recentTalkerIdentity.ts'
const oracle = (entries, talker) => {
  const eventTime = talker.eventEpoch || talker.startedEpoch
  return entries?.map((entry) => ({
    entry,
    distance: Math.min(
      talker.startedEpoch > 0 ? Math.abs(entry.startEpoch - talker.startedEpoch) : Number.MAX_SAFE_INTEGER,
      talker.eventEpoch > 0 ? Math.abs(entry.eventEpoch - talker.eventEpoch) : Number.MAX_SAFE_INTEGER,
    ),
  }))
    .filter(({ entry, distance }) => Boolean(entry.callsign) && eventTime > 0 && distance <= 3)
    .sort((left, right) => left.distance - right.distance)[0]?.entry
}
let count = 0
const epoch = 1800000000
const entries = Object.freeze([
  Object.freeze({ callsign: '', startEpoch: epoch, eventEpoch: epoch }),
  Object.freeze({ callsign: 'FIRST', startEpoch: epoch-1, eventEpoch: epoch+1 }),
  Object.freeze({ callsign: 'TIED', startEpoch: epoch+1, eventEpoch: epoch-1 }),
  Object.freeze({ callsign: 'BOUNDARY', startEpoch: epoch-3, eventEpoch: epoch+3 }),
  Object.freeze({ callsign: 'STALE', startEpoch: epoch-4, eventEpoch: epoch+4 }),
])
const values = [0, -1, NaN, Infinity, epoch-5, epoch-3, epoch-1, epoch, epoch+1, epoch+3, epoch+5]
for (const startedEpoch of values) for (const eventEpoch of values) for (const rows of [undefined, [], entries, [...entries].reverse()]) {
  const t = { startedEpoch, eventEpoch }
  assert.equal(matchingRecentTalker(rows,t), oracle(rows,t))
  count++
}
let seed = 123456789
const random = () => ((seed = (Math.imul(seed,1664525)+1013904223)>>>0)/4294967296)
for (let i=0;i<10000;i++) {
  const rows = Object.freeze(Array.from({ length: 1+Math.floor(random()*8) }, (_,j)=>Object.freeze({
    callsign: random()<.2?'':'TEST'+j, startEpoch: epoch+Math.floor(random()*30)-15, eventEpoch: epoch+Math.floor(random()*30)-15,
  })))
  const t = { startedEpoch: epoch+Math.floor(random()*30)-15, eventEpoch: epoch+Math.floor(random()*30)-15 }
  assert.equal(matchingRecentTalker(rows,t), oracle(rows,t))
  count++
}
console.log('PASS', count, 'event-scoped identity equivalence, boundary and stable-tie cases')
