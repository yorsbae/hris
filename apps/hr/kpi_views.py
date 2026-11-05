"""Halaman menu KPI. HRD/Superadmin: semua departemen; Admin Departemen: hanya departemennya (scope di server); halaman SP & per-departemen khusus HRD."""
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from . import kpi
from .attendance_pages import _month
from .models import AttendanceDaily as AD, Department, Employee, PerformanceReview

both = lambda fn: login_required(require_roles(Role.HRD, Role.DEPT_ADMIN)(fn))
hrd = lambda fn: login_required(require_roles(Role.HRD)(fn))


def _rows(request):
    a, b, mval, mlabel = _month(request.GET.get("month"))
    is_admin = request.user.role == Role.DEPT_ADMIN
    qs = scope_by_department(request.user, AD.objects.filter(date__gte=a, date__lte=b))
    dept = request.GET.get("dept", "")
    if dept.isdigit() and not is_admin: qs = qs.filter(department_id=int(dept))
    rows = kpi.build(qs, a, b, label=mlabel)
    ctx = {"month": mval, "mlabel": mlabel, "is_admin": is_admin, "dept": dept, "depts": Department.objects.order_by("name"), "rules": kpi.RULES, "grades": kpi.GRADES,
           "alfa_sp": kpi.ALFA_SP, "late_warn": kpi.LATE_WARN}
    return rows, ctx, a, b


def _page(request, rows, ctx, size=100):
    qd = request.GET.copy(); qd.pop("page", None); qd.pop("export", None)
    ctx.update(page=Paginator(rows, size).get_page(request.GET.get("page", 1)), qs=qd.urlencode())


@both
def overview(request):
    rows, ctx, a, b = _rows(request)
    if request.GET.get("export"):
        head = ["nik", "nama", "departemen", "hari_kerja", "hadir", "alfa", "terlambat", "pulang_cepat", "izin", "sp_aktif", "skor", "grade", "saran"]
        data = [[r["employee__nik"], r["employee__name"], r["department__name"] or "", r["hari"], r["hadir"], r["alfa"], r["terlambat"], r["pulang_cepat"], r["izin"], r["sp"] or "",
                 "" if r["score"] is None else r["score"], r["grade"], r["saran"]] for r in rows]
        log(request, "hr", "kpi_export", None, None, {"month": ctx["month"], "rows": len(data)})
        return tabular.export_response(f"kpi-kedisiplinan-{ctx['month']}", head, data, request, sheet=f"KPI {ctx['month']}", num_cols=(3, 4, 5, 6, 7, 8, 9, 10))
    ctx["tot"] = kpi.totals(rows); _page(request, rows, ctx); ctx["tab"] = "overview"
    return render(request, "kpi/overview.html", ctx)


@both
def attendance(request):
    rows, ctx, a, b = _rows(request)
    only = request.GET.get("tindak") == "1"
    if only: rows = [r for r in rows if r["saran"]]
    rows = sorted(rows, key=lambda r: (-r["alfa"], -r["alfa3"], -r["terlambat"]))
    ctx.update(tot=kpi.totals(rows), only=only, tab="attendance"); _page(request, rows, ctx)
    return render(request, "kpi/attendance.html", ctx)


@hrd
def warnings(request):
    from apps.hrd.models import WarningLetter
    today = timezone.localdate()
    qs = WarningLetter.objects.select_related("employee", "employee__department").filter(revoked_at__isnull=True, issue_date__lte=today, valid_until__gte=today).order_by("-level", "valid_until")
    dept = request.GET.get("dept", "")
    if dept.isdigit(): qs = qs.filter(employee__department_id=int(dept))
    items = list(qs); cnt = {lv: sum(1 for w in items if w.level == lv) for lv in (1, 2, 3)}
    soon = [w for w in items if (w.valid_until - today).days <= 30]
    ctx = {"items": items, "cnt": cnt, "soon": len(soon), "dept": dept, "depts": Department.objects.order_by("name"), "today": today, "tab": "warnings", "month": ""}
    return render(request, "kpi/warnings.html", ctx)


@hrd
def departments(request):
    rows, ctx, a, b = _rows(request)
    ctx.update(items=kpi.by_department(rows), tot=kpi.totals(rows), tab="departments")
    return render(request, "kpi/departments.html", ctx)


@both
def reviews(request):
    a, b, mval, mlabel = _month(request.GET.get("month"))
    is_admin = request.user.role == Role.DEPT_ADMIN
    emps = scope_by_department(request.user, Employee.objects.select_related("department", "position").exclude(status="nonaktif"))
    dept = request.GET.get("dept", "")
    if dept.isdigit() and not is_admin: emps = emps.filter(department_id=int(dept))
    done = {x.employee_id: x for x in PerformanceReview.objects.filter(period=mval, employee__in=emps)}
    rows = [{"e": e, "r": done.get(e.pk)} for e in emps.order_by("department__name", "name")]
    n_done = sum(1 for x in rows if x["r"]); state = request.GET.get("state", "")
    if state == "belum": rows = [x for x in rows if not x["r"]]
    elif state == "sudah": rows = [x for x in rows if x["r"]]
    ctx = {"month": mval, "mlabel": mlabel, "is_admin": is_admin, "dept": dept, "depts": Department.objects.order_by("name"), "state": state, "n_done": n_done, "n_all": len(emps),
           "tab": "reviews"}
    _page(request, rows, ctx)
    return render(request, "kpi/reviews.html", ctx)


@both
def review_form(request, pk):
    from .models import PerformanceReview as PR
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "position"), pk)
    a, b, mval, mlabel = _month(request.GET.get("month") or request.POST.get("month"))
    obj = PR.objects.filter(employee=e, period=mval).first()
    if request.method == "POST":
        vals = {}
        for k, _ in PR.ASPECTS:
            v = request.POST.get(k, "")
            if v.isdigit() and 1 <= int(v) <= 5: vals[k] = int(v)
        if len(vals) != len(PR.ASPECTS): messages.error(request, "Isi semua aspek dengan nilai 1 sampai 5.")
        else:
            before = {k: getattr(obj, k) for k, _ in PR.ASPECTS} if obj else None
            vals["note"] = request.POST.get("note", "").strip()[:500]; vals["reviewer"] = request.user
            obj, _made = PR.objects.update_or_create(employee=e, period=mval, defaults=vals)
            log(request, "hr", "kpi_review_save", obj, before, {k: getattr(obj, k) for k, _ in PR.ASPECTS})
            messages.success(request, f"Penilaian {e.name} untuk {mlabel} disimpan (skor {obj.score}).")
            return redirect(f"/kpi/penilaian/?month={mval}")
    ctx = {"e": e, "month": mval, "mlabel": mlabel, "obj": obj, "aspects": [(k, lbl, getattr(obj, k) if obj else 3) for k, lbl in PR.ASPECTS], "is_admin": request.user.role == Role.DEPT_ADMIN, "tab": "reviews"}
    return render(request, "kpi/review_form.html", ctx)


@both
def employee(request, pk):
    from apps.hr.models import Contract
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "position"), pk)
    a, b, mval, mlabel = _month(request.GET.get("month"))
    is_admin = request.user.role == Role.DEPT_ADMIN
    trend, scores = [], []
    for i in range(5, -1, -1):
        s = kpi.add_months(a, -i); t = kpi.add_months(s, 1); end = t.replace(day=1) - timedelta(days=1)
        row = (kpi.build(AD.objects.filter(employee=e, date__gte=s, date__lte=end), s, end, label=f"{s:%m/%Y}") or [None])[0]
        trend.append({"month": f"{s:%Y-%m}", "row": row}); scores.append(row["score"] if row else None)
    cur = trend[-1]["row"]; sp = cur["sp"] if cur else (kpi.active_sp([e.pk], b).get(e.pk, 0))
    pill, text = kpi.insight(scores, sp)
    ctx = {"e": e, "month": mval, "mlabel": mlabel, "trend": trend, "cur": cur, "insight": text, "ipill": pill, "is_admin": is_admin, "tab": "", "sp": sp}
    if not is_admin:
        from apps.hrd.models import WarningLetter
        ctx["warnings"] = WarningLetter.objects.filter(employee=e).order_by("-issue_date")[:10]
        ctx["contract"] = Contract.objects.filter(employee=e).order_by("-start").first()
    return render(request, "kpi/employee.html", ctx)
