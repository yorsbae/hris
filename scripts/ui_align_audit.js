// Audit tata letak bilah alat (putaran 46): node scripts/ui_align_audit.js [base_url]
// Aturan: [+ Tambah data] rata KIRI; kolom Cari · Ekspor/Impor · Filter rata KANAN (tepi kanan bilah ≤ 4px dari tepi kanan konten) pada layar > 600px.
// Butuh `npm i puppeteer-core @sparticuz/chromium` (atau CHROME=/path). Env: UI_USER, UI_PASS, UI_PAGES, UI_SHOT (daftar halaman yang di-screenshot).
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const ALL = ['/employees/','/requests/','/requests/g/lembur/','/leave/','/announcements/','/hrd/aids/','/hrd/maternity/','/hrd/projects/','/hrd/catering/','/hrd/warnings/','/hrd/uniforms/','/hrd/uniforms/?tab=pembelian','/hrd/separations/','/hrd/bpjs/','/validasi/','/poli/records/','/poli/referrals/','/poli/medicines/','/poli/billing/','/poli/visits/','/users/','/audit/'];
const PAGES = process.env.UI_PAGES ? process.env.UI_PAGES.split(',') : ALL;
(async () => {
  let exe = process.env.CHROME, args = ['--no-sandbox'];
  if (!exe) { const c = require('@sparticuz/chromium'); const ch = c.default || c; exe = await ch.executablePath(); args = ch.args; }
  const b = await puppeteer.launch({ executablePath: exe, args, headless: 'shell' }); const pg = await b.newPage(); fs.mkdirSync('/tmp/ui', { recursive: true });
  await pg.setViewport({ width: 1366, height: 800 }); await pg.goto(BASE + '/login/');
  await pg.type('input[name=username]', process.env.UI_USER || 'superadmin'); await pg.type('input[name=password]', process.env.UI_PASS || 'Demo#HRIS-2026');
  await Promise.all([pg.waitForNavigation(), pg.click('main form button:not(.eye)')]);
  const out = []; let bad = 0;
  for (const u of PAGES) {
    const r = await pg.goto(BASE + u, { waitUntil: 'networkidle0' }).catch(() => null);
    if (!r || r.status() >= 400) { out.push(`${u}: HTTP ${r && r.status()} (lewati)`); continue; }
    const res = await pg.evaluate(() => {
      const bar = document.querySelector('main .fbar'); if (!bar) return 'tanpa bilah alat';
      const main = document.querySelector('main').getBoundingClientRect(), br = bar.getBoundingClientRect();
      const prim = [...bar.querySelectorAll(':scope > .fprim')].filter(e => e.offsetParent), right = [...bar.querySelectorAll(':scope > .fsearch, :scope > .fsub, :scope > .fwrap')].filter(e => e.offsetParent);
      const o = [];
      if (prim.length && Math.abs(prim[0].getBoundingClientRect().left - br.left) > 4) o.push('Tambah tidak rata kiri');
      if (right.length) { const mx = Math.max(...right.map(e => e.getBoundingClientRect().right)); if (br.right - mx > 4) o.push('kelompok kanan tidak rata kanan (selisih ' + Math.round(br.right - mx) + 'px)'); }
      return o.join('; ') || 'ok';
    });
    if (res !== 'ok' && res !== 'tanpa bilah alat') bad++;
    out.push(`${u}: ${res}`);
    if ((process.env.UI_SHOT || '/hrd/uniforms/,/hrd/catering/,/poli/visits/').split(',').includes(u)) await pg.screenshot({ path: '/tmp/ui/' + u.replace(/\W+/g, '_') + '.png' });
  }
  console.log(out.join('\n')); console.log(bad ? `GAGAL: ${bad} halaman` : 'LULUS'); await b.close(); process.exit(bad ? 1 : 0);
})();
