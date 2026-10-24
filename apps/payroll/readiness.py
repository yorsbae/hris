"""Kesiapan data sebelum run gaji dihitung + pembanding dengan periode sebelumnya. Hanya MEMBACA (tidak menulis); dipakai halaman Run Gaji.

readiness(period) → [{level: ok|warn|bad, title, detail, count, url, label}]  — 'bad' = run tidak bisa menghasilkan slip yang benar, 'warn' = bisa dihitung tapi angka mungkin meleset.
comparison(run)   → {prev, rows, new, gone, delta_net, delta_pct, threshold} — slip yang berubah besar dibanding periode sebelumnya."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.hr import periods
from apps.hr.models import AttendanceDaily, Employee, Holiday
from apps.hrd.models import BpjsDeduction, BpjsMembership, UniformPurchase
from . import calc, engine
from .models import PayComponent, PayrollRun, Payslip

D = Decimal


def _c(level, title, detail="", count=0, url="", label=""): return {"level": level, "title": title, "detail": detail, "count": count, "url": url, "label": label}


def _nik_list(niks, n=6): return ", ".join(niks[:n]) + ("…" if len(niks) > n else "")


def readiness(period):
    start, end = calc.data_bounds(period); out = []
    scale = engine.wage_scale(end); profs = engine.current_profiles(end)
    active = list(Employee.objects.filter(status="aktif", join_date__lte=end).order_by("nik").values_list("pk", "nik"))
    # 1. profil gaji
    no_prof = [nik for pk, nik in active if pk not in profs]
    out.append(_c("bad", "Karyawan aktif tanpa Profil Gaji", f"Tidak ikut run: NIK {_nik_list(no_prof)}.", len(no_prof), reverse("pay_profile_import"), "Impor/isi Profil Gaji") if no_prof
               else _c("ok", "Semua karyawan aktif punya Profil Gaji", count=len(active)))
    # 2. skala gaji
    emp_by_pk = dict(active); no_scale = sorted({emp_by_pk[pk] for pk, p in profs.items() if pk in emp_by_pk and p.wage_override is None and (p.grade, p.tier) not in scale})
    out.append(_c("bad", "Grade/tingkat belum ada di Skala Gaji", f"Tidak ikut run: NIK {_nik_list(no_scale)}.", len(no_scale), reverse("pay_scale_new"), "Tambah Skala Gaji") if no_scale
               else _c("ok", "Skala gaji mencakup semua profil"))
    # 3. komponen
    n_comp = PayComponent.objects.filter(active=True).count()
    out.append(_c("ok", "Komponen slip aktif", count=n_comp) if n_comp else _c("bad", "Tidak ada komponen slip aktif", "Slip tidak dapat dibuat.", 0, reverse("admin:payroll_paycomponent_changelist") if _has_admin() else "", "Aktifkan komponen"))
    # 4. absensi diolah sampai akhir periode
    exp_end = min(end, timezone.localdate() - timedelta(days=1)); last = AttendanceDaily.objects.filter(date__gte=start, date__lte=end).order_by("-date").values_list("date", flat=True).first()
    if last is None: out.append(_c("warn", "Absensi periode ini belum diolah", "Lembur, uang makan, hari libur, dan potongan alfa akan Rp 0.", 0, reverse("att_daily"), "Buka Absensi"))
    elif last < exp_end: out.append(_c("warn", f"Rekap absensi baru sampai {last:%d-%m-%Y}", f"Harus sampai {exp_end:%d-%m-%Y}: hari sesudahnya dihitung kosong.", 0, reverse("att_daily"), "Olah absensi"))
    else: out.append(_c("ok", f"Rekap absensi lengkap sampai {last:%d-%m-%Y}"))
    # 5. tanpa scan belum divalidasi
    n_ts = AttendanceDaily.objects.filter(date__gte=start, date__lte=end, status="tanpa_scan", employee__status="aktif").count()
    if n_ts: out.append(_c("warn", "Hari kerja tanpa scan belum divalidasi", "Tidak dihitung hadir (tanpa uang makan) sampai divalidasi sebagai hadir/izin/alfa.", n_ts, reverse("att_daily"), "Validasi Kehadiran"))
    # 6. tutup periode (HRD) = serah-terima data
    closed = periods.closed_for_label(period)
    if closed: out.append(_c("ok", f"Periode ditutup HRD ({periods.fmt_range(closed.start_date, closed.end_date)})", "Data mengikuti rentang tanggal itu.", 0, reverse("pay_periods"), "Lihat periode dari HRD"))
    else: out.append(_c("warn", "HRD belum menutup periode untuk bulan gaji ini", "Rentang data dipakai satu bulan kalender; rekap masih bisa berubah setelah gaji dihitung.", 0, reverse("pay_periods"), "Periode dari HRD"))
    # 7. potongan BPJS
    members = BpjsMembership.objects.filter(status="aktif").values("employee_id").distinct().count()
    n_ded = BpjsDeduction.objects.filter(period=period).values("employee_id").distinct().count()
    if members and not n_ded: out.append(_c("warn", "Potongan BPJS periode ini belum diisi", "Dipakai perhitungan otomatis dari persentase; isi bila BPJS menagih angka pasti.", members, reverse("hrd_bpjs_deductions"), "Isi Potongan BPJS"))
    elif members: out.append(_c("ok", f"Potongan BPJS terisi untuk {n_ded} karyawan"))
    # 7b. seragam (potongan otomatis)
    n_uni = UniformPurchase.objects.filter(voided_at__isnull=True, deduction_status="belum", purchase_date__lte=end).values("employee_id").distinct().count()
    out.append(_c("ok", f"Pembelian seragam: {n_uni} karyawan akan dipotong otomatis" if n_uni else "Tidak ada potongan seragam", "Potongan seragam diambil dari catatan Seragam (HRD) dan ditandai 'sudah dipotong' saat run dibayar." if n_uni else "", n_uni))
    # 8. info hari libur
    hol = list(Holiday.objects.filter(active=True, date__gte=start, date__lte=end))
    if hol:
        duty = [h for h in hol if h.anyone_works]
        out.append(_c("ok", f"{len(hol)} hari libur di periode ini" + (f" ({len(duty)} tetap berangkat)" if duty else ""), "; ".join(f"{h.date:%d} {h.name}" for h in hol[:6]), 0, reverse("att_holidays"), "Atur Hari Libur"))
    return out


def _has_admin():
    try: reverse("admin:payroll_paycomponent_changelist"); return True
    except Exception: return False


def summary(items): return {"bad": sum(1 for i in items if i["level"] == "bad"), "warn": sum(1 for i in items if i["level"] == "warn")}


def comparison(run, threshold=None, min_abs=None, limit=60):
    thr = D(str(threshold if threshold is not None else getattr(settings, "PAYROLL_VARIANCE_PCT", 15))); floor = D(str(min_abs if min_abs is not None else getattr(settings, "PAYROLL_VARIANCE_MIN_RP", 100000)))
    prev = PayrollRun.objects.filter(period__lt=run.period, n_slips__gt=0).exclude(status="dibatalkan").order_by("-period").first()
    if prev is None or not run.n_slips: return None
    cur = {s.employee_id: s for s in run.slips.all()}; old = {s.employee_id: s for s in prev.slips.all()}
    rows, new, gone = [], [], []
    for eid, s in cur.items():
        o = old.get(eid)
        if o is None: new.append(s); continue
        delta = s.net_pay - o.net_pay; pct = (delta / o.net_pay * 100) if o.net_pay else (D(100) if delta else D(0))
        if abs(delta) >= floor and abs(pct) >= thr:
            why = []
            if s.paid_days != o.paid_days: why.append(f"hari dibayar {o.paid_days:g} → {s.paid_days:g}")
            if s.daily_wage != o.daily_wage: why.append("upah harian berubah")
            if s.total_deductions != o.total_deductions: why.append(f"potongan {o.total_deductions:,.0f} → {s.total_deductions:,.0f}".replace(",", "."))
            rows.append({"slip": s, "prev": o.net_pay, "delta": delta, "pct": pct.quantize(D("0.1")), "why": "; ".join(why) or "komponen berubah"})
    gone = [o for eid, o in old.items() if eid not in cur]
    rows.sort(key=lambda r: -abs(r["delta"]))
    tot_prev = prev.total_net or D(0); delta_net = (run.total_net or D(0)) - tot_prev
    return {"prev": prev, "rows": rows[:limit], "n_rows": len(rows), "new": new[:limit], "n_new": len(new), "gone": gone[:limit], "n_gone": len(gone), "delta_net": delta_net,
            "delta_pct": (delta_net / tot_prev * 100).quantize(D("0.1")) if tot_prev else None, "threshold": thr, "floor": floor}
