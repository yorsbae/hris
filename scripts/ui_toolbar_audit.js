// Audit bilah alat seragam (putaran 41): node scripts/ui_toolbar_audit.js [base_url]
// Butuh `npm i puppeteer-core @sparticuz/chromium` (atau CHROME=/path). Env: UI_USER, UI_PASS, UI_WIDTHS, UI_PAGES.
// Per halaman daftar: (a) tombol Tambah, kolom Cari, Ekspor/Impor, Filter berada SEBARIS (tengah vertikal ≤ 6px) di >600px,
// (b) tidak ada tautan/tombol "Ekspor/Impor/Unduh" yang tersisa di luar popover, (c) popover Ekspor/Impor terbuka utuh di layar.
const puppeteer = require('puppeteer-core'); const fs = require('fs');
const BASE = process.argv[2] || 'http://127.0.0.1:8000';
const ALL = ['/employees/','/requests/','/requests/g/lembur/','/requests/g/jadwal/','/leave/','/announcements/','/hrd/aids/','/hrd/maternity/','/hrd/projects/','/hrd/catering/','/hrd/warnings/','/hrd/bpjs/','/hrd/bpjs/deductions/','/hrd/separations/','/hrd/uniforms/','/hrd/uniforms/?tab=pembelian','/validasi/','/schedule/','/requests/g/lembur/rekap/','/poli/records/','/poli/medicines/','/poli/diagnoses/','/poli/referrals/','/poli/billing/','/poli/reports/stock/','/users/','/audit/','/master/department/','/master/shift/'];
const PAGES = process.env.UI_PAGES ? process.env.UI_PAGES.split(',') : ALL;
(async () => {
  let exe = process.env.CHROME, args = ['--no-sandbox'];
  if (!exe) { const c = require('@sparticuz/chromium'); const ch = c.default || c; exe = await ch.executablePath(); args = ch.args; }
  const b = await puppeteer.launch({ executablePath: exe, args, headless: 'shell' }); const pg = await b.newPage(); fs.mkdirSync('/tmp/ui', { recursive: true });
  await pg.setViewport({ width: 1366, height: 800 }); await pg.goto(BASE + '/login/');
  await pg.type('input[name=username]', process.env.UI_USER || 'superadmin'); await pg.type('input[name=password]', process.env.UI_PASS || 'Demo#HRIS-2026');
  await Promise.all([pg.waitForNavigation(), pg.click('main form button:not(.eye)')]);
  const out = [];
  for (const w of (process.env.UI_WIDTHS || '1366,390').split(',').map(Number)) {
    await pg.setViewport({ width: w, height: 800 });
    for (const u of PAGES) {
      const r = await pg.goto(BASE + u, { waitUntil: 'networkidle0' }).catch(() => null);
      if (!r || r.status() >= 400) { out.push(`${w} ${u}: HTTP ${r && r.status()} (lewati)`); continue; }
      const res = await pg.evaluate((w) => {
        const o = [], bar = document.querySelector('main .fbar'); if (!bar) return { o: ['tanpa bilah alat'] };
        const stray = [...document.querySelectorAll('main a, main button')].filter(e => !e.closest('.epanel,.fpanel,.fwrap,table,.wrap,form:not(.fbar)') && /^(Ekspor|Impor|Unduh (CSV|XLSX))\b/i.test(e.textContent.trim()) && e.offsetParent);
        if (stray.length) o.push('sisa tombol ekspor/impor di luar popover: ' + stray.map(e => e.textContent.trim()).join(' | '));
        const items = [...bar.querySelectorAll(':scope > .fprim, :scope > .fsearch, :scope > .fsub, :scope > .fwrap > .fbtn')].filter(e => e.offsetParent);
        const mid = items.map(e => { const r = e.getBoundingClientRect(); return r.top + r.height / 2; });
        if (w > 600 && mid.length > 1 && Math.max(...mid) - Math.min(...mid) > 6) o.push('tidak sebaris: ' + items.map(e => (e.textContent.trim() || e.className).slice(0, 14) + '@' + Math.round(e.getBoundingClientRect().top)).join(', '));
        const hs = items.map(e => Math.round(e.getBoundingClientRect().height)); if (w > 600 && new Set(hs).size > 1) o.push('tinggi beda: ' + hs.join('/'));
        const eb = bar.querySelector('.epanel') && bar.querySelector('.epanel').parentNode.querySelector('.fbtn');
        let label = '';
        if (eb) { label = eb.textContent.trim(); eb.click(); const p = bar.querySelector('.epanel'), pr = p.getBoundingClientRect();
          if (pr.left < -1 || pr.right > innerWidth + 1 || pr.top < 0) o.push('popover ekspor keluar layar');
          const hit = document.elementFromPoint(pr.left + pr.width / 2, Math.min(pr.top + 24, innerHeight - 4)); if (!hit || !p.contains(hit)) o.push('popover ekspor tertutup');
          if (!p.querySelectorAll('a').length) o.push('popover ekspor kosong'); }
        return { o, label, n: items.length };
      }, w);
      if (res.o.length) out.push(`${w} ${u}: ${res.o.join('; ')}`);
      else out.push(`${w} ${u}: ok [${res.n}]${res.label ? ' ' + res.label : ''}`);
      await pg.screenshot({ path: `/tmp/ui/tb_${w}_${u.replace(/\W+/g, '_')}.png`, clip: { x: 0, y: 0, width: w, height: 460 } });
    }
  }
  console.log(out.join('\n')); await b.close();
})();
