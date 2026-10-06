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

## Belum dibuat (tahap berikut)
Template/UI & dashboard, pengumuman + status baca (model ada, view belum), form izin/cuti per tipe, PDF surat (reportlab),
rujukan & kehamilan (view), upload dokumen karyawan, export, notifikasi UI, test otomatis, enkripsi kolom sensitif,
wsgi.py, Nginx/systemd, modul Absensi (AttendanceRaw/Daily sudah disiapkan) → Lembur → Payroll.

## Status tahap (lihat docs/VISION.md)
- Tahap 1 Fondasi: ✅ auth/role/scope/audit, ✅ dashboard per role (`/api/dashboard/`) — UI belum
- Tahap 2–3: model + workflow service ✅, UI/form belum
- Tahap 5 (sebagian): ✅ surat izin pulang PDF `/api/poli/records/<id>/letter.pdf` (A4 portrait, isi separuh atas, garis potong tengah)

Lihat `docs/PROGRESS.md` untuk status & rencana, `docs/VISION.md` untuk visi.
