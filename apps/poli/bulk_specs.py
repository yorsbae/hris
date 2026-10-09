"""Impor massal Poli: master obat dan master diagnosa (putaran 18). Stok TIDAK diimpor: stok hanya berubah lewat kartu stok (stok masuk/penyesuaian)."""
from apps.core.bulk import Spec
from .forms import DiagnosisForm, MedicineForm, PartnerBillForm
from .models import Diagnosis, Medicine, Partner, PartnerBill

MEDICINE = Spec("obat", "poli", "obat", "/poli/medicines/", Medicine, "code", MedicineForm, ["code", "name", "unit", "min_stock"], ["code", "name", "unit"],
                ["PCT500", "Paracetamol 500 mg", "tablet", "50"], lambda r, c: {"code": r.get("code", ""), "name": r.get("name", ""), "unit": r.get("unit", ""), "min_stock": r.get("min_stock") or 0},
                hint="Stok awal diisi lewat \"Stok masuk\" pada kartu stok obat, bukan lewat impor.")
DIAGNOSIS = Spec("diagnosa", "poli", "diagnosa", "/poli/diagnoses/", Diagnosis, "code", DiagnosisForm, ["code", "name", "category"], ["code", "name"],
                 ["J00", "Nasofaringitis akut (common cold)", "Pernapasan"], lambda r, c: {"code": r.get("code", ""), "name": r.get("name", ""), "category": r.get("category", "")},
                 hint="Cocok untuk memuat daftar ICD-10 dalam jumlah besar.")


# ---------------------------------------------------------------- Tagihan mitra (putaran 24, P6): satu baris = satu tagihan (tanpa rincian)
def _choice(v, choices):
    v = (v or "").strip().lower()
    for code, label in choices:
        if v in (code.lower(), label.lower()): return code
    return v


def _bill_to_data(r, ctx):
    p = Partner.objects.filter(name__iexact=(r.get("mitra") or "").strip(), is_active=True).first()
    if not p: raise ValueError(f"Mitra '{r.get('mitra', '')}' tidak dikenal/nonaktif (lihat Mitra).")
    return {"partner": p.pk, "bill_number": r.get("nomor_tagihan", ""), "bill_date": r.get("tanggal_tagihan", ""), "nik": r.get("nik", ""), "service_date": r.get("tanggal_layanan", ""),
            "service_type": _choice(r.get("jenis_layanan"), PartnerBill.SERVICES), "complaint": r.get("keluhan", ""), "diagnosis_code": r.get("kode_diagnosa", ""),
            "payer": _choice(r.get("penanggung"), PartnerBill.PAYERS), "total_amount": r.get("total", "")}


BILL = Spec("tagihan-mitra", "poli", "tagihan mitra", "/poli/billing/", None, "key", PartnerBillForm,
            ["mitra", "nomor_tagihan", "tanggal_tagihan", "nik", "tanggal_layanan", "jenis_layanan", "kode_diagnosa", "penanggung", "total", "keluhan"],
            ["mitra", "nomor_tagihan", "tanggal_tagihan", "nik", "tanggal_layanan", "total"],
            ["RS Contoh", "INV-001", "2026-10-05", "001", "2026-10-03", "rawat_jalan", "A09", "perusahaan", "450000", ""], _bill_to_data,
            key_fn=lambda r: f"{(r.get('mitra') or '').strip().lower()}|{' '.join((r.get('nomor_tagihan') or '').split()).lower()}",
            hint="Satu baris = satu tagihan (tanpa rincian biaya; rincian diisi lewat form). DATA MEDIS: berkas ini memuat keluhan/diagnosa — perlakukan sebagai rahasia. "
                 "Hanya MENAMBAH; nomor tagihan yang sudah tercatat dari mitra yang sama ditolak. Jenis layanan: rawat_jalan, rawat_inap, lab, obat, kacamata, lainnya. Penanggung: perusahaan, bpjs, karyawan.")
