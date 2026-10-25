"""PPh 21 karyawan tetap/harian bulanan (putaran 60) — fungsi MURNI + pencarian tarif.

Dasar hukum yang diikuti (PP 58/2023, PMK 168/2023, UU HPP):
  • Masa Januari–November : PPh = tarif TER (kategori A/B/C menurut status PTKP) × penghasilan bruto sebulan
  • Masa Desember (atau masa pajak terakhir karyawan yang keluar): hitung ulang setahun dengan tarif Pasal 17, lalu kurangi PPh yang sudah dipotong Jan–Nov
      neto = bruto setahun − biaya jabatan (5%, maks Rp 500.000/bulan = Rp 6.000.000/tahun) − iuran pensiun/JHT karyawan
      PKP  = neto − PTKP (dibulatkan ke bawah ke ribuan penuh) ; tarif 5% ≤ 60 jt · 15% ≤ 250 jt · 25% ≤ 500 jt · 30% ≤ 5 M · 35% di atasnya
  • Tanpa NPWP : tarif lebih tinggi 20% (× 1,2)
Semua angka (biaya jabatan, PTKP, lapisan Pasal 17, tarif TER) adalah MASTER bertanggal; konstanta di sini hanya cadangan bila master kosong.
ASUMSI yang perlu dikonfirmasi Pajak/Finance: bruto = total pendapatan slip (tanpa tali asih/pesangon, yang punya tarif khusus); premi BPJS yang dibayar perusahaan belum ditambahkan ke bruto;
iuran pengurang = potongan Jamsostek karyawan; pembulatan PPh ke rupiah ke bawah."""
from decimal import Decimal as D, ROUND_DOWN

BIAYA_JABATAN_PERSEN = D("5"); BIAYA_JABATAN_MAKS_BULAN = D("500000")
PASAL_17 = ((D("60000000"), D("0.05")), (D("250000000"), D("0.15")), (D("500000000"), D("0.25")), (D("5000000000"), D("0.30")), (None, D("0.35")))


def floor_rp(x): return D(x).quantize(D(1), rounding=ROUND_DOWN)


def ter_rate(table, bruto):
    """table = [(batas_atas|None, persen)] urut naik → persen (Decimal) untuk bruto sebulan."""
    for upper, pct in table:
        if upper is None or bruto <= upper: return D(str(pct))
    return D(0)


def pph_ter(bruto, pct, surcharge=D(1)):
    """PPh masa Jan–Nov = bruto × tarif%. Tanpa NPWP: × surcharge (1,2)."""
    return floor_rp(D(bruto) * D(pct) / 100 * surcharge)


def pasal17(pkp, brackets=None):
    """Pajak setahun atas PKP (progresif). `brackets` = [(batas_atas|None, tarif pecahan)] dari master; kosong → bawaan."""
    pkp, tax, prev = D(pkp), D(0), D(0)
    for upper, rate in (brackets or PASAL_17):
        top = pkp if upper is None else min(pkp, upper)
        if top > prev: tax += (top - prev) * rate
        if upper is None or pkp <= upper: break
        prev = upper
    return floor_rp(tax)


def annual_tax(bruto_year, iuran_year, ptkp, months, surcharge=D(1), bj_persen=None, bj_maks=None, brackets=None):
    """→ dict(biaya_jabatan, neto, pkp, pajak). `months` = jumlah bulan bekerja tahun itu (untuk batas biaya jabatan)."""
    bruto_year, iuran_year = D(bruto_year), D(iuran_year)
    bj = min(bruto_year * D(BIAYA_JABATAN_PERSEN if bj_persen is None else bj_persen) / 100, D(BIAYA_JABATAN_MAKS_BULAN if bj_maks is None else bj_maks) * max(1, months)); bj = floor_rp(bj)
    neto = bruto_year - bj - iuran_year
    pkp = max(D(0), neto - D(ptkp)); pkp = (pkp // 1000) * 1000
    return {"biaya_jabatan": bj, "neto": neto, "pkp": D(pkp), "pajak": floor_rp(pasal17(pkp, brackets) * surcharge)}
