"""Mesin hitung gaji (putaran 57). `build(period)` hanya MEMBACA dan mengembalikan slip-slip (dict) + peringatan; `persist(run, built)` menulisnya.
Jumlah query tetap (bukan per karyawan): profil, skala, Absensi (agregat), lembur per hari, BPJS, seragam, tali asih, penyesuaian — masing-masing satu kueri per kelompok 500 karyawan.

Sumber tiap komponen (urutan baris mengikuti slip contoh):
  Gaji Pokok     = upah harian × hari dibayar (profil → skala/khusus; hari dasar 30 untuk bulan penuh)
  Tunjangan      = profil (jabatan/pendidikan/khusus/lain) + masa kerja (tahun penuh × tarif); dikali hari dibayar ÷ hari dasar
  Overtime       = jam lembur TERBUKTI scan (Absensi) × upah/jam, jam 1 × 1,5 dan berikutnya × 2 per hari (hari libur dikecualikan)
  Hari Libur     = hari hadir di hari libur × upah harian
  Stanby         = menit stand by disetujui ÷ 60 × upah/jam × pengali
  Uang Makan     = hari hadir × tarif (hanya profil bercentang uang makan)
  Sisa Cuti      = saldo cuti × upah harian (opsional, karyawan keluar)
  Tali Asih      = catatan Karyawan Keluar yang ditetapkan dan belum dibayar
  Potongan       = Jamsostek (TK) & BPJS (K): impor/input BPJS bila ada, kalau tidak % × gaji pokok; Seragam: pembelian belum dipotong; Pot. Absensi: hari alfa terverifikasi
  Manual         = PayAdjustment periode itu (insentif, pinjaman, pengobatan, dst.)"""
from collections import defaultdict
from decimal import Decimal as D

from django.db import transaction
from django.db.models import Count, Q, Sum

from apps.hr import leave
from apps.hr.attendance_calc import HOLIDAY_WORK_NOTE
from apps.hr.models import AttendanceDaily, Employee, Shift
from apps.hrd.models import BpjsDeduction, BpjsMembership, Separation, UniformPurchase
from . import calc, params as P, tax, ter_data
from .models import TerRate, EducationAllowance, MealPeriod, MealRate, OvertimeRate, PayAdjustment, PayComponent, PayParam, PayProfile, Payslip, PayslipItem, WageScale

CHUNK = 500


def chunks(seq, n=CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n): yield seq[i:i + n]


# ---------------------------------------------------------------- master efektif
def load_params(on):
    """Nilai parameter berlaku pada tanggal `on`: bawaan, ditimpa baris PayParam tidak batal terbaru dengan effective_from ≤ on."""
    out = P.defaults()
    for r in PayParam.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("effective_from", "id"): out[r.key] = r.value
    return out


def current_profiles(on, employee_ids=None):
    """{employee_id: PayProfile} — baris tidak batal dengan effective_from terbesar ≤ on."""
    qs = PayProfile.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("employee_id", "-effective_from", "-id")
    if employee_ids is not None: qs = qs.filter(employee_id__in=list(employee_ids))
    out = {}
    for p in qs: out.setdefault(p.employee_id, p)
    return out


def wage_scale(on):
    """{(grade, tier): upah harian} berlaku pada `on`."""
    out = {}
    for w in WageScale.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("effective_from", "id"): out[(w.grade, w.tier)] = w.daily_wage
    return out


def daily_wage_of(profile, scale):
    if profile.wage_override is not None: return profile.wage_override
    return scale.get((profile.grade, profile.tier))


def meal_rates(on):
    """{(pay_type, shift_id|None): Rp/hari} berlaku pada `on` (baris tidak batal terbaru per kunci)."""
    out = {}
    for r in MealRate.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("effective_from", "id"): out[(r.pay_type, r.shift_id)] = r.amount
    return out


def meal_rate_for(rates, pay_type, shift_id):
    """Tarif shift itu bila ada barisnya, kalau tidak tarif umum tipe gaji itu, kalau tidak 0."""
    if (pay_type, shift_id) in rates: return rates[(pay_type, shift_id)]
    return rates.get((pay_type, None), D(0))


def overtime_rates(on):
    """{(jenis, pay_type): Rp/jam} berlaku pada `on`; jenis = lembur | standby."""
    out = {}
    for r in OvertimeRate.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("effective_from", "id"): out[(r.kind, r.pay_type)] = r.amount
    return out


def education_allowances(on):
    """{PENDIDIKAN huruf besar: Rp/bulan} standar berlaku pada `on`."""
    out = {}
    for r in EducationAllowance.objects.filter(voided_at__isnull=True, effective_from__lte=on).order_by("effective_from", "id"): out[r.education.strip().upper()] = r.amount
    return out


def meal_windows(start, end):
    """Potongan periode uang makan (tidak batal) yang beririsan dengan [start, end] → [(awal, akhir)]."""
    return [(max(m.start_date, start), min(m.end_date, end)) for m in MealPeriod.objects.filter(voided_at__isnull=True, start_date__lte=end, end_date__gte=start)]


def _meal_days(ids, windows):
    """{employee_id: {shift_id|None: jumlah hari hadir di dalam periode uang makan}}."""
    out = defaultdict(lambda: defaultdict(int))
    if not windows: return out
    cond = Q()
    for a, b in windows: cond |= Q(date__gte=a, date__lte=b)
    for part in chunks(ids):
        for eid, sid in AttendanceDaily.objects.filter(cond, employee_id__in=part, status="hadir").values_list("employee_id", "shift_id"): out[eid][sid] += 1
    return out


# ---------------------------------------------------------------- pembacaan data periode
def _attendance(ids, start, end):
    agg, ot = {}, defaultdict(list)
    for part in chunks(ids):
        for r in (AttendanceDaily.objects.filter(date__gte=start, date__lte=end, employee_id__in=part).values("employee_id")
                  .annotate(rows=Count("id"), hadir=Count("id", filter=Q(status="hadir")), alfa=Count("id", filter=Q(status="alfa")),
                            tanpa_scan=Count("id", filter=Q(status="tanpa_scan")), tidak_lengkap=Count("id", filter=Q(incomplete=True, status="hadir")),
                            konflik=Count("id", filter=~Q(conflict="")), standby=Sum("standby_minutes"),
                            libur_kerja=Count("id", filter=Q(status="hadir", notes__startswith=HOLIDAY_WORK_NOTE)))):
            agg[r["employee_id"]] = r
        for eid, m in (AttendanceDaily.objects.filter(date__gte=start, date__lte=end, employee_id__in=part, overtime_minutes__gt=0, status="hadir")
                       .exclude(notes__startswith=HOLIDAY_WORK_NOTE).values_list("employee_id", "overtime_minutes")): ot[eid].append(m)
    return agg, ot


def _bpjs(ids, period):
    ded = {}
    for part in chunks(ids):
        for r in BpjsDeduction.objects.filter(period=period, employee_id__in=part): ded[(r.employee_id, r.scheme)] = r.employee_amount
    off = {(r.employee_id, r.scheme) for part in chunks(ids) for r in BpjsMembership.objects.filter(employee_id__in=part, status="nonaktif")}
    return ded, off


def _uniform(ids, end):
    out = defaultdict(lambda: [D(0), [], 0])
    for part in chunks(ids):
        for p in UniformPurchase.objects.filter(employee_id__in=part, voided_at__isnull=True, deduction_status="belum", purchase_date__lte=end):
            o = out[p.employee_id]; o[0] += p.deduction_amount; o[1].append(p.pk); o[2] += p.quantity
    return out


def _adjustments(ids, period):
    out = defaultdict(list)
    for part in chunks(ids):
        for a in PayAdjustment.objects.filter(period=period, employee_id__in=part, voided_at__isnull=True).select_related("component"): out[a.employee_id].append(a)
    return out


def pending_tali_asih(end):
    """{employee_id: Separation} — tali asih ditetapkan (> 0), belum dibayar, hari keluar ≤ akhir periode."""
    return {s.employee_id: s for s in Separation.objects.filter(voided_at__isnull=True, tali_asih__gt=0, tali_asih_paid_on__isnull=True, last_date__lte=end)}


# ---------------------------------------------------------------- PPh 21 (putaran 60)
def ter_tables(on):
    """{kategori: [(batas_atas|None, persen)]} berlaku pada `on` (set baris berlaku-sejak terbaru ≤ on per kategori); kosong → data resmi bawaan."""
    out = {}
    for cat in ("A", "B", "C"):
        eff = TerRate.objects.filter(voided_at__isnull=True, category=cat, effective_from__lte=on).order_by("-effective_from").values_list("effective_from", flat=True).first()
        if eff is None: out[cat] = ter_data.TABLES[cat]; continue
        rows = list(TerRate.objects.filter(voided_at__isnull=True, category=cat, effective_from=eff).values_list("upper", "percent"))
        out[cat] = sorted(rows, key=lambda r: (r[0] is None, r[0] or 0))
    return out


def ptkp_label(pf): return pf.ptkp_status or "TK0"


def _ytd(emp_ids, period):
    """{employee_id: dict(earn, pph, iuran, n)} dari slip run lain (bukan batal) tahun yang sama sebelum `period`: dasar penyelesaian PPh 21 masa Desember/terakhir."""
    from django.db.models import Count
    out = {}
    lo = f"{period[:4]}-01"
    for part in chunks(emp_ids):
        base = Payslip.objects.filter(employee_id__in=part, run__period__gte=lo, run__period__lt=period).exclude(run__status="dibatalkan")
        for r in base.values("employee_id").annotate(n=Count("id")): out[r["employee_id"]] = {"earn": D(0), "pph": D(0), "iuran": D(0), "n": r["n"]}
        for r in (PayslipItem.objects.filter(slip__in=base).values("slip__employee_id")
                  .annotate(earn=Sum("amount", filter=~Q(section="potongan") & ~Q(code="tali_asih")), pph=Sum("amount", filter=Q(code="pot_pph21")), iuran=Sum("amount", filter=Q(code="pot_jamsostek")))):
            o = out.setdefault(r["slip__employee_id"], {"earn": D(0), "pph": D(0), "iuran": D(0), "n": 0}); o["earn"], o["pph"], o["iuran"] = r["earn"] or D(0), r["pph"] or D(0), r["iuran"] or D(0)
    return out


# ---------------------------------------------------------------- inti
def build(period):
    """→ {"slips": [dict], "warnings": [str], "params": {kunci: str}}. Tidak menulis apa pun."""
    start, end = calc.data_bounds(period)   # putaran 59: rentang yang ditutup HRD (bisa bukan 1–akhir bulan)
    prm = load_params(end); basis = prm["hari_dasar"]
    scale = wage_scale(end); comps = {c.code: c for c in PayComponent.objects.filter(active=True)}
    seps = {s.employee_id: s for s in Separation.objects.filter(voided_at__isnull=True)}
    tali = pending_tali_asih(end)
    profs = current_profiles(end)
    mrates, orates, windows, edu = meal_rates(end), overtime_rates(end), meal_windows(start, end), education_allowances(end)
    emps = {e.pk: e for e in Employee.objects.filter(join_date__lte=end).select_related("department", "position")}
    warnings = []

    def in_scope(e):
        s = seps.get(e.pk)
        if s: return s.last_date >= start or e.pk in tali          # keluar sebelum periode: hanya bila masih ada tali asih
        return e.status == "aktif"
    todo = [(e, profs[e.pk]) for e in emps.values() if e.pk in profs and in_scope(e)]
    todo.sort(key=lambda t: (t[0].department.name, t[0].name, t[0].pk))
    no_profile = sorted(e.nik for e in emps.values() if e.status == "aktif" and e.pk not in profs)
    if no_profile: warnings.append(f"{len(no_profile)} karyawan aktif belum punya profil gaji dan TIDAK ikut run ini (mis. NIK {', '.join(no_profile[:8])}{'…' if len(no_profile) > 8 else ''}). Lengkapi di Profil Gaji Karyawan lalu hitung ulang.")
    ids = [e.pk for e, _ in todo]
    att, ot = _attendance(ids, start, end)
    if ids and not att: warnings.append("Absensi periode ini belum diolah (tidak ada rekap harian): lembur, hari libur, uang makan, dan potongan alfa bernilai Rp 0. Jalankan Absensi → olah dulu bila perlu.")
    mdays = _meal_days([e.pk for e, p in todo if p.meal_allowance], windows)
    shift_names = {sh.pk: (sh.code or sh.name) for sh in Shift.objects.all()} if windows else {}
    bpjs_amt, bpjs_off = _bpjs(ids, period); uni = _uniform(ids, end); adj = _adjustments(ids, period)
    pph_on = bool(prm["pph21_aktif"]); ter = ter_tables(end) if pph_on else {}
    final_ids = [e.pk for e, _ in todo if (end.month == 12 or (seps.get(e.pk) and start <= seps[e.pk].last_date <= end))] if pph_on else []
    ytd = _ytd(final_ids, period) if final_ids else {}
    slips, missing_scale, no_items = [], [], []
    if not comps: warnings.append("Tidak ada komponen slip aktif (biasanya setelah `manage.py flush`); slip tidak dapat dibuat. Jalankan: python manage.py seed_payroll_demo (demo) atau aktifkan komponen di Payroll → Komponen.")
    for e, pf in todo:
        daily = daily_wage_of(pf, scale)
        if daily is None: missing_scale.append(f"{e.nik} (grade {pf.grade_label})"); continue
        sep, flags, items = seps.get(e.pk), [], []
        last = sep.last_date if sep else None
        days = calc.paid_days(e.join_date, last, start, end, basis); ratio = (days / basis) if basis else D(0)
        a = att.get(e.pk); hourly = calc.hourly_rate(daily, basis, prm["pembagi_jam"])

        def add(code, amount, qty=None, rate=None, source="", note="", refs=None):
            c = comps.get(code)
            if c is None: return
            amount = calc.q0(amount)
            if amount == 0 and code != "gaji_pokok": return
            items.append({"component": c, "amount": amount, "qty": qty, "rate": rate, "source": source, "note": note, "refs": refs or []})
        gp = calc.q0(daily * days)
        if days > 0: add("gaji_pokok", gp, qty=calc.q1(days), rate=calc.q2(daily), source="profil")
        add("t_jabatan", pf.allowance_position * ratio, source="profil"); add("t_pendidikan", (pf.allowance_education or edu.get((e.education or "").strip().upper(), D(0))) * ratio, source="profil" if pf.allowance_education else "master")
        add("t_khusus", pf.allowance_special * ratio, source="profil"); add("t_lain", pf.allowance_other * ratio, source="profil")
        years = calc.full_years(e.join_date, end)
        if days > 0: add("t_masa_kerja", years * prm["tunj_masa_kerja"] * ratio, qty=D(years), rate=prm["tunj_masa_kerja"], source="hitung")
        flat = orates.get(("lembur", pf.pay_type))
        if flat:   # master Lembur per jam (nominal tetap): jam terbukti scan × tarif
            hours = calc.q2(sum((D(int(m)) for m in ot.get(e.pk, []) if m and m > 0), D(0)) / 60)
            if hours > 0: add("lembur_overtime", hours * flat, qty=hours, rate=flat, source="absensi", note=f"tarif tetap {pf.get_pay_type_display().lower()}")
        else:
            hours = calc.weighted_hours(ot.get(e.pk, []), prm["lembur_jam1"], prm["lembur_jam2"])
            if hours > 0: add("lembur_overtime", hours * hourly, qty=hours, rate=calc.q2(hourly), source="absensi")
        if a and a["libur_kerja"]: add("lembur_libur", daily * a["libur_kerja"], qty=D(a["libur_kerja"]), rate=calc.q2(daily), source="absensi")
        if a and a["standby"]:
            sflat = orates.get(("standby", pf.pay_type)); sh = calc.q2(D(a["standby"]) / 60)
            if sflat: add("lembur_standby", sh * sflat, qty=sh, rate=sflat, source="absensi", note=f"tarif tetap {pf.get_pay_type_display().lower()}")
            else: add("lembur_standby", D(a["standby"]) / 60 * hourly * prm["standby_pengali"], qty=sh, rate=calc.q2(hourly), source="absensi")
        if pf.meal_allowance and mdays.get(e.pk):   # hanya hari hadir di dalam Periode Uang Makan; tarif menurut tipe gaji + shift hari itu (0 = shift tanpa uang makan)
            tot, n, parts = D(0), 0, []
            for sid, cnt in sorted(mdays[e.pk].items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
                r = meal_rate_for(mrates, pf.pay_type, sid)
                if r > 0: tot += r * cnt; n += cnt; parts.append(f"{shift_names.get(sid, 'tanpa shift')} {cnt}×{int(r):,}".replace(",", "."))
            if n: add("uang_makan", tot, qty=D(n), rate=calc.q2(tot / n), source="absensi", note=("; ".join(parts))[:200])
        if sep and prm["sisa_cuti_dibayar"] and start <= last <= end:
            bal = leave.balance(e, last.year)
            if bal > 0: add("sisa_cuti", bal * daily, qty=bal, rate=calc.q2(daily), source="hitung", note=f"saldo cuti {last.year}")
        if e.pk in tali: add("tali_asih", tali[e.pk].tali_asih, source="keluar", note=tali[e.pk].tali_asih_note[:200], refs=[tali[e.pk].pk])
        for scheme, code, pk_, bk in (("tk", "pot_jamsostek", "jamsostek_persen", "jamsostek_bulat"), ("kes", "pot_bpjs", "bpjs_kes_persen", "bpjs_kes_bulat")):
            if (e.pk, scheme) in bpjs_off: flags.append(f"BPJS {'K' if scheme == 'kes' else 'TK'} nonaktif: tidak dipotong"); continue
            if (e.pk, scheme) in bpjs_amt: add(code, bpjs_amt[(e.pk, scheme)], source="bpjs", note="dari Potongan BPJS periode ini")
            else: add(code, calc.floor_to(gp * prm[pk_] / 100, prm[bk]), rate=prm[pk_], source="hitung")
        if e.pk in uni: add("pot_seragam", uni[e.pk][0], qty=D(uni[e.pk][2]), source="seragam", refs=uni[e.pk][1])
        if a and a["alfa"]: add("pot_absensi", a["alfa"] * daily * prm["alfa_pengali"], qty=D(a["alfa"]), rate=calc.q2(daily), source="absensi")
        for x in adj.get(e.pk, []):
            if x.component.active: add(x.component.code, x.amount, qty=x.qty, source="manual", note=x.note[:200])
        if a is None and e.status == "aktif": flags.append("Absensi belum diolah untuk karyawan ini")
        if a:
            if a["tanpa_scan"]: flags.append(f"{a['tanpa_scan']} hari kerja tanpa scan (belum divalidasi)")
            if a["tidak_lengkap"]: flags.append(f"{a['tidak_lengkap']} hari scan tidak lengkap")
            if a["konflik"]: flags.append(f"{a['konflik']} hari berkonflik data (lembur/izin) — tinjau di Absensi")
        if last and start <= last <= end: flags.append(f"Keluar {last:%d-%m-%Y}: gaji dihitung sampai hari terakhir kerja")
        elif last and last < start: flags.append("Sudah keluar sebelum periode: hanya komponen tersisa (mis. tali asih)")
        if pph_on and comps.get("pot_pph21") is not None:
            bruto = sum((i["amount"] for i in items if i["component"].section != "potongan" and i["component"].code != "tali_asih"), D(0))
            cat, ptkp = ter_data.STATUS.get(pf.ptkp_status, ter_data.STATUS["TK0"]); sur = D(1) if e.npwp else prm["pph21_tanpa_npwp"]
            if e.pk in final_ids:
                y = ytd.get(e.pk, {"earn": D(0), "pph": D(0), "iuran": D(0), "n": 0})
                first_m = e.join_date.month if e.join_date.year == end.year else 1; expected = max(0, end.month - first_m)
                iuran = sum((i["amount"] for i in items if i["component"].code == "pot_jamsostek"), D(0))
                r = tax.annual_tax(y["earn"] + bruto, y["iuran"] + iuran, ptkp, y["n"] + 1, sur); due = r["pajak"] - y["pph"]
                add("pot_pph21", max(due, D(0)), source="pajak", note=f"Penyelesaian setahun {ptkp_label(pf)}: PKP {int(r['pkp']):,} → pajak {int(r['pajak']):,}; sudah dipotong {int(y['pph']):,}".replace(",", "."))
                if due < 0: flags.append(f"PPh 21 setahun lebih kecil dari yang sudah dipotong (kelebihan Rp {int(-due):,}): kembalikan lewat Tambahan & Potongan".replace(",", "."))
                if y["n"] < expected: flags.append(f"PPh 21 Desember/terakhir: baru {y['n']} dari {expected} slip bulan sebelumnya ada di sistem — hasil perkiraan, cocokkan dengan catatan pajak")
            else:
                pct = tax.ter_rate(ter[cat], bruto)
                add("pot_pph21", tax.pph_ter(bruto, pct, sur), rate=pct, source="pajak", note=f"TER {cat} {pct}% × bruto {int(bruto):,}".replace(",", ".") + ("" if e.npwp else " × tanpa NPWP"))
            if e.pk in tali: flags.append("Tali asih/pesangon: PPh-nya (tarif khusus pesangon) belum dihitung otomatis — hitung & potong manual bila perlu")
        earn = sum((i["amount"] for i in items if i["component"].section != "potongan"), D(0)); ded = sum((i["amount"] for i in items if i["component"].section == "potongan"), D(0))
        if earn - ded < 0: flags.append("Gaji bersih NEGATIF: potongan melebihi pendapatan — tinjau sebelum diajukan")
        if not items: no_items.append(e.nik); continue
        slips.append({"employee": e, "daily": daily, "days": days, "years": years, "grade": pf.grade_label, "items": items, "earn": earn, "ded": ded, "net": earn - ded, "flags": flags})
    if missing_scale: warnings.append(f"{len(missing_scale)} karyawan punya profil tetapi skala gajinya tidak ditemukan dan TIDAK ikut run: {', '.join(missing_scale[:8])}{'…' if len(missing_scale) > 8 else ''}. Tambahkan di Skala Gaji Harian.")
    if no_items: warnings.append(f"{len(no_items)} karyawan tidak menghasilkan baris slip apa pun (mis. NIK {', '.join(no_items[:8])}{'…' if len(no_items) > 8 else ''}): periksa tanggal masuk/keluar dan komponen aktif.")
    if not slips and not warnings: warnings.append("Tidak ada karyawan yang masuk run periode ini (belum ada karyawan aktif dengan tanggal masuk ≤ akhir periode).")
    return {"slips": slips, "warnings": warnings, "params": {k: str(v) for k, v in prm.items()}}


@transaction.atomic
def persist(run, built):
    """Ganti semua slip run ini dengan hasil `built` dan perbarui total. Pemanggil menjaga status/kunci run."""
    old = list(Payslip.objects.filter(run=run).values_list("pk", flat=True))
    if old:
        PayslipItem.objects.filter(slip_id__in=old).delete(); Payslip.objects.filter(pk__in=old).delete()
    slips = []
    for s in built["slips"]:
        e = s["employee"]
        slips.append(Payslip(run=run, employee=e, nik=e.nik, emp_name=e.name, department_name=e.department.name, position_name=e.position.name if e.position_id else "",
                             grade_label=s["grade"], education=e.education, join_date=e.join_date, service_years=s["years"], daily_wage=s["daily"], paid_days=s["days"],
                             total_earnings=s["earn"], total_deductions=s["ded"], net_pay=s["net"], flags=s["flags"], flagged=bool(s["flags"])))
    Payslip.objects.bulk_create(slips, batch_size=500)
    by_emp = {p.employee_id: p.pk for p in Payslip.objects.filter(run=run).only("pk", "employee_id")}
    rows = []
    for s in built["slips"]:
        pk = by_emp[s["employee"].pk]
        for i in s["items"]:
            c = i["component"]
            rows.append(PayslipItem(slip_id=pk, component=c, code=c.code, label=c.label, section=c.section, order=c.order, qty=i["qty"], rate=i["rate"], amount=i["amount"],
                                    source=i["source"], note=i["note"], refs=i["refs"]))
    PayslipItem.objects.bulk_create(rows, batch_size=1000)
    run.params, run.warnings, run.n_slips = built["params"], built["warnings"], len(slips)
    run.total_gross = sum((s["earn"] for s in built["slips"]), D(0)); run.total_deductions = sum((s["ded"] for s in built["slips"]), D(0)); run.total_net = run.total_gross - run.total_deductions
    return run
