// Runtime interaction verification only; explicitly NOT a real-browser test.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {DecompressionStream}=require('node:stream/web');
const dir=process.argv[2],html=fs.readFileSync(dir+'/dashboard.html','utf8');
const packed=html.match(/<script id="packed"[^>]*>([\s\S]*?)<\/script>/)[1];
class El{
 constructor(tag='div'){this.tag=tag;this.children=[];this.style={};this.dataset={};this.value='';this.hidden=false;this.clientWidth=1000;this.clientHeight=190;this.attrs={};this._sub={}}
 append(...es){this.children.push(...es)}replaceChildren(){this.children=[]}setAttribute(k,v){this.attrs[k]=v}getAttribute(k){return this.attrs[k]}setPointerCapture(){}
 querySelector(s){if(!this._sub[s])this._sub[s]=new El(s==='canvas'?'canvas':'p');return this._sub[s]}
 getContext(){return new Proxy({getImageData:()=>({data:[]})},{get:(o,k)=>o[k]||(()=>{})})}
}
const ids={};for(const m of html.matchAll(/id="([^"]+)"/g))ids[m[1]]=new El();ids.packed.textContent=packed;
Object.assign(ids.mode,{value:'A'});Object.assign(ids.asset,{value:'both'});ids.scale.value='15';ids.relwin.value='240';ids.postview.value='outcomes';ids.direction.value='up';ids.event.value='aggregate';ids.heatmetric.value='abs';
const nav=['trend','cross','events','quality'].map(t=>{let e=new El('button');e.dataset.tab=t;e.textContent=t;return e});
const doc={querySelector:s=>ids[s.slice(1)],querySelectorAll:s=>s==='[data-tab]'?nav:[],createElement:t=>new El(t),createTextNode:t=>t};
const errors=[];const ctx=vm.createContext({document:doc,window:{addEventListener(){}},devicePixelRatio:1,Blob,Response,DecompressionStream,Uint8Array,atob,performance,Date,Math,Number,Array,JSON,console:{error:e=>errors.push(String(e))},requestAnimationFrame:fn=>fn()});
const code=html.match(/<script>([\s\S]*)<\/script>/)[1];
(async()=>{await vm.runInContext(code,ctx);assert.equal(errors.length,0,errors.join('\n'));assert.match(ids.health.textContent,/5 original groups/);
const core=[];for(const n of nav){n.onclick();assert.equal(errors.length,0);assert.ok(ids.aux.children.length<=4);core.push(...ids.aux.children.map(x=>x.innerHTML))}for(const word of ['Slope','Correlation','Beta','BTC-adjusted','Z-Score','Cost'])assert.ok(core.some(x=>x.includes(word)),word);
for(const mode of ['A','B']){ids.mode.onchange({target:{value:mode}});for(const a of ['btc','eth','both'])ids.asset.onchange({target:{value:a}});for(const v of ['60','240','1440'])ids.relwin.onchange({target:{value:v}});for(const v of ['5','15','60'])ids.scale.onchange({target:{value:v}})}
ids.preset.onchange({target:{value:'gap'}});assert.match(ids.range.textContent,/2026-10-26T02:10:00Z/);const before=ids.range.textContent;ids.start.value='2027-01-01T00:00';ids.end.value='2027-01-02T00:00';ids.apply.onclick();assert.equal(ids.range.textContent,before);assert.match(ids.message.textContent,/无效/);
ids.reset.onclick();assert.match(ids.range.textContent,/21378/);ids.cursor.oninput({target:{value:'10000'}});assert.match(ids.cursorutc.textContent,/Z/);assert.ok(!ids.features.innerHTML.includes('future'));
nav[2].onclick();ids.postview.value='scatter';ids.postview.onchange();assert.match(ids.poststats.textContent,/NOT an as-of signal/);
for(const v of ['outcomes','paths','scatter','lags','heat','tails']){ids.postview.value=v;ids.postview.onchange();assert.match(ids.poststats.textContent,/Retrospective only/)}for(const v of ['abs','spread','coverage']){ids.postview.value='heat';ids.heatmetric.value=v;ids.heatmetric.onchange()}
nav[0].onclick();ids.price.onpointerdown({offsetX:120,pointerId:1});ids.price.onpointerup({offsetX:700});assert.ok(!ids.range.textContent.includes('21378 UTC'));ids.price.ondblclick();assert.match(ids.range.textContent,/21378/);
const result={status:'PASS',real_browser:false,checks:['six new core charts reachable','four tab navigation','max four auxiliary panels','A/B mode','BTC/ETH toggle','5/15/60 scales','60/240/1440 relationship windows','UTC preset and invalid range rejection','shared historical cursor and no future inspector fields','drag zoom and reset handlers','six retrospective views','three heatmap metrics'],limitation:'Node DOM/Canvas mock only; Chrome connection timed out'};console.log(JSON.stringify(result,null,2));fs.writeFileSync(dir+'/interaction-validation.json',JSON.stringify(result,null,2))})().catch(e=>{console.error(e);process.exitCode=1});
