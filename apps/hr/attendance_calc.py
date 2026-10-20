"""Pengolahan Absensi (Tahap 6, putaran 48): log mentah mesin + jadwal efektif + pengajuan + hari libur + validasi → `AttendanceDaily`.

Rekap harian adalah TABEL TERHITUNG: boleh dihitung ulang kapan saja (idempoten) tanpa kehilangan apa pun, karena sumbernya tetap
(log mesin tidak berubah; pengajuan & validasi punya jejaknya sendiri). Urutan penentuan status satu karyawan-tanggal:

  1. Validasi kehadiran yang SUDAH DIVERIFIKASI HRD (termasuk koreksinya)  → hadir/izin/sakit/cuti/alfa      (sumber 'validasi'; kata Admin Dept + HRD mengalahkan mesin)
  2. Pengajuan izin/cuti/sakit/izin khusus berstatus Dilaksanakan          → izin/cuti/sakit                  (sumber 'pengajuan'; ada scan di hari itu = konflik untuk ditinjau)
  3. Cuti hamil (Operasional HRD, tidak dibatalkan)                        → cuti_hamil                       (sumber 'pengajuan')
  4. Hari libur (nasional / cuti bersama / perusahaan) yang berlaku        → libur_nasional (atau hadir bila ada scan)
  5. Jadwal efektif = libur                                                → libur (atau hadir bila ada scan: kerja di hari libur)
  6. Hari kerja: ada scan → hadir; tidak ada scan sama sekali → tanpa_scan (BUKAN alfa: belum jelas → HRD meminta validasi ke Admin Departemen)

Mesin tidak pernah memutuskan 'alfa' sendirian — alfa hanya lahir dari validasi terverifikasi (keputusan sumber manusia, jejak lengkap).
Terlambat/pulang cepat dihitung dari jam shift pada jadwal efektif; lembur resmi = irisan jam lembur DISETUJUI × jam scan (bukan sekadar lama di pabrik).
Semua ambang adalah konfigurasi (settings/.env); angka bawaan = usulan (PROGRESS A100–A110)."""
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from . import schedule
from .models import AttendanceCheck, AttendanceCheckEvent, AttendanceDaily, AttendanceRaw, ChangeRequest, Employee, Holiday

log = logging.getLogger(__name__)
LEAVE_TYPES = {"cuti": "cuti", "sakit": "sakit", "izin": "izin", "izin_khusus": "izin"}   # jenis pengajuan → status rekap
LEAVE_LABEL = {"cuti": "cuti", "sakit": "sakit", "izin": "izin", "izin_khusus": "izin khusus"}
PARTIAL = ("izin_terlambat", "izin_pulang")
HOLIDAY_WORK_NOTE = "Masuk di hari libur"   # penanda di `notes` untuk hari hadir pada hari libur; DIBACA Payroll (Hari Libur) → jangan diubah tanpa menyesuaikan apps/payroll/engine.py
SIG = ("department_id", "shift_id", "status", "source", "first_in", "last_out", "scan_count", "incomplete", "late_minutes", "early_minutes", "overtime_minutes",
       "overtime_approved_minutes", "standby_minutes", "extra_minutes", "conflict", "notes")   # tanda tangan isi baris: dibandingkan agar baris tak berubah tidak ditulis ulang
FIELDS = ["department", "shift", "status", "source", "first_in", "last_out", "scan_count", "incomplete", "late_minutes", "early_minutes", "overtime_minutes",
          "overtime_approved_minutes", "standby_minutes", "extra_minutes", "conflict", "notes", "computed_at"]


def cfg(name, default): return getattr(settings, name, default)
def _m(name, default): return timedelta(minutes=int(cfg(name, default)))


def shift_bounds(d: date, shift):
    """(mulai, selesai) sebagai datetime lokal polos untuk shift pada tanggal kerja d; shift lewat tengah malam berakhir pada hari berikutnya."""
    st = datetime.combine(d, shift.start); en = datetime.combine(d, shift.end)
    if shift.crosses_midnight or en <= st: en += timedelta(days=1)
    return st, en


_TZ = None
def _tz():
    global _TZ
    if _TZ is None or str(_TZ) != str(timezone.get_current_timezone()): _TZ = timezone.get_current_timezone()
    return _TZ
def _aware(dt): return dt.replace(tzinfo=_tz())       # zoneinfo: tanpa pytz; jauh lebih cepat dari make_aware untuk ratusan ribu baris
def _naive(dt): return dt.astimezone(_tz()).replace(tzinfo=None)


# ---------------------------------------------------------------- scan → tanggal kerja
def assign_workdays(taps, sched):
    """{tanggal_kerja: [scan naik]} — tiap scan dimasukkan ke hari kerja yang jendela shift-nya (mulai − sebelum … selesai + sesudah) memuatnya;
    bila beberapa jendela memuatnya, pilih yang paling dekat dengan jam shift (menangani shift malam yang selesai esok hari). Di luar jendela mana pun
    (hari libur / belum punya shift / scan nyasar) → tanggal kalender scan."""
    inb, outa = _m("ATTENDANCE_IN_BEFORE_MIN", 240), _m("ATTENDANCE_OUT_AFTER_MIN", 360)
    by = defaultdict(list)
    for t in sorted(taps):
        best = None
        for dd in (t.date() - timedelta(days=1), t.date(), t.date() + timedelta(days=1)):
            c = sched.get(dd)
            if not c or c["off"] or not c["shift"]: continue
            st, en = shift_bounds(dd, c["shift"])
            if st - inb <= t <= en + outa:
                dist = timedelta(0) if st <= t <= en else min(abs(t - st), abs(t - en))
                if best is None or dist < best[0]: best = (dist, dd)
        by[best[1] if best else t.date()].append(t)
    return by


def analyze_day(taps, d, shift, off):
    """Dari scan satu tanggal kerja → dict(first_in, last_out, scan_count, incomplete, late, early, extra). Waktu = datetime lokal polos."""
    r = {"first_in": None, "last_out": None, "scan_count": len(taps), "incomplete": False, "late": 0, "early": 0, "extra": 0}
    if not taps: return r
    first, last = min(taps), max(taps)
    st = en = None
    if shift and not off: st, en = shift_bounds(d, shift)
    if last - first < _m("ATTENDANCE_MIN_GAP_MIN", 30):   # hanya satu kejadian scan (atau ketukan ganda) → masuk atau pulang saja
        r["incomplete"] = True
        if st and first > st + (en - st) / 2: r["last_out"] = first
        else: r["first_in"] = first
    else: r["first_in"], r["last_out"] = first, last
    fi, lo = r["first_in"], r["last_out"]
    if st:
        if fi: r["late"] = max(0, int((fi - st).total_seconds() // 60) - int(cfg("ATTENDANCE_LATE_GRACE_MIN", 0)))
        if lo and not r["incomplete"]: r["early"] = max(0, int((en - lo).total_seconds() // 60))
        thr = int(cfg("ATTENDANCE_EXTRA_MIN_MINUTES", 30)); pre = int((st - fi).total_seconds() // 60) if fi else 0; post = int((lo - en).total_seconds() // 60) if lo else 0
        r["extra"] = (pre if pre >= thr else 0) + (post if post >= thr else 0)
    elif fi and lo: r["extra"] = int((lo - fi).total_seconds() // 60)   # hari libur / tanpa shift: seluruh durasi scan adalah 'kelebihan' menurut mesin
    return r


def _overlap(a1, a2, b1, b2):
    s, e = max(a1, b1), min(a2, b2)
    return max(0, int((e - s).total_seconds() // 60))


def overtime_proof(d, windows, fi, lo):
    """(disetujui, terbukti): disetujui = total menit pengajuan; terbukti = irisan jam pengajuan × [scan pertama, scan terakhir] (toleransi bawaan 15 menit)."""
    approved = sum(w[2] for w in windows)
    if not approved: return 0, 0
    proven = 0
    if fi and lo:
        for t0, t1, mins in windows:
            ws = datetime.combine(d, t0); we = ws + timedelta(minutes=mins)
            proven += _overlap(ws, we, fi, lo)
    if proven >= approved - int(cfg("ATTENDANCE_OT_TOLERANCE_MIN", 15)): proven = approved   # selisih kecil = jam scan wajar, tidak merugikan karyawan
    return approved, min(proven, approved)


# ---------------------------------------------------------------- pemuatan data pendukung (jumlah query tetap per kelompok karyawan)
def _checks(ids, start, end):
    """{(employee_id, tanggal): hasil} untuk validasi TERVERIFIKASI (koreksi terakhir menang)."""
    cs = list(AttendanceCheck.objects.filter(employee_id__in=ids, date__gte=start, date__lte=end, status="diverifikasi").values_list("pk", "employee_id", "date", "final_answer"))
    corr = {}
    if cs:
        for att_id, v in AttendanceCheckEvent.objects.filter(att_id__in=[c[0] for c in cs], action="koreksi").order_by("id").values_list("att_id", "value"): corr[att_id] = v
    return {(eid, d): (corr.get(pk) or fa) for pk, eid, d, fa in cs}


def _requests(ids, start, end):
    """(cuti/izin/sakit {(eid,tgl): (jenis, id)}, parsial {(eid,tgl): {jenis}}, lembur {(eid,tgl): [(jam, menit)]}, standby {(eid,tgl): menit})."""
    leave, part, ot, sb = {}, defaultdict(set), defaultdict(list), defaultdict(int)
    since = timezone.now() - timedelta(days=int(cfg("ATTENDANCE_REQUEST_LOOKBACK_DAYS", 400)))
    for r in ChangeRequest.objects.filter(employee_id__in=ids, status="executed", type__in=(*LEAVE_TYPES, *PARTIAL), created_at__gte=since).only("employee_id", "type", "payload"):
        p = r.payload or {}
        try:
            a, b = date.fromisoformat(p.get("start_date") or p.get("date")), date.fromisoformat(p.get("end_date") or p.get("date"))
        except (TypeError, ValueError): continue
        d = max(a, start)
        while d <= min(b, end):
            if r.type in PARTIAL: part[(r.employee_id, d)].add(r.type)
            else: leave.setdefault((r.employee_id, d), (r.type, r.pk))
            d += timedelta(days=1)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    for r in ChangeRequest.objects.filter(employee_id__in=ids, status="executed", type__in=("lembur", "standby"), payload__date__in=days).only("employee_id", "type", "payload"):
        p = r.payload or {}
        try: d = date.fromisoformat(p["date"]); mins = int(p.get("minutes", 0))
        except (KeyError, TypeError, ValueError): continue
        if r.type == "standby": sb[(r.employee_id, d)] += mins
        else:
            try: t0 = datetime.strptime(p["time"], "%H:%M").time()
            except (KeyError, ValueError): t0 = time(0, 0)
            ot[(r.employee_id, d)].append((t0, t0, mins))
    return leave, part, ot, sb


def _maternity(ids, start, end):
    from apps.hrd.models import MaternityLeave
    out = {}
    for eid, a, b in MaternityLeave.objects.filter(employee_id__in=ids, state__in=("aktif", "selesai"), start_date__lte=end, end_date__gte=start).values_list("employee_id", "start_date", "end_date"):
        d = max(a, start)
        while d <= min(b, end): out[(eid, d)] = True; d += timedelta(days=1)
    return out


def _separations():
    from apps.hrd.models import Separation
    return dict(Separation.objects.filter(voided_at__isnull=True).values_list("employee_id", "last_date"))


# ---------------------------------------------------------------- inti
def compute_rows(emps, start, end, sep=None):
    """list[AttendanceDaily] (belum disimpan) untuk `emps` pada [start, end]. Jumlah query tetap, bukan per karyawan."""
    emps = list(emps)
    if not emps: return []
    ids = [e.pk for e in emps]; sep = _separations() if sep is None else sep
    pad0, days = start - timedelta(days=1), (end - start).days + 3
    grid = {e.pk: {c["date"]: c for c in cells} for e, cells in schedule.schedule_grid(emps, pad0, days)}
    taps = defaultdict(list)
    for eid, at in AttendanceRaw.objects.filter(employee_id__in=ids, punched_at__gte=_aware(datetime.combine(pad0, time.min)),
                                               punched_at__lt=_aware(datetime.combine(end + timedelta(days=2), time.min))).values_list("employee_id", "punched_at").iterator(chunk_size=20000):
        taps[eid].append(_naive(at).replace(microsecond=0))
    chk = _checks(ids, start, end); leave, part, ot, sb = _requests(ids, start, end); mat = _maternity(ids, start, end)
    hol = {h.date: h for h in Holiday.objects.filter(active=True, date__gte=start, date__lte=end)}
    out = []
    for e in emps:
        by = assign_workdays(taps.get(e.pk, []), grid[e.pk]); last_day = sep.get(e.pk)
        if e.status == "nonaktif" and last_day is None: continue
        d = max(start, e.join_date)
        while d <= end:
            if last_day is not None and d > last_day: break
            out.append(_row(e, d, grid[e.pk][d], by.get(d, []), chk.get((e.pk, d)), leave.get((e.pk, d)), part.get((e.pk, d), ()), ot.get((e.pk, d), []),
                            sb.get((e.pk, d), 0), mat.get((e.pk, d)), hol.get(d)))
            d += timedelta(days=1)
    return out


def _row(e, d, cell, taps, check, leave, partial, windows, standby, maternity, holiday):
    shift, off = cell["shift"], cell["off"]
    a = analyze_day(taps, d, shift, off)
    status, source, conflict, notes = None, "mesin", "", []
    has_scan = bool(taps)
    if check:
        status, source = check, "validasi"
    elif leave:
        status, source = LEAVE_TYPES[leave[0]], "pengajuan"
        if has_scan: conflict = f"Ada scan pada hari {LEAVE_LABEL[leave[0]]} (pengajuan #{leave[1]})"
    elif maternity:
        status, source = "cuti_hamil", "pengajuan"
        if has_scan: conflict = "Ada scan pada masa cuti hamil"
    elif holiday and (holiday.applies == "all" or not e.is_rotation):
        status, source = ("hadir", "mesin") if has_scan else ("libur_nasional", "libur")
        if has_scan: notes.append(f"{HOLIDAY_WORK_NOTE}: {holiday.name}")
    elif off:
        status, source = ("hadir", "mesin") if has_scan else ("libur", "jadwal")
        if has_scan: notes.append(f"{HOLIDAY_WORK_NOTE} jadwal")
    else:
        status, source = ("hadir", "mesin") if has_scan else ("tanpa_scan", "mesin")
        if not shift: notes.append("Karyawan belum punya shift: terlambat tidak dihitung")
    late, early = (a["late"], a["early"]) if status == "hadir" else (0, 0)
    if "izin_terlambat" in partial and late: notes.append(f"Izin terlambat ({late} menit tidak dihitung)"); late = 0
    if "izin_pulang" in partial and early: notes.append(f"Izin pulang ({early} menit tidak dihitung)"); early = 0
    approved, proven = overtime_proof(d, windows, a["first_in"], a["last_out"])
    if approved and proven < approved:
        conflict = (conflict + " · " if conflict else "") + f"Lembur disetujui {approved} menit, terbukti scan {proven} menit"
    elif a["extra"] >= int(cfg("ATTENDANCE_EXTRA_MIN_MINUTES", 30)) and a["extra"] - approved >= int(cfg("ATTENDANCE_EXTRA_MIN_MINUTES", 30)) and status == "hadir":
        notes.append(f"Kelebihan jam menurut mesin {a['extra']} menit tanpa pengajuan lembur")
    if standby and not has_scan and status not in ("libur", "libur_nasional"): conflict = (conflict + " · " if conflict else "") + "Stand by disetujui tetapi tanpa scan"
    fi = _aware(a["first_in"]) if a["first_in"] else None; lo = _aware(a["last_out"]) if a["last_out"] else None
    return AttendanceDaily(employee=e, date=d, department_id=e.department_id, shift=shift, status=status, source=source, first_in=fi, last_out=lo,
                           scan_count=min(a["scan_count"], 32000), incomplete=a["incomplete"] and status == "hadir", late_minutes=late, early_minutes=early,
                           overtime_minutes=proven, overtime_approved_minutes=approved, standby_minutes=standby, extra_minutes=a["extra"],
                           conflict=conflict[:200], notes="; ".join(notes)[:200])


def process(start: date, end: date, employee_ids=None, today=None, chunk=250):
    """Hitung & simpan (upsert) rekap harian. Hari ini dan masa depan TIDAK diolah (hari belum selesai). → {rows, employees, start, end}."""
    today = today or timezone.localdate(); end = min(end, today - timedelta(days=1))
    if end < start: return {"rows": 0, "changed": 0, "employees": 0, "start": start, "end": end}
    from . import periods   # bulan yang sudah ditutup tidak dihitung ulang
    segs = periods.open_segments(start, end)
    if segs != [(start, end)]:
        tot = {"rows": 0, "changed": 0, "employees": 0, "start": start, "end": end, "skipped_closed": True}
        for a, b in segs:
            r = process(a, b, employee_ids, today, chunk); tot["rows"] += r["rows"]; tot["changed"] += r["changed"]; tot["employees"] = max(tot["employees"], r["employees"])
        return tot
    qs = Employee.objects.filter(join_date__lte=end).select_related("shift", "shift_group").order_by("pk")
    sep = _separations()
    if employee_ids is not None: qs = qs.filter(pk__in=list(employee_ids))
    qs = qs.filter(~Q(status="nonaktif") | Q(pk__in=[k for k, v in sep.items() if v >= start]))  # nonaktif hanya bila masih dalam masa kerja (Karyawan Keluar)
    ids = list(qs.values_list("pk", flat=True)); n = changed = 0
    for i in range(0, len(ids), chunk):
        part = list(qs.filter(pk__in=ids[i:i + chunk]))
        rows = compute_rows(part, start, end, sep); n += len(rows)
        old = {(r[0], r[1]): r[2:] for r in AttendanceDaily.objects.filter(employee_id__in=[e.pk for e in part], date__gte=start, date__lte=end).values_list("employee_id", "date", *SIG)}
        todo = [r for r in rows if old.get((r.employee_id, r.date)) != tuple(getattr(r, f) for f in SIG)]   # hitung ulang yang tidak berubah = tidak ditulis ulang
        changed += len(todo)
        with transaction.atomic():
            for j in range(0, len(todo), 1000):
                AttendanceDaily.objects.bulk_create(todo[j:j + 1000], update_conflicts=True, unique_fields=["employee", "date"], update_fields=FIELDS)
    return {"rows": n, "changed": changed, "employees": len(ids), "start": start, "end": end}


def recompute(employee, d: date):
    """Hitung ulang satu karyawan-tanggal (dipanggil saat validasi diverifikasi/dikoreksi). Tidak boleh menggagalkan pemanggil."""
    try:
        with transaction.atomic(): return process(d, d, [employee.pk])
    except Exception:  # noqa: BLE001 — kegagalan hitung ulang tak boleh membatalkan verifikasi; perintah process_attendance menyusul
        log.exception("Gagal menghitung ulang absensi %s %s", employee.pk, d); return None


# ---------------------------------------------------------------- ringkasan (dipakai rekap bulanan, dashboard, ekspor, kelak Payroll)
def summary(qs):
    """Agregasi per karyawan dari queryset AttendanceDaily → list[dict]. Basis data yang mengagregasi (bukan Python)."""
    rows = qs.values("employee_id", "employee__nik", "employee__name", "department__name").annotate(
        hadir=Count("id", filter=Q(status="hadir")), terlambat=Count("id", filter=Q(late_minutes__gt=0)), terlambat_menit=Sum("late_minutes"),
        pulang_cepat=Count("id", filter=Q(early_minutes__gt=0)), pulang_cepat_menit=Sum("early_minutes"),
        alfa=Count("id", filter=Q(status="alfa")), tanpa_scan=Count("id", filter=Q(status="tanpa_scan")), izin=Count("id", filter=Q(status="izin")),
        sakit=Count("id", filter=Q(status="sakit")), cuti=Count("id", filter=Q(status="cuti")), cuti_hamil=Count("id", filter=Q(status="cuti_hamil")),
        libur=Count("id", filter=Q(status__in=("libur", "libur_nasional"))), lembur=Sum("overtime_minutes"), standby=Sum("standby_minutes"),
        tidak_lengkap=Count("id", filter=Q(incomplete=True)), konflik=Count("id", filter=~Q(conflict="")),
        hari=Count("id", filter=~Q(status__in=("libur", "libur_nasional")))).order_by("department__name", "employee__name")
    out = []
    for r in rows:
        r = dict(r)
        for k in ("terlambat_menit", "pulang_cepat_menit", "lembur", "standby"): r[k] = r[k] or 0
        r["perlu_tinjau"] = r["tanpa_scan"] + r["tidak_lengkap"] + r["konflik"]
        out.append(r)
    return out


def day_overview(d: date, department_id=None):
    """Angka ringkas satu tanggal (untuk dashboard). None bila tanggal itu belum pernah diolah."""
    qs = AttendanceDaily.objects.filter(date=d)
    if department_id: qs = qs.filter(department_id=department_id)
    c = qs.aggregate(total=Count("id"), hadir=Count("id", filter=Q(status="hadir")), terlambat=Count("id", filter=Q(late_minutes__gt=0)),
                     izin=Count("id", filter=Q(status__in=("izin", "sakit", "cuti", "cuti_hamil"))), alfa=Count("id", filter=Q(status="alfa")),
                     tanpa_scan=Count("id", filter=Q(status="tanpa_scan")), libur=Count("id", filter=Q(status__in=("libur", "libur_nasional"))))
    return c if c["total"] else None


def latest_date():
    return AttendanceDaily.objects.order_by("-date").values_list("date", flat=True).first()
