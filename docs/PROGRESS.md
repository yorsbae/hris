# PROGRESS — HRIS & Poliklinik

Terakhir diperbarui: 7 Oktober 2026 (putaran 7) · Acuan tahap, role, dan urutan prioritas: `docs/VISION.md`
Uji: `python manage.py test` (74 tes, SQLite) · Dokumen ini diperbarui di setiap putaran kerja.

## 1. Peta kemajuan terhadap VISION

### Tahapan (VISION → "Tahapan")
| # | Tahap | Backend (model/service) | UI | Catatan |
|---|---|---|---|---|
| 1 | Fondasi: auth, user, role, permission, scope, audit, dashboard | ✅ | ✅ login, dashboard | Manajemen user masih lewat Django admin |
| 2 | HR Core: karyawan, departemen, jabatan, shift, kontrak, histori | ✅ model + histori + enkripsi + soft delete + dokumen | ✅ `/employees/…`, `/master/…`, impor CSV, filter | Sisa: jadwal shift per karyawan (masuk Tahap 3), bagan organisasi, halaman riwayat lintas karyawan |
| 3 | Workflow: mutasi, promosi/demosi, izin, cuti, tukar shift/libur, approval | ✅ | ✅ `/requests/` | Lihat butir sisa di §3 |
| 4 | Informasi: notifikasi, pengumuman, peraturan, read/unread | ✅ | ✅ `/notifications/`, `/announcements/` | Edit/arsip pengumuman belum |
| 5 | Poli: rekam medis, obat, diagnosa, kecelakaan kerja, kehamilan, rujukan, surat izin pulang | ✅ model + stok + PDF surat | ✗ | UI form, master obat, rujukan, kehamilan belum |
| 6 | Absensi | ◐ tabel mentah/harian disiapkan | ✗ | Impor fingerprint, alpa, terlambat, lembur, rekap belum |
| 7 | Payroll | ✗ | ✗ | Sengaja terakhir; data payroll tidak dicampur ke tabel karyawan |

### Modul (VISION → "Modul")
Authentication ✅ · User & Permission ◐ (role + scope ✅, UI user ✗) · Employee ✅ (CRUD, detail per role, soft delete, riwayat, impor CSV, filter) · Organization ◐ (master departemen + induk ✅, bagan organisasi ✗) · Contract ✅ (buat/perpanjang, rantai, reminder) · Shift ◐ (master ✅ UI; jadwal per karyawan ✗, lihat §3) · Mutation ✅ · Leave ✅ (tanpa saldo cuti) · Attendance ✗ · Notification ✅ · Clinic ◐ · Medicine ◐ (kartu stok ✅, UI ✗) · Referral ◐ · Document ✅ (dokumen karyawan; peraturan lewat Pengumuman) · Reporting ✗ · Audit ◐ (tercatat ✅, halaman penelusuran ✗) · Payroll ✗

### Prinsip VISION yang sudah dipenuhi
- **Department scope di backend** (ubah ID di URL → 404): berlaku di karyawan, pengajuan, pengumuman, lampiran, notifikasi (hanya milik sendiri)
- **Histori tidak menimpa data lama**: `EmployeeHistory` ditulis otomatis saat pengajuan dilaksanakan
- **Workflow** Draft → Submitted → Pending → Approved/Rejected → Executed, transisi divalidasi di `services.transition`
- **Audit** user/waktu/IP/module/action/before-after, append-only; akses data sensitif, unduh lampiran, dan semua aksi workflow tercatat
- **Performa**: pagination server-side (50/halaman karyawan & pengajuan, 20 pengumuman, 30 notifikasi), indeks, pencarian NIK/nama
- **Soft delete**: karyawan tidak dihapus fisik (`deleted_at/by/reason`), hilang dari daftar/API/pengajuan, NIK tetap terpakai, dipulihkan Superadmin lewat admin; hapus fisik di admin dimatikan
- **Data sensitif terenkripsi**: NIK KTP, BPJS Kes/TK, NPWP, no. rekening (Fernet, `apps/core/crypto.py`); nilai sensitif **tidak pernah** masuk audit log (hanya penanda "diubah"); akses detail oleh HRD tercatat
- **Performa/Backup**: filter daftar (departemen/jabatan/shift/kontrak) di server; backup kini mencakup `media/` (dokumen karyawan)
- **Cetak**: surat izin pulang A4 portrait, isi separuh atas, garis potong tengah

## 2. Riwayat putaran

### Putaran 1 — Fondasi & model
Proyek Django + PostgreSQL; `core` (User+role, scope, audit, notifikasi, rate limit login); `hr` (departemen, jabatan, shift, karyawan, riwayat, kontrak berantai, `ChangeRequest`, pengumuman + status baca, tabel absensi); `poli` (diagnosa, obat, kartu stok, rekam medis, resep atomik, rujukan, nomor surat); perintah `check_contracts` (90/60/30/14/7 hari); `backup.sh`/`restore.sh`.

### Putaran 2 — Visi & cetak
`docs/VISION.md`; dashboard API per role; surat izin pulang PDF.

### Putaran 3 — Antarmuka dasar
Login, layout responsif (terang/gelap), dashboard, daftar karyawan; output browser memakai `textContent` (anti-XSS); migrasi awal dibuat.

### Putaran 4 — Workflow UI (Tahap 3)
- `/requests/` (filter status/jenis/NIK-nama), `/requests/new/` (form dinamis per jenis), `/requests/<id>/` (detail + aksi)
- 15 jenis pengajuan; validasi server: NIK harus karyawan aktif **dalam scope** ("tidak ditemukan" bila di luar scope), tanggal akhir ≥ mulai, izin/cuti/sakit **tidak boleh tumpang tindih**, tujuan mutasi/jabatan/shift ≠ kondisi sekarang
- Alur: Simpan draft → Ajukan (satu transaksi, HRD dinotifikasi) → HRD Setujui/Tolak (alasan wajib saat tolak) → Laksanakan (update master + riwayat)
- Asumsi: Admin Departemen tidak boleh mengajukan perubahan *status* (`HRD_ONLY_TYPES` di `apps/hr/forms.py`)
- Tes: `apps/hr/tests.py` (11 tes)

### Putaran 5 — Informasi (Tahap 4)
- **Notification center** `/notifications/`: daftar (filter belum dibaca), klik = tandai baca lalu menuju tautan, "tandai semua dibaca"; hanya notifikasi milik sendiri (milik orang lain → 404); tautan eksternal/`//` ditolak (anti open-redirect); badge di header (notifikasi & pengumuman belum dibaca) lewat context processor
- **Pengumuman/peraturan/pemberitahuan** `/announcements/`: HRD membuat, tujuan = semua departemen (termasuk Poli) / departemen tertentu / penerima tertentu; wajib pilih tujuan; daftar dengan filter jenis & belum dibaca; isi di-escape (anti-XSS)
- **Status baca**: tercatat otomatis saat detail dibuka (idempoten); HRD/Superadmin melihat "sudah baca X / Y" dan daftar yang belum membaca; notifikasi terkait ikut ditandai terbaca
- **Lampiran**: PDF/DOCX/XLSX/PNG/JPG, maks 5 MB; disajikan lewat view ber-permission (bukan URL media langsung), unduhan diaudit
- Aturan siapa-melihat-apa terpusat di `apps/hr/info.py` (`visible_to`, `audience`) agar penerima notifikasi, status baca, dan hak akses selalu konsisten
- Migrasi baru: `hr/0002_announcement_attachment`
- Tes: `apps/hr/test_info.py` (10 tes: RBAC, targeting & visibilitas, notifikasi, status baca, lampiran valid/invalid/izin, XSS, notification center, filter)

### Putaran 6 — HR Core + pengamanan data sensitif (Tahap 2)
- **Enkripsi kolom** (`EncryptedTextField`): `nik_ktp, bpjs_kes, bpjs_tk, npwp, bank_account`. Kunci `FIELD_ENCRYPTION_KEY` di `.env` (mendukung beberapa kunci dipisah koma → rotasi). Kunci hilang = data tidak terbaca: **simpan & backup kunci terpisah dari DB**. Nilai kosong tidak dienkripsi; data plaintext lama tetap terbaca sampai `python manage.py encrypt_sensitive [--dry-run]` dijalankan (idempoten). Konsekuensi: kolom ini tidak bisa dicari lewat SQL (pencarian tetap NIK induk/nama)
- **Soft delete** karyawan (`Employee.objects` = hanya yang aktif, `Employee.all_objects` = semua); hapus wajib alasan dan diblokir bila masih ada pengajuan berjalan
- **Karyawan** (HRD/Superadmin menulis): `/employees/new/`, `/employees/<id>/`, `/edit/`, `/delete/`. Validasi: NIK unik (termasuk yang terhapus), NIK KTP 16 digit, format NPWP, tanggal masuk tidak di masa depan, atasan diketik via NIK (bukan dropdown 3.000 baris) dan tidak boleh diri sendiri. Ubah departemen/jabatan/status/shift **menulis `EmployeeHistory`** (tanggal efektif), bukan menimpa diam-diam
- **Detail per role** (ditentukan di view, bukan template): HRD/Superadmin penuh + kontrak + riwayat; Admin Departemen tanpa data sensitif & hanya scope departemennya (404 di luar scope); Poli hanya identitas minimum
- **Kontrak** `/employees/<id>/contracts/new/`: satu kontrak aktif per karyawan; perpanjangan memilih kontrak sebelumnya (rantai `previous`, status lama → `diperpanjang`, mulai harus setelah kontrak lama), tercatat di riwayat & audit
- **Master** `/master/{department,position,shift}/`: kode departemen unik (uppercase), siklus induk ditolak, aturan shift lewat tengah malam divalidasi. Belum ada hapus/nonaktifkan master
- Migrasi baru: `hr/0003_employee_encryption_softdelete`. Tes: `apps/hr/test_core.py` (29 tes: enkripsi di level DB mentah, rotasi kunci, RBAC, scope, riwayat, audit tanpa nilai sensitif, soft delete, kontrak, master, XSS)

### Putaran 7 — Sisa Tahap 2: dokumen, impor CSV, filter ← terbaru
- **Dokumen karyawan** (`EmployeeDocument`, migrasi `hr/0004`): HRD/Superadmin saja (dokumen identitas = sensitif; Admin Dept & Poli → 403, tidak tampil di halaman). Jenis: KTP, KK, ijazah, NPWP, BPJS, kontrak, sertifikat, SP, lainnya. Validasi: ekstensi PDF/DOCX/XLSX/PNG/JPG, maks 5 MB, tidak kosong, dan **isi file harus cocok dengan ekstensi** (magic bytes; `.exe` yang di-rename `.pdf` ditolak). Nama file di disk di-uuid (nama asli hanya untuk unduhan → path traversal tidak berlaku). Unduhan lewat view ber-permission + **diaudit**; hapus = soft delete (alasan wajib, file tetap di disk), hanya POST. Dokumen tidak bisa dibuka lewat URL karyawan lain atau karyawan yang sudah dihapus
- **Impor CSV** `/employees/import/` (HRD): "Periksa saja" lalu "Impor"; **semua-atau-tidak-sama-sekali** dalam satu transaksi; memakai ulang `EmployeeForm` sehingga aturan sama dengan input manual. Departemen via kode, jabatan/shift via nama (harus sudah ada di Master, tidak dibuat otomatis), atasan boleh menunjuk baris lain di file yang sama. Mendukung UTF-8/BOM, koma atau titik-koma, tanggal ISO atau DD/MM/YYYY, maks 5.000 baris/5 MB. Sel berawalan `=`/`@` ditolak (injeksi formula untuk ekspor spreadsheet kelak). Audit hanya mencatat ringkasan (jumlah, nama file), **bukan isi baris**. Template di `/employees/import/template.csv`. Diuji 3.000 baris
- **Filter daftar karyawan**: departemen, jabatan, shift, status kontrak (aktif / habis ≤30 hari / sudah lewat / tanpa kontrak aktif). Filter kontrak **hanya HRD** (role lain mengirimnya pun diabaikan agar info kontrak tidak bocor); Admin Dept tidak bisa keluar dari scope lewat filter departemen; input non-angka diabaikan (sebelumnya `department=abc` menyebabkan 500)
- **Backup**: `scripts/backup.sh` kini juga mengarsipkan `media/` (diverifikasi `tar -t`, rotasi 30 hari); `restore.sh` menerima arsip media sebagai argumen ke-2. Bagian `pg_dump/pg_restore` **belum diuji di sini** (tanpa PostgreSQL)
- Tes: `apps/hr/test_docs_import.py` (24 tes)
- **Pengiriman**: perubahan putaran 6–7 dikirim sebagai satu berkas `tahap2-hr-core.patch` (format `git format-patch`, 3 commit) di atas commit `6aa2fea`. Terapkan: `git am tahap2-hr-core.patch` (mempertahankan commit) atau `git apply tahap2-hr-core.patch` (tanpa commit). Setelah itu: `pip install -r requirements.txt` (menambah `cryptography`), isi `FIELD_ENCRYPTION_KEY`, `python manage.py migrate`, `python manage.py encrypt_sensitive`

## 3. Catatan, risiko, dan utang teknis
- Belum diuji di **PostgreSQL** (wajib sebelum produksi); `.env`, `ALLOWED_HOSTS` perlu diisi; di belakang HTTPS aktifkan `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`
- **Pengajuan `tukar_shift`/`tukar_libur`/`izin`/`cuti` saat "Laksanakan" belum mengubah apa pun** (hanya status). Penyebabnya: tabel jadwal per karyawan (`ShiftAssignment`, disebut di komentar `Shift` tetapi belum ada) dan saldo cuti belum dibuat. Perlu sebelum Absensi
- **Setelah deploy putaran 6**: isi `FIELD_ENCRYPTION_KEY`, backup DB, jalankan `migrate` lalu `encrypt_sensitive`. Soft delete baru untuk karyawan; data penting lain (kontrak, pengumuman, rekam medis) belum
- `alamat` & `telepon` tidak dienkripsi (HRD-only, tetap tercatat saat dilihat); putuskan apakah perlu
- Master (departemen/jabatan/shift) belum bisa dinonaktifkan; departemen/jabatan lama tetap muncul di dropdown
- Dua file `tahap3-workflow-ui.patch` & `tahap4-informasi.patch` di root repo sudah terterap di commit; sebaiknya dihapus dari repo (tidak dihapus otomatis)
- Pengumuman belum bisa diedit/diarsipkan; draft pengajuan belum bisa dibatalkan
- `media/` (dokumen karyawan + lampiran) kini ikut `backup.sh`, tetapi **salinan ke lokasi lain** (rsync/mesin berbeda), backup **mingguan** terpisah, dan **uji restore** nyata masih belum ada (VISION → Backup). `FIELD_ENCRYPTION_KEY` tidak ikut dump: simpan terpisah
- Lampiran pengumuman belum memakai validasi magic bytes seperti dokumen karyawan (hanya ekstensi + ukuran); samakan lewat satu helper
- Impor CSV melakukan beberapa query per baris (memakai ulang form); 3.000 baris wajar untuk impor awal, bukan untuk impor rutin skala besar
- Dokumen karyawan belum bisa diganti/di-versi (unggah baru + hapus lama); belum ada pemindai virus
- Kop surat masih placeholder `PT ........` (`apps/poli/pdf.py`)
- Rate limit login memakai cache default (per proses); untuk multi-worker gunakan cache bersama (mis. Redis/DB cache)

## 4. Rencana berikutnya (diurutkan menurut prioritas VISION: Security › Integritas data › Role › Scope › Approval › Histori › Audit › Backup › Performa)
1. ~~Tahap 2~~ **selesai** (putaran 6–7). Sisa kecil yang ditunda: manajemen user & role lewat UI (kini Django admin), nonaktifkan master, bagan organisasi
2. **Sisa Tahap 3 (berikutnya)**: `ShiftAssignment` + penerapan tukar shift/libur saat dilaksanakan, saldo cuti, batalkan draft, pilih rekan tukar shift, lampiran surat dokter
3. **Tahap 5 — Poli UI**: form rekam medis (berobat, kecelakaan kerja, kehamilan), master obat & kartu stok, diagnosa, rujukan (+ lampiran); data medis tetap tertutup untuk HRD/Admin Departemen
4. **Operasi & keamanan produksi**: Nginx + systemd, backup harian + mingguan + salinan lokasi lain (termasuk `media/`), uji restore, uji PostgreSQL, halaman penelusuran audit, pelaporan/export
5. **Tahap 6 — Absensi**: impor mesin fingerprint, konfirmasi alpa oleh Admin Departemen, koreksi, terlambat, lembur, rekap
6. **Tahap 7 — Payroll**: komponen gaji, tunjangan, potongan, BPJS, periode, slip gaji (tabel terpisah dari karyawan)
