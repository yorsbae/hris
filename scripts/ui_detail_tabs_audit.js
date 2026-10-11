const puppeteer=require('puppeteer-core');
(async()=>{const c=require('@sparticuz/chromium');const ch=c.default||c;const b=await puppeteer.launch({executablePath:await ch.executablePath(),args:ch.args,headless:'shell'});
for(const [u,w] of [['hrd',1366],['hrd',390],['poli',1366],['admin_prd',1366]]){const pg=await b.newPage();await pg.setViewport({width:w,height:800});await pg.goto('http://127.0.0.1:8000/login/');await pg.type('input[name=username]',u);await pg.type('input[name=password]','Demo#HRIS-2026');await Promise.all([pg.waitForNavigation(),pg.click('main form button:not(.eye)')]);
await pg.goto('http://127.0.0.1:8000/employees/',{waitUntil:'networkidle0'});const href=await pg.evaluate(()=>{const a=[...document.querySelectorAll('main table a')].find(x=>/\/employees\/\d+\/$/.test(x.getAttribute('href')));return a&&a.getAttribute('href')});
await pg.goto('http://127.0.0.1:8000'+href,{waitUntil:'networkidle0'});
const r=await pg.evaluate(()=>{const t=[...document.querySelectorAll('nav.tabs a.t')].map(a=>a.textContent.trim());const vis=[...document.querySelectorAll('.tabpane')].filter(p=>!p.hidden).map(p=>p.dataset.tab);return {t,vis,scroll:document.documentElement.scrollWidth>document.documentElement.clientWidth+1}});
console.log(u,w,JSON.stringify(r));
if(r.t.includes('Kontrak')||r.t.includes('Poliklinik')){const k=r.t.includes('Kontrak')?'Kontrak':'Poliklinik';await pg.evaluate(k=>[...document.querySelectorAll('nav.tabs a.t')].find(a=>a.textContent.startsWith(k)).click(),k);const v=await pg.evaluate(()=>[...document.querySelectorAll('.tabpane')].filter(p=>!p.hidden).map(p=>p.dataset.tab));console.log(' klik',k,JSON.stringify(v))}
if(u==='hrd')await pg.screenshot({path:'/tmp/ui/det_'+w+'.png',clip:{x:0,y:0,width:w,height:560}});await pg.close()}
await b.close()})();
