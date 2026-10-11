const puppeteer=require('puppeteer-core');
(async()=>{const c=require('@sparticuz/chromium');const ch=c.default||c;const b=await puppeteer.launch({executablePath:await ch.executablePath(),args:ch.args,headless:'shell'});const pg=await b.newPage();
await pg.setViewport({width:390,height:800});await pg.goto('http://127.0.0.1:8000/login/');await pg.type('input[name=username]','superadmin');await pg.type('input[name=password]','Demo#HRIS-2026');await Promise.all([pg.waitForNavigation(),pg.click('main form button:not(.eye)')]);
for(const u of ['/requests/','/hrd/aids/','/hrd/separations/','/hrd/uniforms/','/poli/records/','/users/','/audit/','/schedule/','/master/shift/','/leave/','/employees/']){await pg.goto('http://127.0.0.1:8000'+u,{waitUntil:'networkidle0'});
const r=await pg.evaluate(()=>{const de=document.documentElement;const t=[...document.querySelectorAll('main .wrap table')];return {cards:t.filter(x=>x.classList.contains('cards')).length,total:t.length,pageScroll:de.scrollWidth>de.clientWidth+1,wrapScroll:[...document.querySelectorAll('.wrap')].some(x=>x.scrollWidth>x.clientWidth+1)}});
console.log(u,JSON.stringify(r));if(u==='/requests/'||u==='/hrd/separations/')await pg.screenshot({path:'/tmp/ui/card'+u.replace(/\W+/g,'_')+'.png',clip:{x:0,y:250,width:390,height:550}})}
await b.close()})();
