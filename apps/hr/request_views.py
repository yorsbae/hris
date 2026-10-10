"""UI pengajuan (Tahap 3): daftar, buat, detail, dan aksi workflow. Semua akses lewat scope departemen."""
import re
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Notification, Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from . import schedule, services
from .forms import FIELDS_BY_TYPE, LABELS, REQUEST_GROUPS, RequestForm, allowed_types
from .models import ChangeRequest

ROLES = (Role.HRD, Role.DEPT_ADMIN)  # Superadmin lolos otomatis di require_roles
FIELD_LABELS = {"start_date": "Tanggal mulai", "end_date": "Tanggal selesai", "date": "Tanggal", "date_to": "Diganti ke tanggal",
                "time": "Jam", "effective_date": "Tanggal efektif", "department_name": "Departemen tujuan",
                "position_name": "Jabatan tujuan", "shift_name": "Shift tujuan", "status": "Status baru", "reason": "Alasan",
                "partner_nik": "NIK rekan tukar", "partner_name": "Rekan tukar", "time_to": "Sampai jam", "minutes": "Durasi (menit)"}
STATUS_LABELS = {"draft": "Draft", "submitted": "Diajukan", "pending": "Menunggu persetujuan", "approved": "Disetujui",
                 "rejected": "Ditolak", "executed": "Dilaksanakan", "cancelled": "Dibatalkan"}


@login_required
@require_roles(*ROLES)
def request_list(request, grp=None):
    if grp is not None and grp not in REQUEST_GROUPS: raise Http404
    qs = scope_by_department(request.user, ChangeRequest.objects.select_related("employee", "department", "requested_by"))
    group_types = [t for t in allowed_types(request.user) if grp is None or t in REQUEST_GROUPS[grp][1]]
    if grp is not None:
        if not group_types: raise Http404  # mis. Admin Dept membuka Perubahan Status (hanya HRD)
        qs = qs.filter(type__in=group_types)
    f = {k: request.GET.get(k, "").strip() for k in ("status", "type", "q", "batch")}
    if f["batch"]:
        if not re.fullmatch(r"[0-9a-f]{32}", f["batch"]): raise Http404
        qs = qs.filter(payload__batch=f["batch"])
    if f["status"]: qs = qs.filter(status=f["status"])
    if f["type"]: qs = qs.filter(type=f["type"])
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if request.GET.get("export"):
        from apps.core import tabular
        rows = [[r.pk, r.type, STATUS_LABELS.get(r.status, r.status), r.employee.nik, r.employee.name, r.department.name, r.requested_by.username, r.created_at.strftime("%Y-%m-%d %H:%M"), summary(r)]
                for r in qs.order_by("-created_at")[:20000]]
        log(request, "hr", "requests_export", None, None, {"rows": len(rows)})
        return tabular.export_response("pengajuan", ["no", "jenis", "status", "nik", "nama", "departemen", "diajukan_oleh", "dibuat", "rincian"], rows, request, sheet="Pengajuan")
    page = Paginator(qs.order_by("-created_at"), 50).get_page(request.GET.get("page", 1))
    for r in page: r.status_label, r.summary, r.type_label = STATUS_LABELS.get(r.status, r.status), summary(r), LABELS.get(r.type, r.type)
    types = {t: LABELS[t] for t in (group_types if grp else LABELS)}
    batch = None
    if f["batch"]:
        c = {s: n for s, n in qs.order_by().values_list("status").annotate(n=Count("id"))}
        batch = {"total": sum(c.values()), "pending": c.get("pending", 0), "counts": [(STATUS_LABELS.get(k, k), n) for k, n in c.items()]}
    return render(request, "requests_list.html", {"page": page, "f": f, "types": types, "statuses": STATUS_LABELS, "grp": grp, "batch": batch,
                                                  "title": REQUEST_GROUPS[grp][0] if grp else "Semua pengajuan"})


@login_required
@require_roles(*ROLES)
def request_new(request):
    grp = request.GET.get("grp") or request.POST.get("grp") or None
    form = RequestForm(request.POST or None, user=request.user, group=grp)
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
    return render(request, "request_form.html", {"form": form, "fields_by_type": FIELDS_BY_TYPE, "grp": grp if grp in REQUEST_GROUPS else "",
                                                 "title": REQUEST_GROUPS[grp][0] if grp in REQUEST_GROUPS else "Buat pengajuan",
                                                 "back": f"/requests/g/{grp}/" if grp in REQUEST_GROUPS else "/requests/", "back_label": REQUEST_GROUPS[grp][0] if grp in REQUEST_GROUPS else "Pengajuan"})


def summary(r):
    """Ringkasan satu baris isi pengajuan untuk daftar (tanggal/jam/durasi), supaya daftar terbaca tanpa membuka detail."""
    p = r.payload
    if p.get("time_to"): return f"{p.get('date')} · {p.get('time')}–{p['time_to']} ({p.get('minutes')} mnt)"
    if p.get("start_date"): return p["start_date"] + (f" s/d {p['end_date']}" if p.get("end_date") and p["end_date"] != p["start_date"] else "")
    if p.get("date"): return p["date"] + (f" → {p['date_to']}" if p.get("date_to") else "") + (f" · {p['time']}" if p.get("time") else "")
    return f"efektif {p['effective_date']}" if p.get("effective_date") else ""


def _actions(user, req):
    """Tombol yang boleh tampil; pengecekan sebenarnya tetap di services.transition."""
    hrd = user.role in (Role.HRD, Role.SUPERADMIN)
    mine = req.requested_by_id == user.id
    if req.status == "draft" and (mine or user.role == Role.SUPERADMIN): return ["submit", "cancelled"]
    if req.status == "pending":
        if hrd: return ["approved", "rejected", "cancelled"]
        return ["cancelled"] if mine else []
    if req.status == "approved" and hrd: return ["executed", "cancelled"]
    if req.status == "executed" and hrd and req.type in services.AUTO_EXEC: return ["cancelled"]  # A80
    return []


@login_required
@require_roles(*ROLES)
def request_detail(request, pk):
    req = get_scoped_or_404(request.user, ChangeRequest.objects.select_related("employee__department", "department", "requested_by", "decided_by"), pk)
    Notification.objects.filter(user=request.user, is_read=False, link__in=[f"/requests/{pk}", f"/requests/{pk}/"]).update(is_read=True)
    rows = [(FIELD_LABELS[k], v) for k, v in req.payload.items() if k in FIELD_LABELS]
    mode = (" — dengan rekan (2 orang)" if req.payload.get("partner_id") else " — sendiri (1 orang)") if req.type in schedule.SWAP_TYPES else ""
    ctx = {"r": req, "rows": rows, "actions": _actions(request.user, req), "label": LABELS[req.type] + mode,
           "status_label": STATUS_LABELS.get(req.status, req.status)}
    if req.status == "executed":  # hasil pelaksanaan: apa yang benar-benar ditulis
        ctx["schedule_rows"] = req.schedule_rows.select_related("shift", "employee").order_by("date", "employee__name")
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
