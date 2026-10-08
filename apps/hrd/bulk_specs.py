"""Spesifikasi impor massal potongan BPJS (putaran 22, P4). Memakai mesin core.bulk: semua-atau-tidak-sama-sekali, "periksa saja", form yang SAMA dengan input manual.
Kunci gabungan nik|program|periode; baris dengan kunci sama di file → ditolak (dobel). Impor ulang periode yang sama MENGGANTI nilai lama."""
from apps.core.bulk import Spec
from .forms import BpjsDeductionForm

_SCHEMES = {"k": "kes", "kes": "kes", "kesehatan": "kes", "bpjs kesehatan": "kes", "tk": "tk", "ketenagakerjaan": "tk", "jht": "tk", "bpjs tk": "tk"}


def _scheme(v): return _SCHEMES.get((v or "").strip().lower(), (v or "").strip().lower())


def _to_data(r, ctx):
    return {"nik": r.get("nik", ""), "scheme": _scheme(r.get("scheme", "")), "period": r.get("period", ""),
            "employee_amount": r.get("employee_amount", ""), "employer_amount": r.get("employer_amount", ""), "note": r.get("note", "")}


BPJS_DEDUCTION = Spec("potongan-bpjs", "hrd", "potongan BPJS", "/hrd/bpjs/deductions/", None, "key", BpjsDeductionForm,
                      ["nik", "scheme", "period", "employee_amount", "employer_amount", "note"], ["nik", "scheme", "period", "employee_amount"],
                      ["001", "kes", "2026-10", "150000", "600000", ""], _to_data,
                      key_fn=lambda r: f"{r.get('nik', '').strip()}|{_scheme(r.get('scheme', ''))}|{r.get('period', '').strip()}",
                      hint="Program: kes (K / Kesehatan) atau tk (TK / Ketenagakerjaan). Periode YYYY-MM. Nominal rupiah penuh (1500000 atau 1.500.000). "
                           "Baris untuk NIK+program+periode yang sudah ada MENGGANTI nilai lama; hasilnya tercatat di audit (ringkasan).")
