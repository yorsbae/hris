# HRIS & Poliklinik (Django + PostgreSQL, LAN)

## Menjalankan
    python -m venv venv && . venv/bin/activate && pip install -r requirements.txt
    cp .env.example .env   # isi secret, DATABASE_URL, FIELD_ENCRYPTION_KEY (cara membuat: lihat .env.example)
    python manage.py migrate   # putaran 26: hr/0009 (validasi kehadiran, 2 tabel baru); putaran 25: hrd/0008–0010 (stok seragam, isi ulang dari pembelian lama, stok minimum); putaran 24: poli/0005 (tagihan mitra); putaran 23: hrd/0006–0007 (seragam + data awal ukuran/jenis/tarif L 19.000 / P 17.000); putaran 22: hrd/0005 (potongan BPJS); putaran 21: core/0004 (kunci akun); putaran 11: core/0003 (wajib-ganti-sandi); putaran 10: poli/0002; stok obat lama: catat selisih lewat "Stok masuk"/"Penyesuaian" (lihat PROGRESS putaran 10)
    python manage.py encrypt_sensitive   # sekali, bila ada data lama (backup dulu; --dry-run untuk simulasi)
    python manage.py init_bpjs_status    # sekali, isi awal status BPJS dari nomor yang sudah ada (--dry-run untuk simulasi)
    python manage.py grant_annual_leave --dry-run   # jatah cuti tahunan; ulangi tanpa --dry-run. WAJIB sebelum Admin Dept mengajukan cuti (cuti butuh saldo)
    python manage.py createsuperuser   # otomatis role='superadmin' (akun lama dengan role kosong: ubah via shell). User lain dibuat di /users/
    pip install -r requirements.txt   # Django 5.2 LTS (+ redis); isi .env (lihat .env.example: REDIS_URL wajib bila gunicorn >1 worker)
    gunicorn config.wsgi -b 127.0.0.1:8000   # taruh Nginx di depan (WAJIB isi TRUSTED_PROXY_IPS di .env, kirim X-Forwarded-For/-Proto; bila HTTPS: HTTPS=True); PostgreSQL hanya listen 127.0.0.1/server

## Data demo & /admin/ (putaran 16)
`python manage.py seed_demo` mengisi data uji coba (master shift berkode + kelompok rotasi resmi A–G (2 shift) / A_pack–G_pack (3 shift/PACK) dari `docs/jadwal_shift_2026.md`, GS 08–16, karyawan, tukar shift/libur 1 & 2 orang, pengajuan, cuti, HRD, poliklinik) dan akun `superadmin`/`hrd`/`poli`/`admin_prd`… (sandi bawaan `Demo#HRIS-2026`; **jangan di produksi**). `/admin/` kini bertema sama dengan dashboard (sidebar navy, KPI). Login: ikon mata, sandi tampil selama ditekan-tahan.

## Keamanan (sudah ada)
Argon2, RBAC (`require_roles`), scope departemen (`scope_by_department`, 404 saat URL diubah), rate limit login,
CSRF, **kunci akun per username (progresif) + throttle API/ekspor/unggah + timeout idle + CSP/header + kebijakan sandi (putaran 21)**, byte NUL ditolak (400), ORM (anti SQL injection), audit log append-only + log login/logout/login gagal (benar-benar tercatat sejak putaran 11; IP klien asli di belakang Nginx via `TRUSTED_PROXY_IPS`), manajemen user & ganti/reset sandi dengan wajib-ganti (`/users/`, `/password/change/`), penelusuran audit (`/audit/`, Superadmin), akses data sensitif tercatat,
**kolom sensitif terenkripsi** (NIK KTP, BPJS, NPWP, rekening; nilainya tidak masuk audit), **soft delete** karyawan,
**data medis hanya Poli** (isi medis tidak masuk audit; akses baca tercatat). *Catatan: kolom medis belum dienkripsi (keputusan A12 di PROGRESS).*
Kunci enkripsi hilang = data tidak bisa dibaca → simpan dan backup terpisah dari database.

## Antarmuka (putaran 12–14)
Mengacu pada `docs/ui-reference/dashboard-hrd.png` (hanya gaya; fitur mengikuti `docs/VISION.md`): **sidebar navy per peran** (`apps/core/navigation.py`, hanya halaman yang sudah ada), bilah atas dengan lonceng notifikasi + menu pengguna, breadcrumb, footer, laci menu di layar kecil, tema terang/gelap, pill status berwarna, anti klik ganda, konfirmasi aksi berbahaya. **Dashboard per peran**: kartu KPI bertautan, grafik SVG buatan sendiri (donat, garis, batang; tanpa CDN), aktivitas terbaru, pending approval; endpoint `GET /api/dashboard/panels/` (agregasi di server, mengikuti scope; aktivitas medis hanya untuk Poli). Pencarian cepat NIK/nama di bilah atas, keadaan kosong seragam, ringkasan galat formulir (putaran 15). Kehadiran tampil "Belum tersedia" sampai Tahap 6. Semua gaya inline di `base.html`/`home.html` (tanpa berkas statis). Env opsional `COMPANY_NAME` (bawaan `PT X`). Sisa rencana UI/UX: `docs/PROGRESS.md` → "Sisa UI/UX".

## Belum dibuat (ringkas; rincian & urutan di `docs/PROGRESS.md`)
Validasi kehadiran, reset sandi mandiri/2FA, ekspor & retensi audit, keputusan enkripsi kolom medis, batalkan resep, register kehamilan, lot/kedaluwarsa obat,
hari libur nasional, halaman HRD untuk edit tabel rotasi, konfirmasi rekan tukar, batalkan pelaksanaan, export/laporan, Nginx/systemd, modul Absensi → Lembur → Payroll.

## Status tahap (acuan: `docs/VISION.md`)
- Tahap 1 Fondasi ✅ (+ `/users/`, `/audit/`, `/password/change/`: Superadmin) · Tahap 3 Workflow ✅ (+ `/schedule/` jadwal mingguan) UI `/requests/` + penerapan tukar shift/libur (`ShiftAssignment`, master shift tidak berubah) + saldo cuti `/leave/` (HRD) + batalkan pengajuan · Tahap 4 Informasi ✅ `/notifications/`, `/announcements/`
- Tahap 2 HR Core: ✅ CRUD karyawan, master, kontrak, riwayat, dokumen, impor CSV, filter (`/employees/`, `/master/`)
- Tahap 5 Poli ✅ `/poli/` (khusus Poli/Superadmin; HRD & Admin Dept → 403): rekam medis + resep (stok berkurang atomik; form menyesuaikan jenis kunjungan: berobat / kecelakaan kerja / kehamilan HPHT-HPL-GPA / pemeriksaan; tambah & hapus baris obat), riwayat poli di detail karyawan (user Poli), surat izin per jenis (pulang / libur / hamil), catatan tambahan, kartu stok obat (stok masuk/penyesuaian), master diagnosa, rujukan + surat rujukan PDF, surat izin pulang PDF
- Tahap 2b Operasional HRD ✅ `/hrd/` (khusus HRD/Superadmin): bantuan, cuti hamil, kerja harian proyek, katering (tepak besar/kecil), status BPJS K/TK, **potongan BPJS per periode (`/hrd/bpjs/deductions/`, submenu sidebar BPJS → Kesehatan/Ketenagakerjaan: input, impor/ekspor XLSX, ringkasan per departemen, anomali — putaran 22–23)**, **Rekap Seragam (`/hrd/uniforms/`: pembelian + ukuran, tarif L/P berlaku-sejak, rekap pesanan per ukuran × jenis kelamin, impor/ekspor, batal beralasan — putaran 23)**
- **Tahap 5c Tagihan Mitra ✅** `/poli/billing/` (khusus Poli/Superadmin; HRD & Admin Dept → 403): mitra rekanan, tagihan + rincian opsional, alur diterima → diverifikasi → disetujui → dibayar / ditolak, **keluhan terenkripsi**, rekap per mitra/departemen/diagnosa/karyawan/status, impor, ekspor ringkasan (tanpa medis) dan lengkap — putaran 24
- Tahap 6–7: belum (tabel absensi sudah disiapkan)
- Tes: `python manage.py test` (717 tes di SQLite, putaran 26; sebelumnya 692 di putaran 25, 672 di putaran 24; sebelumnya 631 di putaran 23, 563 di putaran 22; terakhir penuh di PostgreSQL 16: 450 tes, putaran 17b/17c; lulus di SQLite dan PostgreSQL 16 (putaran 17b); tes konkurensi berthread hanya jalan di PostgreSQL). `media/` (dokumen karyawan, lampiran) ikut `scripts/backup.sh`; salin juga ke lokasi lain dan uji restore.

Lihat `docs/PROGRESS.md` untuk status & rencana, `docs/VISION.md` untuk visi.
