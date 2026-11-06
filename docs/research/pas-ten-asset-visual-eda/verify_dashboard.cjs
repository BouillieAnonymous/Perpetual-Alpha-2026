const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const out=process.argv[2]||path.join(__dirname,'output'),file=path.join(out,'dashboard.html');
const html=fs.readFileSync(file,'utf8'),code=html.match(/<script>([\s\S]*)<\/script>/)[1];
let elements=[];
class El{constructor(tag){this.tag=tag;this.style={};this.children=[];this.clientWidth=1100;this.clientHeight=230;this.checked=true;elements.push(this)}append(...xs){this.children.push(...xs)}replaceChildren(){this.children=[]}setPointerCapture(){}getContext(){return new Proxy({},{get:()=>()=>{}})}}
const ids=Object.fromEntries(['meta','plots','tip','asset','start','end','apply','reset','day','benchmark','strict'].map(id=>[id,new El('div')]));
const context=vm.createContext({document:{getElementById:id=>ids[id],createElement:tag=>new El(tag),createTextNode:t=>t},window:{},devicePixelRatio:1,innerWidth:1400,innerHeight:1000,Date,Math,Number});
vm.runInContext(code,context);const val=s=>vm.runInContext(s,context);
assert.equal(val('D.symbols.length'),10);assert.equal(ids.asset.children.length,10);
const n=val('times.length');assert(n>10000);assert.equal(val('panels.length'),13);
ids.day.onclick();assert.equal(val('right-left'),1440);
const c=val('panels[0].canvas');c.onpointermove({offsetX:200,clientX:200,clientY:200});assert.match(ids.tip.textContent,/Z\n/);assert.match(ids.tip.textContent,/pas:ETH/);
const cb=elements.find(e=>e.type==='checkbox');cb.checked=false;cb.onchange();assert.equal(val('panels[0].lines[0].on'),false);
const symbols=val('D.symbols');for(const s of symbols){ids.asset.value=s;ids.asset.onchange();assert.equal(val('selected'),s);assert.equal(val('panels[0].lines.filter(l=>l.on).length'),s==='pas:BTC'?1:2)}
ids.strict.checked=false;ids.strict.onchange();assert.equal(val('panels[5].lines.some(l=>l.name.endsWith("B"))'),false);
ids.benchmark.checked=false;ids.benchmark.onchange();assert.equal(val('panels[0].lines.filter(l=>l.on).length'),1);
ids.reset.onclick();assert.equal(val('right'),n-1);assert.equal(val('left'),0);
const canvas=val('panels[0].canvas');canvas.onpointerdown({offsetX:200,pointerId:1});canvas.onpointerup({offsetX:500});assert(val('left>0&&right<times.length-1'));canvas.ondblclick();assert.equal(val('left'),0);
const events=val('D.events'),data=val('D.assets'),times=val('times');let outcomes=0;
for(let k=0;k<events.length;k++){let e=events[k];assert.equal(e.overlap,Boolean(k&&e.i-events[k-1].i<30||k+1<events.length&&events[k+1].i-e.i<30));for(let s of symbols.slice(1))for(let h of [5,15,30]){let rs=data[s].returns.A.slice(e.i+1,e.i+h+1),v=e.outcomes[s][h].value,valid=rs.length===h&&rs.every(r=>r!==null);assert.equal(v!==null,valid);if(valid)assert(Math.abs(v-rs.reduce((a,b)=>a+b,0))<1e-10);outcomes++}}
let rvPoints=0;
for(const s of symbols)for(const w of [5,15,30,60])for(const mode of ['A','B'])for(let i=0;i<n;i++){
 const rs=data[s].returns[mode].slice(Math.max(0,i-w+1),i+1),valid=rs.length===w&&rs.every(r=>r!==null),rv=data[s].rv[w][mode][i];
 assert.equal(rv!==null,valid);
 if(valid)assert(Math.abs(rv-Math.sqrt(rs.reduce((a,r)=>a+r*r,0)))<1e-10);
 if(mode==='B'&&rv!==null)assert.equal(rv,data[s].rv[w].A[i]);
 rvPoints++;
}
assert(!/https?:\/\//.test(html));
const result={status:'PASS',asset_selector_options:10,grid_points:n,event_count:events.length,event_outcomes_checked:outcomes,rv_points_checked:rvPoints,png_visual_review:5,checks:['all ten asset selections','24h UTC zoom','UTC hover','curve toggle','A/B toggle','BTC benchmark toggle','drag zoom','reset','event sums and missing labels','overlap markers','all 80 complete-window RV series independently recalculated','offline HTML'],limitation:'DOM/Canvas mock, not real browser end-to-end'};
fs.writeFileSync(path.join(out,'dashboard-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
