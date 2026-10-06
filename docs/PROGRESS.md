# PROGRESS — HRIS & Poliklinik

Terakhir diperbarui: 6 Oktober 2026 · Acuan tahap, role, dan urutan prioritas: `docs/VISION.md`
Uji: `python manage.py test` (21 tes, SQLite) · Dokumen ini diperbarui di setiap putaran kerja.

## 1. Peta kemajuan terhadap VISION

### Tahapan (VISION → "Tahapan")
| # | Tahap | Backend (model/service) | UI | Catatan |
|---|---|---|---|---|
| 1 | Fondasi: auth, user, role, permission, scope, audit, dashboard | ✅ | ✅ login, dashboard | Manajemen user masih lewat Django admin |
| 2 | HR Core: karyawan, departemen, jabatan, shift, kontrak, histori | ✅ model + histori | ◐ hanya daftar karyawan | CRUD, master, kontrak, dokumen, halaman riwayat belum |
| 3 | Workflow: mutasi, promosi/demosi, izin, cuti, tukar shift/libur, approval | ✅ | ✅ `/requests/` | Lihat butir sisa di §3 |
| 4 | Informasi: notifikasi, pengumuman, peraturan, read/unread | ✅ | ✅ `/notifications/`, `/announcements/` | Edit/arsip pengumuman belum |
| 5 | Poli: rekam medis, obat, diagnosa, kecelakaan kerja, kehamilan, rujukan, surat izin pulang | ✅ model + stok + PDF surat | ✗ | UI form, master obat, rujukan, kehamilan belum |
| 6 | Absensi | ◐ tabel mentah/harian disiapkan | ✗ | Impor fingerprint, alpa, terlambat, lembur, rekap belum |
| 7 | Payroll | ✗ | ✗ | Sengaja terakhir; data payroll tidak dicampur ke tabel karyawan |

### Modul (VISION → "Modul")
Authentication ✅ · User & Permission ◐ (role + scope ✅, UI user ✗) · Employee ◐ · Organization ◐ (model ✅, UI ✗) · Contract ◐ (rantai + reminder ✅, UI ✗) · Shift ◐ (master ✅; jadwal per karyawan ✗, lihat §3) · Mutation ✅ · Leave ✅ (tanpa saldo cuti) · Attendance ✗ · Notification ✅ · Clinic ◐ · Medicine ◐ (kartu stok ✅, UI ✗) · Referral ◐ · Document ✗ · Reporting ✗ · Audit ◐ (tercatat ✅, halaman penelusuran ✗) · Payroll ✗

### Prinsip VISION yang sudah dipenuhi
- **Department scope di backend** (ubah ID di URL → 404): berlaku di karyawan, pengajuan, pengumuman, lampiran, notifikasi (hanya milik sendiri)
- **Histori tidak menimpa data lama**: `EmployeeHistory` ditulis otomatis saat pengajuan dilaksanakan
- **Workflow** Draft → Submitted → Pending → Approved/Rejected → Executed, transisi divalidasi di `services.transition`
- **Audit** user/waktu/IP/module/action/before-after, append-only; akses data sensitif, unduh lampiran, dan semua aksi workflow tercatat
- **Performa**: pagination server-side (50/halaman karyawan & pengajuan, 20 pengumuman, 30 notifikasi), indeks, pencarian NIK/nama
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

### Putaran 5 — Informasi (Tahap 4) ← terbaru
- **Notification center** `/notifications/`: daftar (filter belum dibaca), klik = tandai baca lalu menuju tautan, "tandai semua dibaca"; hanya notifikasi milik sendiri (milik orang lain → 404); tautan eksternal/`//` ditolak (anti open-redirect); badge di header (notifikasi & pengumuman belum dibaca) lewat context processor
- **Pengumuman/peraturan/pemberitahuan** `/announcements/`: HRD membuat, tujuan = semua departemen (termasuk Poli) / departemen tertentu / penerima tertentu; wajib pilih tujuan; daftar dengan filter jenis & belum dibaca; isi di-escape (anti-XSS)
- **Status baca**: tercatat otomatis saat detail dibuka (idempoten); HRD/Superadmin melihat "sudah baca X / Y" dan daftar yang belum membaca; notifikasi terkait ikut ditandai terbaca
- **Lampiran**: PDF/DOCX/XLSX/PNG/JPG, maks 5 MB; disajikan lewat view ber-permission (bukan URL media langsung), unduhan diaudit
- Aturan siapa-melihat-apa terpusat di `apps/hr/info.py` (`visible_to`, `audience`) agar penerima notifikasi, status baca, dan hak akses selalu konsisten
- Migrasi baru: `hr/0002_announcement_attachment`
- Tes: `apps/hr/test_info.py` (10 tes: RBAC, targeting & visibilitas, notifikasi, status baca, lampiran valid/invalid/izin, XSS, notification center, filter)

## 3. Catatan, risiko, dan utang teknis
- Belum diuji di **PostgreSQL** (wajib sebelum produksi); `.env`, `ALLOWED_HOSTS` perlu diisi; di belakang HTTPS aktifkan `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`
- **Pengajuan `tukar_shift`/`tukar_libur`/`izin`/`cuti` saat "Laksanakan" belum mengubah apa pun** (hanya status). Penyebabnya: tabel jadwal per karyawan (`ShiftAssignment`, disebut di komentar `Shift` tetapi belum ada) dan saldo cuti belum dibuat. Perlu sebelum Absensi
- Kolom sensitif (NIK KTP, rekening, NPWP, BPJS) belum dienkripsi; belum ada soft delete (VISION mewajibkan untuk data penting)
- Pengumuman belum bisa diedit/diarsipkan; draft pengajuan belum bisa dibatalkan
- Lampiran tersimpan di `media/` lokal: **harus masuk backup** dan salinan ke lokasi lain (VISION → Backup)
- Kop surat masih placeholder `PT ........` (`apps/poli/pdf.py`)
- Rate limit login memakai cache default (per proses); untuk multi-worker gunakan cache bersama (mis. Redis/DB cache)

## 4. Rencana berikutnya (diurutkan menurut prioritas VISION: Security › Integritas data › Role › Scope › Approval › Histori › Audit › Backup › Performa)
1. **Tahap 2 — HR Core UI + pengamanan data sensitif** (dikerjakan bersamaan, karena CRUD akan menulis kolom sensitif): enkripsi kolom, soft delete, CRUD karyawan (HRD), master departemen/jabatan/shift, kontrak & perpanjangan, halaman riwayat, upload dokumen karyawan (disajikan lewat view ber-permission, ikut audit)
2. **Sisa Tahap 3**: `ShiftAssignment` + penerapan tukar shift/libur saat dilaksanakan, saldo cuti, batalkan draft, pilih rekan tukar shift, lampiran surat dokter
3. **Tahap 5 — Poli UI**: form rekam medis (berobat, kecelakaan kerja, kehamilan), master obat & kartu stok, diagnosa, rujukan (+ lampiran); data medis tetap tertutup untuk HRD/Admin Departemen
4. **Operasi & keamanan produksi**: Nginx + systemd, backup harian + mingguan + salinan lokasi lain (termasuk `media/`), uji restore, uji PostgreSQL, halaman penelusuran audit, pelaporan/export
5. **Tahap 6 — Absensi**: impor mesin fingerprint, konfirmasi alpa oleh Admin Departemen, koreksi, terlambat, lembur, rekap
6. **Tahap 7 — Payroll**: komponen gaji, tunjangan, potongan, BPJS, periode, slip gaji (tabel terpisah dari karyawan)
