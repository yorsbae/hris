"""Halaman Operasional HRD. SEMUA view: login + role HRD (Superadmin lolos otomatis di require_roles). Admin Dept & Poli → 403."""
from datetime import date
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from apps.hr.models import Department, Employee
from . import services
from .forms import AidForm, BpjsDeductionForm, BpjsStatusForm, CateringForm, FinishMaternityForm, MaternityForm, ProjectForm, ProjectWorkForm, WarningForm
from .models import Aid, BpjsDeduction, BpjsMembership, BpjsScheme, BpjsState, BpjsStatusLog, CateringOrder, MaternityLeave, Project, ProjectWork, UniformPurchase, WarningLetter

PER_PAGE = 50


def hrd_only(fn):
    @wraps(fn)
    @login_required
    @require_roles(Role.HRD)
    def wrap(*a, **k): return fn(*a, **k)
    return wrap


def paginate(request, qs): return Paginator(qs, PER_PAGE).get_page(request.GET.get("page", 1))


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


def _date(v):
    try: return date.fromisoformat(v)
    except (TypeError, ValueError): return None


def _qs(request, *keys):
    """Query string filter (untuk tautan halaman berikutnya)."""
    from urllib.parse import urlencode
    return urlencode({k: request.GET.get(k, "") for k in keys if request.GET.get(k, "")})


# ================================================================ Hub
@hrd_only
def hub(request):
    today = date.today()
    cards = [
        ("Bantuan (rekap)", "/hrd/aids/", Aid.objects.filter(event_date__year=today.year).count(), "tercatat tahun ini"),
        ("Cuti hamil", "/hrd/maternity/", MaternityLeave.objects.filter(state="aktif", start_date__lte=today, end_date__gte=today).count(), "sedang cuti"),
        ("Pekerja harian proyek", "/hrd/projects/", Project.objects.filter(status="aktif").count(), "proyek aktif"),
        ("Katering (rekap)", "/hrd/catering/", CateringOrder.objects.filter(date=today).count(), "rekap hari ini"),
        ("Surat Peringatan", "/hrd/warnings/", WarningLetter.objects.filter(revoked_at__isnull=True, issue_date__lte=today, valid_until__gte=today).count(), "SP aktif"),
        ("Potongan BPJS", "/hrd/bpjs/deductions/", BpjsDeduction.objects.filter(period=today.strftime("%Y-%m")).count(), "baris potongan bulan ini"),
        ("Seragam", "/hrd/uniforms/", UniformPurchase.objects.filter(deduction_status="belum", voided_at__isnull=True).count(), "pembelian belum dipotong"),
        ("Status BPJS", "/hrd/bpjs/", BpjsMembership.objects.filter(status="nonaktif", employee__status="aktif", employee__deleted_at__isnull=True).count(), "nonaktif (karyawan aktif)"),
    ]
    return render(request, "hrd/hub.html", {"cards": cards})


# ================================================================ BPJS
def _bpjs_status_sq(scheme):
    return Subquery(BpjsMembership.objects.filter(employee=OuterRef("pk"), scheme=scheme).values("status")[:1])


@hrd_only
def bpjs_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "kes", "tk", "emp", "department", "anomaly")}
    f["emp"] = f["emp"] or "aktif"
    qs = Employee.objects.select_related("department").annotate(kes_status=_bpjs_status_sq("kes"), tk_status=_bpjs_status_sq("tk"))
    if f["emp"] in ("aktif", "nonaktif"): qs = qs.filter(status=f["emp"])
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    if _int(f["department"]): qs = qs.filter(department_id=_int(f["department"]))
    for scheme in ("kes", "tk"):
        v = f[scheme]
        if v == "belum": qs = qs.filter(**{f"{scheme}_status__isnull": True})
        elif v in ("aktif", "nonaktif"): qs = qs.filter(**{f"{scheme}_status": v})
    if f["anomaly"]:  # anomali: karyawan NONAKTIF tetapi masih ada BPJS yang aktif (perlu dinonaktifkan)
        qs = qs.filter(Q(status="nonaktif") & (Q(kes_status="aktif") | Q(tk_status="aktif")))
    page = paginate(request, qs.order_by("name", "nik"))
    for e in page:  # nomor TIDAK ditampilkan; hanya penanda terisi/kosong (maks 50 dekripsi per halaman)
        e.has_kes, e.has_tk = bool(e.bpjs_kes), bool(e.bpjs_tk)
    g = request.GET  # "act": pengguna memilih filter sendiri (karyawan "aktif" adalah bawaan) → filter lanjutan terbuka + Reset muncul
    act = bool(g.get("q") or g.get("department") or g.get("kes") or g.get("tk") or g.get("anomaly") or g.get("emp", "aktif") not in ("", "aktif"))
    return render(request, "hrd/bpjs_list.html", {"page": page, "f": f, "act": act, "departments": Department.objects.order_by("name"),
                                                   "qs": _qs(request, "q", "kes", "tk", "emp", "department", "anomaly")})


@hrd_only
def bpjs_detail(request, pk):
    e = get_object_or_404(Employee.objects.select_related("department"), pk=pk)
    form = BpjsStatusForm(request.POST or None, employee=e)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                old = services.set_bpjs_status(e, d["scheme"], d["status"], d["effective_date"], d["note"].strip(), request.user)
                m = BpjsMembership.objects.get(employee=e, scheme=d["scheme"])
                log(request, "hrd", "bpjs_status", m, {"scheme": d["scheme"], "status": old or None}, {"scheme": d["scheme"], "status": d["status"], "effective_date": str(d["effective_date"])})
            messages.success(request, "Status BPJS disimpan.")
            return redirect("hrd_bpjs_detail", pk=e.pk)
        except ValueError as ex: form.add_error(None, str(ex))
    cur = {m.scheme: m for m in e.bpjs_memberships.all()}
    current = [(label, cur.get(code), bool(getattr(e, "bpjs_kes" if code == "kes" else "bpjs_tk"))) for code, label in BpjsScheme.choices]
    return render(request, "hrd/bpjs_detail.html", {"e": e, "form": form, "current": current, "logs": e.bpjs_logs.select_related("changed_by")[:50]})


# ================================================================ Potongan BPJS (putaran 22, P4)
def _period_ok(v): return bool(v) and len(v) == 7 and v[4] == "-" and v[:4].isdigit() and v[5:].isdigit() and 1 <= int(v[5:]) <= 12


def _deduction_qs(f):
    """Baris potongan + penanda anomali: karyawan nonaktif, atau status program bukan 'aktif' pada karyawan itu."""
    member = BpjsMembership.objects.filter(employee=OuterRef("employee"), scheme=OuterRef("scheme"), status="aktif")
    qs = BpjsDeduction.objects.select_related("employee", "employee__department").annotate(member_active=Exists(member))
    if _period_ok(f["period"]): qs = qs.filter(period=f["period"])
    if f["scheme"] in ("kes", "tk"): qs = qs.filter(scheme=f["scheme"])
    if _int(f["department"]): qs = qs.filter(employee__department_id=_int(f["department"]))
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if f["anomaly"] == "dipotong": qs = qs.filter(Q(employee__status="nonaktif") | Q(member_active=False))
    return qs.order_by("employee__name", "scheme")


def _missing_qs(f):
    """Karyawan AKTIF dengan status program 'aktif' tetapi belum punya potongan pada periode terpilih."""
    schemes = [f["scheme"]] if f["scheme"] in ("kes", "tk") else ["kes", "tk"]
    out = []
    for sc in schemes:
        has = BpjsDeduction.objects.filter(employee=OuterRef("employee"), scheme=sc, period=f["period"])
        qs = BpjsMembership.objects.filter(status="aktif", scheme=sc, employee__status="aktif").annotate(has=Exists(has)).filter(has=False).select_related("employee", "employee__department")
        if _int(f["department"]): qs = qs.filter(employee__department_id=_int(f["department"]))
        if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
        out += [(m.employee, m.scheme) for m in qs.order_by("employee__name")]
    return out


@hrd_only
def bpjs_deductions(request, scheme=None):
    """Rekap potongan. `scheme` dari URL (/kes/, /tk/ — submenu sidebar) hanya bawaan: parameter ?scheme= di query menang (termasuk "" = semua)."""
    f = {k: request.GET.get(k, "").strip() for k in ("q", "period", "scheme", "department", "anomaly")}
    if scheme and "scheme" not in request.GET: f["scheme"] = scheme
    f["period"] = f["period"] or date.today().strftime("%Y-%m")
    qs = _deduction_qs(f)
    if request.GET.get("export"):  # nomor BPJS TIDAK ikut; hanya NIK, nama, nominal
        log(request, "hrd", "bpjs_deduction_export", None, None, {"period": f["period"], "scheme": f["scheme"], "rows": qs.count()})
        rows = [(d.employee.nik, d.employee.name, d.employee.department.name, d.get_scheme_display(), d.period, d.employee_amount, d.employer_amount, d.note) for d in qs[:20000]]
        return tabular.export_response(f"potongan-bpjs-{f['period']}", ["nik", "nama", "departemen", "program", "periode", "porsi_karyawan", "porsi_perusahaan", "keterangan"], rows, request,
                                       sheet="Potongan BPJS", money_cols=("porsi_karyawan", "porsi_perusahaan"))
    totals = qs.aggregate(emp=Sum("employee_amount"), er=Sum("employer_amount"), n=Count("id"))
    by_scheme = {r["scheme"]: r for r in qs.values("scheme").annotate(emp=Sum("employee_amount"), er=Sum("employer_amount"), n=Count("id"))}
    by_dept = list(qs.order_by().values("employee__department__name").annotate(emp=Sum("employee_amount"), er=Sum("employer_amount"), n=Count("id")).order_by("employee__department__name"))
    missing = _missing_qs(f) if f["anomaly"] == "belum" and _period_ok(f["period"]) else None
    page = paginate(request, qs)
    from urllib.parse import urlencode
    g = request.GET  # "act": pengguna benar-benar memilih filter (periode & program bawaan dari URL tidak dihitung) → filter lanjutan terbuka + tombol Reset muncul
    act = bool(g.get("q") or g.get("department") or g.get("anomaly") or ("scheme" in g and g.get("scheme", "") != (scheme or "")))
    return render(request, "hrd/bpjs_deductions.html", {"page": page, "f": f, "act": act, "totals": totals, "by_scheme": by_scheme, "by_dept": by_dept, "missing": missing and missing[:200], "missing_n": len(missing) if missing else 0,
                                                       "departments": Department.objects.order_by("name"), "qs": urlencode({k: v for k, v in f.items() if v})})


@hrd_only
def bpjs_deduction_new(request):
    form = BpjsDeductionForm(request.POST or None, user=request.user, initial={"period": date.today().strftime("%Y-%m")})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            d = form.save()
            log(request, "hrd", "bpjs_deduction_set", d, None, {"employee": form.employee.nik, "scheme": d.scheme, "period": d.period, "employee_amount": d.employee_amount, "employer_amount": d.employer_amount})
        messages.success(request, "Potongan BPJS disimpan (periode yang sama diganti bila sudah ada)."); return redirect(f"/hrd/bpjs/deductions/?period={d.period}")
    return render(request, "hrd/form.html", {"form": form, "title": "Potongan BPJS", "back": "/hrd/bpjs/deductions/",
                                             "hint": "Satu baris per karyawan, program, dan periode. Menyimpan ulang kombinasi yang sama menggantikan nilai lama. Untuk banyak baris pakai Impor."})


# ================================================================ Bantuan (rekap)
@hrd_only
def aid_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "kind", "year")}
    qs = Aid.objects.select_related("employee", "employee__department")
    if f["kind"]: qs = qs.filter(kind=f["kind"])
    if _int(f["year"]): qs = qs.filter(event_date__year=_int(f["year"]))
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    agg = qs.aggregate(t=Sum("amount"), n=Count("id"))
    by_kind = list(qs.values("kind").annotate(n=Count("id"), t=Sum("amount")).order_by("-t"))
    labels = dict(Aid.KINDS)
    for r in by_kind: r["label"] = labels.get(r["kind"], r["kind"])
    return render(request, "hrd/aid_list.html", {"page": paginate(request, qs), "f": f, "kinds": Aid.KINDS, "total": agg["t"] or 0, "count": agg["n"] or 0, "by_kind": by_kind,
                                                  "qs": _qs(request, "q", "kind", "year")})


@hrd_only
def aid_new(request):
    form = AidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            a = Aid.objects.create(employee=form.employee, kind=d["kind"], event_date=d["event_date"], amount=d["amount"], description=d["description"], created_by=request.user)
            log(request, "hrd", "aid_create", a, None, {"employee": form.employee.nik, "kind": a.kind, "amount": a.amount, "event_date": str(a.event_date)})
        messages.success(request, "Bantuan dicatat."); return redirect("hrd_aids")
    return render(request, "hrd/form.html", {"form": form, "title": "Bantuan baru", "back": "/hrd/aids/"})


@hrd_only
def aid_edit(request, pk):
    a = get_object_or_404(Aid.objects.select_related("employee"), pk=pk)
    form = AidForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        before = {"kind": a.kind, "amount": a.amount, "event_date": str(a.event_date), "description": a.description}
        with transaction.atomic():
            a.kind, a.event_date, a.amount, a.description = d["kind"], d["event_date"], d["amount"], d["description"]; a.save()
            log(request, "hrd", "aid_update", a, before, {"kind": a.kind, "amount": a.amount, "event_date": str(a.event_date), "description": a.description})
        messages.success(request, "Perubahan disimpan."); return redirect("hrd_aids")
    return render(request, "hrd/form.html", {"form": form, "title": f"Ubah bantuan · {a.employee.name}", "back": "/hrd/aids/"})


@hrd_only
@require_POST
def aid_delete(request, pk):
    """Rekapan boleh dihapus (salah ketik); isi sebelum dihapus tetap tercatat di audit."""
    a = get_object_or_404(Aid.objects.select_related("employee"), pk=pk)
    with transaction.atomic():
        log(request, "hrd", "aid_delete", a, {"employee": a.employee.nik, "kind": a.kind, "amount": a.amount, "event_date": str(a.event_date)}, None)
        a.delete()
    messages.success(request, "Catatan bantuan dihapus."); return redirect("hrd_aids")


# ================================================================ Cuti hamil
@hrd_only
def maternity_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "state")}
    qs = MaternityLeave.objects.select_related("employee", "employee__department")
    if f["state"]: qs = qs.filter(state=f["state"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    today = date.today(); page = paginate(request, qs)
    for m in page: m.phase_label = m.phase(today)
    return render(request, "hrd/maternity_list.html", {"page": page, "f": f, "states": MaternityLeave.STATES, "qs": _qs(request, "q", "state")})


def _warn_overlap(request, emp, start, end):
    hits = services.overlapping_requests(emp, start, end)
    if hits: messages.warning(request, f"Perhatian: ada {len(hits)} pengajuan izin/cuti/sakit {emp.nik} yang tumpang tindih dengan rentang cuti hamil ini (#{', #'.join(str(h.pk) for h in hits)}). Mohon ditinjau.")


@hrd_only
def maternity_new(request):
    form = MaternityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            m = MaternityLeave.objects.create(employee=form.employee, due_date=d["due_date"], start_date=d["start_date"], end_date=d["end_date"], note=d["note"], created_by=request.user)
            log(request, "hrd", "maternity_create", m, None, {"employee": form.employee.nik, "start": str(m.start_date), "end": str(m.end_date)})
        _warn_overlap(request, form.employee, m.start_date, m.end_date)
        messages.success(request, f"Cuti hamil dicatat: {m.start_date:%d-%m-%Y} s/d {m.end_date:%d-%m-%Y}.")
        return redirect("hrd_maternity_detail", pk=m.pk)
    return render(request, "hrd/form.html", {"form": form, "title": "Cuti hamil baru", "back": "/hrd/maternity/",
                                              "hint": "Tanggal mulai/selesai yang dikosongkan dihitung otomatis dari HPL (bawaan 45 hari sebelum dan 45 hari sesudah)."})


@hrd_only
def maternity_detail(request, pk):
    m = get_object_or_404(MaternityLeave.objects.select_related("employee", "employee__department", "created_by"), pk=pk)
    return render(request, "hrd/maternity_detail.html", {"m": m, "phase": m.phase(date.today()), "finish_form": FinishMaternityForm()})


@hrd_only
def maternity_edit(request, pk):
    m = get_object_or_404(MaternityLeave.objects.select_related("employee"), pk=pk)
    if m.state != "aktif":
        messages.error(request, "Hanya cuti hamil berstatus Aktif yang bisa diubah."); return redirect("hrd_maternity_detail", pk=m.pk)
    form = MaternityForm(request.POST or None, instance=m)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        before = {"due_date": str(m.due_date), "start": str(m.start_date), "end": str(m.end_date), "note": m.note}
        with transaction.atomic():
            m.due_date, m.start_date, m.end_date, m.note = d["due_date"], d["start_date"], d["end_date"], d["note"]; m.save()
            log(request, "hrd", "maternity_update", m, before, {"due_date": str(m.due_date), "start": str(m.start_date), "end": str(m.end_date), "note": m.note})
        _warn_overlap(request, m.employee, m.start_date, m.end_date)
        messages.success(request, "Perubahan disimpan."); return redirect("hrd_maternity_detail", pk=m.pk)
    return render(request, "hrd/form.html", {"form": form, "title": f"Ubah cuti hamil #{m.pk}", "back": f"/hrd/maternity/{m.pk}/"})


@hrd_only
@require_POST
def maternity_action(request, pk, action):
    if action not in ("finish", "cancel"): raise Http404
    get_object_or_404(MaternityLeave, pk=pk)
    try:
        with transaction.atomic():
            if action == "finish":
                fm = FinishMaternityForm(request.POST)
                if not fm.is_valid(): raise ValueError("Tanggal lahir tidak valid.")
                m, before = services.maternity_finish(pk, fm.cleaned_data["delivery_date"])
                log(request, "hrd", "maternity_finish", m, before, {"state": "selesai", "delivery_date": str(m.delivery_date) if m.delivery_date else None})
            else:
                m = services.maternity_cancel(pk, request.POST.get("reason", ""))
                log(request, "hrd", "maternity_cancel", m, {"state": "aktif"}, {"state": "batal", "reason": m.cancel_reason})
        messages.success(request, "Cuti hamil ditandai selesai." if action == "finish" else "Cuti hamil dibatalkan.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("hrd_maternity_detail", pk=pk)


# ================================================================ Proyek & kerja harian
@hrd_only
def project_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "status")}
    qs = Project.objects.annotate(n_logs=Count("works"))
    if f["status"]: qs = qs.filter(status=f["status"])
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(code__icontains=f["q"]))
    return render(request, "hrd/project_list.html", {"page": paginate(request, qs.order_by("-start_date", "code")), "f": f, "statuses": Project.STATUSES, "qs": _qs(request, "q", "status")})


@hrd_only
def project_form(request, pk=None):
    p = get_object_or_404(Project, pk=pk) if pk else None
    before = {f: str(getattr(p, f)) for f in ProjectForm.Meta.fields} if p else None
    form = ProjectForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save(commit=False)
            if not p: o.created_by = request.user
            o.save()
            log(request, "hrd", "project_update" if p else "project_create", o, before, {f: str(getattr(o, f)) for f in ProjectForm.Meta.fields})
        messages.success(request, "Proyek disimpan."); return redirect("hrd_project_detail", pk=o.pk)
    return render(request, "hrd/form.html", {"form": form, "title": f"Ubah proyek {p.code}" if p else "Proyek baru", "back": f"/hrd/projects/{p.pk}/" if p else "/hrd/projects/"})


@hrd_only
def project_detail(request, pk):
    p = get_object_or_404(Project, pk=pk)
    qs = p.works.all()
    d1, d2 = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if d1: qs = qs.filter(work_date__gte=d1)
    if d2: qs = qs.filter(work_date__lte=d2)
    q = request.GET.get("q", "").strip()
    if q: qs = qs.filter(worker_name__icontains=q)
    agg = qs.aggregate(t=Sum("wage"), n=Count("id"), days=Count("work_date", distinct=True))
    per_worker = list(qs.values("worker_name").annotate(days=Count("work_date", distinct=True), total=Sum("wage")).order_by("worker_name"))
    page = paginate(request, qs)
    return render(request, "hrd/project_detail.html", {"p": p, "page": page, "agg": agg, "per_worker": per_worker, "from": request.GET.get("from", ""), "to": request.GET.get("to", ""),
                                                        "q": q, "qs": _qs(request, "from", "to", "q")})


@hrd_only
def project_work_form(request, pk, work_id=None):
    p = get_object_or_404(Project, pk=pk)
    obj = get_object_or_404(ProjectWork, pk=work_id, project=p) if work_id else None
    if not obj and p.status != "aktif":
        messages.error(request, "Catatan baru hanya untuk proyek berstatus Aktif."); return redirect("hrd_project_detail", pk=p.pk)
    fields = ("work_date", "worker_name", "activity", "wage", "note")
    before = {f: str(getattr(obj, f)) for f in fields} if obj else None
    form = ProjectWorkForm(request.POST or None, instance=obj, project=p, initial={"work_date": request.GET.get("date") or date.today()} if not obj else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save(commit=False)
            if not obj: o.created_by = request.user
            o.save()
            log(request, "hrd", "project_work_update" if obj else "project_work_create", o, before, {f: str(getattr(o, f)) for f in fields})
        messages.success(request, "Catatan pekerja harian disimpan.")
        if "again" in request.POST: return redirect(f"{request.path}?date={o.work_date.isoformat()}")  # input beruntun untuk hari yang sama
        return redirect("hrd_project_detail", pk=p.pk)
    return render(request, "hrd/form.html", {"form": form, "title": f"{'Ubah' if obj else 'Pekerja harian'} · {p.code} {p.name}", "back": f"/hrd/projects/{p.pk}/", "again": not obj})


@hrd_only
@require_POST
def project_work_delete(request, pk, work_id):
    w = get_object_or_404(ProjectWork, pk=work_id, project_id=pk)
    with transaction.atomic():
        log(request, "hrd", "project_work_delete", w, {"worker": w.worker_name, "date": str(w.work_date), "activity": w.activity, "wage": w.wage}, None)
        w.delete()
    messages.success(request, "Catatan dihapus."); return redirect("hrd_project_detail", pk=pk)


# ================================================================ Katering (rekap)
@hrd_only
def catering_list(request):
    today = date.today()
    d1 = _date(request.GET.get("from")) or today.replace(day=1)
    d2 = _date(request.GET.get("to")) or today
    f = {"meal": request.GET.get("meal", "").strip(), "only": request.GET.get("only", "").strip()}
    qs = CateringOrder.objects.filter(date__gte=d1, date__lte=d2)
    if f["meal"]: qs = qs.filter(meal=f["meal"])
    t = qs.aggregate(ol=Sum("qty_large"), os=Sum("qty_small"), rl=Sum("received_large"), rs=Sum("received_small"))
    pending = qs.filter(received_large__isnull=True).count()  # ikut menyempit oleh periode & jam makan; filter "only" tidak mengubahnya (hasilnya sama)
    if f["only"] == "belum": qs = qs.filter(received_large__isnull=True)
    g = request.GET  # "act": pengguna memilih periode/jam makan/filter sendiri → Reset filter muncul (tanpa parameter = bawaan bulan berjalan)
    act = bool(g.get("from") or g.get("to") or g.get("meal") or f["only"] == "belum")
    return render(request, "hrd/catering_list.html", {"page": paginate(request, qs), "f": f, "act": act, "from": d1.isoformat(), "to": d2.isoformat(), "meals": CateringOrder.MEALS,
                                                       "t": {k: v or 0 for k, v in t.items()}, "pending": pending, "qs": _qs(request, "from", "to", "meal", "only")})


@hrd_only
def catering_form(request, pk=None):
    o = get_object_or_404(CateringOrder, pk=pk) if pk else None
    fields = CateringForm.Meta.fields
    before = {f: str(getattr(o, f)) for f in fields} if o else None
    form = CateringForm(request.POST or None, instance=o)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            x = form.save(commit=False)
            if not o: x.created_by = request.user
            x.save()
            log(request, "hrd", "catering_update" if o else "catering_create", x, before, {f: str(getattr(x, f)) for f in fields})
        messages.success(request, "Rekap katering disimpan."); return redirect("hrd_catering")
    return render(request, "hrd/form.html", {"form": form, "title": "Ubah rekap katering" if o else "Rekap katering baru", "back": "/hrd/catering/"})


@hrd_only
@require_POST
def catering_delete(request, pk):
    o = get_object_or_404(CateringOrder, pk=pk)
    with transaction.atomic():
        log(request, "hrd", "catering_delete", o, {"date": str(o.date), "meal": o.meal, "large": o.qty_large, "small": o.qty_small}, None)
        o.delete()
    messages.success(request, "Rekap dihapus."); return redirect("hrd_catering")


# ================================================================ Surat Peringatan
@hrd_only
def warning_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "level", "state")}
    qs = WarningLetter.objects.select_related("employee", "employee__department")
    today = date.today()
    if f["level"] in ("1", "2", "3"): qs = qs.filter(level=int(f["level"]))
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]) | Q(number__icontains=f["q"]))
    if f["state"] == "aktif": qs = qs.filter(revoked_at__isnull=True, issue_date__lte=today, valid_until__gte=today)
    elif f["state"] == "kedaluwarsa": qs = qs.filter(revoked_at__isnull=True, valid_until__lt=today)
    elif f["state"] == "dicabut": qs = qs.filter(revoked_at__isnull=False)
    page = paginate(request, qs)
    for w in page: w.state_label = w.state(today)
    return render(request, "hrd/warning_list.html", {"page": page, "f": f, "levels": WarningLetter.LEVELS, "qs": _qs(request, "q", "level", "state")})


@hrd_only
def warning_new(request):
    form = WarningForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            w = services.issue_warning(form.employee, d["level"], d["issue_date"], d["valid_until"], d["violation"], d["description"], request.user)
            log(request, "hrd", "warning_create", w, None, {"number": w.number, "employee": form.employee.nik, "level": w.level, "valid_until": str(w.valid_until), "violation": w.violation})
        if form.skipped: messages.warning(request, f"Perhatian: SP {w.level} diterbitkan tanpa SP {w.level - 1} aktif sebelumnya (lompat tingkat).")
        messages.success(request, f"SP {w.level} diterbitkan: {w.number}.")
        return redirect("hrd_warning_detail", pk=w.pk)
    return render(request, "hrd/form.html", {"form": form, "title": "Surat Peringatan baru", "back": "/hrd/warnings/",
                                              "hint": "Satu karyawan tidak boleh punya dua SP aktif pada tingkat sama/lebih rendah. Masa berlaku bawaan 6 bulan."})


@hrd_only
def warning_detail(request, pk):
    w = get_object_or_404(WarningLetter.objects.select_related("employee", "employee__department", "employee__position", "created_by", "revoked_by"), pk=pk)
    history = WarningLetter.objects.filter(employee=w.employee).exclude(pk=w.pk).order_by("-issue_date")[:10]
    return render(request, "hrd/warning_detail.html", {"w": w, "state": w.state(date.today()), "history": history})


@hrd_only
@require_POST
def warning_revoke(request, pk):
    get_object_or_404(WarningLetter, pk=pk)
    try:
        with transaction.atomic():
            w = services.revoke_warning(pk, request.POST.get("reason", ""), request.user)
            log(request, "hrd", "warning_revoke", w, {"revoked": False}, {"revoked": True, "reason": w.revoke_reason})
        messages.success(request, "SP dicabut.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("hrd_warning_detail", pk=pk)


@hrd_only
def warning_pdf(request, pk):
    from .pdf import warning_pdf as make
    w = get_object_or_404(WarningLetter.objects.select_related("employee", "employee__department", "employee__position"), pk=pk)
    if w.revoked_at: raise Http404
    log(request, "hrd", "print_warning", w)
    resp = HttpResponse(make(w), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{w.number.replace("/", "-")}.pdf"'
    return resp
