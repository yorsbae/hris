"""Laporan XLSX Payroll per run gaji (putaran 60) = hasil akhir Payroll, setara berkas Tutup Periode milik HRD. Delapan sheet:

  1 Ringkasan          total & jumlah slip, rentang data, status, parameter yang dipakai
  2 Rekap Gaji         satu baris per karyawan: pokok, tunjangan, premi, lembur, makan, lain-lain, potongan, bersih
  3 Rincian Komponen   semua baris slip (karyawan × komponen) — bahan pivot / jurnal
  4 Per Departemen     jumlah slip dan total per departemen (biaya gaji)
  5 Potongan           Jamsostek, BPJS, seragam, pinjaman, dst. per karyawan (kolom per komponen potongan)
  6 Lembur & Premi     jam & nominal lembur/stand by/hari libur + premi
  7 Transfer Bank      nama, bank, rekening, jumlah bersih (rekening = data sensitif: unduhan diaudit)
  8 Perlu Ditinjau     slip bertanda (negatif, tanpa upah, dsb.)

Menambah sheet = satu fungsi `_xxx(wb, run, slips, items)` di REGISTRY. Hanya MEMBACA. Dibuat segar saat diunduh; saat run DIBAYAR satu salinan diarsipkan (tidak berubah lagi)."""
import io
from collections import defaultdict

from openpyxl import Workbook

from apps.hr.models import Employee
from apps.hr.period_export import _Sheet, _chunks
from . import calc
from .models import PayComponent, PayslipItem

SHEETS = ("Ringkasan", "Rekap Gaji", "Rincian Komponen", "Per Departemen", "Potongan", "Lembur & Premi", "Transfer Bank", "Perlu Ditinjau")
SECTION = dict(PayComponent.SECTIONS)


def _load(run):
    slips = list(run.slips.order_by("department_name", "emp_name", "id"))
    items = defaultdict(list)
    for part in _chunks([s.pk for s in slips]):
        for it in PayslipItem.objects.filter(slip_id__in=part).order_by("order", "id"): items[it.slip_id].append(it)
    return slips, items


def _sec(items, slip, key): return sum(i.amount for i in items[slip.pk] if i.section == key)


def _summary(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[0], [("Keterangan", "t"), ("Nilai", "t")])
    start, end = calc.data_bounds(run.period)
    rows = [("Bulan gaji", run.period), ("Rentang data", f"{start:%d-%m-%Y} s/d {end:%d-%m-%Y}"), ("Status run", run.get_status_display()), ("Jumlah slip", len(slips)),
            ("Total pendapatan", f"{run.total_gross:,.0f}".replace(",", ".")), ("Total potongan", f"{run.total_deductions:,.0f}".replace(",", ".")), ("Total gaji bersih", f"{run.total_net:,.0f}".replace(",", ".")),
            ("Slip perlu ditinjau", sum(1 for s in slips if s.flagged)), ("Dihitung", f"{run.calc_at:%d-%m-%Y %H:%M}" if run.calc_at else ""), ("Dibayar", f"{run.paid_on:%d-%m-%Y}" if run.paid_on else "belum")]
    rows += [("Parameter: " + k, str(v)) for k, v in (run.params or {}).items()]
    rows += [("Peringatan", w) for w in (run.warnings or [])]
    for r in rows: sh.add(*r)
    return sh.finish()


def _rekap(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[1], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Jabatan", "t"), ("Grade", "t"), ("Hari dibayar", "n"), ("Upah harian", "m"), ("Gaji pokok", "m"), ("Tunjangan", "m"),
                                ("Premi", "m"), ("Lembur", "m"), ("Uang makan", "m"), ("Lain-lain", "m"), ("Total pendapatan", "m"), ("Total potongan", "m"), ("Gaji bersih", "m"), ("Catatan", "t")])
    for s in slips:
        sh.add(s.nik, s.emp_name, s.department_name, s.position_name, s.grade_label, s.paid_days, s.daily_wage, *(_sec(items, s, k) for k in ("pokok", "tunjangan", "premi", "lembur", "makan", "lain")),
               s.total_earnings, s.total_deductions, s.net_pay, "; ".join(s.flags))
    return sh.finish()


def _detail(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[2], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Bagian", "t"), ("Komponen", "t"), ("Jumlah", "n"), ("Tarif", "n"), ("Nominal", "m"), ("Sumber", "t"), ("Catatan", "t")])
    for s in slips:
        for i in items[s.pk]: sh.add(s.nik, s.emp_name, s.department_name, SECTION.get(i.section, i.section), i.label, i.qty, i.rate, i.amount, i.source, i.note)
    return sh.finish()


def _dept(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[3], [("Departemen", "t"), ("Jumlah slip", "n"), ("Total pendapatan", "m"), ("Total potongan", "m"), ("Total bersih", "m")])
    agg = defaultdict(lambda: [0, 0, 0, 0])
    for s in slips:
        a = agg[s.department_name]; a[0] += 1; a[1] += s.total_earnings; a[2] += s.total_deductions; a[3] += s.net_pay
    for d in sorted(agg): sh.add(d, *agg[d])
    return sh.finish()


def _components(slips, items, section):
    seen = {}
    for s in slips:
        for i in items[s.pk]:
            if i.section == section: seen.setdefault(i.code, (i.order, i.label))
    return sorted(seen.items(), key=lambda kv: kv[1])


def _deductions(wb, run, slips, items):
    comps = _components(slips, items, "potongan")
    sh = _Sheet(wb, SHEETS[4], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t")] + [(lbl, "m") for _, (_, lbl) in comps] + [("Total potongan", "m")])
    for s in slips:
        by = {i.code: i.amount for i in items[s.pk] if i.section == "potongan"}
        if by: sh.add(s.nik, s.emp_name, s.department_name, *(by.get(c) for c, _ in comps), s.total_deductions)
    return sh.finish()


def _overtime(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[5], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Komponen", "t"), ("Jam / hari", "n"), ("Tarif", "n"), ("Nominal", "m"), ("Catatan", "t")])
    for s in slips:
        for i in items[s.pk]:
            if i.section in ("lembur", "premi"): sh.add(s.nik, s.emp_name, s.department_name, i.label, i.qty, i.rate, i.amount, i.note)
    return sh.finish()


def _bank(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[6], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Bank", "t"), ("No. rekening", "t"), ("Jumlah ditransfer", "m"), ("Catatan", "t")])
    emp = {}
    for part in _chunks([s.employee_id for s in slips]):
        for e in Employee.objects.filter(pk__in=part): emp[e.pk] = e
    for s in slips:
        if s.net_pay <= 0: continue
        e = emp.get(s.employee_id); bank, acc = (e.bank_name, e.bank_account) if e else ("", "")
        sh.add(s.nik, s.emp_name, s.department_name, bank, acc, s.net_pay, "" if (bank and acc) else "Data rekening belum lengkap")
    return sh.finish()


def _review(wb, run, slips, items):
    sh = _Sheet(wb, SHEETS[7], [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("Gaji bersih", "m"), ("Alasan", "t")])
    for s in slips:
        if s.flagged: sh.add(s.nik, s.emp_name, s.department_name, s.net_pay, "; ".join(s.flags))
    return sh.finish()


REGISTRY = (_summary, _rekap, _detail, _dept, _deductions, _overtime, _bank, _review)


def build(run):
    """→ (bytes XLSX, {sheet: jumlah baris})."""
    slips, items = _load(run)
    wb = Workbook(); wb.remove(wb.active); counts = {}
    for fn in REGISTRY:
        n = fn(wb, run, slips, items); counts[wb.worksheets[-1].title] = n
    wb.properties.title = f"Laporan gaji {run.period}"; wb.properties.creator = "HRIS"
    out = io.BytesIO(); wb.save(out); return out.getvalue(), counts
