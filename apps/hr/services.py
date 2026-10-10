from django.db import transaction
from apps.core.models import Notification, User, Role
from . import leave, schedule
from .models import ChangeRequest, EmployeeHistory, Department, Position, Shift

HRD_ONLY = {"approved", "rejected", "executed"}
AUTO_EXEC = {"standby", "lembur"}  # tidak ada langkah "Laksanakan" terpisah: disetujui HRD = final & tercatat (tidak mengubah master)

@transaction.atomic
def transition(req: ChangeRequest, to: str, user, note: str = "", notify: bool = True):
    req = ChangeRequest.objects.select_for_update().get(pk=req.pk)
    undo = req.status == "executed" and to == "cancelled" and req.type in AUTO_EXEC  # A80: lembur/stand by final bisa dibatalkan HRD (dengan alasan)
    if to not in ChangeRequest.FLOW.get(req.status, set()) and not undo:
        raise ValueError(f"Transisi {req.status}→{to} tidak valid")
    if to in HRD_ONLY and user.role not in (Role.HRD, Role.SUPERADMIN):
        raise PermissionError("Hanya HRD yang dapat memutuskan")
    if user.role == Role.DEPT_ADMIN and (req.department_id != user.department_id or req.requested_by_id != user.id):
        raise PermissionError("Di luar scope")
    if to == "cancelled":
        hrd = user.role in (Role.HRD, Role.SUPERADMIN)
        if req.status in ("approved", "executed") and not hrd: raise PermissionError("Hanya HRD yang dapat membatalkan pengajuan yang sudah disetujui")
        if not hrd and req.requested_by_id != user.id: raise PermissionError("Hanya pemohon atau HRD yang dapat membatalkan")
        if req.status != "draft" and not note.strip(): raise ValueError("Alasan pembatalan wajib diisi")
    req.status = to
    if to in ("approved", "rejected"): req.decided_by = user
    if note.strip(): req.note = (req.note + "\n" if req.note else "") + f"[{user.get_username()} → {to}] {note.strip()}"
    req.save()
    if to == "executed": _execute(req, user)
    if not notify: pass  # pengiriman massal: pemanggil mengirim satu ringkasan sendiri
    elif to == "pending":
        Notification.objects.bulk_create([Notification(user=u, kind="request", title=f"Pengajuan baru: {req.type}", link=f"/requests/{req.pk}")
                                          for u in User.objects.filter(role=Role.HRD)])
    elif to in ("approved", "rejected"):
        Notification.objects.create(user=req.requested_by, kind="approval", title=f"Pengajuan {req.type} {to}", link=f"/requests/{req.pk}")
    elif to == "executed" and req.type in schedule.SWAP_TYPES: _notify_swap(req)
    elif to == "cancelled" and req.requested_by_id != user.id:  # dibatalkan HRD: beri tahu pemohon
        Notification.objects.create(user=req.requested_by, kind="approval", title=f"Pengajuan {req.type} dibatalkan HRD", link=f"/requests/{req.pk}")
    if to == "approved" and req.type in AUTO_EXEC: req = transition(req, "executed", user, notify=False)
    return req

def _notify_swap(req):
    """Tukar jadwal dilaksanakan: beri tahu pemohon, dan Admin Departemen rekan (jadwal rekan berubah; tautan ke detail rekan yang masuk scope-nya)."""
    Notification.objects.create(user=req.requested_by, kind="approval", title=f"Tukar jadwal {req.employee.name} sudah dilaksanakan", link=f"/requests/{req.pk}")
    p = schedule.partner_of(req)
    if p:
        for u in User.objects.filter(role=Role.DEPT_ADMIN, department_id=p.department_id, is_active=True).exclude(pk=req.requested_by_id):
            Notification.objects.create(user=u, kind="approval", title=f"Jadwal {p.name} berubah: tukar dengan {req.employee.name}", link=f"/employees/{p.pk}/")


MODELS = {"department": ("department", Department), "position": ("position", Position), "shift": ("shift", Shift)}
def _execute(req, user):
    """Terapkan perubahan ke karyawan + tulis riwayat. Hanya untuk tipe yang mengubah master."""
    if req.type in schedule.SWAP_TYPES: return schedule.apply(req, user)  # tulis ShiftAssignment; master shift tidak berubah
    if req.type == "cuti": return leave.charge(req, user)  # potong saldo cuti (kartu LeaveLedger)
    emp, p = req.employee, req.payload
    changes = {"mutasi_dept": "department", "mutasi_jabatan": "position", "promosi": "position",
               "demosi": "position", "rotasi": "department", "shift": "shift"}.get(req.type)
    if req.type == "status":
        old, emp.status = emp.status, p["status"]; field, new = "status", p["status"]
    elif changes:
        attr, M = MODELS[changes]; old = str(getattr(emp, attr)); obj = M.objects.get(pk=p[f"{attr}_id"])
        setattr(emp, attr, obj); field, new = changes, str(obj)
    else:
        return  # izin/sakit/dst: tidak mengubah master maupun saldo
    emp.save()
    EmployeeHistory.objects.create(employee=emp, field=field, old_value=old, new_value=new,
        effective_date=p.get("effective_date") or req.updated_at.date(), changed_by=user, request=req)


@transaction.atomic
def submit(req: ChangeRequest, user, notify: bool = True):
    """Draft → Submitted → Pending Approval dalam satu transaksi (pemohon menekan satu tombol 'Ajukan')."""
    req = transition(req, "submitted", user, notify=notify)
    return transition(req, "pending", user, notify=notify)
