"""Alur run gaji dan perubahan master Payroll (putaran 57; alur dibalik di putaran 58: HRD → Payroll). Semua perubahan atomik, baris run dikunci (`select_for_update`), setiap transisi menulis jejak append-only.

  draft (HRD buat) → (serahkan, HRD) diserahkan → (hitung, Payroll) dihitung → (tetapkan, Payroll) ditetapkan → (bayar, Payroll) dibayar [terkunci]
          diserahkan/dihitung → (kembalikan ke HRD, Payroll; alasan wajib) draft      ditetapkan → (buka kembali, Payroll) dihitung
          draft/diserahkan/dihitung/ditetapkan → (batalkan, alasan wajib) dibatalkan   (dibayar tidak dapat dibatalkan)
Pemisahan tugas ada pada peran: HRD menyiapkan & menyerahkan data, Payroll menghitung/menetapkan/membayar; Payroll tidak membuat run dan HRD tidak menghitung atau membayar.
Saat DIBAYAR (satu transaksi): seragam di slip ditandai sudah dipotong periode itu, tali asih ditandai dibayar pada tanggal bayar — gagal satu = batal semua."""
from decimal import Decimal as D

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import Notification, Role, User
from apps.hrd import services as hrd_services
from . import calc, engine
from .models import PayAdjustment, PayComponent, PayParam, PayProfile, PayrollRun, PayrollRunEvent, Payslip, PayslipItem, TaliAsihRule, WageScale


def _need(reason, what="Alasan"):
    reason = (reason or "").strip()
    if not reason: raise ValueError(f"{what} wajib diisi.")
    return reason[:300]


def _event(run, action, user, note=""): PayrollRunEvent.objects.create(run=run, action=action, by=user, note=(note or "")[:300])


def _notify(roles, title, link):
    Notification.objects.bulk_create([Notification(user=u, kind="payroll", title=title[:200], link=link) for u in User.objects.filter(role__in=roles, is_active=True)])


def _locked(pk):
    try: return PayrollRun.objects.select_for_update().get(pk=pk)
    except PayrollRun.DoesNotExist: raise ValueError("Run gaji tidak ditemukan.")


# ---------------------------------------------------------------- run
def create_run(period, user, pay_date=None, note=""):
    period = (period or "").strip()
    if not calc.valid_period(period): raise ValueError("Periode harus berformat YYYY-MM (mis. 2026-10).")
    today = timezone.localdate()
    if period > today.strftime("%Y-%m"): raise ValueError("Periode di masa depan belum dapat digaji.")
    with transaction.atomic():
        if PayrollRun.objects.filter(period=period).exclude(status=PayrollRun.CANCELLED).exists(): raise ValueError(f"Run gaji periode {period} sudah ada (batalkan dulu bila ingin membuat ulang).")
        run = PayrollRun.objects.create(period=period, pay_date=pay_date, note=(note or "")[:300], created_by=user)
        _event(run, "buat", user); return run


def handover_issues(period):
    """Catatan kesiapan data HRD untuk periode (bukan penghalang; dicatat di jejak agar Payroll tahu): validasi kehadiran yang belum diverifikasi."""
    from apps.hr.models import AttendanceCheck
    start, end = calc.period_bounds(period)
    n = AttendanceCheck.objects.filter(date__gte=start, date__lte=end, status__in=("diminta", "dijawab", "dikembalikan")).count()
    return [f"{n} validasi kehadiran periode ini belum diverifikasi HRD."] if n else []


def hand_over_run(pk, user):
    """HRD menyerahkan data periode (kehadiran terverifikasi, BPJS, seragam, karyawan keluar/tali asih) ke Payroll. Titik awal alur: HRD → Payroll."""
    with transaction.atomic():
        run = _locked(pk)
        if run.status != PayrollRun.DRAFT: raise ValueError("Hanya run berstatus 'Draft' yang dapat diserahkan ke Payroll.")
        issues = handover_issues(run.period)
        run.status, run.handed_at, run.handed_by = PayrollRun.HANDED, timezone.now(), user; run.save()
        _event(run, "serahkan", user, " ".join(issues)); _notify((Role.PAYROLL,), f"Data gaji {run.period} diserahkan HRD — siap dihitung", f"/payroll/runs/{run.pk}/"); return run


def calculate_run(pk, user):
    """(Hitung ulang) seluruh slip dari data terkini. Hanya setelah HRD menyerahkan; boleh diulang selama belum ditetapkan."""
    with transaction.atomic():
        run = _locked(pk)
        if run.status == PayrollRun.DRAFT: raise ValueError("Data periode ini belum diserahkan HRD. Minta HRD menyerahkan run ke Payroll lebih dulu.")
        if run.status not in (PayrollRun.HANDED, PayrollRun.CALCULATED): raise ValueError(f"Run berstatus '{run.get_status_display()}' tidak dapat dihitung ulang.")
        engine.persist(run, engine.build(run.period))
        run.status, run.calc_at, run.calc_by = PayrollRun.CALCULATED, timezone.now(), user
        run.save(); _event(run, "hitung", user, f"{run.n_slips} slip")
        return run


def finalize_run(pk, user):
    """Payroll menetapkan hasil hitung (terkunci untuk penyesuaian); berikutnya tinggal dibayar. HRD mendapat pemberitahuan."""
    with transaction.atomic():
        run = _locked(pk)
        if run.status != PayrollRun.CALCULATED: raise ValueError("Hanya run berstatus 'Sudah dihitung' yang dapat ditetapkan.")
        if not run.n_slips: raise ValueError("Run tidak berisi slip. Lengkapi profil gaji lalu hitung ulang.")
        if run.total_net < 0: raise ValueError("Total gaji bersih negatif; periksa slip bertanda sebelum menetapkan.")
        if _stale(run): raise ValueError("Ada perubahan data (penyesuaian/profil/skala/parameter) setelah perhitungan terakhir. Hitung ulang dulu.")
        run.status, run.finalized_at, run.finalized_by = PayrollRun.FINALIZED, timezone.now(), user; run.save()
        _event(run, "tetapkan", user); _notify((Role.HRD,), f"Run gaji {run.period} ditetapkan Payroll ({run.n_slips} slip)", f"/payroll/runs/{run.pk}/"); return run


def reopen_run(pk, user):
    """Payroll membuka kembali run yang sudah ditetapkan (belum dibayar) untuk diperbaiki / dihitung ulang."""
    with transaction.atomic():
        run = _locked(pk)
        if run.status != PayrollRun.FINALIZED: raise ValueError("Hanya run yang sudah ditetapkan (belum dibayar) yang dapat dibuka kembali.")
        run.status, run.finalized_at, run.finalized_by = PayrollRun.CALCULATED, None, None; run.save(); _event(run, "buka_kembali", user); return run


def return_to_hrd(pk, reason, user):
    """Payroll mengembalikan run ke HRD karena data periode kurang/janggal (alasan wajib); HRD memperbaiki lalu menyerahkan ulang."""
    reason = _need(reason)
    with transaction.atomic():
        run = _locked(pk)
        if run.status not in (PayrollRun.HANDED, PayrollRun.CALCULATED): raise ValueError("Hanya run yang sudah diserahkan HRD (belum ditetapkan) yang dapat dikembalikan ke HRD.")
        run.status, run.handed_at, run.handed_by = PayrollRun.DRAFT, None, None; run.save()
        _event(run, "kembalikan", user, reason); _notify((Role.HRD,), f"Run gaji {run.period} dikembalikan Payroll: {reason}", f"/payroll/runs/{run.pk}/"); return run


def pay_run(pk, paid_on, user):
    """Tandai dibayar (terkunci). Menandai seragam dipotong & tali asih dibayar dalam transaksi yang sama."""
    today = timezone.localdate()
    if paid_on is None: raise ValueError("Tanggal bayar wajib diisi.")
    if paid_on > today: raise ValueError("Tanggal bayar tidak boleh di masa depan.")
    with transaction.atomic():
        run = _locked(pk)
        if run.status != PayrollRun.FINALIZED: raise ValueError("Hanya run yang sudah ditetapkan Payroll yang dapat ditandai dibayar.")
        items = PayslipItem.objects.filter(slip__run=run, code__in=("pot_seragam", "tali_asih")).select_related("slip")
        for it in items:
            for ref in it.refs:
                try:
                    if it.code == "pot_seragam": hrd_services.mark_uniform_deducted(ref, run.period, user)
                    else:
                        from apps.hrd.models import Separation
                        s = Separation.objects.get(pk=ref)
                        if s.tali_asih_paid_on: raise ValueError("Tali asih sudah ditandai dibayar di tempat lain.")
                        hrd_services.set_tali_asih(ref, s.tali_asih, s.tali_asih_note, paid_on, user)
                except ValueError as e: raise ValueError(f"Slip {it.slip.nik} ({it.label}): {e} Batalkan run ini lalu buat ulang.")
        run.status, run.paid_at, run.paid_by, run.paid_on = PayrollRun.PAID, timezone.now(), user, paid_on; run.save(); _event(run, "bayar", user, paid_on.isoformat()); return run


def cancel_run(pk, reason, user):
    reason = _need(reason)
    with transaction.atomic():
        run = _locked(pk)
        if run.status == PayrollRun.PAID: raise ValueError("Run yang sudah dibayar tidak dapat dibatalkan; koreksi lewat Penyesuaian di periode berikutnya.")
        if run.status == PayrollRun.CANCELLED: raise ValueError("Run sudah dibatalkan.")
        run.status, run.cancelled_at, run.cancelled_by, run.cancel_reason = PayrollRun.CANCELLED, timezone.now(), user, reason; run.save(); _event(run, "batal", user, reason); return run


def _stale(run):
    """True bila ada master/penyesuaian yang dibuat/dibatalkan setelah perhitungan terakhir (hasil perhitungan mungkin usang)."""
    if not run.calc_at: return True
    start, end = calc.period_bounds(run.period)
    for model, extra in ((PayAdjustment, {"period": run.period}), (PayProfile, {}), (WageScale, {}), (PayParam, {})):
        qs = model.objects.filter(**extra)
        if qs.filter(created_at__gt=run.calc_at).exists() or qs.filter(voided_at__gt=run.calc_at).exists(): return True
    return False


def run_is_stale(run): return run.status == PayrollRun.CALCULATED and _stale(run)


def period_open_for_edit(period):
    """Penyesuaian periode ini hanya boleh diubah bila tidak ada run hidup yang diajukan/disetujui/dibayar."""
    r = PayrollRun.objects.filter(period=period).exclude(status=PayrollRun.CANCELLED).first()
    return r is None or r.status in (PayrollRun.DRAFT, PayrollRun.HANDED, PayrollRun.CALCULATED)


# ---------------------------------------------------------------- master (append-only, batal beralasan)
def void_record(model, pk, reason, user):
    reason = _need(reason, "Alasan pembatalan")
    with transaction.atomic():
        o = model.objects.select_for_update().get(pk=pk)
        if o.voided_at: raise ValueError("Data ini sudah dibatalkan.")
        if model is PayAdjustment and not period_open_for_edit(o.period): raise ValueError("Run gaji periode ini sudah diajukan/disetujui/dibayar; tarik atau batalkan run dulu.")
        o.voided_at, o.voided_by, o.void_reason = timezone.now(), user, reason; o.save(update_fields=["voided_at", "voided_by", "void_reason"]); return o


def save_adjustment(emp, period, comp, amount, qty, note, user):
    """Satu baris aktif per karyawan+periode+komponen: yang lama otomatis dibatalkan ('diganti'), bukan ditimpa."""
    if not calc.valid_period(period): raise ValueError("Periode harus berformat YYYY-MM.")
    if comp.kind != "manual" or not comp.active: raise ValueError("Komponen ini tidak dapat diisi manual.")
    if amount < 0 and not comp.signed: raise ValueError(f"Nominal {comp.label} tidak boleh negatif.")
    if amount == 0: raise ValueError("Nominal 0 tidak perlu dicatat.")
    with transaction.atomic():
        if not period_open_for_edit(period): raise ValueError("Run gaji periode ini sudah diajukan/disetujui/dibayar; tarik atau batalkan run dulu.")
        old = PayAdjustment.objects.select_for_update().filter(employee=emp, period=period, component=comp, voided_at__isnull=True).first()
        if old and old.amount == amount and (old.qty or 0) == (qty or 0) and (old.note or "") == (note or "")[:300]:
            old._unchanged = True  # nilai identik: jangan batalkan + buat baris kembar
            return old
        if old: old.voided_at, old.voided_by, old.void_reason = timezone.now(), user, "Diganti nilai baru"; old.save(update_fields=["voided_at", "voided_by", "void_reason"])
        new = PayAdjustment.objects.create(employee=emp, period=period, component=comp, amount=amount, qty=qty, note=(note or "")[:300], created_by=user)
        new._replaced = old is not None
        return new


# ---------------------------------------------------------------- usulan tali asih
def suggest_tali_asih(sep, on=None):
    """Usulan (bukan keputusan) = min(masa kerja penuh × hari/tahun, batas) × upah harian — dari aturan berlaku-sejak per jenis. → (nominal int, dasar teks) atau (None, alasan)."""
    on = on or sep.last_date
    rule = TaliAsihRule.objects.filter(voided_at__isnull=True, kind=sep.kind, effective_from__lte=on).order_by("-effective_from", "-id").first()
    if not rule: return None, "Belum ada aturan tali asih untuk jenis ini (Payroll → Tali Asih)."
    years = calc.full_years(sep.join_date, sep.last_date)
    if years < rule.min_years: return None, f"Masa kerja {years} tahun < minimum {rule.min_years} tahun."
    prof = engine.current_profiles(sep.last_date, [sep.employee_id]).get(sep.employee_id)
    wage = engine.daily_wage_of(prof, engine.wage_scale(sep.last_date)) if prof else None
    if wage is None: return None, "Karyawan belum punya profil/skala gaji."
    days = rule.days_per_year * years
    if rule.max_days is not None: days = min(days, rule.max_days)
    amt = int(calc.q0(days * wage))
    return amt, f"{calc.fmt_qty(days, 1)} hari upah × Rp {int(calc.q0(wage)):,}".replace(",", ".") + f" (masa kerja {years} th × {calc.fmt_qty(rule.days_per_year, 1)} hari" + (f", maks {calc.fmt_qty(rule.max_days, 0)} hari" if rule.max_days is not None else "") + ")"
