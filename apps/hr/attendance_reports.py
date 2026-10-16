"""Laporan & kontrol absensi gaya Fingerspot Personnel (putaran 62).

  • Data Scanlog   (HRD)        — log mentah per ketukan, filter tanggal/departemen/mesin/PIN, ekspor dengan kolom persis template Fingerspot (CSV · XLSX · XML).
  • Shift Harian   (HRD, Admin)  — grid karyawan × tanggal (seperti layar "Shift Harian"): shift, terlambat, tidak hadir, libur, cuti.
  • Kontrol Harian (HRD, Admin)  — ringkasan per departemen: tidak berangkat, terlambat, pulang awal, scan 1x, cuti/izin/sakit, lembur; klik angka → daftar.
  • Master Mesin   (HRD)         — nama/lokasi/status mesin, mesin yang belum terdaftar, peringatan mesin tidak mengirim scan.
Semua dibaca dari log mentah & rekap harian yang sudah ada (tanpa tabel baru selain master mesin); data dibatasi per departemen untuk Admin Departemen."""
from datetime import date, datetime, time, timedelta
from xml.sax.saxutils import quoteattr
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department
from .models import AttendanceDaily as AD, AttendanceDevice, AttendanceRaw, Department, Employee, Shift

both = lambda fn: login_required(require_roles(Role.HRD, Role.DEPT_ADMIN)(fn))   # noqa: E731
hrd = lambda fn: login_required(require_roles(Role.HRD)(fn))                       # noqa: E731

OFF_DAYS = ("libur", "libur_nasional")
ABSENT = ("alfa", "tanpa_scan")                      # "tidak berangkat": alfa terkonfirmasi + hari kerja tanpa scan (belum divalidasi)
LEAVE = ("cuti", "cuti_hamil", "izin", "sakit")
# Satu sumber untuk filter status di Absensi harian, grid Shift Harian dan kontrol (kunci → Q pada AttendanceDaily)
STATUS_FILTERS = {
    "absent": Q(status__in=ABSENT), "late": Q(late_minutes__gt=0), "early": Q(early_minutes__gt=0), "incomplete": Q(incomplete=True),
    "leave": Q(status__in=LEAVE), "ot": Q(overtime_approved_minutes__gt=0), "review": Q(status="tanpa_scan") | Q(incomplete=True) | ~Q(conflict=""),
}
STATUS_LABELS = [("absent", "Tidak berangkat (alfa / tanpa scan)"), ("late", "Terlambat"), ("early", "Pulang awal"), ("incomplete", "Scan 1x (tidak lengkap)"),
                 ("leave", "Cuti / izin / sakit"), ("ot", "Ada lembur disetujui"), ("review", "Perlu ditinjau")]
MAX_GRID_DAYS, MAX_LOG_DAYS, PER_PAGE_LOG, PER_PAGE_GRID = 31, 31, 100, 50
EXPORT_LIMIT = {"csv": 200_000, "xml": 100_000, "xlsx": 50_000}   # baris per ekspor; diukur: XLSX 200 rb baris ±36 dtk (melebihi batas waktu web), jadi ditolak, TIDAK dipotong diam-diam
DEVICE_IDLE_DAYS = 2   # mesin aktif tanpa scan sepanjang ini → peringatan


def _date(s, default=None):
    try: return date.fromisoformat((s or "").strip()[:10])
    except ValueError: return default


def _hhmm(dt): return timezone.localtime(dt).strftime("%H:%M") if dt else ""


def _bounds(d1, d2):
    a = timezone.make_aware(datetime.combine(d1, time.min)); return a, a + timedelta(days=(d2 - d1).days + 1)


def _range(g, days_max, default_last=None):
    """(d1, d2) dari ?from=&to= (satu tanggal bila `to` kosong), dipotong ke `days_max` hari; bawaan = tanggal terakhir yang punya data."""
    last = default_last or timezone.localdate() - timedelta(days=1)
    d1 = _date(g.get("from")) or _date(g.get("date")) or last; d2 = _date(g.get("to")) or d1
    if d2 < d1: d1, d2 = d2, d1
    if (d2 - d1).days + 1 > days_max: d2 = d1 + timedelta(days=days_max - 1)
    return d1, d2


def _latest_scan_date():
    last = AttendanceRaw.objects.order_by("-punched_at").values_list("punched_at", flat=True).first()
    return timezone.localtime(last).date() if last else None


def _qs_without(g, *drop):
    q = g.copy()
    for k in ("page", *drop): q.pop(k, None)
    return q.urlencode()


# ============================================================================= Data Scanlog
SCANLOG_HEAD = ["Tanggal scan", "Tanggal", "Jam", "PIN", "NIP", "Nama", "Jabatan", "Departemen", "Kantor", "Verifikasi", "I/O", "Workcode", "SN", "Mesin"]
XML_NAMES = ["scan_date", "tgl", "jam", "pin", "pegawai_nip", "pegawai_nama", "pembagian1_nama", "pembagian2_nama", "pembagian3_nama", "verifymode", "inoutmode", "work_code", "sn", "device_name"]


def _scan_rows(qs, serials, limit):
    """Baris ekspor persis kolom Fingerspot. 'Mesin' = kode mesin apa adanya (bukan nama master) agar berkas yang diekspor lalu diimpor lagi TIDAK menggandakan scan
    (kunci idempotensi memakai kode mesin). Verifikasi / I/O / Workcode tidak disimpan oleh HRIS → kosong."""
    out = []
    for r in qs[:limit]:
        t = timezone.localtime(r.punched_at); e = r.employee
        out.append([t.strftime("%d-%m-%Y %H:%M:%S"), t.strftime("%d-%m-%Y"), t.strftime("%H:%M:%S"), r.pin, e.nik if e else "", e.name if e else "",
                    e.position.name if e and e.position else "", e.department.name if e else "", "", "", "", "", serials.get(r.device_id, ""), r.device_id])
    return out


def _fingerspot_xml(rows):
    """XML 'GRID' sama dengan ekspor Fingerspot (dapat diimpor kembali di halaman Impor)."""
    names = ["dbg_scanlog" + n for n in XML_NAMES]
    cols = "".join(f'    <COLUMN Name="{n}" Caption={quoteattr(c)} Index="{i}" BandIndex="0" RowIndex="0" ColIndex="{i}"></COLUMN>\n' for i, (n, c) in enumerate(zip(names, SCANLOG_HEAD)))
    body = []
    for r in rows:
        d, tm = r[1].split("-"), r[2]
        iso = f"{d[2]}-{d[1]}-{d[0]}"
        vals = [f"{iso}T{tm}", iso, tm, *r[3:]]
        body.append("    <ROW " + " ".join(f"{n}={quoteattr(str(v))}" for n, v in zip(names, vals)) + "></ROW>\n")
    xml = ('<?xml version="1.0" encoding="UTF-8" ?>\n<GRID ExportVersion="1.0">\n  <BANDS>\n    <BAND Index="0" Caption=""></BAND>\n  </BANDS>\n  <COLUMNS>\n'
           + cols + "  </COLUMNS>\n  <ROWS>\n" + "".join(body) + "  </ROWS>\n</GRID>\n")
    return xml.encode("utf-8")


@hrd
def scanlog(request):
    g = request.GET
    d1, d2 = _range(g, MAX_LOG_DAYS if not g.get("export") else 93, _latest_scan_date())
    a, b = _bounds(d1, d2)
    qs = AttendanceRaw.objects.select_related("employee__department", "employee__position").filter(punched_at__gte=a, punched_at__lt=b)
    q, dept, dev = g.get("q", "").strip()[:60], g.get("dept", ""), g.get("device", "")
    if q: qs = qs.filter(Q(pin__icontains=q) | Q(employee__nik__icontains=q) | Q(employee__name__icontains=q))
    if dept.isdigit(): qs = qs.filter(employee__department_id=int(dept))
    if dev: qs = qs.filter(device_id=dev[:40])
    if g.get("unmapped") == "1": qs = qs.filter(employee__isnull=True)
    qs = qs.order_by("punched_at" if g.get("sort") == "asc" else "-punched_at", "-id")
    devices = {d.code: d for d in AttendanceDevice.objects.all()}
    if g.get("export"):
        kind = g.get("format") if g.get("format") in ("xlsx", "xml") else "csv"
        n = qs.count()
        if n > EXPORT_LIMIT[kind]:
            messages.error(request, f"Hasil {n:,} baris melebihi batas ekspor {kind.upper()} ({EXPORT_LIMIT[kind]:,}). Persempit rentang tanggal/filter, atau pilih CSV (batas {EXPORT_LIMIT['csv']:,}).".replace(",", "."))
            return redirect(f"{request.path}?{_qs_without(g, 'export', 'format')}")
        rows = _scan_rows(qs, {c: d.serial for c, d in devices.items()}, EXPORT_LIMIT[kind])
        log(request, "hr", "attendance_scanlog_export", None, None, {"rows": len(rows), "from": d1.isoformat(), "to": d2.isoformat(), "format": kind})
        name = f"scanlog-{d1}-{d2}"
        if kind == "xml":
            r = HttpResponse(_fingerspot_xml(rows), content_type="application/xml; charset=utf-8"); r["Content-Disposition"] = f'attachment; filename="{name}.xml"'; return r
        return tabular.export_response(name, SCANLOG_HEAD, rows, request, sheet="Scanlog", num_cols=())
    page = Paginator(qs, PER_PAGE_LOG).get_page(g.get("page", 1))
    for r in page:
        t = timezone.localtime(r.punched_at); r.d, r.t = t.strftime("%d-%m-%Y"), t.strftime("%H:%M:%S"); dv = devices.get(r.device_id); r.dev = dv.name if dv else r.device_id
    seen = AttendanceRaw.objects.filter(punched_at__gte=a, punched_at__lt=b).values_list("device_id", flat=True).distinct()
    codes = sorted(set(seen) | set(devices))
    return render(request, "attendance/scanlog.html", {
        "page": page, "qs": _qs_without(g, "export", "format"), "d1": d1, "d2": d2, "f": {"q": q, "dept": dept, "device": dev, "unmapped": g.get("unmapped", ""), "sort": g.get("sort", "")},
        "depts": Department.objects.order_by("name"), "device_opts": [(c, devices[c].name if c in devices else c) for c in codes], "capped": (d2 - d1).days + 1 >= MAX_LOG_DAYS and bool(g.get("to")),
        "act": any((q, dept, dev, g.get("unmapped"))), "limits": EXPORT_LIMIT})


# ============================================================================= Shift Harian (grid karyawan × tanggal)
def _cell(a):
    """→ (css, teks, tooltip). Teks pendek agar 31 kolom muat; tooltip memuat jam scan."""
    if a is None: return "none", "–", "Belum ada rekap"
    tip = " · ".join(x for x in (a.get_status_display(), f"masuk {_hhmm(a.first_in)}" if a.first_in else "", f"pulang {_hhmm(a.last_out)}" if a.last_out else "",
                                 f"terlambat {a.late_minutes} mnt" if a.late_minutes else "", f"pulang awal {a.early_minutes} mnt" if a.early_minutes else "") if x)
    code = (a.shift.code or a.shift.name) if a.shift_id else ""
    s = a.status
    if s in ABSENT: return "absent", "Tidak Hadir" if s == "alfa" else "Tanpa scan", tip
    if s in OFF_DAYS: return "off", "Libur" if s == "libur" else "Libur Nas.", tip
    if s in LEAVE: return "leave", a.get_status_display(), tip
    if a.late_minutes: return "late", f"{code or 'H'} +{a.late_minutes}m", tip
    if a.incomplete: return "warn", f"{code or 'H'} 1x", tip
    return "ok", code or "Hadir", tip


@both
def shift_grid(request):
    g, is_admin = request.GET, request.user.role == Role.DEPT_ADMIN
    d1, d2 = _range(g, MAX_GRID_DAYS, AD.objects.order_by("-date").values_list("date", flat=True).first())
    days = [d1 + timedelta(days=i) for i in range((d2 - d1).days + 1)]
    in_range = AD.objects.filter(date__gte=d1, date__lte=d2)
    emp = scope_by_department(request.user, Employee.objects.select_related("department"))
    emp = emp.filter(Exists(in_range.filter(employee=OuterRef("pk"))))
    q, dept, st, shift = g.get("q", "").strip()[:60], g.get("dept", ""), g.get("status", ""), g.get("shift", "")
    if q: emp = emp.filter(Q(nik__icontains=q) | Q(name__icontains=q))
    if dept.isdigit() and not is_admin: emp = emp.filter(department_id=int(dept))
    cond = Q()
    if st in STATUS_FILTERS: cond &= STATUS_FILTERS[st]
    if shift.isdigit(): cond &= Q(shift_id=int(shift))
    if cond: emp = emp.filter(Exists(in_range.filter(cond, employee=OuterRef("pk"))))
    emp = emp.order_by("department__name", "name")
    if g.get("export"):
        page_rows = list(emp[:5000])
    else:
        page = Paginator(emp, PER_PAGE_GRID).get_page(g.get("page", 1)); page_rows = list(page)
    ids = [e.pk for e in page_rows]
    cells = {}
    for a in AD.objects.filter(employee_id__in=ids, date__gte=d1, date__lte=d2).select_related("shift"): cells[(a.employee_id, a.date)] = a
    grid, summary = [], {}
    for e in page_rows:
        row, hadir, telat, absen = [], 0, 0, 0
        for d in days:
            a = cells.get((e.pk, d)); css, txt, tip = _cell(a); row.append((css, txt, tip))
            if a: hadir += a.status == "hadir"; telat += a.late_minutes > 0; absen += a.status in ABSENT
        grid.append({"e": e, "cells": row, "hadir": hadir, "telat": telat, "absen": absen})
    if g.get("export"):
        head = ["NIK", "Nama", "Departemen", *[d.strftime("%d-%m") for d in days], "Hadir", "Terlambat (hari)", "Tidak hadir (hari)"]
        data = [[r["e"].nik, r["e"].name, r["e"].department.name, *[c[1] if c[0] != "none" else "" for c in r["cells"]], r["hadir"], r["telat"], r["absen"]] for r in grid]
        log(request, "hr", "attendance_shift_grid_export", None, None, {"rows": len(data), "from": d1.isoformat(), "to": d2.isoformat()})
        return tabular.export_response(f"shift-harian-{d1}-{d2}", head, data, request, sheet="Shift harian", num_cols=tuple(range(3 + len(days), 6 + len(days))))
    return render(request, "attendance/shift_grid.html", {
        "page": page, "grid": grid, "days": days, "d1": d1, "d2": d2, "qs": _qs_without(g, "export", "format"), "is_admin": is_admin,
        "f": {"q": q, "dept": dept, "status": st, "shift": shift}, "status_opts": STATUS_LABELS, "depts": Department.objects.order_by("name"),
        "shifts": Shift.objects.filter(active=True).order_by("start"), "act": any((q, dept, st, shift)),
        "capped": (d2 - d1).days + 1 >= MAX_GRID_DAYS and bool(g.get("to"))})


# ============================================================================= Kontrol harian (ringkasan per departemen)
def device_status(window_days=30):
    """Master mesin + statistik 30 hari terakhir (memakai indeks punched_at) + mesin yang muncul di log tetapi belum terdaftar.
    → (daftar baris, kode belum terdaftar, daftar mesin aktif yang diam)."""
    now = timezone.now()
    stat = {r["device_id"]: r for r in AttendanceRaw.objects.filter(punched_at__gte=now - timedelta(days=window_days)).values("device_id").annotate(n=Count("id"), last=Max("punched_at"))}
    rows, idle = [], []
    for d in AttendanceDevice.objects.all():
        s = stat.get(d.code); days = (now - s["last"]).days if s else None
        row = {"d": d, "n": s["n"] if s else 0, "last": s["last"] if s else None, "idle_days": days, "idle": d.active and (days is None or days >= DEVICE_IDLE_DAYS)}
        rows.append(row)
        if row["idle"]: idle.append(row)
    known = {d.code for d in AttendanceDevice.objects.all()}
    unknown = [{"code": c, "n": s["n"], "last": s["last"]} for c, s in sorted(stat.items()) if c not in known]
    return rows, unknown, idle


@both
def control(request):
    g, is_admin = request.GET, request.user.role == Role.DEPT_ADMIN
    d1, d2 = _range(g, 93, AD.objects.order_by("-date").values_list("date", flat=True).first())
    base = scope_by_department(request.user, AD.objects.filter(date__gte=d1, date__lte=d2))
    agg = {n: Count("id", filter=f) for n, f in (
        ("total", Q()), ("kerja", ~Q(status__in=OFF_DAYS)), ("hadir", Q(status="hadir")), ("telat", STATUS_FILTERS["late"]), ("early", STATUS_FILTERS["early"]),
        ("absent", STATUS_FILTERS["absent"]), ("alfa", Q(status="alfa")), ("leave", STATUS_FILTERS["leave"]), ("incomplete", STATUS_FILTERS["incomplete"]), ("ot", STATUS_FILTERS["ot"]))}
    rows = list(base.values("department_id", "department__name").annotate(**agg).order_by("department__name"))
    tot = {k: sum(r[k] for r in rows) for k in agg}
    for r in rows + [tot]:
        r["pct"] = round(100 * r["hadir"] / r["kerja"]) if r["kerja"] else None
        r["links"] = [(key, r[col]) for col, key in (("absent", "absent"), ("telat", "late"), ("early", "early"), ("incomplete", "incomplete"), ("leave", "leave"), ("ot", "ot"))]
    qdate = f"from={d1}&to={d2}"
    if g.get("export"):
        head = ["departemen", "baris_hari_kerja", "hadir", "persen_hadir", "terlambat", "pulang_awal", "tidak_berangkat", "alfa", "cuti_izin_sakit", "scan_1x", "lembur_disetujui"]
        data = [[r["department__name"] or "—", r["kerja"], r["hadir"], r["pct"] if r["pct"] is not None else "", r["telat"], r["early"], r["absent"], r["alfa"], r["leave"], r["incomplete"], r["ot"]] for r in rows]
        log(request, "hr", "attendance_control_export", None, None, {"from": d1.isoformat(), "to": d2.isoformat()})
        return tabular.export_response(f"kontrol-absensi-{d1}-{d2}", head, data, request, sheet="Kontrol", num_cols=tuple(range(1, 11)))
    ctx = {"rows": rows, "tot": tot, "d1": d1, "d2": d2, "qdate": qdate, "is_admin": is_admin, "qs": _qs_without(g, "export", "format")}
    if not is_admin:
        _, unknown, idle = device_status(); ctx.update(idle=idle, unknown_dev=unknown, last_scan=_latest_scan_date())
    return render(request, "attendance/control.html", ctx)


# ============================================================================= Master Mesin
@hrd
def devices(request):
    if request.method == "POST":
        p = request.POST; code = p.get("code", "").strip()[:40]; name = p.get("name", "").strip()[:80]
        if not code or not name: messages.error(request, "Kode mesin dan nama wajib diisi.")
        else:
            d, created = AttendanceDevice.objects.get_or_create(code=code, defaults={"name": name})
            d.name, d.location, d.serial, d.note = name, p.get("location", "").strip()[:100], p.get("serial", "").strip()[:40], p.get("note", "").strip()[:200]
            if created: d.active = True
            elif "edit" in p: d.active = p.get("active") == "1"      # kotak centang "Aktif" hanya ada di formulir ubah
            d.save()
            log(request, "hr", "attendance_device_create" if created else "attendance_device_update", d, None, {"code": d.code, "name": d.name, "active": d.active})
            messages.success(request, f"Mesin {d.name} {'ditambahkan' if created else 'diperbarui'}.")
        return redirect("att_devices")
    rows, unknown, idle = device_status()
    return render(request, "attendance/devices.html", {"rows": rows, "unknown": unknown, "idle": idle, "idle_days": DEVICE_IDLE_DAYS})
