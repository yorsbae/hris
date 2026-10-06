from django.db import transaction
from apps.core.models import Notification, User, Role
from .models import ChangeRequest, EmployeeHistory, Department, Position, Shift

HRD_ONLY = {"approved", "rejected", "executed"}

@transaction.atomic
def transition(req: ChangeRequest, to: str, user):
    req = ChangeRequest.objects.select_for_update().get(pk=req.pk)
    if to not in ChangeRequest.FLOW.get(req.status, set()):
        raise ValueError(f"Transisi {req.status}→{to} tidak valid")
    if to in HRD_ONLY and user.role not in (Role.HRD, Role.SUPERADMIN):
        raise PermissionError("Hanya HRD yang dapat memutuskan")
    if user.role == Role.DEPT_ADMIN and (req.department_id != user.department_id or req.requested_by_id != user.id):
        raise PermissionError("Di luar scope")
    req.status = to
    if to in ("approved", "rejected"): req.decided_by = user
    req.save()
    if to == "executed": _execute(req, user)
    if to == "pending":
        Notification.objects.bulk_create([Notification(user=u, kind="request", title=f"Pengajuan baru: {req.type}", link=f"/requests/{req.pk}")
                                          for u in User.objects.filter(role=Role.HRD)])
    elif to in ("approved", "rejected"):
        Notification.objects.create(user=req.requested_by, kind="approval", title=f"Pengajuan {req.type} {to}", link=f"/requests/{req.pk}")
    return req

MODELS = {"department": ("department", Department), "position": ("position", Position), "shift": ("shift", Shift)}
def _execute(req, user):
    """Terapkan perubahan ke karyawan + tulis riwayat. Hanya untuk tipe yang mengubah master."""
    emp, p = req.employee, req.payload
    changes = {"mutasi_dept": "department", "mutasi_jabatan": "position", "promosi": "position",
               "demosi": "position", "rotasi": "department", "shift": "shift"}.get(req.type)
    if req.type == "status":
        old, emp.status = emp.status, p["status"]; field, new = "status", p["status"]
    elif changes:
        attr, M = MODELS[changes]; old = str(getattr(emp, attr)); obj = M.objects.get(pk=p[f"{attr}_id"])
        setattr(emp, attr, obj); field, new = changes, str(obj)
    else:
        return  # izin/cuti/dst: tidak mengubah master
    emp.save()
    EmployeeHistory.objects.create(employee=emp, field=field, old_value=old, new_value=new,
        effective_date=p.get("effective_date") or req.updated_at.date(), changed_by=user, request=req)
