"""P7 Validasi kehadiran (putaran 26) — HRD membuat permintaan, Admin Departemen menjawab (hanya departemennya → 404 di luar scope), HRD memverifikasi."""
import re
from datetime import date, timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from . import attendance_check as svc
from .models import AttendanceCheck as AC, Department, Employee

both = lambda fn: login_required(require_roles(Role.HRD, Role.DEPT_ADMIN)(fn))
hrd = lambda fn: login_required(require_roles(Role.HRD)(fn))
STATUS_PILL = {"diminta": "pending", "dijawab": "pending", "dikembalikan": "rejected", "diverifikasi": "approved", "dibatalkan": "nonaktif"}


def _date(s, default=None):
    try: return date.fromisoformat((s or "").strip())
    except ValueError: return default


def _get(request, pk):
    # Admin Dept: hanya departemennya (404 di luar). HRD/Superadmin: semua. Poli tidak sampai sini (403 dari dekorator).
    return get_scoped_or_404(request.user, AC.objects.select_related("employee", "department", "answered_by", "verified_by", "requested_by"), pk)


@both
def check_list(request):
    qs = scope_by_department(request.user, AC.objects.select_related("employee", "department")).order_by("-date", "employee__name")
    g, is_admin = request.GET, request.user.role == Role.DEPT_ADMIN
    status = g.get("status", "todo" if is_admin else "")
    if status == "todo": qs = qs.filter(status__in=("diminta", "dikembalikan"))
    elif status == "late": qs = qs.filter(status__in=("diminta", "dikembalikan"), due_date__lt=date.today())
    elif status in dict(AC.STATUSES): qs = qs.filter(status=status)
    if g.get("dept", "").isdigit() and not is_admin: qs = qs.filter(department_id=int(g["dept"]))
    if g.get("q", "").strip(): q = g["q"].strip()[:60]; qs = qs.filter(Q(employee__nik__icontains=q) | Q(employee__name__icontains=q))
    d = _date(g.get("date"))
    if d: qs = qs.filter(date=d)
    if g.get("export") and not is_admin:
        rows = [[a.employee.nik, a.employee.name, a.department.name, a.date.isoformat(), a.get_status_display(), a.hint, a.get_answer_display() if a.answer else "", a.answer_note,
                 dict(AC.ANSWERS).get(a.result, ""), a.verify_note, a.conflict] for a in qs[:20000]]
        log(request, "hr", "attendance_check_export", None, None, {"rows": len(rows)})
        return tabular.export_response("validasi-kehadiran", ["nik", "nama", "departemen", "tanggal", "status", "dugaan_awal", "jawaban_admin", "keterangan_admin", "hasil_terverifikasi", "catatan_hrd", "konflik"], rows, request, sheet="Validasi")
    page = Paginator(qs, 50).get_page(g.get("page", 1)); today = date.today()
    for a in page: a.late = a.is_late(today); a.pill = STATUS_PILL[a.status]
    base = scope_by_department(request.user, AC.objects.all())
    counts = {"todo": base.filter(status__in=("diminta", "dikembalikan")).count(), "late": base.filter(status__in=("diminta", "dikembalikan"), due_date__lt=today).count(),
              "answered": base.filter(status="dijawab").count()}
    qd = g.copy(); qd.pop("page", None)
    return render(request, "hr/attendance_checks.html", {"page": page, "qs": qd.urlencode(), "f": {"status": status, "q": g.get("q", ""), "date": g.get("date", ""), "dept": g.get("dept", "")},
                  "statuses": AC.STATUSES, "depts": Department.objects.order_by("name"), "counts": counts, "is_admin": is_admin, "answers": AC.ANSWERS})


@hrd
def check_new(request):
    ctx = {"depts": Department.objects.order_by("name"), "today": date.today().isoformat(), "v": request.POST}
    if request.method == "POST":
        p = request.POST
        d1 = _date(p.get("date_from")); d2 = _date(p.get("date_to"), d1)
        try:
            if not d1 or not d2 or d2 < d1: raise ValueError("Tanggal tidak valid (selesai tidak boleh sebelum mulai).")
            if (d2 - d1).days + 1 > svc.MAX_DAYS: raise ValueError(f"Rentang maksimal {svc.MAX_DAYS} hari.")
            niks = [x for x in re.split(r"[\s,;]+", p.get("niks", "").strip()) if x]
            if niks:
                emps = list(Employee.objects.select_related("department").filter(nik__in=niks)); miss = sorted(set(niks) - {e.nik for e in emps})
                if miss: raise ValueError("NIK tidak ditemukan: " + ", ".join(miss[:10]))
            elif p.get("department", "").isdigit():
                emps = list(Employee.objects.select_related("department").filter(department_id=int(p["department"]), deleted_at__isnull=True).exclude(status="nonaktif"))
            else: raise ValueError("Isi NIK atau pilih departemen.")
            out = svc.create_checks(request.user, emps, [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)], p.get("question", ""))
            log(request, "hr", "attendance_check_new", None, None, {"rows": len(out), "from": d1.isoformat(), "to": d2.isoformat()})  # tanpa isi catatan
            messages.success(request, f"{len(out)} permintaan dibuat dan diteruskan ke Admin Departemen."); return redirect("/validasi/")
        except ValueError as e: messages.error(request, str(e))
    return render(request, "hr/attendance_check_new.html", ctx)


@both
def check_detail(request, pk):
    a = _get(request, pk); log(request, "hr", "attendance_check_view", a)
    return render(request, "hr/attendance_check_detail.html", {"a": a, "events": a.events.select_related("user"), "answers": AC.ANSWERS, "pill": STATUS_PILL[a.status], "late": a.is_late(),
                  "is_admin": request.user.role == Role.DEPT_ADMIN, "result": a.result, "result_label": dict(AC.ANSWERS).get(a.result, "")})


def _act(request, pk, fn, ok_msg, audit):
    a = _get(request, pk)  # scope dulu (404), lalu aturan servis
    try:
        fn(a); log(request, "hr", audit, a, None, {"status": AC.objects.get(pk=pk).status}); messages.success(request, ok_msg)
    except (ValueError, PermissionError) as e: messages.error(request, str(e))
    return redirect(f"/validasi/{pk}/")


@both
@require_POST
def check_answer(request, pk):
    if request.user.role != Role.DEPT_ADMIN: raise Http404  # Admin Departemen = perantara yang menjawab (A41); HRD/Superadmin memverifikasi
    p = request.POST
    return _act(request, pk, lambda a: svc.answer(request.user, pk, p.get("answer", ""), p.get("note", "")), "Jawaban terkirim ke HRD.", "attendance_check_answer")


@hrd
@require_POST
def check_verify(request, pk):
    p = request.POST
    return _act(request, pk, lambda a: svc.verify(request.user, pk, p.get("action", ""), p.get("note", ""), p.get("final", "")), "Verifikasi tersimpan.", "attendance_check_verify")


@hrd
@require_POST
def check_cancel(request, pk): return _act(request, pk, lambda a: svc.cancel(request.user, pk, request.POST.get("reason", "")), "Permintaan dibatalkan.", "attendance_check_cancel")


@hrd
@require_POST
def check_correct(request, pk):
    p = request.POST
    return _act(request, pk, lambda a: svc.correct(request.user, pk, p.get("answer", ""), p.get("note", "")), "Koreksi dicatat (jawaban asli tetap tersimpan).", "attendance_check_correct")
