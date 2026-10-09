"""P7 Validasi kehadiran (putaran 26). Semua aturan di sini; view hanya membaca input & memanggil servis.
Alur: diminta → dijawab → diverifikasi (terkunci) | dikembalikan → dijawab … | dibatalkan. Koreksi sesudah terkunci = event 'koreksi' (append-only)."""
from datetime import date, timedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from apps.core.models import Notification, Role, User
from .models import AttendanceCheck as AC, AttendanceCheckEvent as Ev, ChangeRequest, Employee

ANSWERS = dict(AC.ANSWERS)
NOTE_REQUIRED = ("izin", "alfa")   # VISION: izin & alfa wajib beralasan
MAX_PAIRS, MAX_DAYS = 200, 31
DUE_WORKDAYS = getattr(settings, "ATTENDANCE_CHECK_DUE_WORKDAYS", 2)  # A41: usulan 2 hari kerja
COVERING = ("cuti", "izin", "sakit", "izin_khusus")


def due_from(d: date, n=DUE_WORKDAYS):
    off = getattr(settings, "REGULAR_OFF_WEEKDAYS", (6,)); while_n = n
    while while_n > 0:
        d += timedelta(days=1)
        if d.weekday() not in off: while_n -= 1
    return d


def _covering(emp, d):
    """Pengajuan cuti/izin/sakit/izin_khusus berstatus Executed yang mencakup tanggal d (payload start_date..end_date, string ISO)."""
    for r in ChangeRequest.objects.filter(employee=emp, status="executed", type__in=COVERING):
        try:
            if date.fromisoformat(r.payload["start_date"]) <= d <= date.fromisoformat(r.payload["end_date"]): return r
        except (KeyError, ValueError, TypeError): continue
    return None


def hint_for(emp, d):
    """Dugaan awal (bukan jawaban): dari pengajuan Executed dan jadwal efektif. Tanpa diagnosa — hanya jenis pengajuan."""
    r = _covering(emp, d)
    if r: return f"Pengajuan {r.type} #{r.pk} sudah dilaksanakan"
    from .schedule import effective_schedule
    try:
        if effective_schedule(emp, d).get("off"): return "Jadwal efektif: libur"
    except Exception: pass  # jadwal tak terbaca tidak boleh menggagalkan permintaan
    return ""


def _ev(att, user, action, frm="", to="", value="", note=""):
    return Ev.objects.create(att=att, user=user, action=action, from_status=frm, to_status=to, value=value, note=note[:300])


def _admins(dept_id): return list(User.objects.filter(role=Role.DEPT_ADMIN, department_id=dept_id, is_active=True))


def _notify(users, title): Notification.objects.bulk_create([Notification(user=u, kind="absensi", title=title[:200], link="/validasi/") for u in users])


def create_checks(user, employees, dates, question=""):
    """Semua-atau-tidak-sama-sekali. employees: iterable Employee; dates: list[date]. Mengembalikan daftar AttendanceCheck."""
    employees, dates, question = list(employees), sorted(set(dates)), (question or "").strip()
    if not employees: raise ValueError("Pilih minimal satu karyawan.")
    if not dates: raise ValueError("Tanggal wajib diisi.")
    if len(dates) > MAX_DAYS: raise ValueError(f"Rentang maksimal {MAX_DAYS} hari.")
    if len(employees) * len(dates) > MAX_PAIRS: raise ValueError(f"Maksimal {MAX_PAIRS} karyawan-tanggal per permintaan.")
    if len(question) > 300: raise ValueError("Catatan maksimal 300 karakter.")
    today, errs, admins = date.today(), [], {}
    if any(d > today for d in dates): raise ValueError("Tanggal masa depan tidak dapat divalidasi.")
    for e in employees:
        if e.status == "nonaktif" or e.deleted_at: errs.append(f"{e.nik}: karyawan tidak aktif.")
        elif e.department_id not in admins:
            admins[e.department_id] = _admins(e.department_id)
        if e.department_id in admins and not admins[e.department_id] and e.status != "nonaktif" and not e.deleted_at:
            errs.append(f"{e.nik}: departemen {e.department.name} belum punya Admin Departemen aktif.")
    live = set(AC.objects.filter(employee__in=employees, date__in=dates).exclude(status="dibatalkan").values_list("employee_id", "date"))
    errs += [f"{e.nik} {d:%d-%m-%Y}: sudah ada permintaan aktif." for e in employees for d in dates if (e.pk, d) in live]
    if errs: raise ValueError(" · ".join(errs[:20]) + (f" (+{len(errs) - 20} lagi)" if len(errs) > 20 else ""))
    out = []
    try:
        with transaction.atomic():
            for e in employees:
                for d in dates:
                    a = AC.objects.create(employee=e, department_id=e.department_id, date=d, question=question, hint=hint_for(e, d), due_date=due_from(today), requested_by=user)
                    _ev(a, user, "minta", "", "diminta", note=question); out.append(a)
    except IntegrityError: raise ValueError("Permintaan ganda untuk karyawan-tanggal yang sama.")
    for dept_id in {a.department_id for a in out}:
        n = sum(1 for a in out if a.department_id == dept_id)
        _notify(admins[dept_id], f"Validasi kehadiran: {n} karyawan-tanggal menunggu jawaban Anda")
    return out


def _locked(pk):
    return AC.objects.select_for_update().select_related("employee", "department").get(pk=pk)


def answer(user, pk, value, note=""):
    """Admin Departemen (departemen sama) menjawab. Role/scope diperiksa pemanggil (404 di luar scope) dan di sini."""
    note = (note or "").strip()
    if value not in ANSWERS: raise ValueError("Pilih jawaban: Hadir / Izin / Sakit / Cuti / Alfa.")
    if value in NOTE_REQUIRED and not note: raise ValueError(f"Keterangan wajib untuk jawaban {ANSWERS[value]}.")
    if len(note) > 300: raise ValueError("Keterangan maksimal 300 karakter.")
    with transaction.atomic():
        a = _locked(pk)
        if user.role == Role.DEPT_ADMIN and a.department_id != user.department_id: raise PermissionError("Di luar departemen Anda.")
        if a.status not in ("diminta", "dikembalikan"): raise ValueError("Permintaan ini tidak sedang menunggu jawaban.")
        frm = a.status; r = _covering(a.employee, a.date)
        a.conflict = f"Jawaban Alfa bertabrakan dengan pengajuan {r.type} #{r.pk} yang sudah dilaksanakan" if value == "alfa" and r else ""
        a.status, a.answer, a.answer_note, a.answered_by, a.answered_at = "dijawab", value, note, user, _now(); a.save()
        _ev(a, user, "jawab", frm, "dijawab", value, note)
        _notify([a.requested_by], f"Validasi kehadiran {a.employee.nik} {a.date:%d-%m-%Y} dijawab: {ANSWERS[value]}")
    return a


def verify(user, pk, action, note="", final=""):
    """HRD: terima | kembalikan (alasan wajib) | ubah (jawaban baru + catatan wajib). Hanya dari status 'dijawab'."""
    note = (note or "").strip()
    if action not in ("terima", "kembalikan", "ubah"): raise ValueError("Aksi tidak dikenal.")
    if action in ("kembalikan", "ubah") and not note: raise ValueError("Catatan wajib diisi.")
    if action == "ubah" and final not in ANSWERS: raise ValueError("Pilih jawaban pengganti.")
    with transaction.atomic():
        a = _locked(pk)
        if a.status != "dijawab": raise ValueError("Hanya jawaban Admin yang dapat diverifikasi.")
        if action == "kembalikan":
            a.status = "dikembalikan"; a.due_date = max(a.due_date, due_from(date.today())); a.save()
            _ev(a, user, "kembalikan", "dijawab", "dikembalikan", note=note)
            _notify(_admins(a.department_id), f"Validasi kehadiran {a.employee.nik} {a.date:%d-%m-%Y} dikembalikan HRD: {note[:80]}")
            return a
        a.final_answer = a.answer if action == "terima" else final
        if a.final_answer == "alfa": r = _covering(a.employee, a.date); a.conflict = f"Alfa bertabrakan dengan pengajuan {r.type} #{r.pk}" if r else ""
        a.status, a.verified_by, a.verified_at, a.verify_note = "diverifikasi", user, _now(), note; a.save()
        _ev(a, user, "verifikasi" if action == "terima" else "ubah", "dijawab", "diverifikasi", a.final_answer, note)
    return a


def cancel(user, pk, reason):
    reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan pembatalan wajib diisi.")
    with transaction.atomic():
        a = _locked(pk)
        if a.status in ("diverifikasi", "dibatalkan"): raise ValueError("Permintaan yang sudah diverifikasi/dibatalkan tidak dapat dibatalkan (gunakan koreksi).")
        frm = a.status; a.status, a.cancel_reason = "dibatalkan", reason[:300]; a.save()
        _ev(a, user, "batal", frm, "dibatalkan", note=reason)
        _notify(_admins(a.department_id), f"Validasi kehadiran {a.employee.nik} {a.date:%d-%m-%Y} dibatalkan HRD")
    return a


def correct(user, pk, value, note):
    """Koreksi sesudah terkunci: event append-only; jawaban terverifikasi asli tetap tersimpan."""
    note = (note or "").strip()
    if value not in ANSWERS: raise ValueError("Pilih jawaban koreksi.")
    if not note: raise ValueError("Catatan koreksi wajib diisi.")
    with transaction.atomic():
        a = _locked(pk)
        if a.status != "diverifikasi": raise ValueError("Koreksi hanya untuk data yang sudah diverifikasi.")
        _ev(a, user, "koreksi", "diverifikasi", "diverifikasi", value, note)
    return a


def _now():
    from django.utils import timezone
    return timezone.now()
