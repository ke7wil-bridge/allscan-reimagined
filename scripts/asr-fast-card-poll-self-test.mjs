import assert from 'node:assert/strict'
import { startFastCardPoll } from '../src/lib/fastCardPoll.ts'
const originals = { window: globalThis.window, document: globalThis.document, performance: globalThis.performance }
const descriptors = Object.fromEntries(Object.keys(originals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]))
const flush = async () => { for(let i=0;i<8;i++) await Promise.resolve() }
try {
  let clock = 0, hidden = false, nextTimer = 1
  const timers = new Map()
  Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>clock}})
  Object.defineProperty(globalThis,'document',{configurable:true,value:{get hidden(){return hidden}}})
  Object.defineProperty(globalThis,'window',{configurable:true,value:{
    setTimeout(fn,delay){const id=nextTimer++;timers.set(id,{fn,delay});return id},
    clearTimeout(id){timers.delete(id)},
  }})
  function feed() {
    const pending = [], ref = {current:{}}, updates = []
    let calls=0
    const stop=startFastCardPoll(()=>{calls++;return new Promise((resolve,reject)=>pending.push({resolve,reject}))},ref,()=>updates.push(ref.current),250)
    return {pending,ref,updates,stop,get calls(){return calls}}
  }
  function fire(expected){
    assert.equal(timers.size,1)
    const [id,timer]=[...timers][0];assert.equal(timer.delay,expected);timers.delete(id);clock+=timer.delay;timer.fn()
  }
  const a=feed();assert.equal(a.calls,1);assert.equal(timers.size,0)
  clock+=40;const first={role:'source',callsign:'N0CALL'};a.pending.shift().resolve(first);await flush()
  assert.equal(a.ref.current,first);assert.deepEqual(a.updates,[first]);fire(210)
  assert.equal(a.calls,2);assert.equal(timers.size,0)
  const duplicate={...first};clock+=20;a.pending.shift().resolve(duplicate);await flush()
  assert.equal(a.ref.current,duplicate);assert.equal(a.updates.length,1);fire(230)
  clock+=400;a.pending.shift().reject(Error('temporary outage'));await flush()
  assert.equal(a.ref.current,duplicate);assert.equal(a.updates.length,1);fire(0)
  hidden=true;const eot={role:'idle',callsign:''};a.pending.shift().resolve(eot);await flush()
  assert.equal(a.ref.current,eot);assert.equal(a.updates.length,2);fire(1000)
  a.stop();a.pending.shift().resolve({role:'relay'});await flush()
  assert.equal(a.ref.current,eot);assert.equal(a.updates.length,2);assert.equal(timers.size,0)
  const b=feed(), c=feed();assert.equal(b.calls,1);assert.equal(c.calls,1)
  b.pending.shift().resolve({role:'source'});c.pending.shift().resolve({role:'relay'});await flush()
  assert.equal(timers.size,2);assert.equal(b.updates.length,1);assert.equal(c.updates.length,1)
  b.stop();assert.equal(timers.size,1);c.stop();assert.equal(timers.size,0)
  console.log('PASS single-flight, 250ms cadence, hidden 1000ms, slow/rejected responses, duplicate suppression, late-response cancellation and independent cleanup')
} finally {
  for(const [key,descriptor] of Object.entries(descriptors)) {
    if(descriptor)Object.defineProperty(globalThis,key,descriptor)
    else delete globalThis[key]
  }
}
