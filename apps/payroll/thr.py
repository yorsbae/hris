"""THR keagamaan (putaran 61) — hitung usulan lalu masukkan ke Tambahan & Potongan (komponen `thr`) periode gaji yang dipilih.

Aturan (Permenaker 6/2016): masa kerja ≥ 12 bulan penuh → 1 bulan upah; 1–<12 bulan → masa kerja(bulan)/12 × 1 bulan upah; < 1 bulan → tidak berhak.
Upah sebulan = upah harian × hari dasar (parameter) + tunjangan tetap di profil. Dihitung per tanggal hari raya. Hanya USULAN: Payroll meninjau, lalu menekan 'Masukkan ke penyesuaian'."""
from decimal import Decimal as D, ROUND_DOWN

from apps.hr.models import Employee
from . import engine, services
from .models import PayAdjustment, PayComponent


def full_months(join, on):
    m = (on.year - join.year) * 12 + on.month - join.month
    return max(0, m - (1 if on.day < join.day else 0))


def compute(on, period):
    """→ list[dict(emp, months, monthly, factor, amount, existing, note)] untuk karyawan aktif ber-profil pada tanggal `on`."""
    prm = engine.load_params(on); basis = prm["hari_dasar"]; scale = engine.wage_scale(on); profs = engine.current_profiles(on)
    exist = {a.employee_id: a.amount for a in PayAdjustment.objects.filter(period=period, component__code="thr", voided_at__isnull=True)}
    out = []
    for e in Employee.objects.filter(status="aktif", join_date__lte=on).select_related("department").order_by("department__name", "name"):
        p = profs.get(e.pk)
        if p is None: continue
        daily = engine.daily_wage_of(p, scale)
        if daily is None: out.append({"emp": e, "months": full_months(e.join_date, on), "monthly": None, "factor": None, "amount": D(0), "existing": exist.get(e.pk), "note": "Upah harian belum ada (cek skala/profil)"}); continue
        months = full_months(e.join_date, on); monthly = D(daily) * basis + p.allowance_position + p.allowance_education + p.allowance_special + p.allowance_other
        factor = D(0) if months < 1 else min(D(months), D(12)) / 12
        amount = (monthly * factor).quantize(D(1), rounding=ROUND_DOWN)
        out.append({"emp": e, "months": months, "monthly": monthly, "factor": factor, "amount": amount, "existing": exist.get(e.pk), "note": "" if months >= 1 else "Masa kerja < 1 bulan: belum berhak"})
    return out


def push(rows, period, user, on):
    """Simpan baris ber-amount > 0 sebagai penyesuaian `thr`. → (jumlah tersimpan, jumlah dilewati)."""
    comp = PayComponent.objects.get(code="thr"); done = skipped = 0
    for r in rows:
        if r["amount"] <= 0: skipped += 1; continue
        services.save_adjustment(r["emp"], period, comp, r["amount"], None, f"THR {r['months']} bln masa kerja per {on:%d-%m-%Y}", user); done += 1
    return done, skipped
