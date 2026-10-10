"""Spesifikasi impor massal potongan BPJS (putaran 22, P4). Memakai mesin core.bulk: semua-atau-tidak-sama-sekali, "periksa saja", form yang SAMA dengan input manual.
Kunci gabungan nik|program|periode; baris dengan kunci sama di file → ditolak (dobel). Impor ulang periode yang sama MENGGANTI nilai lama."""
from apps.core.bulk import Spec
from .forms import BpjsDeductionForm, SeparationForm, UniformPurchaseForm
from .models import Separation, UniformSize, UniformType

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


# ---------------------------------------------------------------- Seragam (putaran 23, P5)
def _uniform_to_data(r, ctx):
    t = UniformType.objects.filter(name__iexact=(r.get("jenis") or "").strip(), is_active=True).first()
    z = UniformSize.objects.filter(code__iexact=(r.get("ukuran") or "").strip(), is_active=True).first()
    if not t: raise ValueError(f"Jenis seragam '{r.get('jenis', '')}' tidak dikenal/nonaktif (lihat Master Seragam).")
    if not z: raise ValueError(f"Ukuran '{r.get('ukuran', '')}' tidak dikenal/nonaktif (lihat Master Seragam).")
    return {"nik": r.get("nik", ""), "purchase_date": r.get("tanggal", ""), "utype": t.pk, "size": z.pk, "quantity": r.get("jumlah", "") or "1", "note": r.get("catatan", "")}


UNIFORM_PURCHASE = Spec("seragam", "hrd", "pembelian seragam", "/hrd/uniforms/", None, "key", UniformPurchaseForm,
                        ["nik", "tanggal", "jenis", "ukuran", "jumlah", "catatan"], ["nik", "tanggal", "jenis", "ukuran"],
                        ["001", "2026-10-05", "Seragam Kerja", "L", "2", ""], _uniform_to_data,
                        key_fn=lambda r: f"{r.get('nik', '').strip()}|{r.get('tanggal', '').strip()}|{r.get('jenis', '').strip().lower()}|{r.get('ukuran', '').strip().lower()}",
                        hint="Hanya MENAMBAH pembelian (tidak menimpa). Tanggal YYYY-MM-DD, jenis & ukuran harus ada di Master Seragam, karyawan harus aktif. "
                             "Tarif potongan dihitung otomatis dari jenis kelamin karyawan pada tanggal pembelian. Pembelian ganda ditolak.")


# ---------------------------------------------------------------- Karyawan keluar (putaran 38)
_KINDS = {**{k: k for k, _ in Separation.Kind.choices}, **{v.lower(): k for k, v in Separation.Kind.choices},
          "resign": "resign", "mengundurkan diri": "resign", "habis kontrak": "habis_kontrak", "kontrak habis": "habis_kontrak", "phk": "phk", "pensiun": "pensiun", "meninggal": "meninggal"}


def _separation_to_data(r, ctx):
    kind = _KINDS.get((r.get("jenis") or "resign").strip().lower())
    if not kind: raise ValueError(f"Jenis '{r.get('jenis', '')}' tidak dikenal (resign, habis_kontrak, phk, pensiun, meninggal, lainnya).")
    return {"nik": r.get("nik", ""), "kind": kind, "request_date": r.get("tgl_pengajuan", ""), "last_date": r.get("tgl_keluar", ""), "reason": r.get("alasan", ""),
            "tali_asih": r.get("tali_asih", ""), "tali_asih_note": r.get("dasar_tali_asih", "")}


SEPARATION = Spec("karyawan-keluar", "hrd", "karyawan keluar", "/hrd/separations/", None, "key", SeparationForm,
                  ["nik", "jenis", "tgl_pengajuan", "tgl_keluar", "alasan", "tali_asih", "dasar_tali_asih"], ["nik", "tgl_keluar"],
                  ["001", "resign", "2026-09-01", "2026-09-30", "Pindah kota", "1500000", "1 bulan gaji"], _separation_to_data,
                  key_fn=lambda r: r.get("nik", "").strip(),
                  hint="Hanya MENAMBAH catatan (tidak menimpa). Satu NIK satu catatan aktif. Jenis: resign, habis_kontrak, phk, pensiun, meninggal, lainnya. Tanggal YYYY-MM-DD. "
                       "Karyawan yang sudah nonaktif boleh (mengarsipkan data lama); yang masih aktif dinonaktifkan setelah tanggal keluar lewat. Tali asih boleh kosong.")
