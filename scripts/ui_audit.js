// Audit tampilan: node scripts/ui_audit.js [base_url] — butuh `npm i puppeteer-core @sparticuz/chromium` (atau Chrome lokal: CHROME=/path).
// Untuk tiap halaman daftar & tiap lebar (1366/768/390): scroll samping halaman/tabel, lalu buka panel Filter dan periksa
// (a) panel di dalam layar, (b) tidak menimpa elemen sesudahnya, (c) tombol/chip tidak keluar layar. Screenshot ke /tmp/ui/.
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const ALL = ['/employees/','/requests/','/leave/','/announcements/','/hrd/aids/','/hrd/maternity/','/hrd/projects/','/hrd/catering/','/hrd/warnings/','/hrd/bpjs/','/hrd/bpjs/deductions/','/hrd/separations/','/hrd/uniforms/','/hrd/uniforms/?tab=pembelian','/hrd/uniforms/?tab=riwayat','/poli/billing/','/poli/medicines/','/poli/records/','/poli/referrals/','/poli/reports/stock/','/users/','/audit/','/validasi/','/schedule/','/requests/g/lembur/rekap/'];
const PAGES = process.env.UI_PAGES ? ALL.filter(u => process.env.UI_PAGES.split(',').some(k => u.includes(k))) : ALL; // UI_USER=poli UI_PAGES=/poli/
(async () => {
  let exe = process.env.CHROME, args = ['--no-sandbox'];
  if (!exe) { const c = require('@sparticuz/chromium'); const ch = c.default || c; exe = await ch.executablePath(); args = ch.args; }
  const b = await puppeteer.launch({ executablePath: exe, args, headless: 'shell' }); const pg = await b.newPage(); fs.mkdirSync('/tmp/ui', { recursive: true });
  await pg.setViewport({ width: 1366, height: 800 }); await pg.goto(BASE + '/login/');
  await pg.type('input[name=username]', process.env.UI_USER || 'hrd'); await pg.type('input[name=password]', process.env.UI_PASS || 'Demo#HRIS-2026');
  await Promise.all([pg.waitForNavigation(), pg.click('main form button:not(.eye)')]);
  const problems = [];
  for (const w of [1366, 768, 390]) {
    await pg.setViewport({ width: w, height: 800 });
    for (const u of PAGES) {
      const r = await pg.goto(BASE + u, { waitUntil: 'networkidle0' }).catch(() => null); if (!r || r.status() >= 400) { problems.push(`${w} ${u}: HTTP ${r && r.status()}`); continue; }
      const res = await pg.evaluate(() => {
        const o = [], de = document.documentElement; if (de.scrollWidth > de.clientWidth + 1) o.push('halaman scroll samping');
        document.querySelectorAll('.wrap').forEach(x => { if (x.scrollWidth > x.clientWidth + 1) o.push('tabel scroll samping'); });
        const f = document.querySelector('[data-fbar]'); if (!f) return { o, nofilter: true };
        const btn = f.querySelector('.fbtn'); btn.click(); const p = f.querySelector('.fpanel'), pr = p.getBoundingClientRect();
        if (pr.left < -1 || pr.right > innerWidth + 1) o.push('panel keluar layar');
        let n = f.nextElementSibling; if (n) { const nr = n.getBoundingClientRect(); if (nr.top < pr.bottom - 1 && nr.height > 0) o.push('panel menimpa elemen sesudahnya'); }
        f.querySelectorAll('.fbtn,.fchip,.fsearch').forEach(e => { const r = e.getBoundingClientRect(); if (r.right > innerWidth + 1) o.push('kontrol keluar layar: ' + e.className); });
        return { o, chips: f.querySelectorAll('.fchip').length };
      });
      if (res.o.length) problems.push(`${w} ${u}: ${[...new Set(res.o)].join('; ')}`); if (res.nofilter) problems.push(`${w} ${u}: (tanpa tombol Filter)`);
      if (w !== 1366 || /aids|uniforms\/$|audit/.test(u)) await pg.screenshot({ path: `/tmp/ui/${w}_${u.replace(/\W+/g, '_')}.png` });
    }
  }
  console.log(problems.length ? problems.join('\n') : 'TIDAK ADA MASALAH'); await b.close();
})();
