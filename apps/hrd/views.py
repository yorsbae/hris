"""Halaman Operasional HRD. SEMUA view: login + role HRD (Superadmin lolos otomatis di require_roles). Admin Dept & Poli → 403."""
from datetime import date
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from apps.hr.models import Department, Employee
from . import services
from .forms import (AidForm, BpjsStatusForm, CateringForm, FinishMaternityForm, MaternityForm, ProjectForm, ProjectLogForm, ReceiveCateringForm)
from .models import (Aid, BpjsMembership, BpjsScheme, BpjsState, BpjsStatusLog, CateringOrder, MaternityLeave, Project, ProjectDailyLog)

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
        ("Bantuan", "/hrd/aids/", Aid.objects.filter(status="diajukan").count(), "menunggu keputusan"),
        ("Cuti hamil", "/hrd/maternity/", MaternityLeave.objects.filter(state="aktif", start_date__lte=today, end_date__gte=today).count(), "sedang cuti"),
        ("Kerja harian proyek", "/hrd/projects/", Project.objects.filter(status="aktif").count(), "proyek aktif"),
        ("Katering", "/hrd/catering/", CateringOrder.objects.filter(date=today).exclude(status="batal").count(), "pesanan hari ini"),
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
    return render(request, "hrd/bpjs_list.html", {"page": page, "f": f, "departments": Department.objects.order_by("name"),
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


# ================================================================ Bantuan
@hrd_only
def aid_list(request):
    f = {k: request.GET.get(k, "").strip() for k in ("q", "status", "kind")}
    qs = Aid.objects.select_related("employee", "employee__department")
    if f["status"]: qs = qs.filter(status=f["status"])
    if f["kind"]: qs = qs.filter(kind=f["kind"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    total = qs.exclude(status="ditolak").aggregate(t=Sum("amount"))["t"] or 0
    return render(request, "hrd/aid_list.html", {"page": paginate(request, qs), "f": f, "statuses": Aid.STATUSES, "kinds": Aid.KINDS, "total": total,
                                                  "qs": _qs(request, "q", "status", "kind")})


@hrd_only
def aid_new(request):
    form = AidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            a = Aid.objects.create(employee=form.employee, kind=d["kind"], event_date=d["event_date"], amount=d["amount"], description=d["description"], created_by=request.user)
            log(request, "hrd", "aid_create", a, None, {"employee": form.employee.nik, "kind": a.kind, "amount": a.amount, "event_date": str(a.event_date)})
        messages.success(request, "Bantuan dicatat (status: Diajukan).")
        return redirect("hrd_aid_detail", pk=a.pk)
    return render(request, "hrd/form.html", {"form": form, "title": "Bantuan baru", "back": "/hrd/aids/"})


@hrd_only
def aid_detail(request, pk):
    a = get_object_or_404(Aid.objects.select_related("employee", "employee__department", "decided_by", "created_by"), pk=pk)
    return render(request, "hrd/aid_detail.html", {"a": a, "next": sorted(Aid.FLOW.get(a.status, ()))})


@hrd_only
def aid_edit(request, pk):
    a = get_object_or_404(Aid.objects.select_related("employee"), pk=pk)
    if a.status != "diajukan":
        messages.error(request, "Hanya bantuan berstatus Diajukan yang bisa diubah."); return redirect("hrd_aid_detail", pk=a.pk)
    form = AidForm(request.POST or None, instance=a)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        before = {"kind": a.kind, "amount": a.amount, "event_date": str(a.event_date), "description": a.description}
        with transaction.atomic():
            a.kind, a.event_date, a.amount, a.description = d["kind"], d["event_date"], d["amount"], d["description"]; a.save()
            log(request, "hrd", "aid_update", a, before, {"kind": a.kind, "amount": a.amount, "event_date": str(a.event_date), "description": a.description})
        messages.success(request, "Perubahan disimpan."); return redirect("hrd_aid_detail", pk=a.pk)
    return render(request, "hrd/form.html", {"form": form, "title": f"Ubah bantuan #{a.pk}", "back": f"/hrd/aids/{a.pk}/"})


@hrd_only
@require_POST
def aid_action(request, pk, action):
    to = {"approve": "disetujui", "reject": "ditolak", "pay": "dibayar"}.get(action)
    if not to: raise Http404
    get_object_or_404(Aid, pk=pk)
    try:
        with transaction.atomic():
            a, before = services.aid_transition(pk, to, request.user, note=request.POST.get("note", ""), paid_at=_date(request.POST.get("paid_at")))
            log(request, "hrd", f"aid_{action}", a, {"status": before}, {"status": a.status, "note": a.decision_note})
        messages.success(request, f"Status bantuan: {a.get_status_display()}.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("hrd_aid_detail", pk=pk)


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
    qs = Project.objects.annotate(n_logs=Count("logs"))
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
    qs = p.logs.prefetch_related("workers")
    d1, d2 = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if d1: qs = qs.filter(work_date__gte=d1)
    if d2: qs = qs.filter(work_date__lte=d2)
    days = qs.values("work_date").distinct().count()
    page = paginate(request, qs)
    return render(request, "hrd/project_detail.html", {"p": p, "page": page, "days": days, "from": request.GET.get("from", ""), "to": request.GET.get("to", ""),
                                                        "qs": _qs(request, "from", "to")})


@hrd_only
def project_log_form(request, pk, log_id=None):
    p = get_object_or_404(Project, pk=pk)
    obj = get_object_or_404(ProjectDailyLog, pk=log_id, project=p) if log_id else None
    if not obj and p.status != "aktif":
        messages.error(request, "Catatan harian baru hanya untuk proyek berstatus Aktif."); return redirect("hrd_project_detail", pk=p.pk)
    before = {"work_date": str(obj.work_date), "activity": obj.activity, "headcount": obj.headcount, "workers": sorted(obj.workers.values_list("nik", flat=True)), "note": obj.note} if obj else None
    form = ProjectLogForm(request.POST or None, instance=obj, project=p)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save(commit=False)
            if not obj: o.created_by = request.user
            o.save(); o.workers.set(form.workers)
            log(request, "hrd", "project_log_update" if obj else "project_log_create", o, before,
                {"work_date": str(o.work_date), "activity": o.activity, "headcount": o.headcount, "workers": sorted(w.nik for w in form.workers), "note": o.note})
        messages.success(request, "Catatan harian disimpan."); return redirect("hrd_project_detail", pk=p.pk)
    return render(request, "hrd/form.html", {"form": form, "title": f"{'Ubah' if obj else 'Catatan harian'} · {p.code} {p.name}", "back": f"/hrd/projects/{p.pk}/"})


# ================================================================ Katering
@hrd_only
def catering_list(request):
    today = date.today()
    d1 = _date(request.GET.get("from")) or today.replace(day=1)
    d2 = _date(request.GET.get("to")) or today
    f = {"meal": request.GET.get("meal", "").strip(), "department": request.GET.get("department", "").strip()}
    qs = CateringOrder.objects.select_related("department").filter(date__gte=d1, date__lte=d2)
    if f["meal"]: qs = qs.filter(meal=f["meal"])
    if _int(f["department"]): qs = qs.filter(department_id=_int(f["department"]))
    live = qs.exclude(status="batal")
    # rekap memakai jumlah diterima bila sudah diterima, selain itu jumlah dipesan
    rows = list(live)
    big = sum((o.received_large if o.status == "diterima" and o.received_large is not None else o.qty_large) for o in rows)
    small = sum((o.received_small if o.status == "diterima" and o.received_small is not None else o.qty_small) for o in rows)
    cost = sum(o.total_cost for o in rows)
    page = paginate(request, qs)
    return render(request, "hrd/catering_list.html", {"page": page, "f": f, "from": d1.isoformat(), "to": d2.isoformat(), "meals": CateringOrder.MEALS,
                                                       "departments": Department.objects.order_by("name"), "sum_large": big, "sum_small": small, "sum_cost": cost,
                                                       "qs": _qs(request, "from", "to", "meal", "department")})


@hrd_only
def catering_form(request, pk=None):
    o = get_object_or_404(CateringOrder, pk=pk) if pk else None
    if o and o.status != "dipesan":
        messages.error(request, "Hanya pesanan berstatus Dipesan yang bisa diubah."); return redirect("hrd_catering")
    before = {f: str(getattr(o, f)) for f in CateringForm.Meta.fields} if o else None
    form = CateringForm(request.POST or None, instance=o)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            x = form.save(commit=False)
            if not o: x.created_by = request.user
            x.save()
            log(request, "hrd", "catering_update" if o else "catering_create", x, before, {f: str(getattr(x, f)) for f in CateringForm.Meta.fields})
        messages.success(request, "Pesanan katering disimpan."); return redirect("hrd_catering")
    return render(request, "hrd/form.html", {"form": form, "title": "Ubah pesanan katering" if o else "Pesanan katering baru", "back": "/hrd/catering/"})


@hrd_only
@require_POST
def catering_action(request, pk, action):
    if action not in ("receive", "cancel"): raise Http404
    get_object_or_404(CateringOrder, pk=pk)
    try:
        with transaction.atomic():
            if action == "receive":
                f = ReceiveCateringForm(request.POST)
                if not f.is_valid(): raise ValueError("Jumlah diterima harus angka 0 atau lebih.")
                o, before = services.catering_receive(pk, f.cleaned_data["received_large"], f.cleaned_data["received_small"])
                log(request, "hrd", "catering_receive", o, before, {"status": "diterima", "large": o.received_large, "small": o.received_small})
            else:
                o = services.catering_cancel(pk, request.POST.get("reason", ""))
                log(request, "hrd", "catering_cancel", o, {"status": "dipesan"}, {"status": "batal", "note": o.note})
        messages.success(request, "Pesanan ditandai diterima." if action == "receive" else "Pesanan dibatalkan.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("hrd_catering")
