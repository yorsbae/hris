"""Halaman Pajak & THR Payroll (putaran 61): THR, PPh 21 (status + rekap tahunan XLSX ≈ bahan 1721-A1)."""
import io
from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.audit import log
from apps.hr.models import Employee
from apps.hr.period_export import _Sheet
from . import calc, engine, thr, ter_data
from .models import PayProfile, Payslip, PayslipItem, TerRate
from .views import payroll_only

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _period(request):
    p = (request.POST.get("period") or request.GET.get("period") or "").strip()
    return p if calc.valid_period(p) else timezone.localdate().strftime("%Y-%m")


def _date(s, default):
    try: return date.fromisoformat(s)
    except (TypeError, ValueError): return default


@payroll_only
def thr_page(request):
    today = timezone.localdate(); period = _period(request)
    on = _date(request.POST.get("on") or request.GET.get("on"), today)
    rows = thr.compute(on, period); total = sum((r["amount"] for r in rows), 0)
    from . import services
    editable = services.period_open_for_edit(period)
    if request.method == "POST":
        if not editable: messages.error(request, f"Run gaji {period} sudah ditetapkan/dibayar; THR tidak bisa dimasukkan.")
        else:
            done, skipped = thr.push(rows, period, request.user, on)
            log(request, "payroll", "thr_push", None, None, {"period": period, "on": str(on), "n": done}); messages.success(request, f"THR {done} karyawan dimasukkan ke Tambahan & Potongan {period} ({skipped} dilewati). Hitung ulang run bila sudah ada.")
            return redirect(f"{request.path}?period={period}&on={on}")
    return render(request, "payroll/thr.html", {"rows": rows, "total": total, "period": period, "on": on, "editable": editable, "n_ok": sum(1 for r in rows if r["amount"] > 0)})


def _year(request):
    try: y = int(request.GET.get("year") or timezone.localdate().year)
    except ValueError: y = timezone.localdate().year
    return y if 2000 < y < 2100 else timezone.localdate().year


def recap(year):
    """{employee_id: dict} per karyawan dari slip run ditetapkan/dibayar pada tahun `year`."""
    slips = Payslip.objects.filter(run__period__startswith=f"{year}-", run__status__in=("ditetapkan", "dibayar"))
    by = {}
    for s in slips.select_related("run").order_by("run__period"):
        d = by.setdefault(s.employee_id, {"nik": s.nik, "name": s.emp_name, "dept": s.department_name, "months": {}, "slips": []}); d["slips"].append(s.pk); d["months"][s.run.period] = [0, 0, 0]
    pk_to_emp = {pk: eid for eid, d in by.items() for pk in d["slips"]}; per = {s.pk: s.run.period for s in slips.select_related("run")}
    for r in PayslipItem.objects.filter(slip__in=slips).values("slip_id").annotate(
            earn=Sum("amount", filter=~Q(section="potongan") & ~Q(code="tali_asih")), pph=Sum("amount", filter=Q(code="pot_pph21")), iuran=Sum("amount", filter=Q(code="pot_jamsostek"))):
        by[pk_to_emp[r["slip_id"]]]["months"][per[r["slip_id"]]] = [r["earn"] or 0, r["iuran"] or 0, r["pph"] or 0]
    return by


def _recap_xlsx(year):
    from openpyxl import Workbook
    by = recap(year); emps = {e.pk: e for e in Employee.objects.filter(pk__in=list(by))}; profs = engine.current_profiles(date(year, 12, 31), list(by))
    wb = Workbook(); wb.remove(wb.active)
    a = _Sheet(wb, "Rekap Tahunan", [("NIK", "t"), ("Nama", "t"), ("Departemen", "t"), ("NPWP", "t"), ("Status PTKP", "t"), ("Jumlah masa (bulan)", "n"), ("Bruto setahun", "m"), ("Iuran JHT/Jamsostek karyawan", "m"), ("PPh 21 dipotong", "m"), ("Catatan", "t")])
    b = _Sheet(wb, "Per Bulan", [("NIK", "t"), ("Nama", "t"), ("Bulan gaji", "t"), ("Bruto", "m"), ("Iuran karyawan", "m"), ("PPh 21", "m")])
    for eid, d in sorted(by.items(), key=lambda kv: (kv[1]["dept"], kv[1]["name"])):
        e = emps.get(eid); p = profs.get(eid); ms = d["months"]
        a.add(d["nik"], d["name"], d["dept"], (e.npwp if e else "") or "", p.ptkp_status if p else "", len(ms), sum(v[0] for v in ms.values()), sum(v[1] for v in ms.values()), sum(v[2] for v in ms.values()),
              "" if (e and e.npwp) else "NPWP kosong")
        for per, v in sorted(ms.items()): b.add(d["nik"], d["name"], per, *v)
    a.finish(); b.finish()
    n = _Sheet(wb, "Catatan", [("Keterangan", "t")])
    for t in ("Bahan penyusunan Bukti Potong 1721-A1 / A2 dan rekonsiliasi PPh 21. BUKAN formulir resmi: unggah/lapor lewat sistem DJP (Coretax) oleh petugas pajak.",
              "Hanya run berstatus Ditetapkan/Dibayar. Bruto = pendapatan slip tanpa tali asih/pesangon. Premi BPJS yang dibayar perusahaan belum ditambahkan.",
              "NPWP & status PTKP diambil dari data karyawan/profil gaji saat ini; periksa sebelum dilaporkan."): n.add(t)
    n.finish(); out = io.BytesIO(); wb.save(out); return out.getvalue()


@payroll_only
def tax_page(request):
    year = _year(request)
    if request.GET.get("download") == "1":
        data = _recap_xlsx(year); log(request, "payroll", "pph21_recap", None, None, {"year": year})
        r = HttpResponse(data, content_type=XLSX); r["Content-Disposition"] = f'attachment; filename="rekap-pph21-{year}.xlsx"'; return r
    prm = engine.load_params(date(year, 12, 31)); active = bool(prm["pph21_aktif"])
    profs = engine.current_profiles(timezone.localdate()); act = Employee.objects.filter(status="aktif")
    no_npwp = act.filter(Q(npwp="") | Q(npwp__isnull=True)).count(); no_prof = act.exclude(pk__in=list(profs)).count()
    stat = defaultdict(int)
    for p in profs.values(): stat[p.ptkp_status] += 1
    by = recap(year); tot = [sum(sum(v[i] for v in d["months"].values()) for d in by.values()) for i in range(3)]
    tables = [(k, ter_data.TABLES[k][:3], len(ter_data.TABLES[k])) for k in "ABC"]
    return render(request, "payroll/tax.html", {"year": year, "active": active, "no_npwp": no_npwp, "no_prof": no_prof, "stat": [(ter_data.STATUS_LABELS[k], stat[k], ter_data.STATUS[k][0]) for k in ter_data.STATUS if stat[k]],
                                                "n_people": len(by), "tot": tot, "ter_n": TerRate.objects.filter(voided_at__isnull=True).count(), "tables": tables})
