# HRIS & Poliklinik (Django + PostgreSQL, LAN)

## Menjalankan
    python -m venv venv && . venv/bin/activate && pip install -r requirements.txt
    cp .env.example .env   # isi secret & DATABASE_URL
    python manage.py makemigrations core hr poli && python manage.py migrate
    python manage.py createsuperuser   # set role='superadmin' via shell/admin
    gunicorn config.wsgi -b 127.0.0.1:8000   # taruh Nginx di depan; PostgreSQL hanya listen 127.0.0.1/server

## Keamanan (sudah ada)
Argon2, RBAC (`require_roles`), scope departemen (`scope_by_department`, 404 saat URL diubah), rate limit login,
CSRF, ORM (anti SQL injection), audit log append-only + log login/logout, akses data sensitif tercatat.

## Belum dibuat (ringkas; rincian & urutan di `docs/PROGRESS.md`)
CRUD karyawan/master/kontrak + upload dokumen (UI), enkripsi kolom sensitif & soft delete, UI Poli (rekam medis, obat, rujukan, kehamilan),
penerapan tukar shift/libur & saldo cuti, export/laporan, Nginx/systemd, modul Absensi → Lembur → Payroll.

## Status tahap (acuan: `docs/VISION.md`)
- Tahap 1 Fondasi ✅ · Tahap 3 Workflow ✅ UI `/requests/` · Tahap 4 Informasi ✅ `/notifications/`, `/announcements/`
- Tahap 2 HR Core: model ✅, UI baru daftar karyawan · Tahap 5 Poli: model + surat izin pulang PDF ✅, UI belum
- Tahap 6–7: belum (tabel absensi sudah disiapkan)
- Tes: `python manage.py test` (21 tes). Lampiran pengumuman tersimpan di `media/` — sertakan dalam backup.

Lihat `docs/PROGRESS.md` untuk status & rencana, `docs/VISION.md` untuk visi.
