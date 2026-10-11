"""Rekap visit pasien (putaran 46): rawat jalan / sakit (istirahat) / kunjungan rumah. SEMUA view: Poli/Superadmin saja (data medis).
Keluhan & hasil terenkripsi dan TIDAK masuk audit/ekspor ringkasan; membuka detail tercatat. Visit tidak dihapus: selesai (hasil wajib) atau batal (alasan wajib)."""
from datetime import date, timedelta
from urllib.parse import urlencode
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.hr.models import Department
from . import services
from .forms import VisitForm
from .models import PatientVisit
from .pages import identity_rows, paginate, poli_only

EXPORT_MAX = 20000
HEADER = ["Tanggal", "NIK", "Nama", "Departemen", "Jenis", "Status", "Lokasi", "Hari istirahat"]


def _date(v):
    try: return date.fromisoformat(v)
    except (TypeError, ValueError): return None


def _filters(request):
    today = timezone.localdate()
    f = {k: request.GET.get(k, "").strip() for k in ("q", "kind", "status", "department", "date_from", "date_to")}
    if "date_from" not in request.GET and "date_to" not in request.GET: f["date_from"], f["date_to"] = today.replace(day=1).isoformat(), today.isoformat()  # bawaan: bulan berjalan
    return f


def _base(f):
    qs = PatientVisit.objects.select_related("employee", "employee__department")
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if f["department"].isdigit(): qs = qs.filter(employee__department_id=int(f["department"]))
    a, b = _date(f["date_from"]), _date(f["date_to"])
    if a: qs = qs.filter(visit_date__gte=a)
    if b: qs = qs.filter(visit_date__lte=b)
    return qs


@poli_only
def visit_list(request):
    f = _filters(request); base = _base(f)
    qs = base
    if f["kind"] in dict(PatientVisit.KINDS): qs = qs.filter(kind=f["kind"])
    if f["status"] in dict(PatientVisit.STATUSES): qs = qs.filter(status=f["status"])
    qs = qs.order_by("-visit_date", "-id")
    if request.GET.get("export"):  # ringkasan TANPA isi medis (keluhan/hasil)
        n = qs.count()
        if n > EXPORT_MAX: messages.error(request, f"Data terlalu banyak ({n}); persempit filter (maks {EXPORT_MAX})."); return redirect("poli_visits")
        log(request, "poli", "visit_export", None, None, {"rows": n, "from": f["date_from"], "to": f["date_to"]})
        rows = [[v.visit_date, v.employee.nik, v.employee.name, v.employee.department.name, v.get_kind_display(), v.get_status_display(), v.location, v.rest_days] for v in qs]
        return tabular.export_response(f"rekap-visit-{f['date_from'] or 'awal'}-{f['date_to'] or 'akhir'}", HEADER, rows, request, sheet="Rekap visit")
    per = {r["kind"]: r["n"] for r in base.values("kind").annotate(n=Count("id"))}
    running = base.filter(status="berjalan").count()
    qstr = urlencode({k: v for k, v in f.items() if v})
    return render(request, "poli/visit_list.html", {"page": paginate(request, qs), "f": f, "kinds": PatientVisit.KINDS, "statuses": PatientVisit.STATUSES, "qs": qstr,
                  "per": [(k, label, per.get(k, 0)) for k, label in PatientVisit.KINDS], "running": running, "departments": Department.objects.order_by("name"), "total": sum(per.values())})


@poli_only
def visit_new(request):
    form = VisitForm(request.POST or None, initial={"nik": request.GET.get("nik", ""), "kind": request.GET.get("kind", ""), "visit_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        c = form.cleaned_data
        try: v = services.create_visit(request.user, form.employee, c["kind"], c["visit_date"], c["complaint"], c["location"], c["rest_days"])
        except ValueError as ex: form.add_error(None, str(ex))
        else:
            log(request, "poli", "visit_create", v, None, {"employee": form.employee.nik, "kind": v.kind, "date": v.visit_date.isoformat()})  # keluhan tidak masuk audit
            messages.success(request, "Visit dicatat."); return redirect("poli_visit_detail", pk=v.pk)
    return render(request, "poli/visit_form.html", {"form": form})


@poli_only
def visit_detail(request, pk):
    v = get_object_or_404(PatientVisit.objects.select_related("employee", "employee__department", "employee__position", "created_by"), pk=pk)
    log(request, "poli", "view_visit", v)
    return render(request, "poli/visit_detail.html", {"v": v, "idn": identity_rows(v.employee), "next": sorted(PatientVisit.FLOW.get(v.status, ()))})


@poli_only
@require_POST
def visit_action(request, pk, action):
    to = {"finish": "selesai", "cancel": "batal"}.get(action)
    if not to: raise Http404
    get_object_or_404(PatientVisit, pk=pk)
    try:
        v, before = services.visit_transition(pk, to, request.user, request.POST.get("outcome", ""))
        log(request, "poli", f"visit_{action}", v, {"status": before}, {"status": v.status})  # hasil/alasan tidak masuk audit
        messages.success(request, f"Status visit: {v.get_status_display()}.")
    except ValueError as ex: messages.error(request, str(ex))
    return redirect("poli_visit_detail", pk=pk)
