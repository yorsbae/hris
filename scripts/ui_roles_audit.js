const puppeteer=require('puppeteer-core');
const PAGES={hrd:['/employees/','/requests/','/leave/','/hrd/aids/','/hrd/maternity/','/hrd/projects/','/hrd/catering/','/hrd/warnings/','/hrd/bpjs/','/hrd/bpjs/deductions/','/hrd/separations/','/hrd/uniforms/','/hrd/uniforms/?tab=stok','/hrd/uniforms/?tab=pembelian','/validasi/','/master/shift/','/master/department/'],
poli:['/poli/records/','/poli/medicines/','/poli/diagnoses/','/poli/referrals/','/poli/billing/','/poli/reports/stock/','/employees/'],
admin_prd:['/employees/','/requests/','/requests/g/lembur/','/validasi/','/schedule/']};
(async()=>{const c=require('@sparticuz/chromium');const ch=c.default||c;const b=await puppeteer.launch({executablePath:await ch.executablePath(),args:ch.args,headless:'shell'});
for(const [u,list] of Object.entries(PAGES)){const pg=await b.newPage();await pg.setViewport({width:390,height:800});await pg.goto('http://127.0.0.1:8000/login/');await pg.type('input[name=username]',u);await pg.type('input[name=password]','Demo#HRIS-2026');await Promise.all([pg.waitForNavigation(),pg.click('main form button:not(.eye)')]);
for(const p of list){const r=await pg.goto('http://127.0.0.1:8000'+p,{waitUntil:'networkidle0'}).catch(()=>null);if(!r||r.status()>=400){console.log(u,p,'HTTP',r&&r.status());continue}
const x=await pg.evaluate(()=>{const de=document.documentElement,t=[...document.querySelectorAll('main .wrap table')];const o=[];if(de.scrollWidth>de.clientWidth+1)o.push('halaman scroll');
t.forEach((tb,i)=>{const w=tb.closest('.wrap');if(w&&w.scrollWidth>w.clientWidth+1&&!tb.classList.contains('cards'))o.push('tabel#'+i+' bergulir ('+tb.rows[0].cells.length+' kol)');
if(tb.classList.contains('cards')){tb.querySelectorAll('td').forEach(td=>{const r=td.getBoundingClientRect();if(r.right>innerWidth+1&&r.width>0)o.push('sel melebihi layar: '+td.dataset.label)});
if(tb.querySelector('td input:not([type=hidden]),td select'))o.push('kartu berisi isian#'+i)}});return {o:[...new Set(o)],cards:t.filter(x=>x.classList.contains('cards')).length,n:t.length}});
console.log(u,p,x.cards+'/'+x.n,x.o.join('; ')||'ok');
if(x.o.some(s=>/isian/.test(s)))await pg.screenshot({path:'/tmp/ui/in_'+u+p.replace(/\W+/g,'_')+'.png'})}
await pg.close()}
await b.close()})();
