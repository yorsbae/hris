"""RBAC + department scope. SEMUA akses data karyawan WAJIB lewat sini."""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from .models import Role

def require_roles(*roles):
    def deco(fn):
        @wraps(fn)
        def wrap(request, *a, **k):
            u = request.user
            if not u.is_authenticated: return JsonResponse({"detail": "login"}, status=401)
            if u.role != Role.SUPERADMIN and u.role not in roles:
                return JsonResponse({"detail": "forbidden"}, status=403)
            return fn(request, *a, **k)
        return wrap
    return deco

def superadmin_only(fn):
    """Login + hanya Superadmin (role lain → 403). Dipakai halaman manajemen user dan penelusuran audit."""
    return login_required(require_roles()(fn))

def scope_by_department(user, qs, field="department"):
    if user.role in (Role.SUPERADMIN, Role.HRD): return qs
    if user.role == Role.DEPT_ADMIN: return qs.filter(**{field: user.department_id})
    if user.role == Role.POLI: return qs  # tampilan dibatasi ke identitas minimum
    return qs.none()

def get_scoped_or_404(user, qs, pk, field="department"):
    try: return scope_by_department(user, qs, field).get(pk=pk)
    except qs.model.DoesNotExist: raise Http404  # 404 agar tidak membocorkan keberadaan data
