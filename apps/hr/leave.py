"""Saldo cuti tahunan berbasis kartu (LeaveLedger): jatah (+), pemakaian (−), koreksi (±).

Asumsi kebijakan (dapat diubah di settings, lihat docs/PROGRESS.md): jatah `ANNUAL_LEAVE_DAYS` per tahun kalender setelah masa kerja
`ANNUAL_LEAVE_MIN_MONTHS`; hari yang dihitung = hari kerja (hari di `REGULAR_OFF_WEEKDAYS` tidak dihitung; hari libur nasional BELUM
dikecualikan). Hanya tipe `cuti` yang memotong saldo (izin/sakit/izin khusus tidak). Cuti hamil di Operasional HRD tidak memotong saldo.
"""
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from django.conf import settings
from django.db.models import Sum
from .models import ChangeRequest, Employee, LeaveLedger

LIVE = ("submitted", "pending", "approved")  # sudah diajukan tetapi belum dipotong: saldo "dicadangkan"
ZERO = Decimal("0.0")


def fmt(x) -> str:
    """Tampilan hari untuk pengguna: 2 → "2", 2.5 → "2,5"."""
    return f"{Decimal(x).normalize():f}".replace(".", ",")


def _d(v): return v if isinstance(v, date) else date.fromisoformat(v)


def charge_by_year(start: date, end: date) -> dict:
    """{tahun: jumlah hari kerja} dalam rentang inklusif (hari libur reguler tidak dihitung)."""
    c, d = Counter(), start
    while d <= end:
        if d.weekday() not in settings.REGULAR_OFF_WEEKDAYS: c[d.year] += 1
        d += timedelta(days=1)
    return {y: Decimal(n) for y, n in c.items()}


def balance(employee, year: int) -> Decimal:
    return LeaveLedger.objects.filter(employee=employee, year=year).aggregate(s=Sum("days"))["s"] or ZERO


def reserved(employee, year: int, exclude_pk=None) -> Decimal:
    """Hari cuti pada pengajuan yang masih hidup tetapi belum dilaksanakan (belum memotong saldo)."""
    tot = ZERO
    for r in ChangeRequest.objects.filter(employee=employee, type="cuti", status__in=LIVE).exclude(pk=exclude_pk):
        p = r.payload
        if p.get("start_date") and p.get("end_date"): tot += charge_by_year(_d(p["start_date"]), _d(p["end_date"])).get(year, ZERO)
    return tot


def available(employee, year: int, exclude_pk=None) -> Decimal: return balance(employee, year) - reserved(employee, year, exclude_pk)


def check_request(employee, start: date, end: date, exclude_pk=None):
    """Pesan galat (str) bila cuti tidak dapat diajukan; None bila aman."""
    need = charge_by_year(start, end)
    if not need: return "Rentang tanggal tidak berisi hari kerja (hanya hari libur reguler)."
    for y, n in sorted(need.items()):
        av = available(employee, y, exclude_pk)
        if av < n: return f"Saldo cuti {y} tidak cukup: tersedia {fmt(av)} hari (sudah dikurangi pengajuan yang berjalan), dibutuhkan {fmt(n)} hari."
    return None


def charge(req: ChangeRequest, user):
    """Potong saldo saat pengajuan cuti DILAKSANAKAN (di dalam transaksi). Ditolak bila saldo tidak cukup; idempoten per (pengajuan, tahun)."""
    emp = Employee.all_objects.select_for_update().get(pk=req.employee_id)  # serialisasi per karyawan
    if emp.deleted_at or emp.status != "aktif": raise ValueError("Karyawan sudah tidak aktif.")
    p = req.payload
    need = charge_by_year(_d(p["start_date"]), _d(p["end_date"]))
    if not need: raise ValueError("Rentang cuti tidak berisi hari kerja.")
    for y, n in sorted(need.items()):
        if LeaveLedger.objects.filter(request=req, year=y, kind=LeaveLedger.USE).exists(): continue
        bal = balance(emp, y)
        if bal < n: raise ValueError(f"Saldo cuti {y} tidak cukup: sisa {fmt(bal)} hari, dibutuhkan {fmt(n)} hari. Beri koreksi saldo di halaman Saldo cuti atau batalkan pengajuan.")
        LeaveLedger.objects.create(employee=emp, year=y, kind=LeaveLedger.USE, days=-n, request=req, created_by=user,
                                   note=f"Cuti {p['start_date']} s/d {p['end_date']}")


def months_of_service(join: date, as_of: date) -> int:
    m = (as_of.year - join.year) * 12 + (as_of.month - join.month)
    return m - (1 if as_of.day < join.day else 0)


def eligible(employee, as_of: date) -> bool:
    return employee.status == "aktif" and not employee.deleted_at and months_of_service(employee.join_date, as_of) >= settings.ANNUAL_LEAVE_MIN_MONTHS
