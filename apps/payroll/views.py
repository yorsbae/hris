"""Halaman Payroll (putaran 57). Akses:
  • Payroll (+ Superadmin): semua halaman Payroll; menghitung, mengajukan, menarik, membayar, membatalkan run; mengelola master & penyesuaian
  • HRD: hanya melihat run + slip, menyetujui / mengembalikan / membatalkan run, dan melihat usulan tali asih
  • Admin Departemen & Poli: 403 di semua halaman
Membuka slip (data upah per orang), mencetak slip, dan mengekspor run SELALU tercatat di audit (tanpa nominal)."""
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from apps.hr.models import Department, Employee, Shift
from apps.hrd.models import Separation
from apps.hrd.views import paginate
from . import calc, engine, params as P, pdf, readiness as RD, services
from .forms import EducationAllowanceForm, MealPeriodForm, MealRateForm, OvertimeRateForm, AdjustmentForm, PayDateForm, ProfileForm, ReasonForm, RunForm, ScaleForm, ParamForm, TaliRuleForm
from .models import EducationAllowance, MealPeriod, MealRate, OvertimeRate, PayAdjustment, PayComponent, PayParam, PayProfile, PayrollRun, Payslip, PayslipItem, TaliAsihRule, WageScale

EXPORT_MAX = 20000
PDF_MAX = 400
STATUS_STEP = {"draft": 1, "diserahkan": 2, "dihitung": 3, "ditetapkan": 4, "dibayar": 5}


def payroll_only(fn):
    @wraps(fn)
    @login_required
    @require_roles(Role.PAYROLL)
    def wrap(*a, **k): return fn(*a, **k)
    return wrap


def viewer(fn):
    """Payroll + HRD (+ Superadmin)."""
    @wraps(fn)
    @login_required
    @require_roles(Role.PAYROLL, Role.HRD)
    def wrap(*a, **k): return fn(*a, **k)
    return wrap


def _is_payroll(user): return user.role in (Role.PAYROLL, Role.SUPERADMIN)
def _is_hrd(user): return user.role in (Role.HRD, Role.SUPERADMIN)
def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _done(request, form_cls, *, title, back, hint, audit, ok, redirect_to, initial=None, **kw):
    """Pola umum halaman form master: form → simpan → audit → pesan → redirect."""
    form = form_cls(request.POST or None, user=request.user, initial=initial or {}, **kw)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(); log(request, "payroll", audit, obj, None, _audit_data(obj))
        messages.success(request, ok); return redirect(redirect_to(obj) if callable(redirect_to) else redirect_to)
    return render(request, "hrd/form.html", {"form": form, "title": title, "back": back, "hint": hint})


def _audit_data(o):
    d = {}
    for f in ("period", "key", "grade", "tier", "kind", "effective_from"):
        if hasattr(o, f): d[f] = str(getattr(o, f))
    if getattr(o, "employee_id", None): d["employee"] = o.employee.nik
    for f in ("pay_type", "start_date", "end_date"):
        if hasattr(o, f): d[f] = str(getattr(o, f))
    for f in ("daily_wage", "value", "amount", "wage_override", "days_per_year"):
        if hasattr(o, f) and getattr(o, f) is not None: d[f] = str(getattr(o, f))   # master gaji: nominal dicatat (akuntabilitas perubahan); audit hanya terbaca Superadmin
    return d


# ================================================================ Beranda
@payroll_only
def home(request):
    """Beranda = alur kerja BERTAHAP (waterfall): 1 Aturan gaji → 2 Karyawan → 3 Data bulan ini → 4 Run gaji → 5 Slip. Tiap langkah menunjukkan status dan tombol tindakan berikutnya."""
    today = timezone.localdate(); cur = today.strftime("%Y-%m")
    live = PayrollRun.objects.exclude(status=PayrollRun.CANCELLED); run = live.filter(period=cur).first()
    act = Employee.objects.filter(status="aktif"); has_prof = Exists(PayProfile.objects.filter(employee_id=OuterRef("pk"), voided_at__isnull=True, effective_from__lte=today))
    no_profile = act.exclude(has_prof).count(); with_profile = act.filter(has_prof).count()
    tali = engine.pending_tali_asih(today + timedelta(days=31))
    ok = lambda v: "ok" if v else "todo"   # noqa: E731
    n = lambda M: M.objects.filter(voided_at__isnull=True).count()   # noqa: E731
    ready = RD.readiness(cur); rs = RD.summary(ready)
    stages = [
        {"no": 1, "title": "Aturan gaji", "when": "Diisi sekali, ubah hanya bila kebijakan berubah", "items": [
            {"t": "Skala gaji harian", "h": "Upah per hari untuk tiap grade + sub-grade.", "st": ok(n(WageScale)), "d": f"{n(WageScale)} baris", "url": "pay_scale", "lb": "Atur skala"},
            {"t": "Lembur & stand by per jam", "h": "Nominal 1 jam, beda untuk karyawan harian dan bulanan. Kosong = rumus upah/jam × pengali.", "st": "ok" if n(OvertimeRate) else "opt", "d": f"{n(OvertimeRate)} tarif" if n(OvertimeRate) else "pakai rumus bawaan", "url": "pay_rates", "lb": "Atur tarif"},
            {"t": "Uang makan (bulan puasa)", "h": "Dibayar hanya pada periode yang Anda tentukan, tarif beda harian/bulanan.", "st": "ok" if n(MealPeriod) and n(MealRate) else "opt", "d": f"{n(MealPeriod)} periode, {n(MealRate)} tarif" if n(MealPeriod) else "belum ada → uang makan Rp 0", "url": "pay_rates", "lb": "Atur uang makan"},
            {"t": "Tunjangan pendidikan standar", "h": "Nilai standar per tingkat pendidikan; bisa ditimpa di profil tiap karyawan.", "st": "ok" if n(EducationAllowance) else "opt", "d": f"{n(EducationAllowance)} tingkat" if n(EducationAllowance) else "opsional", "url": "pay_rates", "lb": "Atur"},
            {"t": "Parameter potongan & hitung", "h": "Jamsostek, BPJS, pembagi lembur, dll. Sudah terisi nilai bawaan.", "st": "ok", "d": "nilai bawaan terisi", "url": "pay_params", "lb": "Lihat"}]},
        {"no": 2, "title": "Data karyawan", "when": "Saat ada karyawan baru atau perubahan gaji", "items": [
            {"t": "Profil gaji tiap karyawan", "h": "Grade, tipe gaji (harian/bulanan), T Khusus & T Lain-lain per orang, hak uang makan.", "st": ok(no_profile == 0 and with_profile), "d": f"{with_profile} lengkap, {no_profile} belum" , "url": "pay_profiles", "lb": "Isi / impor profil"}]},
        {"no": 3, "title": f"Data bulan {cur}", "when": "Tiap bulan sebelum run dihitung", "items": [
            *[{"t": r["title"], "h": r["detail"], "st": {"ok": "ok", "warn": "warn", "bad": "bad"}[r["level"]], "d": (str(r["count"]) if r["count"] and r["level"] != "ok" else ""), "raw": r["url"], "lb": r["label"] or "Buka"} for r in ready],
            {"t": "Tambahan & potongan manual", "h": "Tambahan (+): insentif, premi. Potongan (−): pinjaman, pengobatan, materai, dll.", "st": "opt", "d": f"{PayAdjustment.objects.filter(period=cur, voided_at__isnull=True).count()} isian", "url": "pay_adjustments", "lb": "Isi penyesuaian"}]},
        {"no": 4, "title": "Run gaji", "when": "Tiap bulan: serahkan → hitung → tetapkan → bayar", "run": run, "items": []},
        {"no": 5, "title": "Slip & arsip", "when": "Setelah run dibayar", "items": [
            {"t": "Slip gaji & PDF", "h": "Buka run yang sudah ditetapkan/dibayar lalu cetak slip.", "st": "ok" if live.filter(status__in=("ditetapkan", "dibayar")).exists() else "opt", "d": f"{live.filter(status='dibayar').count()} run dibayar", "url": "pay_runs", "lb": "Daftar run"},
            {"t": "Tali asih karyawan keluar", "h": "Usulan & pembayaran lewat run gaji.", "st": "warn" if tali else "opt", "d": f"{len(tali)} menunggu" if tali else "tidak ada", "url": "pay_tali", "lb": "Buka"}]}]
    flow = [("draft", "Draft"), ("diserahkan", "Diserahkan HRD"), ("dihitung", "Dihitung"), ("ditetapkan", "Ditetapkan"), ("dibayar", "Dibayar")]
    nxt = {None: "Buat run gaji untuk periode ini.", "draft": "HRD: serahkan data periode ke Payroll.", "diserahkan": "Payroll: klik Hitung.", "dihitung": "Payroll: periksa hasil, lalu Tetapkan.", "ditetapkan": "Payroll: bayar (seragam & tali asih otomatis ditandai).", "dibayar": "Selesai — cetak slip."}
    cur_i = [k for k, _ in flow].index(run.status) if run else -1
    flow = [{"k": k, "l": l, "state": "done" if i < cur_i or (run and run.status == "dibayar" and i == cur_i) else ("now" if i == cur_i else "")} for i, (k, l) in enumerate(flow)]
    # tentukan langkah aktif: yang pertama punya item belum siap (todo/bad)
    for st in stages:
        for it in st["items"]: it["link"] = it.get("raw") or (reverse(it["url"]) if it.get("url") else "")
        st["open"] = any(i["st"] in ("todo", "bad") for i in st["items"])
    first_open = next((st["no"] for st in stages if st["open"]), 4)
    return render(request, "payroll/home.html", {"cur": cur, "run": run, "stages": stages, "flow": flow, "next_action": nxt[run.status if run else None], "first_open": first_open, "ready_sum": rs,
                                                 "stale": services.run_is_stale(run) if run else False, "runs": PayrollRun.objects.all()[:6]})


# ================================================================ Run gaji
@viewer
def runs(request):
    form = None
    if request.method == "POST":
        if not _is_hrd(request.user): return HttpResponse(status=403)    # alur HRD → Payroll: HRD yang membuka run periode
        form = RunForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    r = services.create_run(form.cleaned_data["period"], request.user, form.cleaned_data.get("pay_date"), form.cleaned_data.get("note", ""))
                    log(request, "payroll", "run_create", r, None, {"period": r.period})
                messages.success(request, f"Run gaji {r.period} dibuat. Langkah berikutnya: pastikan data periode lengkap lalu Serahkan ke Payroll."); return redirect("pay_run", pk=r.pk)
            except ValueError as e: form.add_error("period", str(e))
    elif _is_hrd(request.user): form = RunForm()
    return render(request, "payroll/runs.html", {"page": paginate(request, PayrollRun.objects.all()), "form": form, "qs": ""})


def _can(user, run):
    """Aksi yang boleh dilakukan `user` pada `run` saat ini."""
    s, p, h = run.status, _is_payroll(user), _is_hrd(user)
    return {"handover": h and s == "draft", "calculate": p and s in ("diserahkan", "dihitung"), "finalize": p and s == "dihitung", "reopen": p and s == "ditetapkan",
            "return": p and s in ("diserahkan", "dihitung"), "pay": p and s == "ditetapkan", "cancel": (p or h) and s in ("draft", "diserahkan", "dihitung", "ditetapkan")}


def _slip_filters(request):
    return {k: request.GET.get(k, "").strip() for k in ("q", "department", "flag")}


def _slips(run, f):
    qs = run.slips.all()
    if f["q"]: qs = qs.filter(Q(emp_name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    if f["department"]: qs = qs.filter(department_name=f["department"])
    if f["flag"] == "1": qs = qs.filter(flagged=True)
    return qs


EXPORT_HEADER = ["nik", "nama", "departemen", "jabatan", "grade", "hari_dibayar", "upah_harian", "gaji_pokok", "tunjangan", "premi", "lembur", "uang_makan", "lain_lain", "total_pendapatan", "total_potongan", "gaji_bersih", "catatan"]


def _export_rows(slips):
    ids = [s.pk for s in slips]; sums = {}
    for part in engine.chunks(ids):
        for r in PayslipItem.objects.filter(slip_id__in=part).values("slip_id", "section").annotate(t=Sum("amount")): sums[(r["slip_id"], r["section"])] = r["t"]
    g = lambda s, k: sums.get((s.pk, k), 0)  # noqa: E731
    return [(s.nik, s.emp_name, s.department_name, s.position_name, s.grade_label, s.paid_days, s.daily_wage, g(s, "pokok"), g(s, "tunjangan"), g(s, "premi"), g(s, "lembur"), g(s, "makan"), g(s, "lain"),
             s.total_earnings, s.total_deductions, s.net_pay, "; ".join(s.flags)) for s in slips]


@viewer
def run_detail(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk); f = _slip_filters(request); qs = _slips(run, f)
    if request.GET.get("export"):
        if not request.user.role == Role.SUPERADMIN and request.user.role not in (Role.PAYROLL, Role.HRD): raise Http404
        log(request, "payroll", "run_export", run, None, {"period": run.period, "rows": qs.count()})
        rows = _export_rows(list(qs.order_by("department_name", "emp_name")[:EXPORT_MAX]))
        return tabular.export_response(f"gaji-{run.period}", EXPORT_HEADER, rows, request, sheet=f"Gaji {run.period}",
                                       money_cols=("upah_harian", "gaji_pokok", "tunjangan", "premi", "lembur", "uang_makan", "lain_lain", "total_pendapatan", "total_potongan", "gaji_bersih"), num_cols=("hari_dibayar",))
    by_dept = list(run.slips.order_by().values("department_name").annotate(n=Count("id"), gross=Sum("total_earnings"), net=Sum("net_pay")).order_by("department_name"))
    flagged = run.slips.filter(flagged=True).count(); can = _can(request.user, run); stale = services.run_is_stale(run)
    ready = RD.readiness(run.period) if run.status in ("draft", "diserahkan", "dihitung") else None
    ctx = {"run": run, "f": f, "page": paginate(request, qs), "by_dept": by_dept, "flagged": flagged, "can": can, "stale": stale, "step_now": STATUS_STEP.get(run.status, 0),
           "events": run.events.select_related("by"), "depts": [d["department_name"] for d in by_dept], "reason_form": ReasonForm(), "pay_form": PayDateForm(),
           "qs": urlencode({k: v for k, v in f.items() if v}), "is_payroll": _is_payroll(request.user),
           "ready": ready, "ready_sum": RD.summary(ready) if ready else None, "compare": RD.comparison(run) if run.status != "dibatalkan" else None,
           "param_rows": [(P.PARAMS[k][0], v, P.PARAMS[k][2]) for k, v in (run.params or {}).items() if k in P.PARAMS]}
    return render(request, "payroll/run_detail.html", ctx)


@viewer
@require_POST
def run_action(request, pk, action):
    run = get_object_or_404(PayrollRun, pk=pk)
    if not _can(request.user, run).get(action): return HttpResponse(status=403)
    u = request.user
    try:
        if action == "calculate":
            r = services.calculate_run(pk, u); messages.success(request, f"Dihitung: {r.n_slips} slip, total bersih {calc_fmt(r.total_net)}." + (" Ada peringatan — baca di bawah." if r.warnings else ""))
        elif action == "handover": services.hand_over_run(pk, u); messages.success(request, "Data periode diserahkan ke Payroll. Payroll akan menghitung.")
        elif action == "finalize": services.finalize_run(pk, u); messages.success(request, "Run ditetapkan. Langkah berikutnya: bayar lalu tandai dibayar.")
        elif action == "reopen": services.reopen_run(pk, u); messages.success(request, "Run dibuka kembali. Perbaiki lalu hitung ulang dan tetapkan lagi.")
        elif action == "return":
            f = ReasonForm(request.POST)
            if not f.is_valid(): raise ValueError("Alasan wajib diisi.")
            services.return_to_hrd(pk, f.cleaned_data["reason"], u); messages.success(request, "Run dikembalikan ke HRD beserta alasannya.")
        elif action == "pay":
            f = PayDateForm(request.POST)
            if not f.is_valid(): raise ValueError("Tanggal bayar tidak valid.")
            services.pay_run(pk, f.cleaned_data["paid_on"], u); messages.success(request, "Ditandai dibayar. Run terkunci; seragam & tali asih di slip ikut ditandai.")
        elif action == "cancel":
            f = ReasonForm(request.POST)
            if not f.is_valid(): raise ValueError("Alasan wajib diisi.")
            services.cancel_run(pk, f.cleaned_data["reason"], u); messages.success(request, "Run dibatalkan.")
        else: raise Http404
        log(request, "payroll", f"run_{action}", run, None, {"period": run.period})
    except ValueError as e: messages.error(request, str(e))
    return redirect("pay_run", pk=pk)


def calc_fmt(v):
    from apps.core.money import format_rupiah
    return format_rupiah(v)


# ================================================================ Slip
@viewer
def slip(request, pk):
    s = get_object_or_404(Payslip.objects.select_related("run"), pk=pk)
    log(request, "payroll", "payslip_view", s, None, {"period": s.run.period, "employee": s.nik})
    items = list(s.items.all()); sections = []
    for key, title in PayComponent.SECTIONS:
        rows = [i for i in items if i.section == key]
        if rows: sections.append({"key": key, "title": title, "rows": rows, "total": sum(r.amount for r in rows)})
    return render(request, "payroll/slip.html", {"s": s, "sections": sections, "period_label": calc.period_label(s.run.period)})


@viewer
def slip_pdf(request, pk):
    s = get_object_or_404(Payslip.objects.select_related("run").prefetch_related("items"), pk=pk)
    log(request, "payroll", "payslip_print", s, None, {"period": s.run.period, "employee": s.nik})
    r = HttpResponse(pdf.slips_pdf([s]), content_type="application/pdf"); r["Content-Disposition"] = f'inline; filename="slip-{s.run.period}-{s.nik}.pdf"'; return r


@viewer
def run_pdf(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk); f = _slip_filters(request)
    qs = _slips(run, f).select_related("run").prefetch_related("items").order_by("department_name", "emp_name")
    n = qs.count()
    if n > PDF_MAX: messages.error(request, f"Terlalu banyak slip ({n}) untuk satu PDF; pilih satu departemen (maks {PDF_MAX})."); return redirect("pay_run", pk=pk)
    log(request, "payroll", "payslip_print_bulk", run, None, {"period": run.period, "count": n})
    r = HttpResponse(pdf.slips_pdf(list(qs)), content_type="application/pdf"); r["Content-Disposition"] = f'inline; filename="slip-{run.period}.pdf"'; return r


# ================================================================ Profil gaji karyawan
@payroll_only
def profiles(request):
    today = timezone.localdate()
    f = {k: request.GET.get(k, "").strip() for k in ("q", "department", "state")}
    has = Exists(PayProfile.objects.filter(employee_id=OuterRef("pk"), voided_at__isnull=True, effective_from__lte=today))
    qs = Employee.objects.filter(status="aktif").select_related("department", "position").order_by("department__name", "name")
    if f["state"] == "tanpa": qs = qs.exclude(has)
    elif f["state"] == "ada": qs = qs.filter(has)
    if _int(f["department"]): qs = qs.filter(department_id=_int(f["department"]))
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    page = paginate(request, qs)
    cur = engine.current_profiles(today, [e.pk for e in page]); scale = engine.wage_scale(today)
    rows = [{"e": e, "p": cur.get(e.pk), "wage": engine.daily_wage_of(cur[e.pk], scale) if e.pk in cur else None} for e in page]
    miss = sum(1 for r in rows if r["p"] and r["wage"] is None)
    n_no = Employee.objects.filter(status="aktif").exclude(has).count()
    return render(request, "payroll/profiles.html", {"rows": rows, "page": page, "f": f, "depts": Department.objects.order_by("name"), "n_no": n_no, "miss": miss,
                                                     "qs": urlencode({k: v for k, v in f.items() if v})})


@payroll_only
def profile_new(request):
    return _done(request, ProfileForm, title="Profil gaji karyawan", back="/payroll/profiles/", initial={"nik": request.GET.get("nik", "")}, audit="profile_set", ok="Profil gaji disimpan.",
                 hint="Berlaku mulai tanggal yang dipilih (riwayat profil lama tetap tersimpan). Pilih grade + sub-grade (upah mengikuti Skala Gaji Harian) ATAU isi upah harian khusus. Tunjangan dalam rupiah per bulan penuh.",
                 redirect_to=lambda o: f"/payroll/profiles/{o.employee_id}/")


@payroll_only
def profile_detail(request, pk):
    e = get_object_or_404(Employee, pk=pk); today = timezone.localdate()
    rows = list(PayProfile.objects.filter(employee=e).order_by("-effective_from", "-id")); cur = engine.current_profiles(today, [e.pk]).get(e.pk)
    log(request, "payroll", "profile_view", e, None, {"employee": e.nik})
    return render(request, "payroll/profile_detail.html", {"e": e, "rows": rows, "cur": cur, "wage": engine.daily_wage_of(cur, engine.wage_scale(today)) if cur else None})


# ================================================================ Skala gaji, parameter
@payroll_only
def scale(request):
    today = timezone.localdate(); rows = list(WageScale.objects.filter(voided_at__isnull=True).order_by("grade", "tier", "-effective_from"))
    cur = engine.wage_scale(today); hist = {}
    for r in rows: hist.setdefault((r.grade, r.tier), []).append(r)
    table = [{"label": f"{g} {t}", "current": cur.get((g, t)), "rows": rs, "future": [x for x in rs if x.effective_from > today]} for (g, t), rs in hist.items()]
    return render(request, "payroll/scale.html", {"table": table, "n": len(rows)})


@payroll_only
def scale_new(request):
    return _done(request, ScaleForm, title="Skala gaji harian", back="/payroll/scale/", audit="scale_set", ok="Upah harian disimpan.", redirect_to="/payroll/scale/",
                 initial={"grade": request.GET.get("grade", ""), "tier": request.GET.get("tier", "")},
                 hint="Upah per HARI untuk satu grade + sub-grade, berlaku sejak tanggal tertentu. Menyimpan pada tanggal berlaku yang sama menggantikan baris lama (yang lama dibatalkan, tidak dihapus).")


@payroll_only
def params(request):
    today = timezone.localdate(); cur = engine.load_params(today)
    over = {}
    for r in PayParam.objects.filter(voided_at__isnull=True).order_by("key", "-effective_from"): over.setdefault(r.key, []).append(r)
    rows = [{"key": k, "label": v[0], "default": v[1], "unit": v[2], "help": v[3], "current": cur[k], "rows": over.get(k, []), "changed": cur[k] != v[1]} for k, v in P.PARAMS.items()]
    return render(request, "payroll/params.html", {"rows": rows, "components": PayComponent.objects.order_by("order", "id")})


@payroll_only
def param_new(request):
    key = request.GET.get("key", "")
    return _done(request, ParamForm, title="Ubah parameter hitung", back="/payroll/params/", audit="param_set", ok="Parameter disimpan (berlaku sejak tanggal yang dipilih).", redirect_to="/payroll/params/",
                 initial={"key": key, "value": P.PARAMS[key][1] if key in P.PARAMS else ""},
                 hint="Nilai baru berlaku untuk periode yang berakhir pada/setelah tanggal berlaku. Run yang sudah dihitung perlu dihitung ulang. Tulis dasar aturan di Alasan.")


# ================================================================ Master Lembur & Uang Makan
@payroll_only
def rates(request):
    today = timezone.localdate()
    meal_rows = list(MealRate.objects.filter(voided_at__isnull=True, shift__isnull=True).order_by("pay_type", "-effective_from", "-id"))
    excluded = set(MealRate.objects.filter(voided_at__isnull=True, shift__isnull=False, amount=0).values_list("shift_id", flat=True))
    shifts = [{"pk": sh.pk, "name": sh.code or sh.name, "gets": sh.pk not in excluded} for sh in Shift.objects.filter(active=True).order_by("code", "name")]
    ot_rows = list(OvertimeRate.objects.filter(voided_at__isnull=True).order_by("pay_type", "-effective_from", "-id"))
    cur_meal, cur_ot = engine.meal_rates(today), engine.overtime_rates(today)
    edu_rows = list(EducationAllowance.objects.filter(voided_at__isnull=True).order_by("education", "-effective_from", "-id"))
    periods = list(MealPeriod.objects.filter(voided_at__isnull=True).order_by("-start_date"))
    for m in periods: m.active_now = m.start_date <= today <= m.end_date
    return render(request, "payroll/rates.html", {"meal_rows": meal_rows, "shifts": shifts, "ot_rows": ot_rows, "edu_rows": edu_rows, "periods": periods, "today": today,
                                                   "cur_meal": [{"pay_type": k[0], "shift": k[1], "amount": v} for k, v in sorted(cur_meal.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))],
                                                   "cur_ot": cur_ot})


@payroll_only
def meal_period_new(request):
    return _done(request, MealPeriodForm, title="Periode uang makan", back="/payroll/rates/", audit="mealperiod_set", ok="Periode uang makan disimpan.", redirect_to="/payroll/rates/",
                 hint="Uang makan HANYA dibayar untuk hari hadir di dalam periode ini (mis. bulan puasa, saat tidak ada makan). Di luar periode = Rp 0. Run gaji yang sudah dihitung perlu dihitung ulang.")


@payroll_only
def meal_rate_new(request):
    return _done(request, MealRateForm, title="Tarif uang makan", back="/payroll/rates/", audit="mealrate_set", ok="Tarif uang makan disimpan.", redirect_to="/payroll/rates/",
                 initial={"pay_type": request.GET.get("pay_type", "harian")},
                 hint="Tarif per HARI HADIR, satu angka untuk karyawan harian dan satu untuk bulanan. Shift mana yang dapat uang makan diatur lewat kotak centang di halaman sebelumnya.")


@payroll_only
@require_POST
def meal_shifts(request):
    """Kotak centang: shift yang DAPAT uang makan. Yang tidak dicentang = tarif 0 (baris khusus shift, dibuat/dibatalkan otomatis)."""
    gets = set(request.POST.getlist("shift")); since = date(2020, 1, 1); changed = 0
    for sh in Shift.objects.filter(active=True):
        zero = list(MealRate.objects.filter(voided_at__isnull=True, shift=sh, amount=0))
        if str(sh.pk) in gets:
            for z in zero: services.void_record(MealRate, z.pk, "Shift dicentang: dapat uang makan", request.user); changed += 1
        elif not zero:
            for t, _ in PayProfile.PAY_TYPES: MealRate.objects.create(pay_type=t, shift=sh, amount=0, effective_from=since, note="Shift tidak dapat uang makan", created_by=request.user)
            changed += 1
    log(request, "payroll", "mealshift_set", None, None, {"dapat": sorted(gets)})
    messages.success(request, "Pengaturan shift disimpan." if changed else "Tidak ada perubahan."); return redirect("/payroll/rates/")


@payroll_only
def ot_rate_new(request):
    return _done(request, OvertimeRateForm, title="Tarif lembur per jam", back="/payroll/rates/", audit="otrate_set", ok="Tarif lembur disimpan.", redirect_to="/payroll/rates/",
                 initial={"pay_type": request.GET.get("pay_type", "harian"), "kind": request.GET.get("kind", "lembur")},
                 hint="Nominal tetap untuk 1 jam lembur atau stand by (terbukti scan). Bila diisi, menggantikan rumus upah/jam × pengali untuk tipe gaji itu.")


@payroll_only
def edu_allow_new(request):
    return _done(request, EducationAllowanceForm, title="Tunjangan pendidikan standar", back="/payroll/rates/", audit="eduallow_set", ok="Tunjangan pendidikan disimpan.", redirect_to="/payroll/rates/",
                 hint="Standar per tingkat pendidikan. Karyawan yang T Pendidikan-nya diisi sendiri di Profil Gaji memakai nilainya sendiri. T Khusus & T Lain-lain selalu diisi per karyawan di Profil Gaji.")


# ================================================================ Penyesuaian
@payroll_only
def adjustments(request):
    period = request.GET.get("period", "").strip() or timezone.localdate().strftime("%Y-%m")
    if not calc.valid_period(period): period = timezone.localdate().strftime("%Y-%m")
    f = {"q": request.GET.get("q", "").strip(), "period": period, "component": request.GET.get("component", "").strip(), "kind": request.GET.get("kind", "").strip()}
    if f["kind"] not in ("tambah", "potongan"): f["kind"] = ""
    base = PayAdjustment.objects.filter(period=period, voided_at__isnull=True)
    adj = base
    if f["kind"] == "tambah": adj = adj.exclude(component__section="potongan")
    elif f["kind"] == "potongan": adj = adj.filter(component__section="potongan")
    if _int(f["component"]): adj = adj.filter(component_id=_int(f["component"]))
    emps = Employee.objects.select_related("department").filter(Exists(adj.filter(employee=OuterRef("pk")))).order_by("name", "pk")
    if f["q"]: emps = emps.filter(Q(name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    page = paginate(request, emps)
    for e in page: e.plus_items, e.minus_items, e.plus, e.minus = [], [], 0, 0
    by = {e.pk: e for e in page}
    for a in adj.filter(employee_id__in=list(by)).select_related("component").order_by("component__section", "component__order", "id"):
        e = by[a.employee_id]
        if a.component.is_deduction: e.minus_items.append(a); e.minus += a.amount
        else: e.plus_items.append(a); e.plus += a.amount
    for e in page: e.net = e.plus - e.minus
    tot = adj.filter(employee__in=emps).aggregate(plus=Sum("amount", filter=~Q(component__section="potongan")), minus=Sum("amount", filter=Q(component__section="potongan")), n=Count("id"), people=Count("employee_id", distinct=True))
    tot["plus"], tot["minus"] = tot["plus"] or 0, tot["minus"] or 0; tot["net"] = tot["plus"] - tot["minus"]
    cnt = base.aggregate(a=Count("id", filter=~Q(component__section="potongan")), b=Count("id", filter=Q(component__section="potongan")))
    run = PayrollRun.objects.filter(period=period).exclude(status="dibatalkan").first()
    return render(request, "payroll/adjustments.html", {"page": page, "f": f, "tot": tot, "cnt": cnt, "run": run, "open": services.period_open_for_edit(period),
                                                       "comps": PayComponent.objects.filter(kind="manual", active=True).order_by("section", "order"), "qs": urlencode({k: v for k, v in f.items() if v})})


def _rp(n): return "Rp " + f"{int(abs(n)):,}".replace(",", ".")


ADJ_ROW_FIELDS = ("kind", "component", "amount", "note")


def _adj_rows(post):
    """Baris isian (kind[], component[], amount[], note[]) → list dict; baris yang benar-benar kosong dibuang."""
    cols = {n: post.getlist(n) for n in ADJ_ROW_FIELDS}; rows = []
    for i in range(max((len(v) for v in cols.values()), default=0)):
        r = {n: (cols[n][i].strip() if i < len(cols[n]) else "") for n in ADJ_ROW_FIELDS}
        if r["kind"] not in ("tambah", "potongan"): r["kind"] = "tambah"
        if r["component"] or r["amount"] or r["note"]: rows.append(r)
    return rows


@payroll_only
def adjustment_new(request):
    """Satu karyawan + satu periode, BANYAK baris: tiap baris = Tambahan (+) atau Potongan (−) + komponen + nominal. Semua baris divalidasi dulu; tersimpan semua atau tidak sama sekali."""
    cur = timezone.localdate().strftime("%Y-%m"); groups = AdjustmentForm(user=request.user).groups
    nik, per = request.GET.get("nik", ""), request.GET.get("period", cur)
    rows, top, posted = [{"kind": request.GET.get("kind") if request.GET.get("kind") in ("tambah", "potongan") else "tambah", "component": "", "amount": "", "note": "", "errors": {}}], [], False
    if request.method == "POST":
        posted = True; nik, per = request.POST.get("nik", "").strip(), request.POST.get("period", "").strip(); rows = _adj_rows(request.POST)
        head = AdjustmentForm({"nik": nik, "period": per}, user=request.user); head.is_valid()
        for k in ("nik", "period"): top += [f"{head.fields[k].label}: {e}" for e in head.errors.get(k, [])]
        forms, seen = [], set()
        if not rows: top.append("Isi minimal satu baris (komponen dan nominal).")
        for r in rows:
            f = AdjustmentForm({"nik": nik, "period": per, **r}, user=request.user); f.is_valid(); forms.append(f)
            r["errors"] = {k: [str(e) for e in f.errors.get(k, [])] for k in ADJ_ROW_FIELDS}
            if r["component"] in seen: r["errors"]["component"].append("Komponen ini sudah ada di baris lain. Gabungkan nominalnya.")
            seen.add(r["component"])
            for e in f.errors.get("__all__", []): top.append(str(e)) if str(e) not in top else None
        if not top and rows and not any(any(r["errors"].values()) for r in rows):
            plus = minus = 0
            with transaction.atomic():
                for f in forms:
                    obj = f.save(); log(request, "payroll", "adjustment_set", obj, None, _audit_data(obj))
                    if obj.component.is_deduction: minus += obj.amount
                    else: plus += obj.amount
                emp = forms[0].employee
            messages.success(request, f"{len(forms)} penyesuaian untuk {emp.name} ({per}) disimpan: tambahan +{_rp(plus)}, potongan −{_rp(minus)}, bersih {'+' if plus >= minus else '−'}{_rp(plus - minus)}. Hitung ulang run periode itu.")
            if request.POST.get("next") == "other": return redirect(f"/payroll/adjustments/new/?{urlencode({'period': per})}")
            return redirect(f"/payroll/adjustments/?period={per}")
    for r in rows: r.setdefault("errors", {})
    emp = Employee.objects.select_related("department").filter(nik=nik.strip()).first() if nik.strip() else None
    existing = list(PayAdjustment.objects.filter(employee=emp, period=per, voided_at__isnull=True).select_related("component").order_by("component__order")) if emp and calc.valid_period(per) else []
    blank = {"kind": "tambah", "component": "", "amount": "", "note": "", "errors": {}}
    return render(request, "payroll/adjustment_form.html", {"nik": nik, "period": per, "emp": emp, "existing": existing, "rows": rows, "blank": blank, "groups": groups, "top": top, "posted": posted,
                                                           "plus": sum((x.amount for x in existing if not x.component.is_deduction), 0), "minus": sum((x.amount for x in existing if x.component.is_deduction), 0)})


VOID = {"profile": (PayProfile, "/payroll/profiles/"), "scale": (WageScale, "/payroll/scale/"), "param": (PayParam, "/payroll/params/"), "adjustment": (PayAdjustment, "/payroll/adjustments/"), "rule": (TaliAsihRule, "/payroll/tali-asih/"),
        "mealperiod": (MealPeriod, "/payroll/rates/"), "mealrate": (MealRate, "/payroll/rates/"), "otrate": (OvertimeRate, "/payroll/rates/"), "eduallow": (EducationAllowance, "/payroll/rates/")}


@payroll_only
@require_POST
def void(request, kind, pk):
    if kind not in VOID: raise Http404
    model, back = VOID[kind]; obj = get_object_or_404(model, pk=pk)
    try:
        with transaction.atomic():
            services.void_record(model, pk, request.POST.get("reason", ""), request.user); log(request, "payroll", f"{kind}_void", obj, None, _audit_data(obj) | {"reason": request.POST.get("reason", "")[:300]})
        messages.success(request, "Dibatalkan (riwayat tetap tersimpan).")
    except ValueError as e: messages.error(request, str(e))
    if kind == "profile": back = f"/payroll/profiles/{obj.employee_id}/"
    elif kind == "adjustment": back = f"/payroll/adjustments/?period={obj.period}"
    return redirect(back)


# ================================================================ Tali asih
@viewer
def tali_asih(request):
    today = timezone.localdate()
    pending = list(Separation.objects.filter(voided_at__isnull=True, tali_asih__gt=0, tali_asih_paid_on__isnull=True).select_related("employee").order_by("last_date")[:200])
    recent = list(Separation.objects.filter(voided_at__isnull=True, tali_asih=0, last_date__gte=today - timedelta(days=120)).select_related("employee").order_by("-last_date")[:100])
    for s in pending + recent: s.suggest, s.basis = services.suggest_tali_asih(s)
    rules = list(TaliAsihRule.objects.filter(voided_at__isnull=True).order_by("kind", "-effective_from"))
    return render(request, "payroll/tali.html", {"pending": pending, "recent": recent, "rules": rules, "is_payroll": _is_payroll(request.user), "is_hrd": _is_hrd(request.user)})


@payroll_only
def tali_rule_new(request):
    return _done(request, TaliRuleForm, title="Aturan usulan tali asih", back="/payroll/tali-asih/", audit="tali_rule_set", ok="Aturan disimpan.", redirect_to="/payroll/tali-asih/",
                 hint="Usulan = min(masa kerja penuh × hari upah per tahun, batas hari) × upah harian. Ini HANYA usulan: nominal resmi ditetapkan HRD di Karyawan Keluar dan baru dibayar lewat run gaji.")
