"""Impor massal Payroll (putaran 57) lewat mesin core.bulk: semua-atau-tidak-sama-sekali, 'periksa saja', form SAMA dengan input manual.
Menyimpan pada kunci yang sama (NIK+berlaku-sejak / grade+sub-grade+berlaku-sejak / NIK+periode+komponen) MENGGANTI baris lama dengan baris baru (lama dibatalkan 'Diganti nilai baru')."""
from apps.core.bulk import Spec
from .forms import AdjustmentForm, ProfileForm, ScaleForm
from .models import PayComponent


def _profile_data(r, ctx):
    yes = (r.get("uang_makan") or "").strip().lower() in ("1", "y", "ya", "yes", "true", "x")
    return {"nik": r.get("nik", ""), "effective_from": r.get("berlaku_sejak", ""), "grade": r.get("grade", ""), "tier": r.get("subgrade", ""), "wage_override": r.get("upah_harian_khusus", ""),
            "allowance_position": r.get("t_jabatan", ""), "allowance_education": r.get("t_pendidikan", ""), "allowance_special": r.get("t_khusus", ""), "allowance_other": r.get("t_lain", ""),
            "meal_allowance": "on" if yes else "", "note": r.get("keterangan", "")}


PROFILE = Spec("profil-gaji", "payroll", "profil gaji karyawan", "/payroll/profiles/", None, "key", ProfileForm,
               ["nik", "berlaku_sejak", "grade", "subgrade", "upah_harian_khusus", "t_jabatan", "t_pendidikan", "t_khusus", "t_lain", "uang_makan", "keterangan"], ["nik", "berlaku_sejak"],
               ["001", "2025-10-01", "9", "A", "", "0", "0", "0", "0", "tidak", ""], _profile_data,
               key_fn=lambda r: f"{r.get('nik', '').strip()}|{r.get('berlaku_sejak', '').strip()}",
               hint="Isi grade + subgrade (upah mengikuti Skala Gaji Harian) ATAU upah_harian_khusus. Tunjangan per bulan penuh dalam rupiah. uang_makan: ya/tidak. Tanggal YYYY-MM-DD. "
                    "NIK + berlaku_sejak yang sudah ada MENGGANTI profil lama (yang lama dibatalkan, tidak dihapus).")


def _scale_data(r, ctx): return {"grade": r.get("grade", ""), "tier": r.get("subgrade", ""), "daily_wage": r.get("upah_harian", ""), "effective_from": r.get("berlaku_sejak", ""), "note": r.get("keterangan", "")}


SCALE = Spec("skala-gaji", "payroll", "skala gaji harian", "/payroll/scale/", None, "key", ScaleForm, ["grade", "subgrade", "upah_harian", "berlaku_sejak", "keterangan"],
             ["grade", "subgrade", "upah_harian", "berlaku_sejak"], ["9", "A", "84480.87", "2025-01-01", "UMK contoh"], _scale_data,
             key_fn=lambda r: f"{r.get('grade', '').strip()}|{r.get('subgrade', '').strip().upper()}|{r.get('berlaku_sejak', '').strip()}",
             hint="Upah harian boleh berdesimal. Grade + subgrade + berlaku_sejak yang sudah ada MENGGANTI nilai lama (yang lama dibatalkan, tidak dihapus).")


def _adj_data(r, ctx):
    c = PayComponent.objects.filter(kind="manual", active=True, code=(r.get("komponen") or "").strip().lower()).first() or PayComponent.objects.filter(kind="manual", active=True, label__iexact=(r.get("komponen") or "").strip()).first()
    if not c: raise ValueError(f"Komponen '{r.get('komponen', '')}' tidak dikenal. Kode yang boleh: " + ", ".join(PayComponent.objects.filter(kind="manual", active=True).values_list("code", flat=True)))
    return {"nik": r.get("nik", ""), "period": r.get("periode", ""), "component": c.pk, "amount": r.get("nominal", ""), "qty": r.get("jumlah", ""), "note": r.get("keterangan", "")}


def _adj_key(r): return f"{r.get('nik', '').strip()}|{r.get('periode', '').strip()}|{r.get('komponen', '').strip().lower()}"


ADJUSTMENT = Spec("penyesuaian-gaji", "payroll", "penyesuaian gaji", "/payroll/adjustments/", None, "key", AdjustmentForm, ["nik", "periode", "komponen", "nominal", "jumlah", "keterangan"],
                  ["nik", "periode", "komponen", "nominal"], ["001", "2026-10", "insentif", "150000", "", "Insentif target"], _adj_data, key_fn=_adj_key,
                  hint="Komponen = kode atau nama komponen manual (insentif, premi_produksi, koreksi, pot_pinjaman, pot_pengobatan, pot_materai, pot_grade_d, pot_lain, lembur_otomatis). "
                       "NIK + periode + komponen yang sudah ada MENGGANTI nilai lama. Ditolak bila run periode itu sudah diajukan/disetujui/dibayar.")
