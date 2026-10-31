"""Kop dan penanda tangan surat cetak, dari konfigurasi (.env) — bukan ditanam di kode (putaran 20)."""
from django.conf import settings

DEFAULT_COMPANY = "PT. SUKOREJO INDAH TEXTILE"


def info(user=None):
    """Dict kop/penanda tangan. `user` = pengguna Poli pembuat surat (dipakai bila POLI_DOCTOR_NAME kosong)."""
    company = DEFAULT_COMPANY if settings.COMPANY_NAME == "PT X" else settings.COMPANY_NAME  # placeholder bawaan → kop asli perusahaan
    doctor = settings.POLI_DOCTOR_NAME or ((user.get_full_name() or user.get_username()) if user is not None else "")
    return {"company": company, "address": settings.COMPANY_ADDRESS, "tagline": settings.COMPANY_TAGLINE, "poli_tagline": settings.POLI_TAGLINE, "city": settings.COMPANY_CITY, "poli": settings.POLI_NAME or f"POLIKLINIK {company}", "doctor": doctor,
            "hrd_unit": settings.HRD_KOP_UNIT, "hrd_name": settings.HRD_SIGNER_NAME, "hrd_title": settings.HRD_SIGNER_TITLE,
            "pers_name": settings.PERSONALIA_SIGNER_NAME, "pers_title": settings.PERSONALIA_SIGNER_TITLE}


KINDS = ("company", "hrd", "poli")


def heading(kind, lh):
    """Judul kop menurut JENIS cetakan — tidak disamaratakan:
    company = surat resmi perusahaan (Surat Peringatan, Paklaring): nama perusahaan + baris usaha (ekspor-impor…) + alamat;
    hrd     = cetakan internal HRD (daftar karyawan dsb.): nama perusahaan + unit HRD + alamat, TANPA baris usaha;
    poli    = dokumen Poliklinik (surat istirahat/rujukan, surat pesanan obat, rekap stok): nama poli + (perusahaan | baris poli) + alamat, TANPA baris usaha.
    → (nama, baris_kedua, alamat)."""
    if kind not in KINDS: raise ValueError(f"Jenis kop tidak dikenal: {kind}")
    addr = lh.get("address") or ""
    if kind == "poli":
        name = lh["poli"]; return name, (lh["company"] if lh["company"].lower() not in name.lower() else lh.get("poli_tagline")) or "", addr   # nama perusahaan selalu tampil di kop poli
    if kind == "hrd": return lh["company"], lh.get("hrd_unit") or "", addr
    return lh["company"], lh.get("tagline") or "", addr
