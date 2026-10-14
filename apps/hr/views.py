import json
from django.core.paginator import Paginator
from datetime import date, timedelta
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Value, When
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department, get_scoped_or_404
from .models import Employee, ChangeRequest, Contract, ShiftGroup
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

# Filter "Shift" = jenis jadwal (A7 = pola 2 shift A–G, A7 Pack = pola 3 shift/PACK A_pack–G_pack, GS = General Shift); angka lama (id shift) tetap diterima.
SHIFT_FILTERS = (("a7", "A7"), ("a7pack", "A7 Pack"), ("gs", "GS"))

def apply_shift_filter(qs, v):
    v = (v or "").strip().lower()
    if v == "a7": return qs.filter(shift_group__pattern=ShiftGroup.P2)
    if v == "a7pack": return qs.filter(shift_group__pattern=ShiftGroup.P3)
    if v == "gs": return qs.filter(shift__is_gs=True)
    if v.isdigit(): return qs.filter(shift_id=int(v))  # kompatibel dengan tautan lama
    return qs

# Urutkan & Kelompokkan di server (halaman Karyawan berpaginasi: urutan harus atas seluruh data, bukan satu halaman)
EMP_SORT = {"nik": "nik", "name": "name", "department": "department__name", "position": "position__name", "status": "status", "join_date": "join_date"}
EMP_GROUP = ("department", "position", "status", "shift")

def group_expr(g):
    """Ungkapan pengelompokan untuk daftar karyawan; None bila g bukan kelompok yang dikenal."""
    if g == "department": return F("department__name")
    if g == "position": return F("position__name")
    if g == "status": return F("status")
    if g == "shift":
        return Case(When(shift_group__pattern=ShiftGroup.P2, then=Value("A7")), When(shift_group__pattern=ShiftGroup.P3, then=Value("A7 Pack")),
                    When(shift__is_gs=True, then=Value("GS")), default=Value("Tanpa kelompok/GS"), output_field=CharField())
    return None

def order_employees(qs, sort, group, hrd=True):
    """Kembalikan (qs, ada_grup). sort: 'name' atau '-name'; field di luar daftar putih diabaikan (bukan 500).
    Tanggal masuk hanya boleh jadi kunci urut bagi HRD/Superadmin (role lain tidak melihat kolom itu)."""
    if (sort or "").lstrip("-") == "join_date" and not hrd: sort = ""
    order = []
    ge = group_expr(group)
    if ge is not None: qs = qs.annotate(grp=ge); order.append("grp")
    key = (sort or "").lstrip("-")
    if key in EMP_SORT: order.append(("-" if (sort or "").startswith("-") else "") + EMP_SORT[key])
    if "name" not in [o.lstrip("-") for o in order]: order.append("name")
    order.append("pk")  # urutan stabil lintas halaman
    return qs.order_by(*order), ge is not None

def _employee_qs(request):
    qs = scope_by_department(request.user, Employee.objects.select_related("department", "position"))
    q = request.GET.get("q", "").strip()
    if q: qs = qs.filter(Q(name__icontains=q) | Q(nik__startswith=q))
    if request.GET.get("status"): qs = qs.filter(status=request.GET["status"])
    qs = apply_shift_filter(qs, request.GET.get("shift", ""))
    for param, col in (("department", "department_id"), ("position", "position_id")):
        v = request.GET.get(param, "")
        if v.isdigit() and not (param == "department" and request.user.role == Role.DEPT_ADMIN):  # input non-angka diabaikan (bukan 500)
            qs = qs.filter(**{col: int(v)})
    c = request.GET.get("contract", "")
    if c and request.user.role in (Role.HRD, Role.SUPERADMIN):  # info kontrak hanya untuk HRD: role lain tidak boleh memakainya sebagai filter
        qs = _contract_filter(qs, c)
    return qs


@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employee_list(request):
    qs, grouped = order_employees(_employee_qs(request), request.GET.get("sort", ""), request.GET.get("group", ""), request.user.role in (Role.HRD, Role.SUPERADMIN))
    page = Paginator(qs, 50).get_page(request.GET.get("page", 1))  # pagination di server
    cols = PUBLIC if request.user.role in (Role.POLI, Role.DEPT_ADMIN) else FULL
    if grouped: cols = cols + ("grp",)
    return JsonResponse({"count": page.paginator.count, "page": page.number, "results": list(page.object_list.values(*cols))})

BULK_MAX = 5000  # batas ID per aksi massal: cukup untuk ±3.000 karyawan, tetap melindungi dari POST/IN-query yang terlalu besar


@require_roles(Role.HRD)
def employee_ids(request):
    """ID SEMUA karyawan yang cocok dengan filter yang sama seperti daftar (bukan hanya satu halaman) untuk 'Pilih semua hasil filter' di aksi massal.
    Hanya HRD/Superadmin (hanya mereka yang boleh aksi massal). Dibatasi BULK_MAX; `truncated` memberi tahu UI bila hasil melebihi batas."""
    qs, _g = order_employees(_employee_qs(request), request.GET.get("sort", ""), request.GET.get("group", ""))
    total = qs.count()
    return JsonResponse({"count": total, "ids": list(qs.values_list("pk", flat=True)[:BULK_MAX]), "limit": BULK_MAX, "truncated": total > BULK_MAX})


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
    from .models import Department, Position
    hrd = request.user.role in (Role.HRD, Role.SUPERADMIN)
    return render(request, "employees.html", {"departments": Department.objects.order_by("name") if request.user.role != Role.DEPT_ADMIN else [],
                                              "positions": Position.objects.order_by("name"), "shift_filters": SHIFT_FILTERS, "hrd": hrd})


EXPORT_COLUMNS = ["nik", "name", "gender", "join_date", "department_code", "position", "shift", "shift_group", "gs_short", "status"]  # tanpa data sensitif


@require_roles(Role.HRD, Role.DEPT_ADMIN)
def employee_export(request):
    """Ekspor karyawan (CSV/XLSX) dengan filter yang sama seperti daftar; Admin Dept hanya departemennya. Kolom sama dengan template impor; tanpa data sensitif."""
    from apps.core import tabular
    qs, _g = order_employees(_employee_qs(request), request.GET.get("sort", ""), request.GET.get("group", ""))  # ekspor mengikuti urutan layar (HRD saja yang boleh mengekspor)
    qs = qs.select_related("department", "position", "shift", "shift_group")[:20000]
    rows = [[e.nik, e.name, e.gender, e.join_date, e.department.code, e.position.name if e.position_id else "", e.shift.name if e.shift_id else "",
             e.shift_group.code if e.shift_group_id else "", e.gs_short if e.shift_id and e.shift.is_gs else "", e.status] for e in qs]
    log(request, "hr", "employee_export", None, None, {"rows": len(rows), "format": tabular.fmt(request)})
    return tabular.export_response("karyawan", EXPORT_COLUMNS, rows, request, sheet="Karyawan")
