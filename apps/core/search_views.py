"""Pencarian global lintas modul (kotak cari di header). Tiap bagian hanya muncul bila peran pengguna memang boleh membuka modulnya,
dan hasilnya memakai scope yang sama dengan halaman modul itu (Admin Dept hanya departemennya). Tidak ada data sensitif di hasil: hanya pengenal + konteks singkat."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from .models import Role
from .scope import scope_by_department, scope_dept_only

LIMIT = 8  # baris per bagian
MIN_LEN = 2


def _section(title, qs, make, see_all=None):
    total = qs.count()
    return {"title": title, "total": total, "items": [make(o) for o in qs[:LIMIT]], "see_all": see_all if total > LIMIT else None} if total else None


@login_required
def global_search(request):
    from apps.hr.forms import allowed_types
    from apps.hr.models import ChangeRequest, Department, Employee, Position
    u, q = request.user, request.GET.get("q", "").strip()[:100]
    sections, hrd, su = [], u.role in (Role.HRD, Role.SUPERADMIN), u.role == Role.SUPERADMIN
    if len(q) >= MIN_LEN:
        if hrd or u.role in (Role.DEPT_ADMIN, Role.POLI):
            emps = scope_by_department(u, Employee.objects.select_related("department")).filter(Q(name__icontains=q) | Q(nik__startswith=q)).order_by("name", "pk")
            sections.append(_section("Karyawan", emps, lambda e: (f"{e.nik} · {e.name}", e.department.name, reverse("employee_detail_page", args=[e.pk])), f"/employees/?q={q}"))
            reqs = scope_dept_only(u, ChangeRequest.objects.select_related("employee")).filter(type__in=allowed_types(u)).filter(
                Q(employee__name__icontains=q) | Q(employee__nik__startswith=q)).order_by("-created_at")
            sections.append(_section("Pengajuan", reqs, lambda r: (f"{r.get_type_display() if hasattr(r, 'get_type_display') else r.type} · {r.employee.name}", f"{r.status} · {r.created_at:%d-%m-%Y}",
                                                                   reverse("request_detail", args=[r.pk])), f"/requests/?q={q}"))
        if hrd:
            sections.append(_section("Departemen", Department.objects.filter(Q(name__icontains=q) | Q(code__icontains=q)).order_by("name"), lambda d: (f"{d.code} · {d.name}", "", reverse("master_list", args=["department"]))))
            sections.append(_section("Jabatan", Position.objects.filter(name__icontains=q).order_by("name"), lambda p: (p.name, "", reverse("master_list", args=["position"]))))
        if su:
            from .models import User
            sections.append(_section("Pengguna", User.objects.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)).order_by("username"),
                                     lambda x: (x.username, x.get_role_display() if hasattr(x, "get_role_display") else x.role, reverse("user_detail", args=[x.pk])), f"/users/?q={q}"))
        if u.role in (Role.POLI, Role.SUPERADMIN):
            from apps.poli.models import Medicine
            sections.append(_section("Obat", Medicine.objects.filter(Q(name__icontains=q) | Q(code__istartswith=q)).order_by("name"),
                                     lambda m: (f"{m.code} · {m.name}", f"stok {m.stock} {m.unit}", reverse("poli_medicine_detail", args=[m.pk])), f"/poli/medicines/?q={q}"))
    sections = [s for s in sections if s]
    return render(request, "search.html", {"q": q, "sections": sections, "too_short": 0 < len(q) < MIN_LEN, "empty": not q, "total": sum(s["total"] for s in sections)})
