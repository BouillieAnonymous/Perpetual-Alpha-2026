const fs=require('fs'),vm=require('vm'),assert=require('assert');
const dir=process.argv[2], html=fs.readFileSync(dir+'/dashboard.html','utf8');
const code=html.match(/<script>([\s\S]*)<\/script>/)[1];
const elements=[];
class El {
 constructor(tag){this.tag=tag;this.style={};this.children=[];this.clientWidth=1100;this.clientHeight=tag==='canvas'?220:0;this.dataset={};elements.push(this)}
 append(...es){this.children.push(...es)}
 insertAdjacentHTML(){}
 setPointerCapture(){}
 getContext(){return new Proxy({}, {get:(_,k)=>()=>{}})}
}
const ids=Object.fromEntries(['plots','tip','start','end','apply','reset'].map(id=>[id,new El('div')]));
const presets=['2026-10-29,2026-10-31','2026-10-28,2026-10-29','2026-10-31,2026-11-02'].map(r=>{let b=new El('button');b.dataset.range=r;return b});
const ctx=vm.createContext({document:{querySelector:q=>ids[q.slice(1)],querySelectorAll:()=>presets,createElement:t=>new El(t),createTextNode:t=>t},window:{},devicePixelRatio:1,innerWidth:1400,innerHeight:1000,Date,Math,Number,console});
vm.runInContext(code,ctx);
const val=s=>vm.runInContext(s,ctx);
assert.equal(val('panels.length'),13);assert.equal(val('D.events.length'),2181);
presets[0].onclick();assert.equal(ids.start.value,'2026-10-29T00:00');assert.equal(ids.end.value,'2026-10-31T00:00');
const cv=val('panels[0].canvas');cv.onpointermove({offsetX:200,clientX:200,clientY:200});assert.match(ids.tip.textContent,/2026-10-29T.*Z/);assert.match(ids.tip.textContent,/pas:BTC/);
const cb=elements.find(e=>e.type==='checkbox');cb.checked=false;cb.onchange();assert.equal(val('panels[0].lines[0].on'),false);cb.checked=true;cb.onchange();
ids.reset.onclick();assert.equal(val('left'),0);assert.equal(val('right'),21377);
cv.onpointerdown({offsetX:200,pointerId:1});cv.onpointerup({offsetX:400});assert(val('left>0 && right<21377'));
cv.ondblclick();assert.equal(val('left'),0);
ids.start.value='2099-01-01T00:00';ids.end.value='2099-01-02T00:00';ids.apply.onclick();assert.equal(val('left'),0);
const ev=elements.find(e=>e.tag==='canvas'&&e.onmousemove);ev.onmousemove({offsetX:300,offsetY:50,clientX:300,clientY:50});assert.match(ids.tip.textContent,/cluster/);
assert(!/https?:\/\//.test(html));
console.log(JSON.stringify({status:'PASS',time_panels:13,events:2181,checks:['preset UTC range','UTC hover','BTC toggle','drag zoom','reset','out-of-sample range rejected','event hover','no network dependency'],limitation:'DOM/Canvas mock; not real browser end-to-end'},null,2));
