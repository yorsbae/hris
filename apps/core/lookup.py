"""Pencarian rekomendasi (autocomplete) untuk input NIK, obat, dan diagnosa.

Prinsip: JANGAN pernah mengirim seluruh tabel. Setiap endpoint menuntut minimal MIN_CHARS karakter, membatasi hasil
(LIMIT) dan hanya mengembalikan kolom yang perlu. Izin dan scope sama dengan halaman asalnya:
  • karyawan  → Superadmin/HRD semua, Admin Departemen hanya departemennya, Poli identitas minimum (aktif saja)
  • obat/diagnosa → hanya Poli (data poliklinik tertutup untuk HRD/Admin Departemen)
Hasil bersifat saran saja; validasi akhir tetap di form/servis (NIK/ID dicek ulang saat disimpan).
"""
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .models import Role
from .scope import require_roles, scope_by_department

MIN_CHARS = 2
LIMIT = 10


def _q(request):
    q = request.GET.get("q", "").strip()[:60]
    return q if len(q) >= MIN_CHARS and "\x00" not in q else ""


def _flag(request, key): return request.GET.get(key, "") in ("1", "true", "yes")


@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
@require_GET
def employees(request):
    """?q=NIK/nama &gender=P|L &department=<id> &exclude=<nik>. Hanya karyawan aktif yang belum dihapus (soft delete tersaring manager)."""
    from apps.hr.models import Employee
    q = _q(request)
    if not q: return JsonResponse({"results": []})
    qs = scope_by_department(request.user, Employee.objects.filter(status="aktif"))
    qs = qs.filter(Q(nik__istartswith=q) | Q(name__icontains=q))
    g = request.GET.get("gender", "")
    if g in ("L", "P"): qs = qs.filter(gender=g)
    dep = request.GET.get("department", "")
    if dep.isdigit() and int(dep) < 2**31: qs = qs.filter(department_id=int(dep))
    ex = request.GET.get("exclude", "").strip()
    if ex: qs = qs.exclude(nik=ex)
    rows = qs.select_related("department", "position", "shift", "shift_group").order_by("nik")[:LIMIT]  # NIK diawali dulu bila q numerik; urut NIK stabil
    return JsonResponse({"results": [{
        "nik": e.nik, "name": e.name, "gender": e.gender, "department": e.department.name, "department_id": e.department_id,
        "position": e.position.name if e.position_id else "", "shift": e.shift.name if e.shift_id else "",
        "group": e.shift_group.code if e.shift_group_id else ""} for e in rows]})


@require_roles(Role.POLI)
@require_GET
def medicines(request):
    """?q=kode/nama &available=1 (hanya yang stoknya > 0)."""
    from apps.poli.models import Medicine
    q = _q(request)
    if not q: return JsonResponse({"results": []})
    qs = Medicine.objects.filter(Q(code__istartswith=q) | Q(name__icontains=q))
    if _flag(request, "available"): qs = qs.filter(stock__gt=0)
    return JsonResponse({"results": [{"id": m.pk, "code": m.code, "name": m.name, "unit": m.unit, "stock": m.stock, "low": m.stock <= m.min_stock}
                                     for m in qs.order_by("name")[:LIMIT]]})


@require_roles(Role.POLI)
@require_GET
def diagnoses(request):
    """?q=kode/nama. Tiap hasil membawa obat yang ditautkan di master (maks 10) → form kunjungan mengisi resep otomatis."""
    from apps.poli.models import Diagnosis, DiagnosisMedicine
    q = _q(request)
    if not q: return JsonResponse({"results": []})
    rows = list(Diagnosis.objects.filter(Q(code__istartswith=q) | Q(name__icontains=q)).order_by("code")[:LIMIT])
    links = {}
    for l in DiagnosisMedicine.objects.filter(diagnosis__in=rows).select_related("medicine").order_by("position", "id"):
        links.setdefault(l.diagnosis_id, []).append({"id": l.medicine_id, "code": l.medicine.code, "name": l.medicine.name, "unit": l.medicine.unit,
                                                     "stock": l.medicine.stock, "qty": l.qty, "dosage": l.dosage})
    return JsonResponse({"results": [{"id": d.pk, "code": d.code, "name": d.name, "category": d.category, "medicines": links.get(d.pk, [])[:10]} for d in rows]})
