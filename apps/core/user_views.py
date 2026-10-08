"""Manajemen user (Superadmin saja; role lain → 403). Tidak ada hapus fisik: akun dinonaktifkan (jejak audit tetap menunjuk user-nya)."""
from django.contrib import messages
from django.contrib.auth.views import PasswordChangeView
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.hr.models import Department
from .audit import log
from .models import AuditLog, Role, User
from .scope import superadmin_only
from .user_forms import DeactivateForm, ResetPasswordForm, SelfPasswordChangeForm, UserCreateForm, UserEditForm
from .user_services import UserRuleError, guard

PER_PAGE = 50
EDIT_FIELDS = ["first_name", "last_name", "email", "role", "department"]  # save terbatas: tidak menimpa password/is_active/last_login yang bisa berubah di proses lain


def _snap(u):
    """Snapshot untuk audit. Tidak pernah menyertakan sandi/hash."""
    return {"username": u.username, "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
            "role": u.role, "department": u.department.code if u.department_id else "", "is_active": u.is_active}


def _int(v):
    try: return int(v)
    except (TypeError, ValueError): return None


@superadmin_only
def user_list(request):
    g = request.GET
    f = {"q": g.get("q", "").strip(), "role": g.get("role", ""), "active": g.get("active", ""), "department": g.get("department", "")}
    qs = User.objects.select_related("department").order_by("username")
    if f["q"]: qs = qs.filter(Q(username__icontains=f["q"]) | Q(first_name__icontains=f["q"]) | Q(last_name__icontains=f["q"]))
    if f["role"] in Role.values: qs = qs.filter(role=f["role"])
    if f["active"] == "aktif": qs = qs.filter(is_active=True)
    elif f["active"] == "nonaktif": qs = qs.filter(is_active=False)
    if _int(f["department"]): qs = qs.filter(department_id=_int(f["department"]))
    page = Paginator(qs, PER_PAGE).get_page(g.get("page", 1))
    from urllib.parse import urlencode
    qstr = urlencode({k: v for k, v in f.items() if v})
    return render(request, "users/list.html", {"page": page, "f": f, "roles": Role.choices, "qs": qstr,
                                               "departments": Department.objects.order_by("name")})


@superadmin_only
def user_new(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        u = form.save(commit=False)
        u.set_password(form.cleaned_data["password1"]); u.must_change_password = True; u.is_active = True
        try:
            with transaction.atomic():
                u.save()
                log(request, "users", "user_create", u, after=_snap(u))
        except IntegrityError:  # dua Superadmin membuat username yang sama bersamaan
            form.add_error("username", "Username sudah dipakai.")
        else:
            messages.success(request, f"User {u.username} dibuat. Sandi awal harus diganti saat login pertama.")
            return redirect("user_detail", pk=u.pk)
    return render(request, "users/form.html", {"form": form, "title": "User baru", "back": "/users/"})


@superadmin_only
def user_detail(request, pk):
    u = get_object_or_404(User.objects.select_related("department"), pk=pk)
    recent = AuditLog.objects.filter(user=u).defer("before", "after").order_by("-id")[:20]
    from . import lockout
    return render(request, "users/detail.html", {"u": u, "is_self": u.pk == request.user.pk, "recent": recent, "deactivate": DeactivateForm(),
                                                 "locked": lockout.is_locked(u), "lock_minutes": -(-lockout.remaining_seconds(u) // 60)})


@superadmin_only
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related("department"), pk=pk)
    is_self = target.pk == request.user.pk
    before = _snap(target)  # SEBELUM form dibuat: ModelForm.is_valid() sudah mengubah instance (pelajaran putaran 10)
    form = UserEditForm(request.POST or None, instance=target, lock_role=is_self)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                guard(request.user.pk, pk, form.cleaned_data["role"], target.is_active)
                form.save(commit=False)
                target.save(update_fields=EDIT_FIELDS)
                after = _snap(target)
                if before != after: log(request, "users", "user_update", target, before=before, after=after)
        except UserRuleError as e: form.add_error(None, str(e))
        else:
            messages.success(request, "Perubahan disimpan." if before != after else "Tidak ada perubahan.")
            return redirect("user_detail", pk=pk)
    return render(request, "users/form.html", {"form": form, "title": f"Ubah user {target.username}", "back": f"/users/{pk}/",
                                               "hint": "Role/departemen akun sendiri tidak bisa diubah." if is_self else ""})


@superadmin_only
def user_reset_password(request, pk):
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, "Untuk akun Anda sendiri gunakan 'Ganti sandi'.")
        return redirect("/password/change/")
    form = ResetPasswordForm(request.POST or None, user=target)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            target.set_password(form.cleaned_data["password1"]); target.must_change_password = True
            target.failed_logins, target.last_failed_at, target.locked_until = 0, None, None  # sandi baru → kunci akun ikut dibuka
            target.save(update_fields=["password", "must_change_password", "failed_logins", "last_failed_at", "locked_until"])  # hash berubah → semua sesi aktif user ini terputus otomatis
            log(request, "users", "password_reset", target, after={"must_change_password": True})  # TANPA sandi
        messages.success(request, f"Sandi {target.username} direset; semua sesi lamanya diputus dan ia wajib menggantinya saat login.")
        return redirect("user_detail", pk=pk)
    return render(request, "users/form.html", {"form": form, "title": f"Reset sandi {target.username}", "back": f"/users/{pk}/"})


@require_POST
@superadmin_only
def user_deactivate(request, pk):
    form = DeactivateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Alasan wajib diisi.")
        return redirect("user_detail", pk=pk)
    get_object_or_404(User, pk=pk)
    try:
        with transaction.atomic():
            cur = guard(request.user.pk, pk, None, False)  # role None ≠ role mana pun; yang dinilai hanya is_active=False
            if not cur.is_active:
                messages.info(request, "User sudah nonaktif."); return redirect("user_detail", pk=pk)
            cur.is_active = False; cur.save(update_fields=["is_active"])
            log(request, "users", "user_deactivate", cur, before={"is_active": True}, after={"is_active": False, "reason": form.cleaned_data["reason"]})
    except UserRuleError as e:
        messages.error(request, str(e)); return redirect("user_detail", pk=pk)
    messages.success(request, "User dinonaktifkan; sesi yang sedang berjalan langsung tidak berlaku.")
    return redirect("user_detail", pk=pk)


@require_POST
@superadmin_only
def user_unlock(request, pk):
    """Buka kunci akun akibat salah sandi berulang (putaran 21, P1). Tercatat di audit."""
    from . import lockout
    with transaction.atomic():
        t = get_object_or_404(User.objects.select_for_update(), pk=pk)
        was = lockout.is_locked(t)
        lockout.clear(t)
        log(request, "users", "user_unlock", t, before={"locked": was}, after={"locked": False})
    messages.success(request, f"Kunci akun {t.username} dibuka." if was else f"{t.username} tidak sedang terkunci; hitungan gagal dibersihkan.")
    return redirect("user_detail", pk=pk)


@require_POST
@superadmin_only
def user_activate(request, pk):
    get_object_or_404(User, pk=pk)
    with transaction.atomic():
        cur = guard(request.user.pk, pk, User.objects.get(pk=pk).role, True)
        if cur.is_active:
            messages.info(request, "User sudah aktif."); return redirect("user_detail", pk=pk)
        cur.is_active = True; cur.save(update_fields=["is_active"])
        log(request, "users", "user_activate", cur, before={"is_active": False}, after={"is_active": True})
    messages.success(request, "User diaktifkan kembali.")
    return redirect("user_detail", pk=pk)


class PasswordChange(PasswordChangeView):
    """Ganti sandi sendiri (semua role). Menghapus flag `must_change_password`; sesi tetap hidup (hash sesi diperbarui)."""
    template_name = "registration/password_change.html"
    form_class = SelfPasswordChangeForm
    success_url = "/"

    def form_valid(self, form):
        resp = super().form_valid(form)
        u = self.request.user
        if u.must_change_password: User.objects.filter(pk=u.pk).update(must_change_password=False)
        log(self.request, "users", "password_change", u)
        messages.success(self.request, "Sandi diganti.")
        return resp
