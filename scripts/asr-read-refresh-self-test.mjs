import assert from 'node:assert/strict'
import { startReadRefresh } from '../src/lib/readRefresh.ts'
const original=Object.getOwnPropertyDescriptor(globalThis,'window')
const flush=async()=>{for(let i=0;i<6;i++)await Promise.resolve()}
try {
  let next=1
  const listeners=new Set(),timers=new Map()
  const calls=[]
  Object.defineProperty(globalThis,'window',{configurable:true,value:{
    addEventListener(type,fn){assert.equal(type,'focus');listeners.add(fn);calls.push('add')},
    removeEventListener(type,fn){assert.equal(type,'focus');assert(listeners.delete(fn));calls.push('remove')},
    setInterval(fn,ms){const id=next++;timers.set(id,{fn,ms});calls.push('set:'+ms);return id},
    clearInterval(id){assert(timers.delete(id));calls.push('clear')},
  }})
  for(const options of [{intervalMs:15000},{onFocus:true},{onFocus:true,intervalMs:60000},{intervalMs:60000},{intervalMs:120000}]){
    const pending=[],accepted=[]
    calls.length=0
    const stop=startReadRefresh(async(isActive)=>{
      calls.push('request')
      try {
        const value=await new Promise((resolve,reject)=>pending.push({resolve,reject}))
        if(isActive())accepted.push(value)
      }catch{if(isActive())accepted.push('failure')}
    },options)
    assert.deepEqual(calls,['request',...(options.onFocus?['add']:[]),...(options.intervalMs?['set:'+options.intervalMs]:[])])
    assert.equal(listeners.size,options.onFocus?1:0)
    assert.equal(timers.size,options.intervalMs?1:0)
    pending.shift().resolve('initial');await flush()
    assert.deepEqual(accepted,['initial'])
    for(const f of listeners)f()
    for(const t of timers.values()){assert.equal(t.ms,options.intervalMs);t.fn()}
    assert.equal(pending.length,(options.onFocus?1:0)+(options.intervalMs?1:0))
    pending.shift().reject(Error('fixture failure'));await flush()
    assert.deepEqual(accepted,['initial','failure'])
    for(const f of listeners)f()
    for(const t of timers.values())t.fn()
    stop()
    assert.equal(listeners.size,0);assert.equal(timers.size,0)
    for(const task of pending)task.resolve('late')
    await flush()
    assert.deepEqual(accepted,['initial','failure'])
  }
  // Independent read lifecycles: disposing one cannot cancel another.
  const stopA=startReadRefresh(async()=>{}, {intervalMs:15000})
  const stopB=startReadRefresh(async()=>{}, {intervalMs:15000})
  assert.equal(timers.size,2);stopA();assert.equal(timers.size,1);stopB();assert.equal(timers.size,0)
  console.log('PASS five read-refresh configurations: immediate reads, focus, interval, failures, late-result guards, independent cleanup')
}finally{
  if(original)Object.defineProperty(globalThis,'window',original)
  else delete globalThis.window
}
