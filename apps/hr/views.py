import json
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department, get_scoped_or_404
from .models import Employee, ChangeRequest
from . import services

PUBLIC = ("id", "nik", "name", "department__name", "position__name", "status")
FULL = PUBLIC + ("join_date", "gender", "marital_status", "education")  # tanpa data sensitif

@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employee_list(request):
    qs = scope_by_department(request.user, Employee.objects.select_related("department", "position"))
    q = request.GET.get("q", "").strip()
    if q: qs = qs.filter(Q(name__icontains=q) | Q(nik__startswith=q))
    if request.GET.get("status"): qs = qs.filter(status=request.GET["status"])
    if request.GET.get("department") and request.user.role != Role.DEPT_ADMIN:
        qs = qs.filter(department_id=request.GET["department"])
    page = Paginator(qs.order_by("name"), 50).get_page(request.GET.get("page", 1))  # pagination di server
    cols = PUBLIC if request.user.role in (Role.POLI, Role.DEPT_ADMIN) else FULL
    return JsonResponse({"count": page.paginator.count, "page": page.number, "results": list(page.object_list.values(*cols))})

@require_roles(Role.HRD)
def employee_detail(request, pk):
    emp = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    log(request, "hr", "view_sensitive", emp)  # akses data sensitif tercatat
    data = {f.name: str(getattr(emp, f.name)) for f in Employee._meta.concrete_fields}
    return JsonResponse(data)

@require_POST
@require_roles(Role.HRD, Role.DEPT_ADMIN)
def request_transition(request, pk, to):
    req = get_scoped_or_404(request.user, ChangeRequest.objects.all(), pk)  # URL tampering → 404
    before = {"status": req.status}
    try: req = services.transition(req, to, request.user)
    except PermissionError as e: return JsonResponse({"detail": str(e)}, status=403)
    except ValueError as e: return JsonResponse({"detail": str(e)}, status=400)
    log(request, "hr", f"request_{to}", req, before, {"status": req.status})
    return JsonResponse({"id": req.pk, "status": req.status})

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employees_page(request):
    return render(request, "employees.html")
