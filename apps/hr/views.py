import json
from django.core.paginator import Paginator
from datetime import date, timedelta
from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department, get_scoped_or_404
from .models import Employee, ChangeRequest, Contract
from . import services

PUBLIC = ("id", "nik", "name", "department__name", "position__name", "status")
FULL = PUBLIC + ("join_date", "gender", "marital_status", "education")  # tanpa data sensitif

def _contract_filter(qs, kind):
    act = Contract.objects.filter(employee=OuterRef("pk"), status="aktif"); today = date.today()
    if kind == "none": return qs.filter(~Exists(act))
    if kind == "aktif": return qs.filter(Exists(act.filter(Q(end__isnull=True) | Q(end__gte=today))))
    if kind == "expiring": return qs.filter(Exists(act.filter(end__gte=today, end__lte=today + timedelta(days=30))))
    if kind == "expired": return qs.filter(Exists(act.filter(end__lt=today)))
    return qs

def _employee_qs(request):
    qs = scope_by_department(request.user, Employee.objects.select_related("department", "position"))
    q = request.GET.get("q", "").strip()
    if q: qs = qs.filter(Q(name__icontains=q) | Q(nik__startswith=q))
    if request.GET.get("status"): qs = qs.filter(status=request.GET["status"])
    for param, col in (("department", "department_id"), ("position", "position_id"), ("shift", "shift_id")):
        v = request.GET.get(param, "")
        if v.isdigit() and not (param == "department" and request.user.role == Role.DEPT_ADMIN):  # input non-angka diabaikan (bukan 500)
            qs = qs.filter(**{col: int(v)})
    c = request.GET.get("contract", "")
    if c and request.user.role in (Role.HRD, Role.SUPERADMIN):  # info kontrak hanya untuk HRD: role lain tidak boleh memakainya sebagai filter
        qs = _contract_filter(qs, c)
    return qs


@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employee_list(request):
    qs = _employee_qs(request)
    page = Paginator(qs.order_by("name"), 50).get_page(request.GET.get("page", 1))  # pagination di server
    cols = PUBLIC if request.user.role in (Role.POLI, Role.DEPT_ADMIN) else FULL
    return JsonResponse({"count": page.paginator.count, "page": page.number, "results": list(page.object_list.values(*cols))})

@require_roles(Role.HRD)
def employee_detail(request, pk):
    emp = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    log(request, "hr", "view_sensitive", emp)  # akses data sensitif tercatat
    data = {f.name: str(getattr(emp, f.name)) for f in Employee._meta.concrete_fields}
    return JsonResponse(data)

@require_POST
@require_roles(Role.HRD, Role.DEPT_ADMIN)
def request_transition(request, pk, to):
    req = get_scoped_or_404(request.user, ChangeRequest.objects.all(), pk)  # URL tampering → 404
    before = {"status": req.status}
    try: req = services.transition(req, to, request.user)
    except PermissionError as e: return JsonResponse({"detail": str(e)}, status=403)
    except ValueError as e: return JsonResponse({"detail": str(e)}, status=400)
    log(request, "hr", f"request_{to}", req, before, {"status": req.status})
    return JsonResponse({"id": req.pk, "status": req.status})

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employees_page(request):
    from .models import Department, Position, Shift
    hrd = request.user.role in (Role.HRD, Role.SUPERADMIN)
    return render(request, "employees.html", {"departments": Department.objects.order_by("name") if request.user.role != Role.DEPT_ADMIN else [],
                                              "positions": Position.objects.order_by("name"), "shifts": Shift.objects.order_by("name"), "hrd": hrd})


EXPORT_COLUMNS = ["nik", "name", "gender", "join_date", "department_code", "position", "shift", "shift_group", "gs_short", "status"]  # tanpa data sensitif


@require_roles(Role.HRD, Role.DEPT_ADMIN)
def employee_export(request):
    """Ekspor karyawan (CSV/XLSX) dengan filter yang sama seperti daftar; Admin Dept hanya departemennya. Kolom sama dengan template impor; tanpa data sensitif."""
    from apps.core import tabular
    qs = _employee_qs(request).select_related("department", "position", "shift", "shift_group").order_by("name")[:20000]
    rows = [[e.nik, e.name, e.gender, e.join_date, e.department.code, e.position.name if e.position_id else "", e.shift.name if e.shift_id else "",
             e.shift_group.code if e.shift_group_id else "", e.gs_short if e.shift_id and e.shift.is_gs else "", e.status] for e in qs]
    log(request, "hr", "employee_export", None, None, {"rows": len(rows), "format": tabular.fmt(request)})
    return tabular.export_response("karyawan", EXPORT_COLUMNS, rows, request, sheet="Karyawan")
