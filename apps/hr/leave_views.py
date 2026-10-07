"""Saldo cuti (HRD/Superadmin): daftar saldo, kartu per karyawan, jatah & koreksi. Pemakaian dicatat otomatis saat pengajuan cuti dilaksanakan."""
from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from . import leave
from .leave_forms import LeaveEntryForm
from .models import Department, Employee, LeaveLedger


def _year(v):
    try: y = int(v)
    except (TypeError, ValueError): return date.today().year
    return y if 2000 <= y <= 2100 else date.today().year


@login_required
@require_roles(Role.HRD)
def leave_list(request):
    year = _year(request.GET.get("year"))
    f = {k: request.GET.get(k, "").strip() for k in ("q", "department", "filter")}
    qs = Employee.objects.filter(status="aktif").select_related("department")
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    if f["department"].isdigit(): qs = qs.filter(department_id=int(f["department"]))
    qs = qs.annotate(
        bal=Coalesce(Sum("leave_ledger__days", filter=Q(leave_ledger__year=year)), Value(0), output_field=DecimalField()),
        grants=Count("leave_ledger", filter=Q(leave_ledger__year=year, leave_ledger__kind=LeaveLedger.GRANT)))
    if f["filter"] == "nogrant": qs = qs.filter(grants=0)
    elif f["filter"] == "low": qs = qs.filter(grants__gt=0, bal__lte=3)
    page = Paginator(qs.order_by("name"), 50).get_page(request.GET.get("page", 1))
    return render(request, "leave_list.html", {"page": page, "f": f, "year": year, "departments": Department.objects.order_by("name")})


@login_required
@require_roles(Role.HRD)
def leave_detail(request, pk):
    e = get_object_or_404(Employee.objects.select_related("department"), pk=pk)
    form = LeaveEntryForm(request.POST or None, employee=e)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            Employee.all_objects.select_for_update().get(pk=e.pk)  # serialisasi per karyawan
            if d["kind"] == LeaveLedger.ADJUST and leave.balance(e, d["year"]) + d["days"] < 0:  # cek ulang di bawah kunci
                messages.error(request, "Saldo berubah; koreksi akan membuat saldo negatif."); return redirect("leave_detail", pk=e.pk)
            row = LeaveLedger.objects.create(employee=e, year=d["year"], kind=d["kind"], days=d["days"], note=d["note"].strip(), created_by=request.user)
            log(request, "hr", f"leave_{d['kind']}", e, None, {"year": d["year"], "days": str(d["days"]), "ledger_id": row.pk, "note": row.note})
        messages.success(request, "Tercatat.")
        return redirect("leave_detail", pk=e.pk)
    year = _year(request.GET.get("year"))
    years = sorted({y for y in LeaveLedger.objects.filter(employee=e).values_list("year", flat=True)} | {date.today().year}, reverse=True)
    summary = [(y, leave.balance(e, y), leave.reserved(e, y), leave.available(e, y)) for y in years]
    return render(request, "leave_detail.html", {"e": e, "form": form, "summary": summary, "year": year,
                  "ledger": LeaveLedger.objects.filter(employee=e, year=year).select_related("request", "created_by").order_by("-id")})
