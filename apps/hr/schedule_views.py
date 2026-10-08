"""Jadwal shift mingguan per departemen (baca saja): rotasi kelompok / shift tetap + penyesuaian tukar. HRD/Superadmin semua; Admin Dept hanya departemennya."""
from datetime import date, timedelta
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department
from . import schedule
from .models import Department, Employee, ShiftGroup


def _monday(raw):
    try: d = date.fromisoformat(raw)
    except (TypeError, ValueError): d = date.today()
    if not 2000 <= d.year <= 2100: d = date.today()
    return d - timedelta(days=d.weekday())


@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN)
def schedule_week(request):
    start = _monday(request.GET.get("start", ""))
    qs = scope_by_department(request.user, Employee.objects.filter(status="aktif").select_related("department", "shift", "shift_group"))
    f = {"q": request.GET.get("q", "").strip()[:60], "department": request.GET.get("department", ""), "group": request.GET.get("group", "")}
    if "\x00" in f["q"]: f["q"] = ""
    if f["q"]: qs = qs.filter(Q(name__icontains=f["q"]) | Q(nik__startswith=f["q"]))
    if f["department"].isdigit() and request.user.role != Role.DEPT_ADMIN and int(f["department"]) < 2**31: qs = qs.filter(department_id=int(f["department"]))
    if f["group"].isdigit() and int(f["group"]) < 2**31: qs = qs.filter(shift_group_id=int(f["group"]))
    page = Paginator(qs.order_by("department__name", "name"), 50).get_page(request.GET.get("page", 1))
    fq = "&".join(f"{k}={v}" for k, v in f.items() if v)
    return render(request, "schedule_week.html", {
        "rows": schedule.schedule_grid(page.object_list, start, 7), "days": [start + timedelta(days=i) for i in range(7)], "page": page, "f": f,
        "prev": (start - timedelta(days=7)).isoformat(), "next": (start + timedelta(days=7)).isoformat(), "start": start, "fq": fq, "qs": f"start={start.isoformat()}" + (f"&{fq}" if fq else ""),
        "departments": Department.objects.order_by("name") if request.user.role != Role.DEPT_ADMIN else [], "groups": ShiftGroup.objects.all()})
