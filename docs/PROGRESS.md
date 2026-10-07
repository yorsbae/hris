# PROGRESS — HRIS & Poliklinik

Terakhir diperbarui: 7 Oktober 2026 (putaran 8) · Acuan tahap, role, dan urutan prioritas: `docs/VISION.md`
Uji: `python manage.py test` (151 tes; lulus di **SQLite dan PostgreSQL 16**, 1 tes konkurensi hanya jalan di PostgreSQL) · Dokumen ini diperbarui di setiap putaran kerja.

## 1. Peta kemajuan terhadap VISION

### Tahapan (VISION → "Tahapan")
| # | Tahap | Backend (model/service) | UI | Catatan |
|---|---|---|---|---|
| 1 | Fondasi: auth, user, role, permission, scope, audit, dashboard | ✅ | ✅ login, dashboard | Manajemen user masih lewat Django admin |
| 2 | HR Core: karyawan, departemen, jabatan, shift, kontrak, histori | ✅ model + histori + enkripsi + soft delete + dokumen | ✅ `/employees/…`, `/master/…`, impor CSV, filter | Sisa: jadwal shift per karyawan (masuk Tahap 3), bagan organisasi, halaman riwayat lintas karyawan |
| 2b | **Operasional HRD** (VISION → "Operasional HRD"): bantuan, cuti hamil, kerja harian proyek, katering, status BPJS | ✅ | ✅ `/hrd/…` | Putaran 8. Sisa & utang: lihat §3 |
| 3 | Workflow: mutasi, promosi/demosi, izin, cuti, tukar shift/libur, approval | ✅ | ✅ `/requests/` | Lihat butir sisa di §3 |
| 4 | Informasi: notifikasi, pengumuman, peraturan, read/unread | ✅ | ✅ `/notifications/`, `/announcements/` | Edit/arsip pengumuman belum |
| 5 | Poli: rekam medis, obat, diagnosa, kecelakaan kerja, kehamilan, rujukan, surat izin pulang | ✅ model + stok + PDF surat | ✗ | UI form, master obat, rujukan, kehamilan belum |
| 6 | Absensi | ◐ tabel mentah/harian disiapkan | ✗ | Impor fingerprint, alpa, terlambat, lembur, rekap belum |
| 7 | Payroll | ✗ | ✗ | Sengaja terakhir; data payroll tidak dicampur ke tabel karyawan |

### Modul (VISION → "Modul")
Authentication ✅ · User & Permission ◐ (role + scope ✅, UI user ✗) · Employee ✅ (CRUD, detail per role, soft delete, riwayat, impor CSV, filter) · Organization ◐ (master departemen + induk ✅, bagan organisasi ✗) · Contract ✅ (buat/perpanjang, rantai, reminder) · Shift ◐ (master ✅ UI; jadwal per karyawan ✗, lihat §3) · Mutation ✅ · Leave ✅ (tanpa saldo cuti) · Attendance ✗ · Notification ✅ · Clinic ◐ · Medicine ◐ (kartu stok ✅, UI ✗) · Referral ◐ · Document ✅ (dokumen karyawan; peraturan lewat Pengumuman) · **Aid ✅ · Maternity ✅ (administratif; belum terhubung ke absensi/saldo cuti) · ProjectLog ✅ · Catering ✅ · BPJS Status ✅** · Reporting ✗ · Audit ◐ (tercatat ✅, halaman penelusuran ✗) · Payroll ✗

### Prinsip VISION yang sudah dipenuhi
- **Department scope di backend** (ubah ID di URL → 404): berlaku di karyawan, pengajuan, pengumuman, lampiran, notifikasi (hanya milik sendiri)
- **Histori tidak menimpa data lama**: `EmployeeHistory` ditulis otomatis saat pengajuan dilaksanakan
- **Workflow** Draft → Submitted → Pending → Approved/Rejected → Executed, transisi divalidasi di `services.transition`
- **Audit** user/waktu/IP/module/action/before-after, append-only; akses data sensitif, unduh lampiran, dan semua aksi workflow tercatat
- **Performa**: pagination server-side (50/halaman karyawan & pengajuan, 20 pengumuman, 30 notifikasi), indeks, pencarian NIK/nama
- **Soft delete**: karyawan tidak dihapus fisik (`deleted_at/by/reason`), hilang dari daftar/API/pengajuan, NIK tetap terpakai, dipulihkan Superadmin lewat admin; hapus fisik di admin dimatikan
- **Data sensitif terenkripsi**: NIK KTP, BPJS Kes/TK, NPWP, no. rekening (Fernet, `apps/core/crypto.py`); nilai sensitif **tidak pernah** masuk audit log (hanya penanda "diubah"); akses detail oleh HRD tercatat
- **Performa/Backup**: filter daftar (departemen/jabatan/shift/kontrak) di server; backup kini mencakup `media/` (dokumen karyawan)
- **Operasional HRD hanya HRD/Superadmin**: Admin Departemen & Poli → 403 di semua halaman dan aksi (diuji); data cuti hamil **tanpa data medis**; nomor BPJS tidak tampil di daftar dan tidak masuk audit
- **Teruji di PostgreSQL** (putaran 8): seluruh suite lulus di PostgreSQL 16, bukan hanya SQLite; byte NUL di parameter ditolak 400 terpusat (`RejectNulMiddleware`)
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

### Putaran 7 — Sisa Tahap 2: dokumen, impor CSV, filter
- **Dokumen karyawan** (`EmployeeDocument`, migrasi `hr/0004`): HRD/Superadmin saja (dokumen identitas = sensitif; Admin Dept & Poli → 403, tidak tampil di halaman). Jenis: KTP, KK, ijazah, NPWP, BPJS, kontrak, sertifikat, SP, lainnya. Validasi: ekstensi PDF/DOCX/XLSX/PNG/JPG, maks 5 MB, tidak kosong, dan **isi file harus cocok dengan ekstensi** (magic bytes; `.exe` yang di-rename `.pdf` ditolak). Nama file di disk di-uuid (nama asli hanya untuk unduhan → path traversal tidak berlaku). Unduhan lewat view ber-permission + **diaudit**; hapus = soft delete (alasan wajib, file tetap di disk), hanya POST. Dokumen tidak bisa dibuka lewat URL karyawan lain atau karyawan yang sudah dihapus
- **Impor CSV** `/employees/import/` (HRD): "Periksa saja" lalu "Impor"; **semua-atau-tidak-sama-sekali** dalam satu transaksi; memakai ulang `EmployeeForm` sehingga aturan sama dengan input manual. Departemen via kode, jabatan/shift via nama (harus sudah ada di Master, tidak dibuat otomatis), atasan boleh menunjuk baris lain di file yang sama. Mendukung UTF-8/BOM, koma atau titik-koma, tanggal ISO atau DD/MM/YYYY, maks 5.000 baris/5 MB. Sel berawalan `=`/`@` ditolak (injeksi formula untuk ekspor spreadsheet kelak). Audit hanya mencatat ringkasan (jumlah, nama file), **bukan isi baris**. Template di `/employees/import/template.csv`. Diuji 3.000 baris
- **Filter daftar karyawan**: departemen, jabatan, shift, status kontrak (aktif / habis ≤30 hari / sudah lewat / tanpa kontrak aktif). Filter kontrak **hanya HRD** (role lain mengirimnya pun diabaikan agar info kontrak tidak bocor); Admin Dept tidak bisa keluar dari scope lewat filter departemen; input non-angka diabaikan (sebelumnya `department=abc` menyebabkan 500)
- **Backup**: `scripts/backup.sh` kini juga mengarsipkan `media/` (diverifikasi `tar -t`, rotasi 30 hari); `restore.sh` menerima arsip media sebagai argumen ke-2. Bagian `pg_dump/pg_restore` **belum diuji di sini** (tanpa PostgreSQL)
- Tes: `apps/hr/test_docs_import.py` (24 tes)
- **Pengiriman**: perubahan putaran 6–7 dikirim sebagai satu berkas `tahap2-hr-core.patch` (format `git format-patch`, 3 commit) di atas commit `6aa2fea`. Terapkan: `git am tahap2-hr-core.patch` (mempertahankan commit) atau `git apply tahap2-hr-core.patch` (tanpa commit). Setelah itu: `pip install -r requirements.txt` (menambah `cryptography`), isi `FIELD_ENCRYPTION_KEY`, `python manage.py migrate`, `python manage.py encrypt_sensitive`

### Putaran 8 — Operasional HRD (Tahap 2b) + perbaikan PostgreSQL ← terbaru
Dasar: bagian baru di `docs/VISION.md` ("Operasional HRD"). App baru `apps/hrd` (migrasi `hrd/0001_initial`), pintu masuk `/hrd/` (tautan "Operasional HRD" di header untuk HRD/Superadmin). Semua view dibungkus `hrd_only` (login + role HRD; Superadmin lolos). Aturan bisnis di `apps/hrd/services.py` (atomik, baris dikunci), validasi di `apps/hrd/forms.py`, setiap perubahan masuk audit (module `hrd`).
- **Status BPJS K/TK** `/hrd/bpjs/`: status *saat ini* di `BpjsMembership` (unik per karyawan+program) + histori append-only `BpjsStatusLog` (tidak menimpa; model menolak ubah/hapus). Aktif/nonaktif + tanggal efektif; nonaktif wajib alasan; tanggal tidak boleh sebelum tanggal masuk atau lebih awal dari status saat ini; status sama ditolak. Daftar server-side (50/hal): filter K/TK (aktif/nonaktif/**belum dicatat**), departemen, status karyawan, cari NIK/nama, dan filter **anomali** (karyawan nonaktif tetapi BPJS masih aktif). Nomor BPJS **tidak ditampilkan**, hanya penanda "no. terisi/kosong". Perintah `python manage.py init_bpjs_status [--dry-run]` mengisi awal dari nomor yang sudah ada (aktif sejak tanggal masuk; idempoten; tidak menimpa status yang sudah dicatat). Tautan "Status BPJS" di detail karyawan
- **Bantuan** `/hrd/aids/`: 7 jenis (kematian keluarga, pernikahan, kelahiran, musibah, rawat inap, pendidikan, lainnya). Alur Diajukan → Disetujui/Ditolak → Dibayar (transisi divalidasi di servis; klik ganda/dua HRD tidak memproses dua kali; tolak wajib alasan). Satu kejadian (karyawan+jenis+tanggal) tidak boleh dobel kecuali yang sebelumnya ditolak. Ubah hanya saat Diajukan (NIK terkunci), audit before/after. Total nominal pada hasil filter menyingkirkan yang ditolak
- **Cuti hamil** `/hrd/maternity/`: hanya karyawan **aktif & perempuan**; HPL → mulai/selesai terhitung otomatis (bawaan 45 hari sebelum + 45 hari sesudah, `MATERNITY_DAYS_BEFORE/AFTER` di settings) dan dapat diubah; tumpang tindih dengan cuti hamil lain ditolak (rentang berdampingan boleh; yang dibatalkan tidak dihitung); tumpang tindih dengan pengajuan izin/cuti/sakit hanya **peringatan**. Status aktif/selesai/dibatalkan (selesai: tanggal lahir opsional, tidak boleh masa depan; batal: alasan wajib). Fase tampil: Belum mulai / Sedang cuti / Lewat masa cuti (tandai selesai). Tanpa kolom medis
- **Kerja harian proyek** `/hrd/projects/`: master proyek (kode unik uppercase) + catatan harian "tanggal X mengerjakan apa". Boleh >1 catatan per proyek per hari (regu berbeda). Pekerja diisi lewat NIK (maks 300, hanya karyawan aktif, duplikat dibuang, NIK tak dikenal dilaporkan) → jumlah orang terisi otomatis; tanpa NIK dipakai angka manual. Tanggal tidak boleh masa depan / di luar rentang proyek; catatan baru hanya untuk proyek Aktif (koreksi catatan lama tetap boleh); edit diaudit (before/after termasuk daftar pekerja); filter rentang tanggal
- **Katering** `/hrd/catering/`: pesanan per tanggal + waktu makan (sarapan/siang/malam/snack) + departemen opsional (kosong = umum) dengan **tepak besar** dan **tepak kecil**, harga satuan & vendor opsional. Wajib ≥ 1 tepak; slot (tanggal+waktu+departemen) tidak boleh dobel — termasuk yang "umum" (NULL tidak tertangkap unique DB, jadi dijaga di form), slot bisa dipakai lagi setelah pesanan dibatalkan. Dipesan → Diterima (jumlah diterima dicatat; 0 sah) / Dibatalkan (alasan wajib). Rekap periode (bawaan: bulan berjalan) memakai jumlah diterima bila sudah diterima, mengabaikan yang batal
- **Dashboard HRD**: tambah "Cuti hamil berjalan", "Bantuan menunggu keputusan", "BPJS nonaktif (karyawan aktif)"
- **Temuan dari uji PostgreSQL**: `?q=%00` (byte NUL) membuat PostgreSQL error → **500 juga di endpoint lama** (`/api/employees/`, `/requests/`), tidak terlihat di SQLite. Diperbaiki terpusat: `RejectNulMiddleware` (GET/POST berisi NUL → 400) + tes regresi `apps/core/test_middleware.py`
- **Uji mutasi** (sengaja merusak 9 aturan penting satu per satu): semua tertangkap tes; satu yang awalnya lolos (cek tanggal mundur di servis, karena form menahannya lebih dulu) kini punya tes langsung
- Tes baru: `apps/hrd/test_access.py` (RBAC semua URL, 405 untuk aksi POST-only, 404), `test_bpjs.py` (+konkurensi dua thread di PostgreSQL), `test_aid_maternity.py`, `test_ops.py` (proyek & katering), `apps/core/test_middleware.py` → total 151 tes
- **Pengiriman**: perubahan dikirim sebagai `git format-patch` (2 commit: VISION; modul Operasional HRD + perbaikan NUL + dokumentasi) di atas commit `d1076b9`. Terapkan: `git am putaran8-operasional-hrd.patch`. Lalu: `python manage.py migrate` dan (sekali, bila sudah ada nomor BPJS) `python manage.py init_bpjs_status --dry-run` lalu tanpa `--dry-run`. Tidak ada dependensi baru

## 3. Catatan, risiko, dan utang teknis
- Suite Django kini **lulus di PostgreSQL 16** (putaran 8, di sandbox). Yang **belum** diuji: `scripts/backup.sh`/`restore.sh` (`pg_dump/pg_restore`), Nginx, dan beban nyata 3.000+ karyawan di PostgreSQL. `.env`, `ALLOWED_HOSTS` perlu diisi; di belakang HTTPS aktifkan `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`
- **Pengajuan `tukar_shift`/`tukar_libur`/`izin`/`cuti` saat "Laksanakan" belum mengubah apa pun** (hanya status). Penyebabnya: tabel jadwal per karyawan (`ShiftAssignment`, disebut di komentar `Shift` tetapi belum ada) dan saldo cuti belum dibuat. Perlu sebelum Absensi
- **Setelah deploy putaran 6**: isi `FIELD_ENCRYPTION_KEY`, backup DB, jalankan `migrate` lalu `encrypt_sensitive`. Soft delete baru untuk karyawan; data penting lain (kontrak, pengumuman, rekam medis) belum
- `alamat` & `telepon` tidak dienkripsi (HRD-only, tetap tercatat saat dilihat); putuskan apakah perlu
- Master (departemen/jabatan/shift) belum bisa dinonaktifkan; departemen/jabatan lama tetap muncul di dropdown
- Pengumuman belum bisa diedit/diarsipkan; draft pengajuan belum bisa dibatalkan
- `media/` (dokumen karyawan + lampiran) kini ikut `backup.sh`, tetapi **salinan ke lokasi lain** (rsync/mesin berbeda), backup **mingguan** terpisah, dan **uji restore** nyata masih belum ada (VISION → Backup). `FIELD_ENCRYPTION_KEY` tidak ikut dump: simpan terpisah
- Lampiran pengumuman belum memakai validasi magic bytes seperti dokumen karyawan (hanya ekstensi + ukuran); samakan lewat satu helper
- Impor CSV melakukan beberapa query per baris (memakai ulang form); 3.000 baris wajar untuk impor awal, bukan untuk impor rutin skala besar
- Dokumen karyawan belum bisa diganti/di-versi (unggah baru + hapus lama); belum ada pemindai virus
- **Operasional HRD — utang & batasan yang disengaja** (putaran 8):
  - Cuti hamil belum memengaruhi apa pun di luar halamannya: belum terhubung ke absensi (status "cuti"), saldo cuti, atau tampilan Admin Departemen (mereka belum melihat karyawan sedang cuti hamil). Dikerjakan bersama sisa Tahap 3 + Tahap 6
  - Bantuan: belum ada lampiran bukti, dan pembuat bisa juga menyetujui (pemisahan tugas belum diberlakukan); nominal belum terhubung ke Payroll
  - Catatan harian proyek bisa diubah tetapi **tidak bisa dihapus** (koreksi lewat ubah; jejaknya ada di audit). Belum ada rekap upah/lembur per pekerja per proyek, belum ada ekspor
  - Katering: belum ada master vendor/menu, belum ada ekspor/rekap CSV untuk penagihan vendor, harga diketik per pesanan
  - BPJS: belum ada ubah massal/impor status, belum ada validasi format nomor BPJS (13 digit untuk Kesehatan; format TK berbeda), belum ada iuran/potongan (itu Payroll). Status tidak otomatis nonaktif saat karyawan dinonaktifkan; hanya ditandai lewat filter anomali
  - Semua halaman baru belum punya hapus/arsip (hanya batal/selesai); belum ada halaman penelusuran audit khusus modul ini
- Kop surat masih placeholder `PT ........` (`apps/poli/pdf.py`)
- Rate limit login memakai cache default (per proses); untuk multi-worker gunakan cache bersama (mis. Redis/DB cache)

## 4. Rencana berikutnya (diurutkan menurut prioritas VISION: Security › Integritas data › Role › Scope › Approval › Histori › Audit › Backup › Performa)

### Asumsi putaran 8 yang perlu dikonfirmasi (mudah diubah bila keliru)
1. **"Bantuan"** diartikan bantuan/santunan kepada karyawan (kematian keluarga, pernikahan, dst.) dengan alur persetujuan sederhana. Jika yang dimaksud berbeda (mis. bantuan sembako rutin, atau pinjaman), model `Aid` perlu disesuaikan
2. **"Tepak besar/kecil"** diartikan dua ukuran kotak makan katering, dicatat per tanggal + waktu makan (+ departemen opsional). Jika katering dihitung per orang/menu, atau perlu daftar penerima, strukturnya perlu diperluas
3. **Cuti hamil 45 + 45 hari** dari HPL hanya nilai bawaan; HRD bisa ubah per kasus. Sesuaikan konstanta dengan kebijakan perusahaan (termasuk kasus keguguran/komplikasi)
4. **Kerja harian proyek**: pekerja dicatat per catatan (NIK) atau hanya jumlah orang. Perlu rekap upah/lembur proyek? Itu masuk Payroll
5. **Status BPJS** dicatat manual oleh HRD; sumber kebenaran tetap BPJS resmi, bukan sistem ini
1. ~~Tahap 2~~ **selesai** (putaran 6–7). Sisa kecil yang ditunda: manajemen user & role lewat UI (kini Django admin), nonaktifkan master, bagan organisasi
2. **Sisa Tahap 3 (berikutnya; sekaligus menghubungkan cuti hamil ke jadwal/saldo)**: `ShiftAssignment` + penerapan tukar shift/libur saat dilaksanakan, saldo cuti, batalkan draft, pilih rekan tukar shift, lampiran surat dokter
3. **Tahap 5 — Poli UI**: form rekam medis (berobat, kecelakaan kerja, kehamilan), master obat & kartu stok, diagnosa, rujukan (+ lampiran); data medis tetap tertutup untuk HRD/Admin Departemen
4. **Operasi & keamanan produksi**: Nginx + systemd, backup harian + mingguan + salinan lokasi lain (termasuk `media/`), uji restore, uji PostgreSQL, halaman penelusuran audit, pelaporan/export
5. **Tahap 6 — Absensi**: impor mesin fingerprint, status "cuti hamil" otomatis dari `MaternityLeave`, konfirmasi alpa oleh Admin Departemen, koreksi, terlambat, lembur, rekap
6. **Tahap 7 — Payroll**: komponen gaji, tunjangan, potongan, BPJS (memakai `BpjsMembership`), bantuan (`Aid`), upah proyek (`ProjectDailyLog`), periode, slip gaji (tabel terpisah dari karyawan)
