"""Snapshot XLSX tutup periode = data serah-terima HRD → Payroll (putaran 59). Lima sheet, header di baris 1 (mudah diimpor/diolah mesin):

  1 Rekap Absensi      per karyawan: hadir, libur, cuti, izin, sakit, alfa, tanpa scan, terlambat, pulang cepat
  2 Lembur & Standby   per hari: Lembur, Stand By, Masuk Hari Libur (menit disetujui & terbukti scan)
  3 BPJS               per karyawan × program: status, porsi karyawan & perusahaan (gaji bulan label), perubahan status di rentang
  4 Seragam            pembelian seragam: baru di rentang + yang belum dipotong dari periode lalu
  5 Bantuan & Lainnya  bantuan, karyawan keluar (tali asih), cuti hamil, pekerja harian proyek
  6 Perubahan Karyawan karyawan baru masuk + perubahan departemen/jabatan/status/shift di rentang (memengaruhi hitungan gaji)

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
from .models import AttendanceDaily, Employee

SHEETS = ("Rekap Absensi", "Lembur & Standby", "BPJS", "Seragam", "Bantuan & Lainnya", "Perubahan Karyawan")
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


# ---------------------------------------------------------------- sheet 2
def _overtime(wb, start, end, label=None):
    sh = _Sheet(wb, SHEETS[1], [("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jenis", "t"), ("Menit disetujui", "n"), ("Menit terbukti scan", "n"), ("Jam terbukti", "n"), ("Keterangan", "t")])
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
    sh = _Sheet(wb, SHEETS[2], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Program", "t"), ("Status BPJS sekarang", "t"), ("Potongan karyawan", "m"), ("Porsi perusahaan", "m"),
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
    sh = _Sheet(wb, SHEETS[3], [("Tanggal beli", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jenis", "t"), ("Ukuran", "t"), ("L/P", "t"), ("Jumlah", "n"), ("Tarif satuan", "m"),
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
    sh = _Sheet(wb, SHEETS[4], [("Jenis", "t"), ("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen / proyek", "t"), ("Keterangan", "t"), ("Nominal", "m"), ("Status", "t")])
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
    sh = _Sheet(wb, SHEETS[5], [("Jenis", "t"), ("Tanggal", "d"), ("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Perubahan", "t"), ("Dari", "t"), ("Menjadi", "t")])
    rows = []
    for e in Employee.objects.filter(join_date__gte=start, join_date__lte=end).select_related("department", "position"):
        rows.append(("Karyawan baru", e.join_date, e.nik, e.name, e.department.name, "Masuk kerja", "", e.position.name if e.position_id else ""))
    for h in EmployeeHistory.objects.filter(effective_date__gte=start, effective_date__lte=end).select_related("employee__department"):
        rows.append(("Perubahan data", h.effective_date, h.employee.nik, h.employee.name, h.employee.department.name, h.field, h.old_value, h.new_value))
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    for r in rows: sh.add(*r)
    return sh.finish()


REGISTRY = (_attendance, _overtime, _bpjs, _uniforms, _others, _changes)


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
