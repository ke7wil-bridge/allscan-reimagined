import assert from 'node:assert/strict';
import {hslToFavoriteHex, nextSort, pickFavoriteHex} from '../src/lib/dashboardInteraction.ts';
const oldHsl=(hue,saturation,lightness)=>{
 const a=saturation*Math.min(lightness,1-lightness);
 const f=n=>{const k=(n+hue/30)%12;return lightness-a*Math.max(-1,Math.min(k-3,9-k,1))};
 return '#'+[f(0),f(8),f(4)].map(v=>Math.round(255*v).toString(16).padStart(2,'0')).join('');
};
let n=0;
for(let hue=0;hue<=360;hue++)for(const sat of [0,0.001,0.1,0.25,0.5,0.75,0.999,1])for(const light of [0,0.001,0.1,0.25,0.5,0.75,0.999,1]){
 assert.equal(hslToFavoriteHex(hue,sat,light),oldHsl(hue,sat,light));n++;
}
let seed=123456789;const rand=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296};
for(let i=0;i<20000;i++){
 const h=rand()*360,s=rand(),l=rand();assert.equal(hslToFavoriteHex(h,s,l),oldHsl(h,s,l));n++;
 const rect={left:rand()*100,top:rand()*100,width:1+rand()*250,height:1+rand()*250};const x=rect.left+rect.width*(rand()*2-0.5),y=rect.top+rect.height*(rand()*2-0.5);
 const expected=oldHsl(h,Math.max(0,Math.min(1,(x-rect.left)/rect.width)),Math.max(0,Math.min(1,1-((y-rect.top)/rect.height))));
 assert.equal(pickFavoriteHex(x,y,rect,h),expected);n++;
}
for(const keys of [['index','node','name','desc','location'],['node','info','received','direction','connected','mode']])for(const key of keys)for(const direction of ['asc','desc'])for(const next of keys){
 const current=Object.freeze({key,direction});const expected=key===next?{key:next,direction:direction==='asc'?'desc':'asc'}:{key:next,direction:'asc'};
 assert.deepEqual(nextSort(current,next),expected);n++;
}
console.log('PASS',n,'exact color, pointer-coordinate and sort transition comparisons');
