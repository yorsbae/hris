# Menjalankan situs publik (Company Profile + Karir) — Tahap 11

Dua cara memakai kode yang sama (`apps/company`):

| | Aplikasi internal (LAN) | Situs publik (internet) |
|---|---|---|
| `SITE_MODE` | `internal` (bawaan) | `public` |
| URLconf | `config/urls.py` | `config/urls_public.py` — **hanya** profil + karir; tidak ada `/admin/`, `/login/`, `/employees/`, `/poli/`, `/hrd/`, `/api/…` (diuji) |
| Isi situs | dikelola di **Company Profile** & **Rekrutmen** (HRD/Superadmin); pratinjau draf di `/situs/` | membaca isi yang sama dan menerima lamaran |
| Host | `hris.lan` | `wadimor.co.id` (contoh) |

## Keputusan arsitektur (putaran 48) — dan batasannya
VISION menuntut situs publik di **host terpisah dengan penyimpanan sendiri dan sinkronisasi satu arah**. Putaran 48 menyediakan **fondasi yang berfungsi** dengan satu basis data bersama:
1. **Versi 1 (sekarang, paling sederhana):** satu basis data. Jalankan proses Django kedua dengan `SITE_MODE=public` (user DB **terpisah dengan hak terbatas**: boleh baca/tulis tabel `company_*` saja). Permukaan serangan di internet = URLconf publik saja; tidak ada sesi/login/peran internal di sana.
2. **Versi 2 (disarankan sebelum trafik besar):** replika baca untuk konten situs + antrean lamaran tahan lama (tabel `company_application` di basis data situs publik yang ditarik HRIS). Antarmuka `apps/company/careers.create_application` sengaja satu pintu agar mudah dipindah. **Belum dikerjakan** (butuh keputusan hosting).

## Langkah (versi 1)
```
# .env situs publik (ADA di host publik; TANPA rahasia HRIS yang tak perlu)
SITE_MODE=public
DJANGO_SECRET_KEY=...            # BEDA dari aplikasi internal
DATABASE_URL=postgres://pub_user:...@db-host/hris   # user dengan hak terbatas ke tabel company_* (lihat bawah)
FIELD_ENCRYPTION_KEY=...         # SAMA dengan internal (data pelamar terenkripsi harus terbaca HRD)
ALLOWED_HOSTS=wadimor.co.id
PUBLIC_BASE_URL=https://wadimor.co.id
HTTPS=True
CSRF_TRUSTED_ORIGINS=https://wadimor.co.id
PRIVATE_MEDIA_ROOT=/srv/wadimor/private_media   # CV terenkripsi; BUKAN folder yang disajikan Nginx; harus dibaca juga oleh aplikasi internal (NFS/rsync/disk bersama)
MEDIA_ROOT: gambar situs (company/…) harus tersedia di host publik (rsync dari internal atau unggah dari backend yang sama)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend  + EMAIL_HOST/PORT/USER/PASSWORD/TLS + DEFAULT_FROM_EMAIL
CLAMAV_ADDRESS=unix:/var/run/clamav/clamd.ctl   # sangat disarankan; tanpa ini berkas ditandai "unscanned" dan HRD melihatnya
REDIS_URL=redis://127.0.0.1:6379/1              # WAJIB bila gunicorn > 1 worker (rate limit bersama)
gunicorn config.wsgi -b 127.0.0.1:8100 --workers 3
```
Nginx: `scripts/nginx-publik.conf.example` (HSTS bertahap, batas laju, batas unggahan 4 MB). Cron: `scripts/cron-hris.example` (`purge_applicants`).

### Hak basis data user publik (PostgreSQL, contoh)
```
CREATE ROLE pub_user LOGIN PASSWORD '…';
GRANT SELECT ON company_sitesetting, company_page, company_productcategory, company_product, company_productimage, company_article, company_storelocation, company_faq, company_job TO pub_user;
GRANT SELECT, INSERT, UPDATE ON company_application, company_contactmessage TO pub_user;   -- UPDATE: verifikasi/batal/hapus data oleh pelamar
GRANT INSERT ON company_applicationevent TO pub_user;  GRANT SELECT ON company_applicationevent TO pub_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pub_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON django_session TO pub_user;    -- bila tabel sesi dipakai CSRF/pesan (situs publik tidak membuat sesi login)
-- core_notification / core_user: SELECT + INSERT notifikasi untuk HRD (pesan/lamaran baru):
GRANT SELECT ON core_user TO pub_user; GRANT INSERT ON core_notification TO pub_user;
```
Uji dengan `python manage.py check --deploy` dan pindai headers (`curl -I`). Sebelum dibuka ke publik: uji penetrasi dasar, uji ponsel, pastikan `/healthz` mengembalikan `ok`.

## Yang dilindungi (ringkas)
- CSP tanpa `unsafe-inline` (tes menjaga templat publik bebas `style=`/`<style>`/`on*=`), `nosniff`, `Referrer-Policy`; halaman berformulir `no-store`; halaman status pelamar `no-referrer` + `noindex`; tautan status = token acak 192-bit, hanya hash yang dicari.
- Formulir: honeypot + cap waktu bertanda tangan (terlalu cepat/kedaluwarsa ditolak) + rate limit per IP (Redis) + batas 3 lamaran/email/hari + kunci idempotensi (kirim ganda tidak menggandakan) + validasi server.
- Unggahan: ekstensi + magic bytes + ukuran + PDF berskrip ditolak + DOCX bermakro ditolak + ClamAV opsional (bila dikonfigurasi tapi mati → ditolak, bukan dilolos); disimpan **terenkripsi** di folder privat dengan nama acak; diunduh HRD lewat view berizin (`attachment`, `nosniff`, tercatat).
- Data pelamar (email, telepon, tgl lahir, surat lamaran, jawaban, token) terenkripsi kolom; audit tidak berisi data pribadi; hak pelamar: unduh data (JSON), batalkan, hapus; retensi otomatis (`CAREER_RETENTION_MONTHS`, ×2 bila setuju talent pool).
- Teks dari editor/pelamar tidak pernah jadi HTML (filter `rich` meng-escape lalu hanya mengenali paragraf, `## judul`, daftar `- `, `**tebal**`). Gambar situs hanya JPG/PNG/WebP (bukan SVG), isi diperiksa.
- Gagal kirim email tidak menghilangkan lamaran (dicatat sebagai jejak `mail_failed`).
