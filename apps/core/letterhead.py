"""Kop dan penanda tangan surat cetak, dari konfigurasi (.env) — bukan ditanam di kode (putaran 20)."""
from django.conf import settings


def info(user=None):
    """Dict kop/penanda tangan. `user` = pengguna Poli pembuat surat (dipakai bila POLI_DOCTOR_NAME kosong)."""
    company = settings.COMPANY_NAME
    doctor = settings.POLI_DOCTOR_NAME or ((user.get_full_name() or user.get_username()) if user is not None else "")
    return {"company": company, "address": settings.COMPANY_ADDRESS, "poli": settings.POLI_NAME or f"POLIKLINIK {company}", "doctor": doctor,
            "hrd_name": settings.HRD_SIGNER_NAME, "hrd_title": settings.HRD_SIGNER_TITLE,
            "pers_name": settings.PERSONALIA_SIGNER_NAME, "pers_title": settings.PERSONALIA_SIGNER_TITLE}
