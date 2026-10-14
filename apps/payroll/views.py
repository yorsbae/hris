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
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from apps.hr.models import Department, Employee
from apps.hrd.models import Separation
from apps.hrd.views import paginate
from . import calc, engine, params as P, pdf, services
from .forms import AdjustmentForm, PayDateForm, ProfileForm, ReasonForm, RunForm, ScaleForm, ParamForm, TaliRuleForm
from .models import PayAdjustment, PayComponent, PayParam, PayProfile, PayrollRun, Payslip, PayslipItem, TaliAsihRule, WageScale

EXPORT_MAX = 20000
PDF_MAX = 400
STATUS_STEP = {"draft": 1, "dihitung": 2, "diajukan": 3, "disetujui": 4, "dibayar": 5}


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
    for f in ("daily_wage", "value", "amount", "wage_override", "days_per_year"):
        if hasattr(o, f) and getattr(o, f) is not None: d[f] = str(getattr(o, f))   # master gaji: nominal dicatat (akuntabilitas perubahan); audit hanya terbaca Superadmin
    return d


# ================================================================ Beranda
@payroll_only
def home(request):
    today = timezone.localdate(); cur = today.strftime("%Y-%m")
    live = PayrollRun.objects.exclude(status=PayrollRun.CANCELLED)
    run = live.filter(period=cur).first()
    no_profile = Employee.objects.filter(status="aktif").exclude(Exists(PayProfile.objects.filter(employee_id=OuterRef("pk"), voided_at__isnull=True, effective_from__lte=today))).count()
    with_profile = Employee.objects.filter(status="aktif").filter(Exists(PayProfile.objects.filter(employee_id=OuterRef("pk"), voided_at__isnull=True, effective_from__lte=today))).count()
    tali = engine.pending_tali_asih(today + timedelta(days=31))
    waiting_pay = live.filter(status=PayrollRun.APPROVED).count()
    returned = PayrollRun.objects.filter(status=PayrollRun.CALCULATED, events__action="kembalikan").distinct().count()
    steps = {"profil": no_profile == 0, "skala": WageScale.objects.filter(voided_at__isnull=True).exists(), "run": bool(run)}
    return render(request, "payroll/home.html", {"cur": cur, "run": run, "no_profile": no_profile, "with_profile": with_profile, "tali_n": len(tali), "waiting_pay": waiting_pay, "returned": returned,
                                                 "runs": PayrollRun.objects.all()[:6], "steps": steps, "scale_n": WageScale.objects.filter(voided_at__isnull=True).count(),
                                                 "stale": services.run_is_stale(run) if run else False})


# ================================================================ Run gaji
@viewer
def runs(request):
    form = None
    if request.method == "POST":
        if not _is_payroll(request.user): return HttpResponse(status=403)
        form = RunForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    r = services.create_run(form.cleaned_data["period"], request.user, form.cleaned_data.get("pay_date"), form.cleaned_data.get("note", ""))
                    log(request, "payroll", "run_create", r, None, {"period": r.period})
                messages.success(request, f"Run gaji {r.period} dibuat. Langkah berikutnya: Hitung."); return redirect("pay_run", pk=r.pk)
            except ValueError as e: form.add_error("period", str(e))
    elif _is_payroll(request.user): form = RunForm()
    return render(request, "payroll/runs.html", {"page": paginate(request, PayrollRun.objects.all()), "form": form, "qs": ""})


def _can(user, run):
    """Aksi yang boleh dilakukan `user` pada `run` saat ini."""
    s, p, h = run.status, _is_payroll(user), _is_hrd(user)
    return {"calculate": p and s in ("draft", "dihitung", "diajukan"), "submit": p and s == "dihitung", "withdraw": p and s == "diajukan", "approve": h and s == "diajukan",
            "return": h and s == "diajukan", "pay": p and s == "disetujui", "cancel": (p or h) and s in ("draft", "dihitung", "diajukan", "disetujui")}


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
    ctx = {"run": run, "f": f, "page": paginate(request, qs), "by_dept": by_dept, "flagged": flagged, "can": can, "stale": stale, "step_now": STATUS_STEP.get(run.status, 0),
           "events": run.events.select_related("by"), "depts": [d["department_name"] for d in by_dept], "reason_form": ReasonForm(), "pay_form": PayDateForm(),
           "qs": urlencode({k: v for k, v in f.items() if v}), "is_payroll": _is_payroll(request.user),
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
        elif action == "submit": services.submit_run(pk, u); messages.success(request, "Diajukan ke HRD. Menunggu persetujuan.")
        elif action == "withdraw": services.withdraw_run(pk, u); messages.success(request, "Pengajuan ditarik. Perbaiki lalu ajukan lagi.")
        elif action == "approve": services.approve_run(pk, u); messages.success(request, "Run disetujui. Payroll akan membayar dan menandai dibayar.")
        elif action == "return":
            f = ReasonForm(request.POST)
            if not f.is_valid(): raise ValueError("Alasan wajib diisi.")
            services.return_run(pk, f.cleaned_data["reason"], u); messages.success(request, "Run dikembalikan ke Payroll beserta alasannya.")
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


# ================================================================ Penyesuaian
@payroll_only
def adjustments(request):
    period = request.GET.get("period", "").strip() or timezone.localdate().strftime("%Y-%m")
    if not calc.valid_period(period): period = timezone.localdate().strftime("%Y-%m")
    f = {"q": request.GET.get("q", "").strip(), "period": period, "component": request.GET.get("component", "").strip()}
    qs = PayAdjustment.objects.filter(period=period, voided_at__isnull=True).select_related("employee", "employee__department", "component").order_by("component__order", "employee__name")
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if _int(f["component"]): qs = qs.filter(component_id=_int(f["component"]))
    tot = qs.aggregate(plus=Sum("amount", filter=~Q(component__section="potongan")), minus=Sum("amount", filter=Q(component__section="potongan")), n=Count("id"))
    run = PayrollRun.objects.filter(period=period).exclude(status="dibatalkan").first()
    return render(request, "payroll/adjustments.html", {"page": paginate(request, qs), "f": f, "tot": tot, "run": run, "open": services.period_open_for_edit(period),
                                                       "comps": PayComponent.objects.filter(kind="manual", active=True).order_by("section", "order"), "qs": urlencode({k: v for k, v in f.items() if v})})


@payroll_only
def adjustment_new(request):
    return _done(request, AdjustmentForm, title="Penyesuaian gaji", back="/payroll/adjustments/", audit="adjustment_set", ok="Penyesuaian disimpan (nilai sebelumnya untuk komponen + periode yang sama diganti).",
                 redirect_to=lambda o: f"/payroll/adjustments/?period={o.period}", initial={"period": request.GET.get("period", timezone.localdate().strftime("%Y-%m")), "nik": request.GET.get("nik", "")},
                 hint="Insentif, premi produksi, pinjaman, pengobatan, materai, dst. untuk SATU karyawan pada SATU periode. Untuk banyak baris gunakan Impor. Setelah mengubah, hitung ulang run periode itu.")


VOID = {"profile": (PayProfile, "/payroll/profiles/"), "scale": (WageScale, "/payroll/scale/"), "param": (PayParam, "/payroll/params/"), "adjustment": (PayAdjustment, "/payroll/adjustments/"), "rule": (TaliAsihRule, "/payroll/tali-asih/")}


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
