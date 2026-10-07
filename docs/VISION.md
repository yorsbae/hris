# Visi: Platform HRIS + Absensi + Payroll + Poliklinik (LAN, 3.000+ karyawan)

Bukan sekadar CRUD karyawan, tetapi **platform HRIS jangka panjang**. Browser → server LAN → Python (Django) → PostgreSQL.
Database tidak pernah di komputer client.

## Role
| Role | Cakupan |
|---|---|
| Superadmin | Semua: user, role, permission, master, konfigurasi, audit, backup |
| HRD | Karyawan, organisasi, shift, kontrak, mutasi, izin/cuti, absensi, informasi, laporan, **halaman Operasional HRD** (bantuan, cuti hamil, kerja harian proyek, katering, status BPJS) |
| Admin Departemen (±20) | Hanya departemennya: lihat karyawan, konfirmasi absensi, **mengajukan** mutasi/izin/cuti/tukar shift/tukar libur, terima informasi HRD. Tanpa CRUD master, data sensitif, atau data medis |
| Poli | Identitas minimum karyawan + seluruh modul poliklinik |

## Keamanan
RBAC + **department scope di backend/query** (ubah ID di URL → 404). Data sensitif/medis hanya role berwenang.
Password hash, rate limit login, CSRF, validasi input, session aman, audit log, soft delete untuk data penting.

## Prinsip data
- Jangan menimpa data lama: simpan **histori** departemen, jabatan, status, shift, kontrak, organisasi.
- Workflow: `Draft → Submitted → Pending Approval → Approved/Rejected → Executed` (Executed = update master + histori otomatis).
- Tukar shift/libur: master shift tidak berubah sebelum disetujui.
- Obat memakai **kartu stok** (masuk/keluar/saldo), obat keluar lewat rekam medis.
- Data payroll **tidak dicampur** ke tabel karyawan.
- Audit: user, waktu, IP, module, action, data sebelum/sesudah.

## Performa
Pagination server-side, indeks, search NIK/nama, filter departemen/jabatan/status/shift/kontrak. Jangan kirim 3.000+ data sekaligus.

## Backup
Harian + mingguan, retensi, **lokasi berbeda dari server utama**, termasuk file/dokumen, prosedur restore teruji.

## Modul
Authentication · User & Permission · Employee · Organization · Contract · Shift · Mutation · Leave · Attendance ·
Notification · Clinic · Medicine · Referral · Document · Reporting · Audit · Payroll (future) ·
**Aid (bantuan) · Maternity (cuti hamil) · ProjectLog (kerja harian proyek) · Catering · BPJS Status** (semua di bawah Operasional HRD)

## Rantai masa depan
Karyawan → Jabatan → Status → Kontrak → Shift → Absensi → Lembur → Izin/Cuti → Tunjangan → Potongan → BPJS → Payroll → Slip Gaji

## Tahapan
1. **Fondasi**: auth, user, role, permission, scope, audit, dashboard
2. **HR Core**: karyawan, departemen, jabatan, shift, kontrak, histori
2b. **Operasional HRD** (halaman khusus akun HRD, lihat bagian di bawah): bantuan, cuti hamil, kerja harian proyek, katering, status BPJS
3. **Workflow**: mutasi, promosi/demosi, izin, cuti, tukar shift/libur, approval
4. **Informasi**: notifikasi, pengumuman, peraturan, read/unread
5. **Poli**: rekam medis, obat, diagnosa, kecelakaan kerja, kehamilan, rujukan, surat izin pulang
6. **Absensi**: integrasi mesin, jam masuk/keluar, terlambat, lembur, rekap
7. **Payroll**: komponen gaji, tunjangan, potongan, BPJS, periode, slip gaji

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
   Catatan adalah rekaman kejadian: tidak boleh tanggal masa depan dan tidak boleh di luar rentang proyek. Disiapkan sebagai sumber data upah/lembur proyek di Payroll.
4. **Katering** — pesanan makan per tanggal dan waktu makan (opsional per departemen) dalam dua ukuran: **tepak besar** dan **tepak kecil**.
   Mencatat jumlah dipesan, jumlah diterima, vendor, dan harga satuan opsional; rekap per periode untuk penagihan vendor.
5. **Status BPJS** — status keanggotaan **BPJS Kesehatan (K)** dan **BPJS Ketenagakerjaan (TK)** per karyawan: **aktif / nonaktif**, tanggal efektif, alasan.
   Setiap perubahan menambah baris histori (tidak menimpa). Nomor BPJS tetap terenkripsi di data karyawan dan tidak ditampilkan di daftar.
   Daftar dapat difilter (aktif / nonaktif / belum dicatat) dan menandai anomali (mis. karyawan nonaktif tetapi BPJS masih aktif).

## Urutan prioritas
Security › Integritas data › Role/permission › Department scope › Approval › Histori › Audit › Backup › Performa › Scalability › Maintainability › Integrasi masa depan.
Jangan mengorbankan struktur database demi CRUD cepat.

## Pencetakan
Dokumen cetak (mis. surat izin pulang): **A4 portrait, isi hanya separuh atas halaman** (garis potong di tengah).
