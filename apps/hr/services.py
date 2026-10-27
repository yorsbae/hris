import logging
from datetime import date
from django.db import transaction
from apps.core import notify as notify_mod
from apps.core.models import Notification, User, Role
from apps.core.scope import DEPT_ACTORS
from . import leave, schedule
from .models import ChangeRequest, EmployeeHistory, Department, Position, Shift

log = logging.getLogger(__name__)
HRD_ONLY = {"approved", "rejected", "executed"}
AUTO_EXEC = {"standby", "lembur"}
LEAVE_TYPES = {"cuti", "izin", "sakit", "izin_khusus", "izin_terlambat", "izin_pulang"}
UNDOABLE = AUTO_EXEC | LEAVE_TYPES | set(schedule.SWAP_TYPES)   # hasil pelaksanaannya bisa dibalik HRD (dengan alasan); mutasi/promosi/status mengubah master → tidak
ATT_TYPES = LEAVE_TYPES | AUTO_EXEC | set(schedule.SWAP_TYPES)   # jenis yang memengaruhi rekap absensi  # tidak ada langkah "Laksanakan" terpisah: disetujui HRD = final & tercatat (tidak mengubah master)

@transaction.atomic
def transition(req: ChangeRequest, to: str, user, note: str = "", notify: bool = True):
    req = ChangeRequest.objects.select_for_update().get(pk=req.pk)
    undo = req.status == "executed" and to == "cancelled" and req.type in UNDOABLE  # A80 (diperluas): pelaksanaan lembur/stand by/izin/cuti/sakit/tukar bisa dibatalkan HRD (alasan wajib)
    if to not in ChangeRequest.FLOW.get(req.status, set()) and not undo:
        raise ValueError(f"Transisi {req.status}→{to} tidak valid")
    if to in HRD_ONLY and user.role not in (Role.HRD, Role.SUPERADMIN):
        raise PermissionError("Hanya HRD yang dapat memutuskan")
    if user.role in DEPT_ACTORS and (req.department_id != user.department_id or req.requested_by_id != user.id):
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
    if undo: _reverse(req, user)
    if to == "executed": _execute(req, user)
    if to == "executed" or undo: refresh_attendance(req)
    if not notify: pass  # pengiriman massal: pemanggil mengirim satu ringkasan sendiri
    elif to == "pending":
        notify_mod.send((Role.HRD,), "request", f"Pengajuan baru: {req.type}", f"/requests/{req.pk}")
    elif to in ("approved", "rejected"):
        Notification.objects.create(user=req.requested_by, kind="approval", title=f"Pengajuan {req.type} {to}", link=f"/requests/{req.pk}")
    elif to == "executed" and req.type in schedule.SWAP_TYPES: _notify_swap(req)
    elif to == "cancelled" and req.requested_by_id != user.id:  # dibatalkan HRD: beri tahu pemohon
        Notification.objects.create(user=req.requested_by, kind="approval", title=f"Pengajuan {req.type} dibatalkan HRD", link=f"/requests/{req.pk}")
    if undo and req.type in schedule.SWAP_TYPES: _notify_swap_undo(req)
    if to == "approved" and req.type in AUTO_EXEC: req = transition(req, "executed", user, notify=False)
    return req

def _notify_swap(req):
    """Tukar jadwal dilaksanakan: beri tahu pemohon, dan Admin Departemen rekan (jadwal rekan berubah; tautan ke detail rekan yang masuk scope-nya)."""
    Notification.objects.create(user=req.requested_by, kind="approval", title=f"Tukar jadwal {req.employee.name} sudah dilaksanakan", link=f"/requests/{req.pk}")
    p = schedule.partner_of(req)
    if p:
        for u in User.objects.filter(role__in=DEPT_ACTORS, department_id=p.department_id, is_active=True).exclude(pk=req.requested_by_id):
            Notification.objects.create(user=u, kind="approval", title=f"Jadwal {p.name} berubah: tukar dengan {req.employee.name}", link=f"/employees/{p.pk}/")


def _notify_swap_undo(req):
    p = schedule.partner_of(req)
    if p:
        for u in User.objects.filter(role__in=DEPT_ACTORS, department_id=p.department_id, is_active=True).exclude(pk=req.requested_by_id):
            Notification.objects.create(user=u, kind="approval", title=f"Tukar jadwal {p.name} dengan {req.employee.name} dibatalkan HRD; jadwal kembali normal", link=f"/employees/{p.pk}/")


def _reverse(req, user):
    """Balik hasil pelaksanaan (di dalam transaksi transition): jadwal tukar dikembalikan, saldo cuti dikembalikan. Lembur/izin/sakit tidak menulis apa pun selain statusnya."""
    if req.type in schedule.SWAP_TYPES: schedule.revert(req, user)
    elif req.type == "cuti": leave.refund(req, user)


def _att_range(req):
    p = req.payload or {}
    keys = schedule.dates_of(req.type, p) if req.type in schedule.SWAP_TYPES else [p.get(k) for k in ("start_date", "end_date", "date") if p.get(k)]
    try: ds = sorted(date.fromisoformat(str(k)) for k in keys)
    except ValueError: return None
    return (ds[0], ds[-1]) if ds else None


def refresh_attendance(req, max_days=62):
    """Hitung ulang rekap absensi karyawan terkait setelah pengajuan dilaksanakan / pelaksanaannya dibatalkan (hari yang belum selesai & bulan tertutup dilewati oleh `process`).
    Gagal tidak boleh menggagalkan pengajuan: rekap menyusul lewat cron `process_attendance`."""
    if req.type not in ATT_TYPES: return None
    rng = _att_range(req)
    if not rng or (rng[1] - rng[0]).days > max_days: return None
    ids = [req.employee_id] + ([req.payload["partner_id"]] if (req.payload or {}).get("partner_id") else [])
    from . import attendance_calc
    try:
        with transaction.atomic(): return attendance_calc.process(rng[0], rng[1], ids)
    except Exception:  # noqa: BLE001
        log.exception("Gagal menghitung ulang absensi untuk pengajuan %s", req.pk); return None


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
