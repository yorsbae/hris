# HRIS & Poliklinik (Django + PostgreSQL, LAN)

## Menjalankan
    python -m venv venv && . venv/bin/activate && pip install -r requirements.txt
    cp .env.example .env   # isi secret, DATABASE_URL, FIELD_ENCRYPTION_KEY (cara membuat: lihat .env.example)
    python manage.py migrate   # putaran 10 menambah migrasi poli/0002; stok obat lama: catat selisih lewat "Stok masuk"/"Penyesuaian" (lihat PROGRESS putaran 10)
    python manage.py encrypt_sensitive   # sekali, bila ada data lama (backup dulu; --dry-run untuk simulasi)
    python manage.py init_bpjs_status    # sekali, isi awal status BPJS dari nomor yang sudah ada (--dry-run untuk simulasi)
    python manage.py grant_annual_leave --dry-run   # jatah cuti tahunan; ulangi tanpa --dry-run. WAJIB sebelum Admin Dept mengajukan cuti (cuti butuh saldo)
    python manage.py createsuperuser   # set role='superadmin' via shell/admin
    gunicorn config.wsgi -b 127.0.0.1:8000   # taruh Nginx di depan; PostgreSQL hanya listen 127.0.0.1/server

## Keamanan (sudah ada)
Argon2, RBAC (`require_roles`), scope departemen (`scope_by_department`, 404 saat URL diubah), rate limit login,
CSRF, byte NUL ditolak (400), ORM (anti SQL injection), audit log append-only + log login/logout, akses data sensitif tercatat,
**kolom sensitif terenkripsi** (NIK KTP, BPJS, NPWP, rekening; nilainya tidak masuk audit), **soft delete** karyawan,
**data medis hanya Poli** (isi medis tidak masuk audit; akses baca tercatat). *Catatan: kolom medis belum dienkripsi (keputusan A12 di PROGRESS).*
Kunci enkripsi hilang = data tidak bisa dibaca → simpan dan backup terpisah dari database.

## Belum dibuat (ringkas; rincian & urutan di `docs/PROGRESS.md`)
manajemen user (UI), keputusan enkripsi kolom medis, batalkan resep, register kehamilan, lot/kedaluwarsa obat,
pola libur reguler per karyawan & hari libur nasional, batalkan pelaksanaan, export/laporan, Nginx/systemd, modul Absensi → Lembur → Payroll.

## Status tahap (acuan: `docs/VISION.md`)
- Tahap 1 Fondasi ✅ · Tahap 3 Workflow ✅ UI `/requests/` + penerapan tukar shift/libur (`ShiftAssignment`, master shift tidak berubah) + saldo cuti `/leave/` (HRD) + batalkan pengajuan · Tahap 4 Informasi ✅ `/notifications/`, `/announcements/`
- Tahap 2 HR Core: ✅ CRUD karyawan, master, kontrak, riwayat, dokumen, impor CSV, filter (`/employees/`, `/master/`)
- Tahap 5 Poli ✅ `/poli/` (khusus Poli/Superadmin; HRD & Admin Dept → 403): rekam medis + resep (stok berkurang atomik), kecelakaan kerja, kehamilan, catatan tambahan, kartu stok obat (stok masuk/penyesuaian), master diagnosa, rujukan + surat rujukan PDF, surat izin pulang PDF
- Tahap 2b Operasional HRD ✅ `/hrd/` (khusus HRD/Superadmin): bantuan, cuti hamil, kerja harian proyek, katering (tepak besar/kecil), status BPJS K/TK
- Tahap 6–7: belum (tabel absensi sudah disiapkan)
- Tes: `python manage.py test` (254 tes; lulus di SQLite dan PostgreSQL 16; 8 tes konkurensi berthread hanya jalan di PostgreSQL). `media/` (dokumen karyawan, lampiran) ikut `scripts/backup.sh`; salin juga ke lokasi lain dan uji restore.

Lihat `docs/PROGRESS.md` untuk status & rencana, `docs/VISION.md` untuk visi.
