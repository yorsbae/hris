"""UI pengajuan (Tahap 3): daftar, buat, detail, dan aksi workflow. Semua akses lewat scope departemen."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Notification, Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from . import services
from .forms import FIELDS_BY_TYPE, LABELS, RequestForm
from .models import ChangeRequest

ROLES = (Role.HRD, Role.DEPT_ADMIN)  # Superadmin lolos otomatis di require_roles
FIELD_LABELS = {"start_date": "Tanggal mulai", "end_date": "Tanggal selesai", "date": "Tanggal", "date_to": "Diganti ke tanggal",
                "time": "Jam", "effective_date": "Tanggal efektif", "department_name": "Departemen tujuan",
                "position_name": "Jabatan tujuan", "shift_name": "Shift tujuan", "status": "Status baru", "reason": "Alasan"}
STATUS_LABELS = {"draft": "Draft", "submitted": "Diajukan", "pending": "Menunggu persetujuan", "approved": "Disetujui",
                 "rejected": "Ditolak", "executed": "Dilaksanakan", "cancelled": "Dibatalkan"}


@login_required
@require_roles(*ROLES)
def request_list(request):
    qs = scope_by_department(request.user, ChangeRequest.objects.select_related("employee", "department", "requested_by"))
    f = {k: request.GET.get(k, "").strip() for k in ("status", "type", "q")}
    if f["status"]: qs = qs.filter(status=f["status"])
    if f["type"]: qs = qs.filter(type=f["type"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    page = Paginator(qs.order_by("-created_at"), 50).get_page(request.GET.get("page", 1))
    for r in page: r.status_label = STATUS_LABELS.get(r.status, r.status)
    return render(request, "requests_list.html", {"page": page, "f": f, "types": LABELS, "statuses": STATUS_LABELS})


@login_required
@require_roles(*ROLES)
def request_new(request):
    form = RequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        req = ChangeRequest.objects.create(type=d["type"], employee=form.employee, department=form.employee.department,
                                           payload=form.payload(), requested_by=request.user)
        log(request, "hr", "request_create", req, None, {"type": req.type, "employee": form.employee.nik})
        if "submit" in request.POST:
            try: services.submit(req, request.user)
            except (PermissionError, ValueError) as e:
                messages.error(request, str(e)); return redirect("request_detail", pk=req.pk)
            log(request, "hr", "request_submit", req, {"status": "draft"}, {"status": "pending"})
            messages.success(request, "Pengajuan dikirim ke HRD.")
        else:
            messages.success(request, "Draft tersimpan. Ajukan dari halaman detail bila sudah siap.")
        return redirect("request_detail", pk=req.pk)
    return render(request, "request_form.html", {"form": form, "fields_by_type": FIELDS_BY_TYPE})


def _actions(user, req):
    """Tombol yang boleh tampil; pengecekan sebenarnya tetap di services.transition."""
    hrd = user.role in (Role.HRD, Role.SUPERADMIN)
    mine = req.requested_by_id == user.id
    if req.status == "draft" and (mine or user.role == Role.SUPERADMIN): return ["submit", "cancelled"]
    if req.status == "pending":
        if hrd: return ["approved", "rejected", "cancelled"]
        return ["cancelled"] if mine else []
    if req.status == "approved" and hrd: return ["executed", "cancelled"]
    return []


@login_required
@require_roles(*ROLES)
def request_detail(request, pk):
    req = get_scoped_or_404(request.user, ChangeRequest.objects.select_related("employee__department", "department", "requested_by", "decided_by"), pk)
    Notification.objects.filter(user=request.user, is_read=False, link__in=[f"/requests/{pk}", f"/requests/{pk}/"]).update(is_read=True)
    rows = [(FIELD_LABELS[k], v) for k, v in req.payload.items() if k in FIELD_LABELS]
    ctx = {"r": req, "rows": rows, "actions": _actions(request.user, req), "label": LABELS[req.type],
           "status_label": STATUS_LABELS.get(req.status, req.status)}
    if req.status == "executed":  # hasil pelaksanaan: apa yang benar-benar ditulis
        ctx["schedule_rows"] = req.schedule_rows.select_related("shift").order_by("date")
        ctx["leave_rows"] = req.leave_rows.order_by("year")
    return render(request, "request_detail.html", ctx)


@require_POST
@login_required
@require_roles(*ROLES)
def request_action(request, pk, action):
    req = get_scoped_or_404(request.user, ChangeRequest.objects.all(), pk)  # URL tampering → 404
    if action not in ("submit", "approved", "rejected", "executed", "cancelled"):
        messages.error(request, "Aksi tidak dikenal."); return redirect("request_detail", pk=pk)
    note = request.POST.get("note", "")
    if action == "rejected" and not note.strip():
        messages.error(request, "Alasan penolakan wajib diisi."); return redirect("request_detail", pk=pk)
    if action == "cancelled" and req.status != "draft" and not note.strip():
        messages.error(request, "Alasan pembatalan wajib diisi."); return redirect("request_detail", pk=pk)
    before = {"status": req.status}
    try:
        new = services.submit(req, request.user) if action == "submit" else services.transition(req, action, request.user, note)
    except PermissionError as e:
        messages.error(request, str(e)); return redirect("request_detail", pk=pk)
    except (ValueError, KeyError, ObjectDoesNotExist) as e:  # termasuk target (departemen/jabatan/shift) yang sudah tidak ada
        messages.error(request, f"Tidak dapat diproses: {e}"); return redirect("request_detail", pk=pk)
    log(request, "hr", f"request_{action}", new, before, {"status": new.status})
    messages.success(request, f"Status sekarang: {STATUS_LABELS.get(new.status, new.status)}.")
    return redirect("request_detail", pk=pk)
