# Memisahkan HRIS (lokal) dan Situs Publik (internet): tetap 1 repo

## 1. Arsitektur

```
                       INTERNET                                              LAN KANTOR
 pengunjung ──► Cloudflare ──► HOST PUBLIK                                   HRIS (hris.lan)
               (DDoS/WAF/cache)  nftables → Nginx(cache, laju) → gunicorn     config.settings
                                 config.settings_public                       semua modul HR/Poli/Payroll
                                 DB PUBLIK sendiri + .env + kunci sendiri     DB HRIS sendiri
                                 isi: konten TERBIT + lamaran sementara       TIDAK terhubung ke internet masuk
                                       ▲                    │
              bundle konten+status     │                    │  HRIS menghubungi host publik (SSH), bukan sebaliknya
              (zip, HMAC) ─────────────┘                    ▼
              publish_site (rsync/ssh, KELUAR)         pull_applications (ssh, KELUAR)
```

**Satu repo, dua yang dideploy.** Kode tetap satu pohon (`apps/`), yang berbeda hanya *entry point*, *database*, *.env*, *host*:

| | HRIS (LAN) | Situs publik (internet) |
|---|---|---|
| Settings / WSGI | `config.settings` / `config.wsgi` | `config.settings_public` / `config.wsgi_public` |
| URLconf | `config/urls.py` (semua modul + `/company/`, `/recruitment/` untuk HRD) | `config/urls_public.py` (profil + karir saja) |
| Aplikasi dimuat | semua | `core`, `hr` (hanya cangkang model karena FK), `company` |
| Dependensi | `requirements.txt` | `requirements-public.txt` (tanpa reportlab/openpyxl/pyzk/argon2) |
| Database | DB HRIS | DB publik **sendiri** (tidak pernah berisi data karyawan) |
| Kunci enkripsi | `FIELD_ENCRYPTION_KEY` HRIS | kunci **berbeda**; kunci HRIS tidak ada di host publik |
| Siapa menghubungi siapa | HRIS → publik (SSH keluar) | tidak pernah menghubungi LAN |

Yang dikerjakan HRD tetap di HRIS (`/company/`, `/recruitment/`): produk, artikel, lowongan, pelamar. Hasilnya **didorong** ke situs publik tiap 5 menit.

### Alur data
- **Turun (HRIS → publik)**: `publish_site` membuat bundle zip berisi **hanya konten berstatus terbit** (draf tidak pernah keluar dari LAN), gambar, dan status lamaran. Ditandatangani HMAC (`SYNC_SHARED_SECRET`). `import_site_bundle` di host publik menolak: tanda tangan salah, hash tidak cocok, nama berkas di luar pola `company/<jenis>/<32hex>.(jpg|png|webp)`, isi gambar tidak sesuai ekstensi, bundle lama (anti-replay). Penerapan atomik.
- **Naik (publik → HRIS)**: HRIS menjalankan `pull_applications` lewat SSH. Lamaran dibaca di host publik, didekripsi di sana, dikirim lewat SSH, **dienkripsi ulang dengan kunci HRIS**; CV diperiksa ulang (jenis, magic bytes, ukuran) di HRIS. Idempoten (upsert per nomor lamaran).
- **Minimisasi**: setelah HRIS menarik, bundle berikutnya membuat host publik **membuang CV, telepon, tgl lahir, surat lamaran, jawaban**. Tersisa nama, email (kirim ulang tautan), token & status. Jadi bila host publik dibobol, yang bocor hanya lamaran yang belum ditarik (≤ 5 menit) + data minimal.
- **Tidak ada** port, API, atau tunnel dari internet ke LAN.

## 2. Mengapa tetap 1 repo (dan caranya tidak saling mengganggu)
1. Model `apps/company` dipakai kedua sisi; dua repo = dua salinan yang pasti beda → sinkronisasi rusak.
2. Rilis publik dibuat dari repo yang sama, **hanya folder yang perlu**: `deploy/public/build-release.sh` (tanpa `apps/hrd`, `poli`, `payroll`, `scripts`, `docs`). Server publik tidak pernah memegang kode Payroll/Poli.
3. CI memeriksa **dua** hal: seluruh tes HRIS, dan job `public-site` (boot dengan `settings_public`, migrasi dari nol, rute internal harus 404, tanpa library HR).
4. Aturan tim: perubahan `apps/company/models.py` = migrasi + deploy **kedua** sisi (HRIS dulu, lalu publik); `SYNC_SHARED_SECRET` sama di keduanya.

## 3. Memasang host publik (urut)

**3.1 Server**: VPS/VM Linux khusus (jangan berbagi dengan HRIS/aplikasi lain), Ubuntu LTS, update otomatis keamanan (`unattended-upgrades`), tanpa PHP/Apache/MySQL terpasang.
```
adduser --system --group --home /srv/wadimor hrispub
mkdir -p /srv/wadimor/{app,media,private,state,inbox} /var/log/wadimor /var/cache/nginx/wadimor
chown -R hrispub:hrispub /srv/wadimor /var/log/wadimor ; chmod 700 /srv/wadimor/private /srv/wadimor/state
```
**3.2 Kode & dependensi** (dari rilis, bukan `git clone` penuh):
```
./deploy/public/build-release.sh   # di mesin build → wadimor-release.tar.gz
tar -xzf wadimor-release.tar.gz -C /srv/wadimor/app && python3 -m venv /srv/wadimor/venv
/srv/wadimor/venv/bin/pip install --require-hashes -r requirements-public.txt   # idealnya dikunci hash (pip-compile --generate-hashes)
cp deploy/public/env.example /srv/wadimor/app/.env && chmod 600 /srv/wadimor/app/.env   # isi semua nilai
```
**3.3 Database publik** (PostgreSQL lokal, hanya `127.0.0.1`):
```
CREATE ROLE pub_user LOGIN PASSWORD '…' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE wadimor_public OWNER pub_user;
```
Karena DB ini milik situs publik saja, tidak perlu hak per-tabel seperti rancangan lama. Jalankan `DJANGO_SETTINGS_MODULE=config.settings_public python manage.py migrate` dan `collectstatic`. (Tabel `core_*`/`hr_*` ikut terbuat **kosong**: cangkang karena FK; lihat §8.)

**3.4 Mount tanpa eksekusi (inti anti-web-shell)**: `/srv/wadimor/media` dan `/srv/wadimor/private` pada partisi/loop terpisah:
```
# /etc/fstab
/dev/vdb1  /srv/wadimor/media    ext4  defaults,noexec,nosuid,nodev  0 2
```
**3.5 Layanan & jaringan**: salin `wadimor-public.service` (sandbox systemd), `nginx-publik.conf`, `nftables.conf` (ganti `ADMIN_IP`), `fail2ban/*`; `certbot` atau sertifikat Cloudflare Origin; `cron-publik` untuk user `hrispub`.
```
systemctl enable --now wadimor-public nftables fail2ban redis-server ; nginx -t && systemctl reload nginx
```
**3.6 Kunci SSH terbatas** (HRIS → publik). Di `~hrispub/.ssh/authorized_keys`, kunci hanya bisa menjalankan satu perintah, tanpa shell/forwarding:
```
# kunci TARIK (hanya mengekspor lamaran)
restrict,from="IP_PUBLIK_KANTOR",command="cd /srv/wadimor/app && DJANGO_SETTINGS_MODULE=config.settings_public /srv/wadimor/venv/bin/python manage.py export_applications ${SSH_ORIGINAL_COMMAND##*export_applications}" ssh-ed25519 AAAA… hrispull@hris
# kunci DORONG (hanya rsync menulis ke inbox)
restrict,from="IP_PUBLIK_KANTOR",command="rrsync -wo /srv/wadimor/inbox" ssh-ed25519 AAAA… hrispush@hris
```
(`pull_applications` memanggil `ssh user@host "cd … && …export_applications"`; dengan `command=` di atas, argumen `--since` diteruskan dan perintah lain diabaikan. Uji kedua kunci sebelum diaktifkan di cron. Bila memakai satu user, gunakan dua entri di `authorized_keys` user yang sama seperti contoh.)

**3.7 HRIS (LAN)**: tambahkan `deploy/hris/env.sync.example` ke `.env`, pasang `deploy/hris/cron-sync`, `known_hosts` host publik (StrictHostKeyChecking=yes). HRIS **tidak membuka port apa pun** ke internet; bind `127.0.0.1`/LAN, firewall hanya LAN/VPN.

## 4. Keamanan berlapis

### 4.1 Soal "php2shell"/web shell
Aplikasi ini Django (Python), bukan PHP, jadi CMS/plugin PHP yang biasa disusupi tidak ada. Yang tetap bisa terjadi dan sudah ditutup:
| Jalur web shell | Penutup |
|---|---|
| Unggah berkas lalu dieksekusi | Hanya jpg/png/webp (gambar, dari HRIS, tervalidasi magic bytes) & CV (pdf/doc/docx/jpg/png; PDF berskrip & DOCX makro ditolak; ClamAV). CV **terenkripsi**, nama acak, di folder privat yang **tidak disajikan**. Folder media `noexec,nosuid,nodev`. Nginx hanya melayani pola `company/<jenis>/<hex>.(jpg|png|webp)` dengan `nosniff` + CSP `sandbox`. |
| Permintaan `.php`/`wp-login`/`.env`/`.git` | Nginx `return 444` (putus) + fail2ban memblokir IP 24 jam setelah 3 kali. |
| Eksploitasi library | `requirements-public.txt` minimal; versi dikunci hash; `pip-audit` di CI/jadwal mingguan. |
| Proses disusupi | systemd sandbox: tanpa privilege, `ProtectSystem=strict`, hanya 4 folder yang boleh ditulis, `MemoryDenyWriteExecute`, filter syscall. User tanpa shell. |
| Pencurian kredensial/rahasia | `.env` mode 600; **host publik tak punya kunci HRIS, tak punya data karyawan, tak punya akses LAN**; DB publik terpisah. |
| Bundle sinkronisasi palsu | HMAC + manifest hash + whitelist nama + validasi isi + anti-replay. |
| SSH | Hanya IP admin (nftables), kunci saja (`PasswordAuthentication no`, `PermitRootLogin no`), kunci sinkronisasi `restrict`+`command=`. |
| Penyusupan tak terdeteksi | `aide`/`rkhunter` terjadwal, log ke luar host (`rsyslog`/`vector`), alarm via `check_health`. |

### 4.2 Banyak permintaan & DDoS: jujur soal batasnya
- **Serangan volumetrik (L3/L4, ratusan Mbps ke atas) tidak dapat dihentikan oleh server sendiri** (jalur internet sudah penuh sebelum paket sampai ke Nginx). Wajib ada di depan: **Cloudflare** (gratis cukup untuk awal), lalu: DNS proxied (awan oranye), SSL Full (strict) + Origin Certificate, **Authenticated Origin Pulls**, nftables hanya menerima 80/443 dari IP Cloudflare (set `cloudflare` dari daftar resmi), Bot Fight Mode, WAF rule managed, Rate Limiting rule untuk `/karir/*/lamar/` (mis. 5/menit/IP), *Under Attack Mode* saat diserang, Cache Rules: cache semua GET halaman publik & `/static`, `/media` (edge cache).
- **L7 (banjir HTTP)**: Cloudflare cache menyerap sebagian besar. Sisanya: Nginx micro-cache 60 dtk + `use_stale` (ribuan pengunjung/dtk ke halaman sama = 1 request ke Django), `limit_req` per IP (10/dtk umum, 6/menit lamaran, 10/menit formulir), `limit_conn` 30/IP, timeout pendek anti-slowloris, nftables batas laju koneksi baru + blok sementara.
- **Kapasitas**: halaman GET dilayani dari cache → ribuan req/dtk di 2 vCPU. Yang mahal hanyalah formulir lamaran, dan itu dibatasi 6/jam/IP di aplikasi (Redis), 6/menit di Nginx, + honeypot + cap waktu + ClamAV. gunicorn 4 worker × 2 thread, `--max-requests` agar memori tidak membengkak; bila perlu naikkan vCPU, bukan longgarkan batas.
- **Uji sebelum rilis**: `k6`/`wrk` dari mesin lain terhadap staging (jangan terhadap produksi tanpa pemberitahuan); target awal: beranda ≥ 1.000 req/dtk (cache), lamaran ≥ 20 sukses/dtk tanpa error 5xx; pastikan 429 muncul tepat saat batas terlewati.

### 4.3 Aplikasi (sudah ada, dipertahankan)
CSP tanpa `unsafe-inline`, `nosniff`, `Referrer-Policy`, `no-store` di halaman formulir, token status 192-bit (hash saja yang dicari), honeypot + cap waktu bertanda tangan, idempotensi, kolom pribadi terenkripsi, retensi otomatis (`purge_applicants`), teks pengguna tidak pernah jadi HTML.

## 5. Operasi
- **Pantau**: `/healthz` hanya dari localhost (Nginx), alarm `check_health`, `systemctl status`, `fail2ban-client status`, ukuran `private/` (seharusnya mendekati kosong), umur `.sync_serial` (> 15 menit tanpa bundle baru = alarm).
- **Backup**: DB publik **bukan sumber kebenaran** (konten ada di HRIS; lamaran ditarik tiap 5 menit). Cukup dump harian DB kecil ini + `media/`. Pemulihan terburuk: pasang host baru, `migrate`, tunggu bundle berikutnya.
- **Kunci bocor/host dibobol (runbook)**: (1) matikan host (Cloudflare → *Pause* / hapus DNS), (2) putar `SYNC_SHARED_SECRET`, `DJANGO_SECRET_KEY`, kunci SSH sinkronisasi, (3) bangun host baru dari rilis bersih (jangan memulihkan disk lama), (4) lamaran yang belum ditarik dianggap bocor → beri tahu pelamar sesuai UU PDP. Karena HRIS tidak menyimpan kredensial yang bisa dipakai balik dari host publik (hanya kunci SSH keluar milik HRIS), LAN tetap aman.
- **Pembaruan**: HRIS dulu (`migrate`), lalu rilis publik (`migrate`, restart). Bundle yang dibuat HRIS tanpa field baru tetap diterima publik lama (field tak dikenal diabaikan); urutan terbalik tidak aman.

## 6. Perintah
| Perintah | Dijalankan di | Fungsi |
|---|---|---|
| `publish_site --out DIR [--push user@host:/inbox/]` | HRIS | bangun + dorong bundle |
| `import_site_bundle INBOX` | publik (cron tiap menit) | verifikasi + terapkan |
| `export_applications [--since ISO]` | publik (dipanggil lewat SSH) | keluarkan lamaran (JSON/baris) |
| `pull_applications --ssh U@H --remote-dir DIR` | HRIS | tarik + enkripsi ulang + notifikasi HRD |
| `purge_applicants` | keduanya | retensi |

## 7. Pengujian
`python manage.py test apps.company.test_sync` (9 tes: draf tidak keluar, tanpa penunjuk internal, roundtrip, tanda tangan/replay/path traversal/isi gambar palsu ditolak, lamaran idempoten & dienkripsi ulang, minimisasi, status turun, *withdraw* pelamar tidak tertimpa). CI `public-site`: boot + migrasi + rute internal 404 dengan `settings_public`.

## 8. Keputusan & yang belum dikerjakan (jujur)
1. **Cangkang `core`/`hr` di DB publik**: `Stamped.updated_by`→User, `ApplicationEvent.by`→User, `Job.department`→Department membuat tabel itu ikut dimigrasi (kosong, tanpa data). Fase 2 yang disarankan: ubah FK itu menjadi `PositiveBigIntegerField` (migrasi + sedikit penyesuaian view) agar host publik tidak memuat `apps.hr` sama sekali.
2. **Belum diuji di server sungguhan**: Nginx/nftables/systemd/fail2ban/SSH `command=` ditulis mengikuti praktik umum tetapi belum dijalankan di host Anda, uji di staging, `nginx -t`, `nft -c -f`, `systemd-analyze security wadimor-public`.
3. **Bundle penuh tiap 5 menit** cukup untuk skala ini (puluhan produk, ratusan lamaran). Jika gambar membesar, ganti ke bundle diferensial.
4. **Kontak/pesan masuk** (`ContactMessage`) saat ini tinggal di host publik dan belum ditarik ke HRIS (hanya lamaran). Menyusul dengan pola yang sama (`export_messages`).
5. `pull_applications` memakai `StrictHostKeyChecking=yes`: simpan sidik jari host publik di `known_hosts` HRIS lebih dulu.
