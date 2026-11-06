// Independent reference arithmetic + UI runtime checks. This is not a real-browser test.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),zlib=require('zlib'),path=require('path');
const {DecompressionStream}=require('node:stream/web');
const dir=process.argv[2],source=fs.readFileSync(path.join(dir,'dashboard.html'),'utf8');
const script=[...source.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const instances=[];
class El{
 constructor(tag='div'){this.tag=tag;this.children=[];this.style={};this.dataset={};this.attrs={};this.textContent='';this.value='';this.clientWidth=900;this.clientHeight=190;this.hidden=false;this._sub={};instances.push(this)}
 append(...es){this.children.push(...es);for(const e of es)if(e instanceof El)e.parentElement=this;if(this.tag==='head')for(const s of es)if(s.tag==='script')vm.runInContext(fs.readFileSync(path.join(dir,s.src),'utf8'),ctx)}
 replaceChildren(){this.children=[]}setAttribute(k,v){this.attrs[k]=v}getAttribute(k){return this.attrs[k]}setPointerCapture(){}
 querySelector(s){return this._sub[s]??=new El(s==='canvas'?'canvas':'p')}
 getContext(){return new Proxy({getImageData:()=>({data:[]})},{get:(o,k)=>o[k]||(()=>{})})}
}
const ids={};for(const m of source.matchAll(/id="([^"]+)"/g))ids[m[1]]=new El();
ids.mode.value='A';ids.scale.value='15';ids.window.value='240';const nav=['trend','pair','matrix','quality'].map(x=>{let e=new El('button');e.dataset.tab=x;e.textContent=x;return e});
function descendants(e){return e.children.flatMap(c=>c instanceof El?[c,...descendants(c)]:[])}
const doc={head:new El('head'),querySelector:s=>ids[s.slice(1)],querySelectorAll:s=>s==='[data-tab]'?nav:s==='#budget-rows input'?descendants(ids['budget-rows']).filter(e=>e.tag==='input'):[],createElement:t=>new El(t),createTextNode:t=>t};
const ctx=vm.createContext({document:doc,window:{addEventListener(){}},Blob,Response,DecompressionStream,Uint8Array,atob,performance,devicePixelRatio:1,Date,Math,JSON,Number,Array,Map,Set,Error,requestAnimationFrame:fn=>fn(),console});ctx.window.window=ctx.window;
vm.runInContext(script[0],ctx);
const injected=script[1].replace("updateRange();assetUI();chooseUI();refresh();", "globalThis.__test={pair,load,F,refresh,setPair,matrix,getState:()=>({mode,X,Y,selected,left,right,cursor,workspace,cacheSize:cache.size,pairCacheSize:pairCache.size}),pairCache};updateRange();assetUI();chooseUI();refresh();");assert.notEqual(injected,script[1]);
const sleep=()=>new Promise(r=>setImmediate(r));
function close(a,b){assert.equal(a===null,b===null);if(a!==null)assert.ok(Math.abs(a-b)<1e-6*(1+Math.abs(a)),`${a} vs ${b}`)}
(async()=>{
vm.runInContext(injected,ctx);const test=ctx.__test;await test.refresh();assert.equal(test.getState().cacheSize,2,'initial load only required pair');
const refs=JSON.parse(fs.readFileSync(path.join(dir,'numeric-reference.json'),'utf8'));let checked=0,last='';
for(const ref of refs){let key=ref.mode+ref.x+ref.y;if(key!==last){ids.mode.onchange({target:{value:ref.mode}});test.setPair(ref.x,ref.y);await test.refresh();last=key}let p=test.pair(),qualified=ref.global_gate==='DATA_READY_FOR_PROTOCOL';close(p['corr'+ref.w][ref.i],qualified?ref.corr:null);close(p['beta'+ref.w][ref.i],qualified?ref.beta:null);assert.equal(p['n'+ref.w][ref.i],ref.n);checked+=3}
assert.ok(test.getState().pairCacheSize<=2);
// All ten assets, single-asset display and four-market cap via real event handlers in the mock.
ids.mode.onchange({target:{value:'A'}});test.setPair('BTC','ETH');await test.refresh();
for(const ident of ['HYPE','SP500']){let box=instances.filter(e=>e.tag==='input'&&e.type==='checkbox'&&e.parentElement?.className==='market-check'&&e.parentElement.children.includes(ident)).at(-1);assert.ok(box);box.checked=true;box.onchange();await test.refresh()}
let fifth=instances.filter(e=>e.tag==='input'&&e.type==='checkbox'&&e.parentElement?.className==='market-check'&&e.parentElement.children.includes('GOLD')).at(-1);fifth.checked=true;fifth.onchange();assert.equal(fifth.checked,false);assert.equal(test.getState().selected.length,4);
for(const asset of ['BTC','ETH','HYPE','SP500','EWY','EWJ','BRENT','GOLD','USDEUR','USDJPY'])await test.load(asset);assert.equal(test.getState().cacheSize,10);
nav[2].onclick();await test.refresh();await test.matrix();assert.equal(ids.matrix.children.length,11);assert.equal(descendants(ids.matrix).filter(e=>e.tag==='button').length,90);assert.equal(ids['matrix-time'].dataset.mode,'A');
const cell=ids.matrix.children[1].children[3].children[0];cell.onclick();await test.refresh();assert.equal(test.getState().X,'BTC');assert.equal(test.getState().Y,'HYPE');
ids.mode.onchange({target:{value:'B'}});await test.refresh();assert.match(ids['pair-info'].textContent,/INSUFFICIENT_DATA/);
ids.preset.onchange({target:{value:'gap'}});await test.refresh();assert.match(ids.range.textContent,/51 grid points/);ids.price.onpointerdown({offsetX:100,pointerId:1});ids.price.onpointerup({offsetX:600});await test.refresh();assert.ok(test.getState().right-test.getState().left<50);ids.reset.onclick();await test.refresh();assert.equal(test.getState().right-test.getState().left+1,15815);
nav[3].onclick();await test.refresh();assert.match(ids['pair-cost'].textContent,/sum MISSING/);let inputs=descendants(ids['budget-rows']).filter(e=>e.tag==='input');inputs.forEach(e=>e.value=e.dataset.kind==='fee'?'1':'2');ids['apply-budgets'].onclick();await test.refresh();assert.ok(!ids['pair-cost'].textContent.includes('sum MISSING'));assert.match(ids['pair-cost'].textContent,/executable net not computed/);
assert.ok(!ids.inspector.innerHTML.includes('future'));const result={status:'PASS',real_browser:false,direct_pair_numeric_fields:checked,tests:['initial two-market lazy load','ten-market metadata selectors','four-asset limit','two-pair LRU cache','144 direct covariance references A/B','matrix 90 off-diagonal cells and click direction','B insufficiency gate','UTC gap/drag/reset handlers','unknown default and explicit per-leg budgets','no future inspector labels'],scope:'Mock validation supplements separate Chrome acceptance record'};fs.writeFileSync(path.join(dir,'interaction-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2))
})().catch(e=>{console.error(e);process.exitCode=1});
