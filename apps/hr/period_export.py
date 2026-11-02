"""Snapshot XLSX tutup periode = data serah-terima HRD → Payroll (putaran 59). Tujuh sheet, header di baris 1 (mudah diimpor/diolah mesin):

  1 Rekap Absensi      per karyawan: hadir, libur, cuti, izin, sakit, alfa, tanpa scan, terlambat, pulang cepat
  2 Detail Absensi Harian  per karyawan × tanggal (kolom 1 s/d akhir rentang): kode status per hari (H hadir, C cuti, CH cuti melahirkan, S sakit, I izin, A alfa, L libur, dst) + total
  3 Lembur & Standby   per hari: Lembur, Stand By, Masuk Hari Libur (menit disetujui & terbukti scan)
  4 BPJS               per karyawan × program: status, porsi karyawan & perusahaan (gaji bulan label), perubahan status di rentang
  5 Seragam            pembelian seragam: baru di rentang + yang belum dipotong dari periode lalu
  6 Bantuan & Lainnya  bantuan, karyawan keluar (tali asih), cuti hamil, pekerja harian proyek
  7 Perubahan Karyawan karyawan baru masuk + perubahan departemen/jabatan/status/shift di rentang (memengaruhi hitungan gaji)

Menambah sheet = tulis satu fungsi `_xxx(wb, start, end, label) → jumlah baris` lalu daftarkan di REGISTRY (urutan = urutan sheet).
`build(start, end, label)` → (bytes, {sheet: jumlah_baris}). Hanya MEMBACA data. Tanpa nomor BPJS/KTP/rekening (kolom terenkripsi tidak pernah disalin).
Sel teks yang diawali = + - @ diberi apostrof (cegah rumus tereksekusi di spreadsheet)."""
import io
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.core.money import XLSX_RUPIAH_FORMAT
from . import attendance_calc as calc
from .models import AttendanceDaily, ChangeRequest, Employee, ShiftAssignment

SHEETS = ("Rekap Absensi", "Detail Absensi Harian", "Lembur & Standby", "BPJS", "Seragam", "Bantuan & Lainnya", "Perubahan Karyawan")
DATE_FMT = "DD-MM-YYYY"
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F"); HEAD_FONT = Font(bold=True, color="FFFFFF")
CHUNK = 500   # batas variabel SQL (SQLite 999)


def _chunks(seq, n=CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n): yield seq[i:i + n]


def _t(v):
    """Teks aman untuk spreadsheet."""
    if v is None: return ""
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


def _money(v): return None if v in (None, "") else (int(v) if Decimal(v) == Decimal(v).to_integral_value() else float(v))


class _Sheet:
    """Satu sheet: header + baris; kolom bertipe: 't' teks, 'n' angka, 'm' rupiah, 'd' tanggal."""
    def __init__(self, wb, title, cols):
        self.ws = wb.create_sheet(title); self.cols = cols; self.n = 0; self.width = [len(c[0]) for c in cols]
        self.ws.append([c[0] for c in cols])
        for i, c in enumerate(self.ws[1]): c.fill, c.font, c.alignment = HEAD_FILL, HEAD_FONT, Alignment(vertical="center", wrap_text=True)
        self.ws.freeze_panes = "A2"

    def add(self, *vals):
        row = []
        for (name, kind), v in zip(self.cols, vals):
            if kind == "t": v = _t(v)
            elif kind == "m": v = _money(v)
            elif kind == "d" and isinstance(v, datetime): v = v.date()
            row.append(v)
        self.ws.append(row); self.n += 1
        r = self.n + 1
        for i, ((name, kind), v) in enumerate(zip(self.cols, row), start=1):
            cell = self.ws.cell(row=r, column=i)
            if kind == "m": cell.number_format = XLSX_RUPIAH_FORMAT
            elif kind == "d": cell.number_format = DATE_FMT
            self.width[i - 1] = max(self.width[i - 1], min(len(str(v)) if v is not None else 0, 48))

    def finish(self):
        for i, w in enumerate(self.width, start=1): self.ws.column_dimensions[get_column_letter(i)].width = max(9, min(w + 2, 50))
        self.ws.auto_filter.ref = f"A1:{get_column_letter(len(self.cols))}{max(self.n + 1, 1)}"
        return self.n


def _emp_info(ids):
    """{employee_id: (jabatan, status gaji)}."""
    out = {}
    for part in _chunks(ids):
        for e in Employee.objects.filter(pk__in=part).select_related("position"):
            out[e.pk] = (e.position.name if e.position_id else "", e.get_pay_basis_display() if hasattr(e, "get_pay_basis_display") else e.pay_basis)
    return out


# ---------------------------------------------------------------- sheet 1
def _attendance(wb, start, end, label=None):
    sh = _Sheet(wb, SHEETS[0], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jabatan", "t"), ("Status gaji", "t"), ("Hari tercatat", "n"), ("Hadir", "n"), ("Libur / libur nasional", "n"),
                                ("Cuti", "n"), ("Cuti hamil", "n"), ("Izin", "n"), ("Sakit", "n"), ("Alfa", "n"), ("Tanpa scan", "n"), ("Terlambat (kali)", "n"), ("Terlambat (menit)", "n"),
                                ("Pulang cepat (kali)", "n"), ("Pulang cepat (menit)", "n"), ("Perlu ditinjau (hari)", "n")])
    data = calc.summary(AttendanceDaily.objects.filter(date__gte=start, date__lte=end))
    info = _emp_info([r["employee_id"] for r in data])
    for r in data:
        pos, basis = info.get(r["employee_id"], ("", ""))
        sh.add(r["employee__nik"], r["employee__name"], r["department__name"] or "", pos, basis, r["hari"] + r["libur"], r["hadir"], r["libur"], r["cuti"], r["cuti_hamil"], r["izin"], r["sakit"], r["alfa"],
               r["tanpa_scan"], r["terlambat"], r["terlambat_menit"], r["pulang_cepat"], r["pulang_cepat_menit"], r["perlu_tinjau"])
    return sh.finish()


# ---------------------------------------------------------------- sheet 2: detail absensi per tanggal
DAY_CODES = {"hadir": "H", "libur": "L", "libur_nasional": "LN", "cuti": "C", "cuti_hamil": "CH", "izin": "I", "sakit": "S", "alfa": "A", "tanpa_scan": "NS"}
DAY_LEGEND = (("H", "Hadir (berangkat)"), ("L", "Libur / hari libur jadwal"), ("LN", "Libur nasional"), ("C", "Cuti"), ("CH", "Cuti melahirkan (cuti hamil)"), ("I", "Izin"),
              ("S", "Sakit"), ("A", "Alfa"), ("NS", "Tanpa scan (hari kerja tanpa scan, belum ada keterangan)"), ("TS", "Tukar shift (hari itu jadwal shift ditukar)"), ("TL+", "Tukar libur: hari yang tadinya libur menjadi masuk"), ("TL-", "Tukar libur: hari yang tadinya masuk menjadi libur"), ("(kosong)", "Tidak ada rekap harian pada tanggal itu"))
DAY_TOTALS = (("Hadir", ("hadir",)), ("Libur", ("libur", "libur_nasional")), ("Cuti", ("cuti",)), ("Cuti melahirkan", ("cuti_hamil",)), ("Izin", ("izin",)), ("Sakit", ("sakit",)),
              ("Alfa", ("alfa",)), ("Tanpa scan", ("tanpa_scan",)))
DAY_FILL = {"A": "F8D7DA", "NS": "FFF3CD", "TS": "FDE2C4", "TL+": "E8D9F5", "TL-": "D9C2EE", "S": "D1ECF1", "I": "E2E3F3", "C": "D4EDDA", "CH": "D4EDDA", "L": "ECECEC", "LN": "ECECEC"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


def _day_label(d, multi_month):
    """Judul kolom tanggal: '1', '2', … bila rentang di satu bulan; '26 Sep', '1 Okt', … bila melewati bulan (agar tidak ambigu)."""
    return f"{d.day} {MONTHS[d.month - 1]}" if multi_month else str(d.day)


REQ_LABEL = {"tukar_shift": "Tukar shift", "tukar_libur": "Tukar libur", "izin": "Izin", "cuti": "Cuti", "sakit": "Sakit", "izin_terlambat": "Izin terlambat", "izin_pulang": "Izin pulang cepat",
             "izin_khusus": "Izin khusus", "lembur": "Lembur", "standby": "Stand by", "shift": "Ubah shift", "rotasi": "Rotasi"}
ASSIGN_LABEL = {ShiftAssignment.SHIFT: "masuk shift {shift}", ShiftAssignment.WORK: "masuk (shift reguler)", ShiftAssignment.OFF: "libur"}


def _iso(v):
    try: return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError): return None


def _notes(emp_ids, start, end):
    """{employee_id: [(tanggal, teks)]} — keterangan per hari di rentang: hasil tukar shift/libur (penyesuaian jadwal yang berlaku), pengajuan yang dilaksanakan
    (izin, cuti, sakit, lembur, stand by, dst.) dan catatan rekap harian. Hanya membaca."""
    out = {}; names = {}
    def put(eid, d, txt):
        if d and start <= d <= end and txt not in [t for dd, t in out.get(eid, []) if dd == d]: out.setdefault(eid, []).append((d, txt))
    for part in _chunks(emp_ids):
        for a in ShiftAssignment.objects.filter(employee_id__in=part, date__gte=start, date__lte=end).select_related("shift", "request", "request__employee"):
            rq = a.request; lbl = REQ_LABEL.get(rq.type, rq.type); pid = (rq.payload or {}).get("partner_id")
            other = rq.employee_id if rq.employee_id != a.employee_id else pid       # lawan tukar: pemohon (bila ini rekan) atau rekan (bila ini pemohon)
            if other and other not in names: names[other] = (Employee.all_objects.filter(pk=other).values_list("name", flat=True).first() or "")
            who = f" dgn {names[other]}" if other and names.get(other) else ""
            put(a.employee_id, a.date, f"{lbl}: {ASSIGN_LABEL.get(a.kind, a.kind).format(shift=a.shift.name if a.shift_id else '')}{who}")
        for r in ChangeRequest.objects.filter(employee_id__in=part, status="executed", type__in=[t for t in REQ_LABEL if not t.startswith("tukar")]):
            p = r.payload or {}; a, b = _iso(p.get("start_date") or p.get("date")), _iso(p.get("end_date") or p.get("date"))
            if not a or not b or b < start or a > end: continue
            extra = f" ({p['minutes']} mnt)" if p.get("minutes") else ""
            d = max(a, start)
            while d <= min(b, end): put(r.employee_id, d, REQ_LABEL[r.type] + extra); d = date.fromordinal(d.toordinal() + 1)
        for eid, d, nt in AttendanceDaily.objects.filter(employee_id__in=part, date__gte=start, date__lte=end).exclude(notes="").values_list("employee_id", "date", "notes"): put(eid, d, nt)
    return out


def _attendance_daily(wb, start, end, label=None):
    from datetime import timedelta
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]; multi = (start.year, start.month) != (end.year, end.month)
    sh = _Sheet(wb, SHEETS[1], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Bagian / Jabatan", "t"), ("Shift", "t")] + [(_day_label(d, multi), "t") for d in days] + [(n, "n") for n, _ in DAY_TOTALS]
                + [("Keterangan (tukar shift, tukar libur, izin, dst.)", "t")])
    people = {}
    for eid, d, st, dept, nik, name in AttendanceDaily.objects.filter(date__gte=start, date__lte=end).order_by("date").values_list(
            "employee_id", "date", "status", "department__name", "employee__nik", "employee__name"):
        p = people.setdefault(eid, {"id": eid, "nik": nik, "name": name, "dept": dept or "", "days": {}}); p["days"][d] = st; p["dept"] = dept or p["dept"]      # departemen = yang terakhir tercatat
    emps = {}
    for part in _chunks(list(people)):
        for e in Employee.all_objects.filter(pk__in=part).select_related("position", "shift", "shift_group"): emps[e.pk] = e
    notes = _notes(list(people), start, end)
    swaps = {}                                                                   # (karyawan, tanggal) → TS / TL bila hari itu hasil tukar shift / tukar libur yang berlaku
    for part in _chunks(list(people)):
        for eid, d, t, k in ShiftAssignment.objects.filter(employee_id__in=part, date__gte=start, date__lte=end, request__type__in=("tukar_shift", "tukar_libur")).values_list("employee_id", "date", "request__type", "kind"):
            swaps[(eid, d)] = "TS" if t == "tukar_shift" else ("TL-" if k == ShiftAssignment.OFF else "TL+")
    first = 6
    for p in sorted(people.values(), key=lambda p: (p["dept"].casefold(), p["name"].casefold(), p["nik"])):
        codes = [DAY_CODES.get(p["days"].get(d), "") if p["days"].get(d) else "" for d in days]
        codes = [swaps.get((p["id"], d), c) if c in ("H", "L") else c for c, d in zip(codes, days)]   # TS/TL+/TL- menimpa H/L saja; alfa, izin, sakit, cuti, CH tetap terlihat
        sts = list(p["days"].values()); e = emps.get(p["id"])
        ket = "; ".join(f"{_day_label(d, True)}: {t}" for d, t in sorted(notes.get(p["id"], [])))
        sh.add(p["nik"], p["name"], p["dept"], (e.position.name if e and e.position_id else ""), (e.shift_label if e else ""), *codes, *[sum(1 for x in sts if x in keys) for _, keys in DAY_TOTALS], ket)
        r = sh.n + 1
        for i, c in enumerate(codes):
            cell = sh.ws.cell(row=r, column=first + i); cell.alignment = Alignment(horizontal="center")
            if c in DAY_FILL: cell.fill = PatternFill("solid", fgColor=DAY_FILL[c])
    n = sh.finish(); ws = sh.ws; ws.freeze_panes = "D2"
    for i in range(len(days)): ws.column_dimensions[get_column_letter(first + i)].width = 6 if multi else 4.5
    ws.column_dimensions[get_column_letter(len(sh.cols))].width = 60
    for c in ws[1][first - 1:first - 1 + len(days)]: c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.append([]); ws.append(["Kode", "Keterangan"])
    for c in ws[ws.max_row]: c.font = Font(bold=True)
    for code, text in DAY_LEGEND: ws.append([code, text])                       # legenda di bawah tabel (di luar rentang filter), jauh dari baris data
    return n


# ---------------------------------------------------------------- sheet 3
def _overtime(wb, start, end, label=None):
    sh = _Sheet(wb, SHEETS[2], [("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jenis", "t"), ("Menit disetujui", "n"), ("Menit terbukti scan", "n"), ("Jam terbukti", "n"), ("Keterangan", "t")])
    base = AttendanceDaily.objects.filter(date__gte=start, date__lte=end).select_related("employee", "department")
    rows = []
    for a in base.filter(overtime_approved_minutes__gt=0) | base.filter(overtime_minutes__gt=0):
        rows.append((a.date, a.employee.nik, a.employee.name, a.department.name if a.department_id else "", "Lembur", a.overtime_approved_minutes, a.overtime_minutes, round(a.overtime_minutes / 60, 2),
                     "Disetujui tetapi tidak terbukti scan" if a.overtime_approved_minutes and not a.overtime_minutes else ""))
    for a in base.filter(standby_minutes__gt=0):
        rows.append((a.date, a.employee.nik, a.employee.name, a.department.name if a.department_id else "", "Stand By", a.standby_minutes, None, None, "Tidak diverifikasi mesin (bekerja saat istirahat)"))
    for a in base.filter(status="hadir", notes__startswith=calc.HOLIDAY_WORK_NOTE):
        rows.append((a.date, a.employee.nik, a.employee.name, a.department.name if a.department_id else "", "Masuk Hari Libur", None, None, None, a.notes))
    rows.sort(key=lambda r: (r[0], r[3], r[2], r[4]))
    for r in rows: sh.add(*r)
    return sh.finish()


# ---------------------------------------------------------------- sheet 3
def _bpjs(wb, start, end, label=None):
    from apps.hrd.models import BpjsDeduction, BpjsMembership, BpjsScheme, BpjsStatusLog
    sh = _Sheet(wb, SHEETS[3], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Program", "t"), ("Status BPJS sekarang", "t"), ("Potongan karyawan", "m"), ("Porsi perusahaan", "m"),
                                ("Perubahan status di periode", "t"), ("Catatan", "t")])
    scheme = dict(BpjsScheme.choices)
    ded = {(d.employee_id, d.scheme): d for d in BpjsDeduction.objects.filter(period=label).select_related("employee__department")}
    logs = {}
    for l in BpjsStatusLog.objects.filter(effective_date__gte=start, effective_date__lte=end).select_related("employee__department").order_by("effective_date", "id"):
        logs.setdefault((l.employee_id, l.scheme), []).append(l)
    keys = sorted(set(ded) | set(logs), key=lambda k: ((ded.get(k) or logs[k][0]).employee.department.name, (ded.get(k) or logs[k][0]).employee.name, k[1]))
    status = {}
    for part in _chunks({k[0] for k in keys}):
        for m in BpjsMembership.objects.filter(employee_id__in=part): status[(m.employee_id, m.scheme)] = m.get_status_display()
    for k in keys:
        d = ded.get(k); e = (d or logs[k][0]).employee
        change = "; ".join(f"{l.effective_date:%d-%m-%Y}: {l.old_status or '-'} → {l.new_status}" for l in logs.get(k, []))
        sh.add(e.nik, e.name, e.department.name, scheme.get(k[1], k[1]), status.get(k, ""), d.employee_amount if d else None, d.employer_amount if d else None, change,
               d.note if d else ("Belum ada potongan untuk gaji " + label if label else ""))
    return sh.finish()


# ---------------------------------------------------------------- sheet 4
def _uniforms(wb, start, end, label=None):
    from django.db.models import Q
    from apps.hrd.models import UniformPurchase
    sh = _Sheet(wb, SHEETS[4], [("Tanggal beli", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jenis", "t"), ("Ukuran", "t"), ("L/P", "t"), ("Jumlah", "n"), ("Tarif satuan", "m"),
                                ("Potongan", "m"), ("Status potong", "t"), ("Periode dipotong", "t"), ("Keterangan", "t")])
    qs = (UniformPurchase.objects.filter(voided_at__isnull=True).filter(Q(purchase_date__gte=start, purchase_date__lte=end) | Q(deduction_status="belum", purchase_date__lt=start))
          .select_related("employee__department", "utype", "size").order_by("purchase_date", "employee__name", "id"))
    for p in qs:
        sh.add(p.purchase_date, p.employee.nik, p.employee.name, p.employee.department.name, p.utype.name, p.size.code, p.gender, p.quantity, p.rate_amount, p.deduction_amount,
               p.get_deduction_status_display(), p.deducted_period, "Tertunda dari periode sebelumnya (belum dipotong)" if p.purchase_date < start else ("Baru di periode ini" if not p.note else p.note))
    return sh.finish()


# ---------------------------------------------------------------- sheet 5
def _others(wb, start, end, label=None):
    from django.db.models import Q
    from apps.hrd.models import Aid, MaternityLeave, ProjectWork, Separation
    sh = _Sheet(wb, SHEETS[5], [("Jenis", "t"), ("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen / proyek", "t"), ("Keterangan", "t"), ("Nominal", "m"), ("Status", "t")])
    rows = []
    kinds = dict(Aid.KINDS)
    for a in Aid.objects.filter(event_date__gte=start, event_date__lte=end).select_related("employee__department"):
        rows.append(("Bantuan – " + kinds.get(a.kind, a.kind), a.event_date, a.employee.nik, a.employee.name, a.employee.department.name, a.description, a.amount, ""))
    sep_kind = dict(Separation.Kind.choices)
    for s in (Separation.objects.filter(voided_at__isnull=True).filter(Q(last_date__gte=start, last_date__lte=end) | Q(tali_asih__gt=0, tali_asih_paid_on__isnull=True, last_date__lte=end))
              .select_related("employee")):
        status = "" if not s.tali_asih else (f"Tali asih dibayar {s.tali_asih_paid_on:%d-%m-%Y}" if s.tali_asih_paid_on else "Tali asih belum dibayar")
        rows.append(("Karyawan keluar – " + sep_kind.get(s.kind, s.kind), s.last_date, s.employee.nik, s.emp_name, s.department_name,
                     " · ".join(x for x in (s.reason, s.tali_asih_note) if x), s.tali_asih or None, status))
    for m in MaternityLeave.objects.filter(state="aktif", start_date__lte=end, end_date__gte=start).select_related("employee__department"):
        rows.append(("Cuti hamil", m.start_date, m.employee.nik, m.employee.name, m.employee.department.name, f"Cuti {m.start_date:%d-%m-%Y} s/d {m.end_date:%d-%m-%Y} (HPL {m.due_date:%d-%m-%Y})", None, ""))
    for w in ProjectWork.objects.filter(work_date__gte=start, work_date__lte=end).select_related("project"):
        rows.append(("Pekerja harian proyek", w.work_date, "", w.worker_name, w.project.name, w.activity, w.wage, ""))
    rows.sort(key=lambda r: (r[0].split(" – ")[0], r[1], r[3]))
    for r in rows: sh.add(*r)
    return sh.finish()


# ---------------------------------------------------------------- sheet 6
def _changes(wb, start, end, label=None):
    from .models import EmployeeHistory
    sh = _Sheet(wb, SHEETS[6], [("Jenis", "t"), ("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Perubahan", "t"), ("Dari", "t"), ("Menjadi", "t")])
    rows = []
    for e in Employee.objects.filter(join_date__gte=start, join_date__lte=end).select_related("department", "position"):
        rows.append(("Karyawan baru", e.join_date, e.nik, e.name, e.department.name, "Masuk kerja", "", e.position.name if e.position_id else ""))
    for h in EmployeeHistory.objects.filter(effective_date__gte=start, effective_date__lte=end).select_related("employee__department"):
        rows.append(("Perubahan data", h.effective_date, h.employee.nik, h.employee.name, h.employee.department.name, h.field, h.old_value, h.new_value))
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    for r in rows: sh.add(*r)
    return sh.finish()


REGISTRY = (_attendance, _attendance_daily, _overtime, _bpjs, _uniforms, _others, _changes)


def build(start, end, label):
    """→ (bytes XLSX, {nama sheet: jumlah baris})."""
    wb = Workbook(); wb.remove(wb.active)
    counts = {}
    for fn in REGISTRY:
        n = fn(wb, start, end, label); counts[wb.worksheets[-1].title] = n
    wb.properties.title = f"Tutup periode {start:%d-%m-%Y} s/d {end:%d-%m-%Y} (gaji {label})"
    wb.properties.subject = "Serah-terima data HRD ke Payroll"; wb.properties.creator = "HRIS"
    out = io.BytesIO(); wb.save(out)
    return out.getvalue(), counts
