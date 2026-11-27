import assert from 'node:assert/strict'
import { replaceDashboardQuery } from '../src/lib/dashboardQuery.ts'
const original = Object.getOwnPropertyDescriptor(globalThis,'window')
let count=0
try {
  for (const pathname of ['/asr/','/nested/asr/','/']) for (const hash of ['', '#favorites', '#one%20two']) for (const search of ['', 'reportBug=1','supportFeedback=1&updateAsr=1','x=hello+world&x=%2B&updateAsr=1','empty=&reportBug=1&reportBug=0','q=%E2%98%83&weird=%26%3D']) for (const flags of [['reportBug','supportFeedback'],['updateAsr']]) {
    const params=new URLSearchParams(search)
    for(const flag of flags)params.delete(flag)
    const query=params.toString()
    const expected=[null,'',pathname+(query?'?'+query:'')+hash]
    const calls=[]
    Object.defineProperty(globalThis,'window',{configurable:true,value:{location:{pathname,hash},history:{replaceState:(...args)=>calls.push(args)}}})
    replaceDashboardQuery(params)
    assert.deepEqual(calls,[expected])
    assert.equal(params.toString(),query)
    count++
  }
  console.log('PASS',count,'exact query reconstruction cases, duplicate values, encoding and fragments')
} finally {
  if(original)Object.defineProperty(globalThis,'window',original)
  else delete globalThis.window
}
