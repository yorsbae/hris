# PROGRESS — HRIS & Poliklinik

Terakhir diperbarui: 6 Oktober 2026 · Acuan tahap: `docs/VISION.md`

## ✅ Sudah dikerjakan

### Putaran 1 — Fondasi & model (Tahap 1–2, 5 sebagian)
- Proyek Django + PostgreSQL (`config/`, konfigurasi via `.env`, logging berputar)
- `core`: User + role, **department scope** (`scope.py`), audit log append-only, notifikasi, rate limit login, log login/logout
- `hr`: departemen, jabatan, shift, karyawan, **riwayat perubahan**, kontrak berantai, pengajuan (`ChangeRequest`) dengan workflow Draft→Executed, pengumuman + status baca (model), tabel absensi mentah/harian (siap fingerprint)
- `poli`: diagnosa, obat, **kartu stok**, rekam medis, resep (stok keluar atomik), rujukan, nomor surat otomatis
- Perintah `check_contracts` (reminder 90/60/30/14/7), skrip `backup.sh` / `restore.sh`

### Putaran 2 — Visi & cetak
- `docs/VISION.md` (ringkasan visi & tahapan)
- Dashboard API per role (`/api/dashboard/`)
- **Surat izin pulang PDF**: A4 portrait, isi separuh atas, garis potong tengah

### Putaran 3 — Antarmuka web (sekarang)
- Login, layout responsif (terang/gelap), halaman Dashboard, halaman Karyawan (cari NIK/nama, filter status, pagination 50/halaman dari server)
- Output di browser memakai `textContent` (aman dari XSS)
- Migrasi database sudah dibuat (`apps/*/migrations/`)
- **Sudah diuji (SQLite, tes otomatis sederhana):** login, scope Admin Departemen (hanya melihat departemennya; ubah ID lintas departemen → 404), HRD ditolak di modul Poli (403), dashboard 3 role, PDF terbuat & tampil benar, audit log tercatat

## ⚠️ Catatan / belum teruji
- Belum diuji di **PostgreSQL** (uji sebelum produksi); `.env` & `ALLOWED_HOSTS` perlu diisi
- Kop surat masih placeholder `PT ........` (`apps/poli/pdf.py`)
- Belum ada test suite permanen (baru smoke test sementara)
- Kolom sensitif (NIK KTP, rekening, NPWP) belum dienkripsi
- Belum ada soft delete di semua model penting

## ⏭️ Akan dikerjakan selanjutnya (berurutan)
1. **Tahap 3 — Workflow UI**: form pengajuan (mutasi, izin, cuti, tukar shift/libur), daftar & detail pengajuan, tombol approve/reject HRD, tanggal efektif
2. **Tahap 4 — Informasi**: notification center (UI), buat/baca pengumuman-peraturan-pemberitahuan, status sudah/belum dibaca, lampiran
3. **Tahap 2 — HR Core UI**: CRUD karyawan (HRD), master departemen/jabatan/shift, kontrak & perpanjangan, upload dokumen, halaman riwayat
4. **Tahap 5 — Poli UI**: form rekam medis (berobat, kecelakaan kerja, kehamilan), master obat & kartu stok, diagnosa, rujukan (+ lampiran)
5. **Keamanan & operasi**: test suite permanen, enkripsi kolom sensitif, soft delete, Nginx + systemd, backup mingguan + salinan ke lokasi lain
6. **Tahap 6 — Absensi**: impor mesin fingerprint, konfirmasi alpa, koreksi, terlambat, lembur, rekap
7. **Tahap 7 — Payroll**: komponen gaji, tunjangan, potongan, BPJS, periode, slip gaji
