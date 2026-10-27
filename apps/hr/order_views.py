"""Order Karyawan: Admin Departemen mengajukan permintaan tambahan tenaga ke HRD; HRD menjawab ACC (boleh sebagian) atau Ditolak dengan alasan.
Scope: Admin Dept/Poli hanya melihat order departemennya (404 bila URL diubah); HRD & Superadmin melihat semua. Keputusan hanya HRD/Superadmin."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import notify
from apps.core.audit import log
from apps.core.models import Notification, Role
from apps.core.scope import get_scoped_or_404, is_dept_actor, require_roles, scope_dept_only
from .models import Department, StaffOrder
from .order_forms import MAX_QTY, StaffOrderForm

ROLES = (Role.HRD, Role.DEPT_ADMIN, Role.POLI)  # Superadmin lolos otomatis
S = StaffOrder.Status


def _can_decide(u): return u.role in (Role.HRD, Role.SUPERADMIN)


def _snap(o): return {"status": o.status, "qty": o.qty, "qty_approved": o.qty_approved, "section": o.section}


@login_required
@require_roles(*ROLES)
def order_list(request):
    u = request.user
    f = {k: request.GET.get(k, "").strip() for k in ("status", "dept", "q")}
    base = scope_dept_only(u, StaffOrder.objects.select_related("department", "requested_by", "decided_by"))
    qs = base
    if f["status"] in dict(S.choices): qs = qs.filter(status=f["status"])
    if f["dept"].isdigit() and _can_decide(u): qs = qs.filter(department_id=int(f["dept"]))
    if f["q"]: qs = qs.filter(Q(section__icontains=f["q"]) | Q(reason__icontains=f["q"]))
    counts = base.aggregate(**{s: Count("id", filter=Q(status=s)) for s in dict(S.choices)})
    ctx = {"page": Paginator(qs, 25).get_page(request.GET.get("page", 1)), "f": f, "counts": counts, "statuses": S.choices, "can_decide": _can_decide(u),
           "can_create": is_dept_actor(u) and bool(u.department_id), "depts": Department.objects.order_by("name") if _can_decide(u) else [],
           "qs": "&".join(f"{k}={v}" for k, v in f.items() if v)}
    return render(request, "hr/order_list.html", ctx)


@login_required
@require_roles(Role.DEPT_ADMIN, Role.POLI)
def order_new(request):
    u = request.user
    if not u.department_id:
        messages.error(request, "Akun Anda belum terhubung ke departemen. Minta Superadmin mengisinya di menu Pengguna."); return redirect("order_list")
    form = StaffOrderForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        o = form.save(commit=False); o.department_id, o.requested_by = u.department_id, u; o.save()   # departemen SELALU dari akun, bukan dari input
        log(request, "hr", "order_create", o, None, _snap(o))
        notify.send([Role.HRD], "request", f"Order karyawan baru: {o.department.name} · {o.section} ×{o.qty}", f"/orders/{o.pk}/")
        messages.success(request, f"Order {o.code} terkirim ke HRD. Anda akan diberi notifikasi saat HRD menjawab.")
        return redirect("order_detail", pk=o.pk)
    return render(request, "hr/order_form.html", {"form": form, "dept": u.department})


@login_required
@require_roles(*ROLES)
def order_detail(request, pk):
    o = get_scoped_or_404(request.user, StaffOrder.objects.select_related("department", "position", "requested_by", "decided_by"), pk, strict=True)
    Notification.objects.filter(user=request.user, is_read=False, link__in=[f"/orders/{pk}", f"/orders/{pk}/"]).update(is_read=True)
    u = request.user
    return render(request, "hr/order_detail.html", {"o": o, "can_decide": _can_decide(u) and o.status == S.PENDING, "max_qty": MAX_QTY,
                  "can_cancel": is_dept_actor(u) and o.status == S.PENDING and o.department_id == u.department_id})


@login_required
@require_roles(Role.HRD)
@require_POST
def order_decide(request, pk, action):
    if action not in ("acc", "tolak"): return redirect("order_detail", pk=pk)
    note = request.POST.get("note", "").strip()[:1000]
    if action == "tolak" and len(note) < 5:
        messages.error(request, "Penolakan wajib disertai alasan (minimal 5 karakter)."); return redirect("order_detail", pk=pk)
    with transaction.atomic():
        o = get_scoped_or_404(request.user, StaffOrder.objects.select_for_update(), pk)
        if o.status != S.PENDING:
            messages.error(request, f"Order ini sudah berstatus “{o.get_status_display()}”, tidak bisa diputuskan lagi."); return redirect("order_detail", pk=pk)
        before = _snap(o); qa = o.qty
        if action == "acc":
            raw = request.POST.get("qty_approved", "").strip()
            if raw:
                if not raw.isdigit() or not 1 <= int(raw) <= o.qty:
                    messages.error(request, f"Jumlah di-ACC harus 1–{o.qty} (tidak boleh melebihi permintaan)."); return redirect("order_detail", pk=pk)
                qa = int(raw)
            o.qty_approved = qa
        o.status = S.APPROVED if action == "acc" else S.REJECTED
        o.decision_note, o.decided_by, o.decided_at = note, request.user, timezone.now()
        o.save(update_fields=["status", "qty_approved", "decision_note", "decided_by", "decided_at"])
        log(request, "hr", "order_acc" if action == "acc" else "order_tolak", o, before, _snap(o))
    if action == "acc":
        t = f"Order {o.code} di-ACC HRD ({qa} dari {o.qty} orang)" + (" — sebagian" if qa < o.qty else "")
    else: t = f"Order {o.code} ditolak HRD: {note}"
    notify.send([Role.DEPT_ADMIN, Role.POLI], "approval", t, f"/orders/{o.pk}/", department_id=o.department_id)
    messages.success(request, "Order di-ACC." if action == "acc" else "Order ditolak; pemohon diberi tahu beserta alasannya.")
    return redirect("order_detail", pk=pk)


@login_required
@require_roles(Role.DEPT_ADMIN, Role.POLI)
@require_POST
def order_cancel(request, pk):
    with transaction.atomic():
        o = get_scoped_or_404(request.user, StaffOrder.objects.select_for_update(), pk, strict=True)
        if o.status != S.PENDING:
            messages.error(request, "Hanya order yang masih menunggu HRD yang bisa dibatalkan."); return redirect("order_detail", pk=pk)
        before = _snap(o); o.status = S.CANCELLED; o.save(update_fields=["status"])
        log(request, "hr", "order_cancel", o, before, _snap(o))
    messages.success(request, "Order dibatalkan.")
    return redirect("order_detail", pk=pk)
