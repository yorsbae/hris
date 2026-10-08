# HRIS & Poliklinik (Django + PostgreSQL, LAN)

## Menjalankan
    python -m venv venv && . venv/bin/activate && pip install -r requirements.txt
    cp .env.example .env   # isi secret, DATABASE_URL, FIELD_ENCRYPTION_KEY (cara membuat: lihat .env.example)
    python manage.py migrate   # putaran 11: core/0003 (wajib-ganti-sandi); putaran 10: poli/0002; stok obat lama: catat selisih lewat "Stok masuk"/"Penyesuaian" (lihat PROGRESS putaran 10)
    python manage.py encrypt_sensitive   # sekali, bila ada data lama (backup dulu; --dry-run untuk simulasi)
    python manage.py init_bpjs_status    # sekali, isi awal status BPJS dari nomor yang sudah ada (--dry-run untuk simulasi)
    python manage.py grant_annual_leave --dry-run   # jatah cuti tahunan; ulangi tanpa --dry-run. WAJIB sebelum Admin Dept mengajukan cuti (cuti butuh saldo)
    python manage.py createsuperuser   # otomatis role='superadmin' (akun lama dengan role kosong: ubah via shell). User lain dibuat di /users/
    gunicorn config.wsgi -b 127.0.0.1:8000   # taruh Nginx di depan (WAJIB isi TRUSTED_PROXY_IPS di .env, kirim X-Forwarded-For/-Proto; bila HTTPS: HTTPS=True); PostgreSQL hanya listen 127.0.0.1/server

## Data demo & /admin/ (putaran 16)
`python manage.py seed_demo` mengisi data uji coba (master shift berkode + kelompok rotasi A–G / A_pack–G_pack, karyawan, tukar shift/libur 1 & 2 orang, pengajuan, cuti, HRD, poliklinik) dan akun `superadmin`/`hrd`/`poli`/`admin_prd`… (sandi bawaan `Demo#HRIS-2026`; **jangan di produksi**). `/admin/` kini bertema sama dengan dashboard (sidebar navy, KPI). Login: ikon mata, sandi tampil selama ditekan-tahan.

## Keamanan (sudah ada)
Argon2, RBAC (`require_roles`), scope departemen (`scope_by_department`, 404 saat URL diubah), rate limit login,
CSRF, byte NUL ditolak (400), ORM (anti SQL injection), audit log append-only + log login/logout/login gagal (benar-benar tercatat sejak putaran 11; IP klien asli di belakang Nginx via `TRUSTED_PROXY_IPS`), manajemen user & ganti/reset sandi dengan wajib-ganti (`/users/`, `/password/change/`), penelusuran audit (`/audit/`, Superadmin), akses data sensitif tercatat,
**kolom sensitif terenkripsi** (NIK KTP, BPJS, NPWP, rekening; nilainya tidak masuk audit), **soft delete** karyawan,
**data medis hanya Poli** (isi medis tidak masuk audit; akses baca tercatat). *Catatan: kolom medis belum dienkripsi (keputusan A12 di PROGRESS).*
Kunci enkripsi hilang = data tidak bisa dibaca → simpan dan backup terpisah dari database.

## Antarmuka (putaran 12–14)
Mengacu pada `docs/ui-reference/dashboard-hrd.png` (hanya gaya; fitur mengikuti `docs/VISION.md`): **sidebar navy per peran** (`apps/core/navigation.py`, hanya halaman yang sudah ada), bilah atas dengan lonceng notifikasi + menu pengguna, breadcrumb, footer, laci menu di layar kecil, tema terang/gelap, pill status berwarna, anti klik ganda, konfirmasi aksi berbahaya. **Dashboard per peran**: kartu KPI bertautan, grafik SVG buatan sendiri (donat, garis, batang; tanpa CDN), aktivitas terbaru, pending approval; endpoint `GET /api/dashboard/panels/` (agregasi di server, mengikuti scope; aktivitas medis hanya untuk Poli). Pencarian cepat NIK/nama di bilah atas, keadaan kosong seragam, ringkasan galat formulir (putaran 15). Kehadiran tampil "Belum tersedia" sampai Tahap 6. Semua gaya inline di `base.html`/`home.html` (tanpa berkas statis). Env opsional `COMPANY_NAME` (bawaan `PT X`). Sisa rencana UI/UX: `docs/PROGRESS.md` → "Sisa UI/UX".

## Belum dibuat (ringkas; rincian & urutan di `docs/PROGRESS.md`)
reset sandi mandiri/2FA, ekspor & retensi audit, keputusan enkripsi kolom medis, batalkan resep, register kehamilan, lot/kedaluwarsa obat,
hari libur nasional, halaman HRD untuk edit tabel rotasi, konfirmasi rekan tukar, batalkan pelaksanaan, export/laporan, Nginx/systemd, modul Absensi → Lembur → Payroll.

## Status tahap (acuan: `docs/VISION.md`)
- Tahap 1 Fondasi ✅ (+ `/users/`, `/audit/`, `/password/change/`: Superadmin) · Tahap 3 Workflow ✅ (+ `/schedule/` jadwal mingguan) UI `/requests/` + penerapan tukar shift/libur (`ShiftAssignment`, master shift tidak berubah) + saldo cuti `/leave/` (HRD) + batalkan pengajuan · Tahap 4 Informasi ✅ `/notifications/`, `/announcements/`
- Tahap 2 HR Core: ✅ CRUD karyawan, master, kontrak, riwayat, dokumen, impor CSV, filter (`/employees/`, `/master/`)
- Tahap 5 Poli ✅ `/poli/` (khusus Poli/Superadmin; HRD & Admin Dept → 403): rekam medis + resep (stok berkurang atomik), kecelakaan kerja, kehamilan, catatan tambahan, kartu stok obat (stok masuk/penyesuaian), master diagnosa, rujukan + surat rujukan PDF, surat izin pulang PDF
- Tahap 2b Operasional HRD ✅ `/hrd/` (khusus HRD/Superadmin): bantuan, cuti hamil, kerja harian proyek, katering (tepak besar/kecil), status BPJS K/TK
- Tahap 6–7: belum (tabel absensi sudah disiapkan)
- Tes: `python manage.py test` (450 tes; lulus di SQLite dan PostgreSQL 16 (putaran 17b); tes konkurensi berthread hanya jalan di PostgreSQL). `media/` (dokumen karyawan, lampiran) ikut `scripts/backup.sh`; salin juga ke lokasi lain dan uji restore.

Lihat `docs/PROGRESS.md` untuk status & rencana, `docs/VISION.md` untuk visi.
