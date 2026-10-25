"""Parameter hitung gaji (putaran 57). Nilai BAWAAN ada di sini (supaya sistem langsung bisa menghitung dan tes bisa mengukur terhadap slip contoh);
nilai yang dipakai = baris `PayParam` berlaku-sejak terbaru ≤ akhir periode, bila ada. Mengubah angka = menambah baris baru bertanggal (riwayat tidak ditimpa).
Angka bawaan diturunkan dari slip contoh Okt-2025 (lihat docs/PROGRESS.md putaran 57, A122–A137) dan WAJIB dikonfirmasi pemilik produk."""
from decimal import Decimal as D

from . import ter_data

# kunci → (label, bawaan, satuan, penjelasan)
PARAMS = {
    "hari_dasar": ("Hari dibayar per bulan penuh", D("30"), "hari",
                   "Karyawan yang bekerja sebulan penuh dibayar sejumlah ini (slip contoh: 30,0), berapa pun jumlah hari kalendernya. Masuk/keluar di tengah bulan: hari kalender bekerja, paling banyak sejumlah ini."),
    "pembagi_jam": ("Pembagi upah per jam", D("173"), "",
                    "Upah per jam = (upah harian × hari dasar) ÷ pembagi. 173 = 1/173 upah sebulan (Kepmenaker 102/2004); cocok dengan slip contoh (11 jam → Rp 161.148)."),
    "lembur_jam1": ("Pengali lembur jam pertama", D("1.5"), "× upah/jam", "Per hari kerja: jam lembur pertama dibayar sekian kali upah per jam (Kepmenaker 102/2004: 1,5)."),
    "lembur_jam2": ("Pengali lembur jam berikutnya", D("2"), "× upah/jam", "Jam lembur kedua dan seterusnya pada hari yang sama (Kepmenaker 102/2004: 2)."),
    "standby_pengali": ("Pengali jam Stand By", D("1"), "× upah/jam", "Stand By (kerja saat waktu istirahat) disetujui HRD dibayar per jam sekian kali upah per jam. Usulan: 1 (belum ada ketentuan)."),
    "tunj_masa_kerja": ("Tunjangan masa kerja per tahun penuh", D("3000"), "Rp / tahun", "Masa kerja penuh pada akhir periode × nilai ini (slip contoh: 4 tahun → Rp 12.000)."),
    "jamsostek_persen": ("Potongan Jamsostek (JHT + JP) karyawan", D("3"), "% gaji pokok", "Slip contoh: Rp 76.030 ≈ 3% × gaji pokok Rp 2.534.426 (dibulatkan ke bawah)."),
    "jamsostek_bulat": ("Pembulatan ke bawah Jamsostek", D("10"), "Rp", "Hasil dibulatkan ke bawah ke kelipatan nilai ini."),
    "bpjs_kes_persen": ("Potongan BPJS Kesehatan karyawan", D("2"), "% gaji pokok", "Slip contoh: Rp 50.688 = 2% × gaji pokok Rp 2.534.426 (dibulatkan ke bawah). Konfirmasi: tarif resmi karyawan 1% — angka slip 2%."),
    "bpjs_kes_bulat": ("Pembulatan ke bawah BPJS Kesehatan", D("1"), "Rp", "Hasil dibulatkan ke bawah ke kelipatan nilai ini."),
    "alfa_pengali": ("Potongan per hari alfa", D("1"), "× upah harian", "Hari alfa terverifikasi (Validasi Kehadiran) × upah harian × nilai ini."),
    "pph21_aktif": ("PPh 21 dipotong di slip", D("0"), "0 = tidak, 1 = ya", "Bila 1: PPh 21 dihitung otomatis (TER Jan-Nov, tarif Pasal 17 di masa Desember/terakhir) dan dipotong dari gaji karyawan. Bawaan 0 sampai Pajak/Finance mengonfirmasi tabel TER & status PTKP karyawan."),
    "pph21_tanpa_npwp": ("Pengali PPh 21 karyawan tanpa NPWP", D("1.2"), "x", "Karyawan tanpa NPWP dikenai tarif 20% lebih tinggi (1,2). Isi 1 bila tidak diberlakukan. NPWP dibaca dari Data Karyawan."),
    "biaya_jabatan_persen": ("Biaya jabatan (pengurang pajak setahun)", D("5"), "% bruto", "Pengurang penghasilan bruto di perhitungan setahun (Desember/masa terakhir). Aturan saat ini 5%; ubah bila peraturan berubah."),
    "biaya_jabatan_maks": ("Biaya jabatan maksimum per bulan", D("500000"), "Rp / bulan", "Batas atas biaya jabatan per bulan bekerja (aturan saat ini Rp 500.000 = Rp 6.000.000 setahun)."),
    **{f"ptkp_{k.lower()}": (f"PTKP setahun {lbl}", D(v[1]), "Rp / tahun", "Penghasilan Tidak Kena Pajak setahun untuk status ini; ubah dengan tanggal berlaku bila peraturan PTKP berubah.") for k, (v, lbl) in {s: (ter_data.STATUS[s], ter_data.STATUS_LABELS[s]) for s in ter_data.STATUS}.items()},
    "sisa_cuti_dibayar": ("Bayar sisa cuti karyawan keluar", D("0"), "0 = tidak, 1 = ya", "Bila 1: saldo cuti tahun terakhir × upah harian dibayar di slip bulan hari terakhir kerja (komponen Sisa Cuti). Bawaan 0: tidak otomatis."),
}


def label(key): return PARAMS[key][0]


def defaults(): return {k: v[1] for k, v in PARAMS.items()}


CHOICES = [(k, v[0]) for k, v in PARAMS.items()]
