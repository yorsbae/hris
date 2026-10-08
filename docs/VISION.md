# Visi: Platform HRIS + Absensi + Payroll + Poliklinik — LAN, 3.000+ karyawan

Bukan sekadar CRUD karyawan, tetapi **platform HRIS jangka panjang**. Browser → server LAN → Python (Django) → PostgreSQL.
Database tidak pernah di komputer client.

> **Riwayat dokumen.** Versi ini menggabungkan visi awal dengan *Spesifikasi HRMS & Poliklinik PT X* (role, dashboard, SP, MCU,
> recruitment, meal, laporan/ekspor, dst.). **Dari spesifikasi hanya fitur/kebutuhannya yang diambil; platform tetap Django (tanpa Odoo).**
> Gambar dashboard (`docs/ui-reference/dashboard-hrd.png`) dipakai **hanya sebagai rujukan tampilan** agar UI/UX rapi dan ramah pengguna;
> fitur ditentukan oleh visi ini, bukan oleh isi gambar. Bagian bertanda **[BARU]** berasal dari penggabungan; keputusan dicatat di "Keputusan penyelarasan".

## Role
| Role | Cakupan |
|---|---|
| Superadmin | Semua: user, role, permission, master, konfigurasi sistem, audit, backup, pengaturan perusahaan (nama, logo, alamat, kontak, tahun berjalan) |
| HRD | Dashboard HRD, karyawan, struktur organisasi, departemen, jabatan, grade, status kepegawaian, shift, kontrak, mutasi/promosi, tukar shift, izin/cuti/libur, absensi, **surat peringatan**, BPJS, **recruitment**, informasi, laporan, monitoring pengajuan, **halaman Operasional HRD** (bantuan, cuti hamil, kerja harian proyek, katering/meal, status BPJS) |
| Admin Departemen (±20) | Hanya departemennya: lihat karyawan, konfirmasi absensi, **mengajukan** mutasi/izin/cuti/tukar shift/tukar libur/**administrasi**, memantau status & riwayat pengajuan, terima informasi HRD. Tanpa CRUD master, data sensitif, data medis, tanpa approve, tanpa mengubah BPJS bebas |
| Poli (Medis) | Identitas minimum karyawan + seluruh modul poliklinik (pemeriksaan, rekam medis, diagnosis, tindakan, obat, stok, MCU, laporan medis) |
| Employee | **Bukan role/login.** Data dikelola HRD; pengajuan lewat Admin Departemen **[BARU, dikonfirmasi]** |

## Keamanan
RBAC + **department scope di backend/query** (ubah ID di URL → 404). Data sensitif/medis hanya role berwenang.
Password hash, rate limit login, CSRF, validasi input, session aman, audit log, soft delete untuk data penting.
Data medis wajib berpembatasan akses (karena sensitif). **[BARU]** Hak akses akhirnya berbasis aksi — *View · Create · Edit · Delete · Approve · Reject · Export · Print* —
per role (saat ini masih per role + scope; matriks per aksi dikerjakan bertahap, lihat A7 di PROGRESS).

## Prinsip data
- Jangan menimpa data lama: simpan **histori** departemen, jabatan, grade, lokasi, status, shift, kontrak, organisasi, mutasi, pendidikan, pekerjaan sebelumnya.
- Workflow: `Draft → Submitted → Pending Approval → Approved/Rejected → Executed` (Executed = update master + histori otomatis); `Cancelled` untuk pembatalan.
- Tukar shift/libur: master shift tidak berubah sebelum disetujui (dan tidak pernah berubah oleh tukar; hanya jadwal per tanggal).
- Obat memakai **kartu stok** (masuk/keluar/saldo), obat keluar lewat rekam medis; **[BARU]** tambahan: retur, stock opname, kedaluwarsa/lot.
- Data payroll **tidak dicampur** ke tabel karyawan.
- Audit: user, waktu, IP, module, action, data sebelum/sesudah.
- BPJS adalah bagian data karyawan (master pada profil), **tanpa workflow pengajuan/approval**.

## Shift, jadwal & tukar shift/libur  **[BARU]**
**Master shift** (`/master/shift/`): nama, **kode** (PAGI, SIANG, MALAM, GS-12, GS-14, GS-16), jam masuk/pulang, melewati tengah malam, **GS** (general shift, tidak ikut rotasi kelompok), **aktif** (nonaktif = tidak ditawarkan lagi di form, histori tetap).
**Kelompok rotasi**: pola **2 shift** (A–G; Pagi–Siang) dan pola **3 shift/PACK** (A_pack–G_pack; Pagi–Siang–Malam). Kelompok pola 2 dan pola 3 **berbeda walau hurufnya sama**. Tiap kelompok punya **tabel rotasi mingguan** (hari → shift, atau kosong = libur kelompok). Tabel resmi mengikuti *Aturan Pengaturan Jadwal Shift 2026* (diisi lewat `/admin/` → Kelompok shift & rotasi; belum ada halaman khusus).
**Sumber jadwal dasar karyawan = salah satu**: kelompok rotasi **atau** shift tetap/GS (tidak keduanya).
**Jadwal efektif** pada tanggal D (urutan prioritas): ① penyesuaian hasil tukar yang sudah dilaksanakan (per tanggal) → ② rotasi kelompok → ③ shift tetap/GS (Minggu libur reguler). Master (`Employee.shift`, `shift_group`) **tidak pernah diubah oleh tukar**.

**Tukar shift/libur — dua mode** (satu pengajuan, satu persetujuan HRD, dilaksanakan **bersama atau tidak sama sekali**):
| Jenis | 1 orang (menukar sendiri) | 2 orang (dengan rekan) |
|---|---|---|
| Tukar shift | pindah ke shift lain pada tanggal itu (hari kerja) | pada satu tanggal keduanya sama-sama masuk dengan shift **berbeda** → shift saling ditukar |
| Tukar libur | memindahkan **liburnya sendiri**: tanggal libur → masuk, hari kerja lain → libur | libur **saling ditukar**: pada tanggal 1 pemohon libur & rekan masuk, pada tanggal 2 rekan libur & pemohon masuk → pemohon masuk di shift rekan (tgl 1), rekan masuk di shift pemohon (tgl 2) |
Aturan: tanggal tidak lampau; tidak bentrok dengan tukar lain (baik sebagai pemohon **maupun rekan**) atau penyesuaian yang sudah ada; tidak jatuh pada izin/cuti/sakit (kedua orang); Admin Departemen hanya boleh memilih rekan dari departemennya (HRD lintas departemen); aturan yang sama diperiksa ulang saat **Laksanakan** karena kondisi bisa berubah sejak diajukan.
**Akan dikerjakan:** halaman HRD untuk tabel rotasi & jadwal per departemen/minggu · konfirmasi/notifikasi ke rekan (dan Admin Departemen rekan) · batalkan pelaksanaan (baris pembalik, kedua orang sekaligus) · hari libur nasional/cuti bersama · aturan kebijakan tukar (batas per bulan, jeda minimal antar shift, GS/pola 3 shift ↔ pola 2 shift) · jadwal efektif sebagai sumber Absensi (Tahap 6). Detail & asumsi: `PROGRESS.md` → putaran 17b (A20–A28).

## Performa
Pagination server-side, indeks, search NIK/nama, filter departemen/jabatan/status/shift/kontrak. Jangan kirim 3.000+ data sekaligus.
**[BARU]** Dashboard memakai agregasi di server (hitung/kelompokkan di database, bukan memuat baris), dengan indeks pendukung dan cache singkat bila perlu.

## Backup
Harian + mingguan, retensi, **lokasi berbeda dari server utama**, termasuk file/dokumen, prosedur restore teruji.

## Modul
Authentication · User & Permission · Employee · Organization · Contract · Shift · Mutation · Leave · Attendance ·
Notification · Clinic · Medicine · Referral · Document · Reporting · Audit · Payroll (tetap dalam visi; pengerjaan belakangan) ·
**Aid (bantuan) · Maternity (cuti hamil) · ProjectLog (kerja harian proyek) · Catering/Meal · BPJS Status** (semua di bawah Operasional HRD) ·
**[BARU]** Warning (Surat Peringatan) · Recruitment (+ Career Portal) · MCU · Export · Company/System settings · Holiday/Calendar.

## Rantai masa depan
Karyawan → Jabatan → Status → Kontrak → Shift → Absensi → Lembur → Izin/Cuti → Tunjangan → Potongan → BPJS → Payroll → Slip Gaji
Payroll tetap bagian dari visi walau belum diproses/dikerjakan sekarang, supaya bila scope diperbarui rantai dan struktur datanya sudah searah.

## Tahapan
1. **Fondasi**: auth, user, role, permission, scope, audit, dashboard
2. **HR Core**: karyawan, departemen, jabatan, shift, kontrak, histori. **[BARU]** juga grade, bagian, lokasi kerja, jenis karyawan, status karyawan, company, hari libur & kalender kerja, data keluarga, riwayat pendidikan/pekerjaan.
2b. **Operasional HRD** (halaman khusus akun HRD, lihat bagian di bawah): bantuan, cuti hamil, kerja harian proyek, katering/meal, status BPJS
2c. **[BARU] HR Lanjutan**: Surat Peringatan (SP1–SP3), pengajuan administrasi, BPJS lanjutan (kelas, faskes, JKK/JHT/JKM/JP, iuran, laporan)
3. **Workflow**: mutasi, promosi/demosi, izin, cuti, tukar shift/libur (**1 orang atau 2 orang**), **administrasi**, approval
4. **Informasi**: notifikasi, pengumuman, peraturan, read/unread. **[BARU]** reminder (cuti, SP, kontrak, stok menipis, obat mendekati kedaluwarsa, sinkronisasi absensi)
5. **Poli**: rekam medis, obat, diagnosa, kecelakaan kerja, kehamilan, rujukan, surat izin pulang
5b. **[BARU] Poli Lanjutan**: MCU (jenis, hasil, status kesehatan, follow-up, dokumen), master tindakan medis, stock opname, kedaluwarsa/lot, laporan medis
6. **Absensi**: integrasi mesin **Fingerspot** (impor, sinkronisasi, mapping NIK↔mesin, log/riwayat sinkronisasi), jam masuk/keluar, terlambat, pulang cepat, lembur, rekap (per karyawan/departemen/bulan), monitoring kehadiran
7. **Payroll** — *tetap dalam visi, dikerjakan paling akhir (belum diproses sekarang)*: komponen gaji, tunjangan, potongan, BPJS, periode, slip gaji. Data payroll tetap di tabel terpisah dari karyawan.
8. **[BARU] Laporan & Ekspor**: laporan HR, Poli, Meal; ekspor Excel/PDF/cetak; pencarian global
9. **[BARU] Recruitment**: lowongan, kandidat, lamaran, screening, interview, seleksi, laporan; Career Portal (profil perusahaan, daftar lowongan, apply)

## Operasional HRD (halaman khusus akun HRD)
Satu pintu `/hrd/` dengan lima halaman. **Hanya HRD (dan Superadmin)**; Admin Departemen dan Poli tidak punya akses (403).
Semua perubahan tercatat di audit log; data tidak ditimpa diam-diam (ada histori/status).

1. **Bantuan** — pencatatan bantuan kepada karyawan (mis. kematian keluarga, pernikahan, kelahiran, musibah, rawat inap, pendidikan).
   Alur: Diajukan → Disetujui/Ditolak → Dibayar. Satu kejadian yang sama tidak boleh dicatat dua kali. Nominal disimpan di tabel
   sendiri (bukan di tabel karyawan; selaras dengan prinsip payroll).
2. **Cuti hamil** — jadwal dan status cuti melahirkan: perkiraan lahir (HPL), tanggal mulai/selesai (bawaan dihitung dari HPL dan dapat diubah),
   tanggal lahir sebenarnya, status (aktif/selesai/dibatalkan). Hanya karyawan perempuan; tidak boleh tumpang tindih. **Tidak menyimpan data medis**:
   kehamilan secara klinis tetap milik modul Poli dan tertutup untuk HRD.
3. **Kerja harian proyek** — master proyek dan catatan harian: pada hari X mengerjakan apa, siapa saja yang mengerjakan (atau jumlah orang).
   Catatan adalah rekaman kejadian: tidak boleh tanggal masa depan dan tidak boleh di luar rentang proyek. Disiapkan sebagai sumber data upah/lembur proyek bila Payroll kelak dikerjakan.
4. **Katering / Meal Management** — pesanan makan per tanggal dan waktu makan (opsional per departemen) dalam dua ukuran: **tepak besar** dan **tepak kecil**.
   Mencatat jumlah dipesan, jumlah diterima, vendor, dan harga satuan opsional; rekap per periode untuk penagihan vendor.
   **[BARU]** Waktu distribusi baku: **09:00, 12:00, 18:00, 02:00** (**waktu 02:00 dihitung ke tanggal kerja shift malam**, bukan tanggal kalender berikutnya);
   **master vendor**; kebutuhan dihitung dari jumlah karyawan per departemen/shift/jenis meal; rekap kebutuhan, rekap distribusi, monitoring vendor, laporan per departemen/shift/waktu distribusi.
5. **Status BPJS** — status keanggotaan **BPJS Kesehatan (K)** dan **BPJS Ketenagakerjaan (TK)** per karyawan: **aktif / nonaktif**, tanggal efektif, alasan.
   Setiap perubahan menambah baris histori (tidak menimpa). Nomor BPJS tetap terenkripsi di data karyawan dan tidak ditampilkan di daftar.
   Daftar dapat difilter (aktif / nonaktif / belum dicatat) dan menandai anomali (mis. karyawan nonaktif tetapi BPJS masih aktif).
   **[BARU]** Kelengkapan yang dituju: kelas, faskes, cabang, tanggal kepesertaan, komponen JKK/JHT/JKM/JP, iuran, sinkronisasi, laporan.

## [BARU] Cakupan fungsional dari spesifikasi HRMS
Ringkasan butir spesifikasi yang kini menjadi bagian visi. Status pengerjaan ada di `docs/PROGRESS.md`.

- **Data karyawan**: identitas (NIK karyawan, NIK KTP, nama, foto, jenis kelamin, tempat/tanggal lahir, alamat, HP, email, status pernikahan, pendidikan, agama, kontak darurat);
  pekerjaan (departemen, bagian, jabatan, grade, lokasi kerja, status karyawan, jenis hubungan kerja, tanggal masuk, masa kerja, atasan, shift, aktif/nonaktif);
  tambahan (rekening, data keluarga, dokumen, riwayat pekerjaan/jabatan/departemen/shift/pendidikan/mutasi).
- **Master**: Company, Departemen, Bagian, Jabatan, Grade, Work Location, Jenis Karyawan, Status Karyawan, Shift (nama, jam masuk/pulang, break, toleransi terlambat, hari kerja, shift malam/normal),
  Jenis Cuti (+kuota), Hari Libur/Kalender perusahaan, Jenis Surat, Jenis Pengajuan, Master Obat, Master Diagnosa, Master Tindakan Medis.
- **Cuti & libur**: saldo, pengajuan/approval/reject, riwayat, rekap, monitoring, **kalender cuti**, per departemen/karyawan. Status: Draft, Submitted, Approved, Rejected, Cancelled.
- **Mutasi**: karyawan, departemen & jabatan asal/tujuan, grade, tanggal efektif, alasan, keterangan, **dokumen pendukung**; alur Admin Dept → HRD review → Approve/Reject → data karyawan + histori diperbarui.
- **Surat Peringatan**: nomor SP, karyawan, jenis, tingkat (SP1/SP2/SP3), tanggal, masa berlaku, alasan, keterangan, dokumen; buat/lihat/ubah/**cetak**, riwayat per karyawan, monitoring masa berlaku (reminder).
  Seperti rekam medis, SP sebaiknya tidak ditimpa: koreksi dengan catatan tambahan/pencabutan, bukan edit diam-diam.
- **Recruitment**: lowongan (posisi, departemen, kualifikasi, status), kandidat, lamaran, screening, interview, seleksi, riwayat kandidat, laporan; Career Portal publik.
  *Catatan keamanan*: Career Portal adalah satu-satunya bagian yang menghadap publik — harus dipisah dari aplikasi internal LAN (aplikasi/host terpisah, bukan membuka sistem internal ke internet).
- **Poliklinik**: dashboard poli; data pasien (identitas minimum + nomor BPJS); pemeriksaan (nomor kunjungan, keluhan, riwayat penyakit, tanda vital, pemeriksaan fisik, diagnosis, tindakan, obat, catatan, status Menunggu/Pemeriksaan/Selesai);
  EMR dengan timeline (keluhan → pemeriksaan → diagnosis → tindakan → obat); riwayat kunjungan (filter tanggal/karyawan/departemen/diagnosis/jenis kunjungan); **MCU**; obat & stok (stok minimum, menipis, kedaluwarsa, retur, opname, riwayat, laporan).
- **Absensi & integrasi**: kehadiran, terlambat, pulang cepat, tidak hadir, sakit, izin, cuti, alpha, rekap; Fingerspot (impor, sinkronisasi, mapping NIK, log).
- **Notifikasi**: pengajuan baru/disetujui/ditolak/menunggu, reminder cuti & SP, stok obat menipis, obat mendekati kedaluwarsa, sinkronisasi absensi, notifikasi sistem.
- **Laporan**: HR (karyawan aktif/nonaktif, turnover, mutasi, jabatan, departemen, grade, absensi, cuti, shift, SP, BPJS), Poli (kunjungan, pasien, diagnosis, penyakit, obat, stok, MCU), Meal (jumlah, tepak, per departemen/shift/waktu, vendor).
- **Ekspor**: Excel, PDF, cetak, untuk Employee, Attendance, Shift, Leave, Mutation, SP, BPJS, Medical, Medicine, Stock, Meal, Recruitment.
  *Aturan*: ekspor data sensitif (NIK KTP, rekening, BPJS, medis) hanya untuk role berwenang, tercatat di audit, dan hasilnya tidak memuat nilai sensitif kecuali memang diperlukan.
- **Pencarian global**: NIK, nama, departemen, jabatan (sesuai scope role). Filter: departemen, jabatan, grade, status, shift, tanggal, jenis & status pengajuan.
- **Konfigurasi sistem**: informasi perusahaan, logo, tahun berjalan, kalender kerja, hari libur, serta master di atas.

## [BARU] Rujukan UI/UX
Gambar **`docs/ui-reference/dashboard-hrd.png`** (1586×992) adalah **contoh tampilan untuk Superadmin**. Fungsinya **hanya rujukan visual** agar antarmuka rapi dan ramah pengguna.
Yang ditiru: tata letak, gaya, dan pola komponen. Yang **tidak** mengikat: butir menu, kartu, grafik, dan angka di gambar (482 karyawan, 37 kunjungan, dst. hanyalah contoh).
**Fitur mengikuti visi ini; tampilan menyesuaikan fitur dan peran, bukan sebaliknya.** Sistem harus tetap tahan untuk 3.000+ karyawan.

![Contoh dashboard Superadmin](ui-reference/dashboard-hrd.png)

### Pola visual yang dirujuk
- **Sidebar kiri gelap (navy)**, ±260 px, logo + nama sistem di atas; butir menu berikon garis, butir aktif berlatar biru. Kelompok menu berjudul huruf kapital kecil. Di layar kecil menjadi laci (tombol Menu).
- **Bilah atas putih**: lonceng notifikasi dengan lencana belum dibaca; menu pengguna (inisial/foto, nama, label peran, ganti sandi, keluar).
- **Isi halaman**: breadcrumb, judul besar, sub-judul sambutan, tanggal dan jam di kanan atas; footer tipis (nama sistem + versi, "Internal Use Only · PT X").
- **Kartu KPI** di baris atas (ikon dalam lingkaran lembut, label, angka besar, selisih ↑ hijau / ↓ merah), tiap kartu berupa tautan ke daftar terkait.
- **Kartu grafik** dengan judul berikon + pemilih periode: donat (persentase di tengah, legenda jumlah + persen), garis area, batang berlabel nilai.
- **Daftar ringkas**: "Aktivitas Terbaru" (tabel dengan pill status) dan "Pending Approval" (kartu berikon, pill "Menunggu", tautan "Lihat Semua").
- **Palet**: navy (sidebar) · biru primer (aksen/tombol/tautan) · hijau = berhasil/disetujui/selesai · oranye = menunggu/perhatian · merah = ditolak/bahaya · abu-abu = netral. Kartu putih bersudut membulat, bayangan tipis, sans-serif bersih. Tema gelap tetap tersedia.

### Tampilan menyesuaikan peran
Gambar memperlihatkan Superadmin (melihat semua kelompok menu dan semua kartu). Peran lain memakai pola visual yang sama tetapi **menu dan dashboard hanya berisi yang relevan bagi perannya**:

| Peran | Menu | Dashboard |
|---|---|---|
| Superadmin | Semua kelompok + Pengaturan (Users, Roles/Permissions, Company, Master Data, Audit Log) | Ringkasan HR + pengajuan + (kelak) kehadiran + ringkasan Poli (agregat) + aktivitas sistem |
| HRD | Dashboard · Data Karyawan · Recruitment · Mutasi & Promosi · Shift & Jadwal · Cuti & Libur · Surat Peringatan · BPJS · Operasional HRD · Laporan (+ Absensi bila ada) | Kartu/grafik HR, kontrak yang akan habis, pengajuan menunggu, cuti; **tanpa data poli** |
| Admin Departemen | Dashboard · Data Karyawan Departemen · Pengajuan Mutasi/Shift/Cuti-Libur/Administrasi · Monitoring Pengajuan | **Hanya angka departemennya**: karyawan, status pengajuan miliknya, kehadiran departemen (kelak); tanpa data poli |
| Poli | Dashboard · Data Pasien/Karyawan · Pemeriksaan · Rekam Medis · Obat · Stok Obat · Riwayat Kunjungan · Laporan Medis (+ MCU) | Kunjungan hari ini, pasien menunggu/selesai, statistik penyakit, penggunaan obat, stok menipis; aktivitas medis hanya tampil di sini |

Menyembunyikan menu hanya kenyamanan; izin tetap diperiksa di server.

### Aturan penerapan
1. **Isi dashboard per peran** seperti tabel di atas. Aktivitas/keterangan tidak boleh memuat nilai sensitif (NIK KTP, rekening, nomor BPJS), dan aktivitas medis (nama pasien, keluhan) hanya untuk Poli (dan Superadmin).
2. **Kartu/grafik muncul sesuai fitur yang sudah ada.** Yang bergantung modul belum jadi (mis. kehadiran, alpha, sakit → Tahap 6) menampilkan keadaan kosong yang jujur ("Belum tersedia"), bukan angka karangan.
3. **Grafik tanpa layanan luar**: server LAN mungkin tanpa internet, jadi tanpa CDN — SVG inline buatan sendiri atau pustaka lokal berversi tetap. Gaya mengikuti arsitektur saat ini (inline di `base.html`) kecuali diputuskan lain.
4. **Angka dihitung di server** (agregasi, bukan memuat ribuan baris), mengikuti scope role; setiap kartu menaut ke daftar terfilter yang sama dengan angkanya.
5. **Pembanding ("dari bulan lalu")** hanya ditampilkan bila dapat dihitung andal dari histori; bila tidak, disembunyikan.
6. **Keadaan antarmuka wajib**: memuat (skeleton), kosong, galat dengan "Coba lagi"; teks server lewat `textContent`; aksi berbahaya minta konfirmasi; aksesibilitas (kontras, fokus, label) dipertahankan seperti putaran 12.
7. **Istilah dan ikon konsisten** antara sidebar, judul halaman, dan breadcrumb.

## [BARU] Keputusan penyelarasan (dikonfirmasi pemilik produk)
| # | Hal | Keputusan |
|---|---|---|
| 1 | Platform | **Django + PostgreSQL.** Dari spesifikasi hanya **fitur** yang diambil; bagian Odoo (Community 17, addon `hrms_*`) tidak dipakai |
| 2 | Payroll | **Tetap dalam visi** (Tahap 7, rantai masa depan) walau belum diproses/dikerjakan, agar bila scope diperbarui arahnya tetap sama. Data payroll tidak dicampur ke tabel karyawan |
| 3 | Employee self-service | Tidak ada login karyawan; pengajuan lewat Admin Departemen |
| 4 | Status pengajuan | Alur lengkap visi (`… → Executed`) + `Cancelled`; status spesifikasi adalah himpunan bagiannya |
| 5 | Gambar dashboard | Contoh tampilan **Superadmin**; **hanya rujukan UI/UX** (rapi & ramah pengguna). Peran lain menyesuaikan; fitur menyesuaikan visi |
| 6 | Dashboard per peran | Tiap peran melihat dashboard sesuai perannya (lihat tabel); data medis hanya Poli (dan Superadmin); Admin Dept hanya departemennya |
| 7 | Pengajuan Administrasi | Jenis pengajuan keempat (selain mutasi, tukar shift, cuti/libur). *Masih perlu didefinisikan:* jenis, isian, dan efek saat "Executed" (mis. surat keterangan kerja) |
| 8 | Career Portal | Dipisah dari aplikasi internal (host/aplikasi terpisah) karena aplikasi inti hanya di LAN |
| 9 | Meal 02:00 | Dihitung ke **tanggal kerja shift malam** |
| 10 | Tidak dipakai | WhatsApp, Active Directory, ERP/Finance, Odoo Accounting, workflow pengajuan BPJS |
| 11 | Tukar shift/libur | **Dua mode**: 1 orang (menukar liburnya/shiftnya sendiri) dan 2 orang (dengan rekan; kedua jadwal berubah dalam satu pengajuan). Lihat "Shift, jadwal & tukar shift/libur" |
| 12 | Master shift baru | Kode shift, GS, kelompok rotasi (pola 2 shift A–G / pola 3 shift A_pack–G_pack) dan tabel rotasi mingguan menjadi sumber jadwal dasar. *Tabel rotasi resmi & jam GS-12/14/16 masih perlu dimasukkan dari Aturan Pengaturan Jadwal Shift 2026* |

## Urutan prioritas
Security › Integritas data › Role/permission › Department scope › Approval › Histori › Audit › Backup › Performa › Scalability › Maintainability › Integrasi masa depan.
Jangan mengorbankan struktur database demi CRUD cepat. **[BARU]** Dan jangan mengorbankan keamanan/scope demi tampilan: gambar rujukan hanya soal gaya, bukan izin menampilkan data lintas peran.

## Pencetakan
Dokumen cetak (mis. surat izin pulang, surat rujukan, **[BARU]** surat peringatan): **A4 portrait, isi hanya separuh atas halaman** (garis potong di tengah).
Kop surat memakai nama dan logo perusahaan dari konfigurasi sistem.
