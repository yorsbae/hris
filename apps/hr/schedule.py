"""Jadwal efektif per tanggal dan penerapan tukar shift/libur.

Prinsip VISION: master shift (`Employee.shift`) TIDAK berubah oleh tukar shift/libur. Tukar yang sudah dilaksanakan hanya menulis
`ShiftAssignment` (penyesuaian per tanggal, append-only). Jadwal efektif = penyesuaian bila ada, kalau tidak shift master.
Batasan: pola libur reguler per karyawan belum dimodelkan; bawaannya hanya hari di `settings.REGULAR_OFF_WEEKDAYS` (Minggu).
"""
from datetime import date
from django.conf import settings
from django.utils import timezone
from .models import ChangeRequest, Employee, Shift, ShiftAssignment

SWAP_TYPES = ("tukar_shift", "tukar_libur")
LIVE = ("submitted", "pending", "approved")  # pengajuan yang belum dilaksanakan tetapi masih hidup


def _d(v): return v if isinstance(v, date) else date.fromisoformat(v)


def regular_off(d: date) -> bool: return d.weekday() in settings.REGULAR_OFF_WEEKDAYS


def effective_schedule(employee: Employee, d: date) -> dict:
    """{'off': bool, 'shift': Shift|None, 'source': 'override'|'master', 'kind': str, 'request_id': int|None}"""
    a = ShiftAssignment.objects.select_related("shift").filter(employee=employee, date=d).first()
    if a:
        if a.kind == ShiftAssignment.OFF: return {"off": True, "shift": None, "source": "override", "kind": a.kind, "request_id": a.request_id}
        return {"off": False, "shift": a.shift or employee.shift, "source": "override", "kind": a.kind, "request_id": a.request_id}
    if regular_off(d): return {"off": True, "shift": None, "source": "master", "kind": "off", "request_id": None}
    return {"off": False, "shift": employee.shift, "source": "master", "kind": "work", "request_id": None}


def plan(req: ChangeRequest):
    """Baris ShiftAssignment yang akan dibuat untuk pengajuan tukar: [(tanggal, kind, shift|None)]."""
    p = req.payload
    if req.type == "tukar_shift":
        return [(_d(p["date"]), ShiftAssignment.SHIFT, Shift.objects.get(pk=p["shift_id"]))]
    if req.type == "tukar_libur":  # libur tanggal `date` dipindah ke `date_to`: date → masuk, date_to → libur
        return [(_d(p["date"]), ShiftAssignment.WORK, None), (_d(p["date_to"]), ShiftAssignment.OFF, None)]
    raise ValueError("Bukan pengajuan tukar jadwal")


def dates_of(req_or_payload_type, payload):
    keys = ("date",) if req_or_payload_type == "tukar_shift" else ("date", "date_to")
    return [payload[k] for k in keys if payload.get(k)]


def live_conflict(employee, type_, payload, exclude_pk=None):
    """Tanggal yang sudah dipakai pengajuan tukar lain yang masih hidup (submitted/pending/approved) atau penyesuaian yang sudah ada."""
    mine = set(dates_of(type_, payload))
    for r in ChangeRequest.objects.filter(employee=employee, type__in=SWAP_TYPES, status__in=LIVE).exclude(pk=exclude_pk):
        hit = mine & set(dates_of(r.type, r.payload))
        if hit: return min(hit)
    exist = ShiftAssignment.objects.filter(employee=employee, date__in=[_d(x) for x in mine]).order_by("date").first()
    return exist.date.isoformat() if exist else None


def apply(req: ChangeRequest, user):
    """Dipanggil dari services._execute (di dalam transaksi). Gagal dengan ValueError bila tidak aman diterapkan."""
    rows, today = plan(req), timezone.localdate()
    for d, _, _ in rows:
        if d < today:
            raise ValueError(f"Tanggal {d:%d-%m-%Y} sudah lewat; tukar jadwal tidak dapat diterapkan. Batalkan pengajuan ini lalu ajukan ulang bila perlu.")
    emp = Employee.all_objects.select_for_update().get(pk=req.employee_id)  # serialisasi per karyawan
    if emp.deleted_at or emp.status != "aktif": raise ValueError("Karyawan sudah tidak aktif.")
    for d, kind, shift in rows:
        if ShiftAssignment.objects.filter(employee=emp, date=d).exists():
            raise ValueError(f"Tanggal {d:%d-%m-%Y} sudah punya penyesuaian jadwal dari pengajuan lain.")
        if kind == ShiftAssignment.SHIFT and emp.shift_id == shift.pk:
            raise ValueError("Shift tujuan sama dengan shift reguler karyawan saat ini.")
    for d, kind, shift in rows:
        ShiftAssignment.objects.create(employee=emp, date=d, kind=kind, shift=shift, request=req, created_by=user)
