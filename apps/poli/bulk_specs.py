"""Impor massal Poli: master obat dan master diagnosa (putaran 18). Stok TIDAK diimpor: stok hanya berubah lewat kartu stok (stok masuk/penyesuaian)."""
from apps.core.bulk import Spec
from .forms import DiagnosisForm, MedicineForm
from .models import Diagnosis, Medicine

MEDICINE = Spec("obat", "poli", "obat", "/poli/medicines/", Medicine, "code", MedicineForm, ["code", "name", "unit", "min_stock"], ["code", "name", "unit"],
                ["PCT500", "Paracetamol 500 mg", "tablet", "50"], lambda r, c: {"code": r.get("code", ""), "name": r.get("name", ""), "unit": r.get("unit", ""), "min_stock": r.get("min_stock") or 0},
                hint="Stok awal diisi lewat \"Stok masuk\" pada kartu stok obat, bukan lewat impor.")
DIAGNOSIS = Spec("diagnosa", "poli", "diagnosa", "/poli/diagnoses/", Diagnosis, "code", DiagnosisForm, ["code", "name", "category"], ["code", "name"],
                 ["J00", "Nasofaringitis akut (common cold)", "Pernapasan"], lambda r, c: {"code": r.get("code", ""), "name": r.get("name", ""), "category": r.get("category", "")},
                 hint="Cocok untuk memuat daftar ICD-10 dalam jumlah besar.")
