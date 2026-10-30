"""Laporan & kontrol absensi gaya Fingerspot Personnel (putaran 62).

  • Data Scanlog   (HRD)        — log mentah per ketukan, filter tanggal/departemen/mesin/PIN, ekspor dengan kolom persis template Fingerspot (CSV · XLSX · XML).
  • Shift Harian   (HRD, Admin)  — grid karyawan × tanggal (seperti layar "Shift Harian"): shift, terlambat, tidak hadir, libur, cuti.
  • Kontrol Harian (HRD, Admin)  — ringkasan per departemen: tidak berangkat, terlambat, pulang awal, scan 1x, cuti/izin/sakit, lembur; klik angka → daftar.
  • Master Mesin   (HRD)         — nama/lokasi/status mesin, mesin yang belum terdaftar, peringatan mesin tidak mengirim scan.
Semua dibaca dari log mentah & rekap harian yang sudah ada (tanpa tabel baru selain master mesin); data dibatasi per departemen untuk Admin Departemen."""
from datetime import date, datetime, time, timedelta
from xml.sax.saxutils import quoteattr
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles, scope_by_department
from .views import SHIFT_GROUP_FILTERS, apply_shift_filter
from .models import AttendanceDaily as AD, AttendanceDevice, AttendanceImport, AttendanceRaw, DeviceFingerprint, DeviceUser, Department, Employee, Shift

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


HIST_PER_PAGE = 20


@hrd
def scanlog(request):
    """Riwayat Impor Scanlog: tiap kali berkas Fingerspot diunggah → berapa baris, berapa scan baru / duplikat / gagal, rentang tanggal, PIN yang belum dipetakan.
    Scan mentahnya dibuka per impor lewat `scans` (Data Scan)."""
    g = request.GET
    state, q = g.get("state", ""), g.get("q", "").strip()[:60]
    qs = AttendanceImport.objects.select_related("created_by")
    qs = qs.filter(state=state) if state in ("selesai", "draft", "dibatalkan") else qs.exclude(state="dibatalkan")
    if q: qs = qs.filter(original_name__icontains=q)
    f1, f2 = _date(g.get("from")), _date(g.get("to"))                          # tanggal impor (bukan tanggal scan)
    if f1: qs = qs.filter(created_at__date__gte=f1)
    if f2: qs = qs.filter(created_at__date__lte=f2)
    page = Paginator(qs, HIST_PER_PAGE).get_page(g.get("page", 1))
    for i in page: i.when = i.committed_at or i.created_at; i.by = (i.created_by.get_full_name() or i.created_by.get_username()) if i.created_by_id else "-"; i.gagal_pct = round(100 * i.rows_invalid / i.rows_total) if i.rows_total else 0
    k = AttendanceImport.objects.filter(state="selesai").aggregate(n=Count("id"), baru=Sum("taps_new"), dup=Sum("taps_duplicate"), gagal=Sum("rows_invalid"), last=Max("committed_at"))
    for n in ("baru", "dup", "gagal"): k[n] = k[n] or 0
    k["last"] = timezone.localtime(k["last"]) if k["last"] else None
    um = AttendanceRaw.objects.filter(employee__isnull=True).aggregate(scans=Count("id"), pins=Count("pin", distinct=True))
    latest = _latest_scan_date(); stale = (timezone.localdate() - latest).days if latest else None
    qd = g.copy(); qd.pop("page", None)
    return render(request, "attendance/import_history.html", {"page": page, "k": k, "um": um, "latest": latest, "stale": stale, "qs": qd.urlencode(), "n_rows": page.paginator.count,
                  "f": {"q": q, "state": state, "from": g.get("from", ""), "to": g.get("to", "")}, "act": any((q, state, f1, f2)), "states": (("selesai", "Selesai"), ("draft", "Draft (belum diimpor)"), ("dibatalkan", "Dibatalkan"))})


@hrd
def scans(request):
    g = request.GET
    batch = AttendanceImport.objects.filter(pk=int(g["batch"])).first() if g.get("batch", "").isdigit() else None
    gr = g.copy()
    if batch and batch.date_min and not (g.get("from") or g.get("date")): gr["from"], gr["to"] = batch.date_min.isoformat(), batch.date_max.isoformat()   # dari riwayat: langsung tampilkan rentang berkas itu
    d1, d2 = _range(gr, MAX_LOG_DAYS if not g.get("export") else 93, _latest_scan_date())
    a, b = _bounds(d1, d2)
    qs = AttendanceRaw.objects.select_related("employee__department", "employee__position").filter(punched_at__gte=a, punched_at__lt=b)
    if batch: qs = qs.filter(batch=batch)
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
    # Ringkasan rentang (tanpa filter pencarian) + pintasan rentang tanggal, supaya jelas "apa isi data ini" dan "apa langkah berikutnya"
    k = AttendanceRaw.objects.filter(punched_at__gte=a, punched_at__lt=b, **({"batch": batch} if batch else {})).aggregate(total=Count("id"), unmapped=Count("id", filter=Q(employee__isnull=True)), pins=Count("pin", distinct=True), last=Max("punched_at"))
    k["last"] = timezone.localtime(k["last"]) if k["last"] else None
    latest = _latest_scan_date()
    def _rng(x, y): q = g.copy(); [q.pop(n, None) for n in ("page", "export", "format", "date")]; q["from"], q["to"] = x.isoformat(), y.isoformat(); return q.urlencode()
    ranges = [(lbl, _rng(x, y), (x, y) == (d1, d2)) for lbl, x, y in (("Hari scan terakhir", latest, latest), ("7 hari terakhir", latest - timedelta(days=6), latest), ("31 hari terakhir", latest - timedelta(days=30), latest))] if latest else []
    uq = g.copy(); [uq.pop(n, None) for n in ("page", "export", "format")]; uq["unmapped"] = "1"; uq.setdefault("from", d1.isoformat()); uq.setdefault("to", d2.isoformat())
    return render(request, "attendance/scanlog.html", {"batch": batch, "k": k, "ranges": ranges, "unmapped_qs": uq.urlencode(), "n_rows": page.paginator.count,
        "page": page, "qs": _qs_without(g, "export", "format"), "d1": d1, "d2": d2, "f": {"q": q, "dept": dept, "device": dev, "unmapped": g.get("unmapped", ""), "sort": g.get("sort", "")},
        "depts": Department.objects.order_by("name"), "device_opts": [(c, devices[c].name if c in devices else c) for c in codes], "capped": (d2 - d1).days + 1 >= MAX_LOG_DAYS and bool(g.get("to")),
        "act": any((q, dept, dev, g.get("unmapped"))), "limits": EXPORT_LIMIT})


# ============================================================================= Shift Harian (grid karyawan × tanggal)
def _shift_label(a, e=None):
    """Label shift di grid, selaras dengan halaman Karyawan: GS-16 → "GS" (GS-14/GS-12 tetap, karena jam pulangnya berbeda);
    PAGI/SIANG/MALAM → shift karyawan (A7…G7 = pola 2 shift, A7Pack…G7Pack = pola 3 shift/PACK, GS/N)."""
    if not a.shift_id: return ""
    code = a.shift.code or a.shift.name
    if a.shift.is_gs: return "GS" if code == "GS-16" else code
    g = getattr(e, "shift_group", None)
    return g.label if g else code


def _cell(a, e=None):
    """→ (css, teks, tooltip). Teks pendek agar 31 kolom muat; tooltip memuat nama shift & jam scan."""
    if a is None: return "none", "–", "Belum ada rekap"
    tip = " · ".join(x for x in (a.get_status_display(), a.shift.name if a.shift_id else "", f"masuk {_hhmm(a.first_in)}" if a.first_in else "", f"pulang {_hhmm(a.last_out)}" if a.last_out else "",
                                 f"terlambat {a.late_minutes} mnt" if a.late_minutes else "", f"pulang awal {a.early_minutes} mnt" if a.early_minutes else "") if x)
    code = _shift_label(a, e)
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
    emp = scope_by_department(request.user, Employee.objects.select_related("department", "shift_group"))
    emp = emp.filter(Exists(in_range.filter(employee=OuterRef("pk"))))
    q, dept, st, shift = g.get("q", "").strip()[:60], g.get("dept", ""), g.get("status", ""), g.get("shift", "")
    if q: emp = emp.filter(Q(nik__icontains=q) | Q(name__icontains=q))
    if dept.isdigit() and not is_admin: emp = emp.filter(department_id=int(dept))
    cond = Q()
    if st in STATUS_FILTERS: cond &= STATUS_FILTERS[st]
    emp = apply_shift_filter(emp, shift)      # GS / GS/N / A7…G7 / A7Pack…G7Pack: satu kelompok per pilihan (id shift lama tetap diterima)
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
            a = cells.get((e.pk, d)); css, txt, tip = _cell(a, e); row.append((css, txt, tip))
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
        "shift_opts": SHIFT_GROUP_FILTERS, "act": any((q, dept, st, shift)),
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
        data = [[r["department__name"] or "-", r["kerja"], r["hadir"], r["pct"] if r["pct"] is not None else "", r["telat"], r["early"], r["absent"], r["alfa"], r["leave"], r["incomplete"], r["ot"]] for r in rows]
        log(request, "hr", "attendance_control_export", None, None, {"from": d1.isoformat(), "to": d2.isoformat()})
        return tabular.export_response(f"kontrol-absensi-{d1}-{d2}", head, data, request, sheet="Kontrol", num_cols=tuple(range(1, 11)))
    ctx = {"rows": rows, "tot": tot, "d1": d1, "d2": d2, "qdate": qdate, "is_admin": is_admin, "qs": _qs_without(g, "export", "format")}
    if not is_admin:
        _, unknown, idle = device_status(); ctx.update(idle=idle, unknown_dev=unknown, last_scan=_latest_scan_date())
    return render(request, "attendance/control.html", ctx)


# ============================================================================= Master Mesin
def _device_action(request):
    from . import attendance_sync as sy
    p = request.POST; act = p.get("action")
    try:
        if act == "import_list":
            raw = (request.FILES["file"].read() if request.FILES.get("file") else p.get("paste", "").encode()); name = request.FILES["file"].name if request.FILES.get("file") else "paste.txt"
            made, upd, warn = sy.import_machine_list(raw, name, p.get("protocol") if p.get("protocol") in dict(AttendanceDevice.PROTOCOLS) else "zk", int(p["port"]) if p.get("port", "").isdigit() else 4370)
            log(request, "hr", "attendance_device_import", None, None, {"created": made, "updated": upd})
            messages.success(request, f"Daftar mesin diimpor: {made} baru, {upd} diperbarui." + (f" {len(warn)} perlu diperiksa: " + "; ".join(warn[:5]) if warn else ""))
            return redirect("att_devices")
        ids = [int(i) for i in p.getlist("ids") if i.isdigit()]
        devs = AttendanceDevice.objects.filter(pk__in=ids, active=True)
        if not devs: messages.error(request, "Centang minimal satu mesin aktif."); return redirect("att_devices")
        if act == "test":
            res = sy.probe_many(devs)
        elif act in ("users_down", "users_up"):
            res = [sy.run_action(d, act, pins=_pins(p.get("pins")), fingerprints=p.get("fingerprints", "1") == "1") for d in devs]
            log(request, "hr", "attendance_device_" + act, None, None, {"machines": len(res), "users": sum(r.get("users", 0) for r in res)})
        elif act == "sync":
            d1 = date.fromisoformat(p["from"]) if p.get("from") else None; d2 = date.fromisoformat(p["to"]) if p.get("to") else None
            mode = p.get("mode") if p.get("mode") in ("latest", "all", "range") else "latest"
            if mode == "range" and not (d1 or d2): raise ValueError("Isi tanggal Dari/Sampai untuk mode rentang.")
            res = sy.sync_many(devs, d1, d2, mode=mode); log(request, "hr", "attendance_device_sync", None, None, {"machines": len(res), "new": sum(r.get("new", 0) for r in res)})
        else: return redirect("att_devices")
    except (ValueError, KeyError) as e: messages.error(request, f"Gagal: {e}"); return redirect("att_devices")
    names = {d.code: d.name for d in devs}; ok = sum(r["ok"] for r in res)
    (messages.success if ok == len(res) else messages.warning)(request, f"{ {'test': 'Tes koneksi', 'users_down': 'Download user', 'users_up': 'Upload user'}.get(act, 'Download scanlog') }: {ok} dari {len(res)} mesin berhasil.")
    for r in res: (messages.info if r["ok"] else messages.error)(request, f"{names.get(r['device'], r['device'])}: {r['note']}")
    return redirect("att_devices")


def _pins(raw): return [x for x in (raw or "").replace(",", " ").split() if x][:2000]


@hrd
@require_POST
def device_job(request):
    """Satu aksi untuk SATU mesin → JSON. Popup progres memanggilnya per mesin (paralel terbatas), jadi tiap permintaan pendek dan kemajuan nyata per mesin.
    Respons tidak pernah memuat template sidik jari."""
    from . import attendance_sync as sy
    p = request.POST; act = p.get("action")
    if act not in sy.ACTIONS: return JsonResponse({"ok": False, "note": "Aksi tidak dikenal."}, status=400)
    dev = AttendanceDevice.objects.filter(pk=int(p["id"]), active=True).first() if p.get("id", "").isdigit() else None
    if not dev: return JsonResponse({"ok": False, "note": "Mesin tidak ditemukan atau nonaktif."}, status=404)
    try:
        mode = p.get("mode") if p.get("mode") in ("latest", "all", "range") else "latest"
        d1 = date.fromisoformat(p["from"]) if p.get("from") else None; d2 = date.fromisoformat(p["to"]) if p.get("to") else None
        if act == "sync" and mode == "range" and not (d1 or d2): raise ValueError("Isi tanggal Dari/Sampai untuk mode rentang.")
    except ValueError as e: return JsonResponse({"ok": False, "note": f"Gagal: {e}"}, status=400)
    job = p.get("job", "")[:40] if p.get("job", "").replace("-", "").isalnum() else None
    res = sy.run_action(dev, act, mode=mode, d1=d1, d2=d2, pins=_pins(p.get("pins")), fingerprints=p.get("fingerprints", "1") == "1", job=job)
    log(request, "hr", "attendance_device_" + act, dev, None, {k: res.get(k) for k in ("ok", "new", "users", "fingers", "failed", "skipped") if k in res})
    return JsonResponse({"device": dev.code, "name": dev.name, "ok": bool(res.get("ok")), "note": res.get("note", ""),
                         **{k: res[k] for k in ("new", "dup", "fetched", "users", "updated", "fingers", "failed", "skipped") if k in res}})


@hrd
def device_progress(request):
    """Kemajuan 0-100% satu pekerjaan/mesin (dipoll popup tiap detik). Catatan: kemajuan disimpan di cache Django — bila server memakai banyak proses, set REDIS_URL agar cache dipakai bersama."""
    from . import attendance_sync as sy
    job = request.GET.get("job", "")[:40]; dev = request.GET.get("id", "")
    if not job.replace("-", "").isalnum() or not dev.isdigit(): return JsonResponse({"pct": 0, "note": ""}, status=400)
    return JsonResponse(sy.get_progress(job, int(dev)))


@hrd
def devices(request):
    if request.method == "POST" and request.POST.get("action"):
        return _device_action(request)
    if request.method == "POST":
        p = request.POST; code = p.get("code", "").strip()[:40]; name = p.get("name", "").strip()[:80]
        if not code or not name: messages.error(request, "Kode mesin dan nama wajib diisi.")
        else:
            d, created = AttendanceDevice.objects.get_or_create(code=code, defaults={"name": name})
            d.name, d.location, d.serial, d.note = name, p.get("location", "").strip()[:100], p.get("serial", "").strip()[:40], p.get("note", "").strip()[:200]
            if "edit" in p or created:
                d.host, d.machine_no, d.screen = p.get("host", "").strip()[:100], p.get("machine_no", "").strip()[:10], p.get("screen", "").strip()[:10]
                if p.get("connection") in dict(AttendanceDevice.CONNECTIONS): d.connection = p["connection"]
                if p.get("protocol") in dict(AttendanceDevice.PROTOCOLS): d.protocol = p["protocol"]
                if p.get("port", "").isdigit() and 0 < int(p["port"]) < 65536: d.port = int(p["port"])
                if p.get("comm_key", "").strip(): d.comm_key = p["comm_key"].strip()[:100]       # kosong = tidak diubah
                if "edit" in p: d.sync_enabled = p.get("sync_enabled") == "1"
            if created: d.active = True
            elif "edit" in p: d.active = p.get("active") == "1"      # kotak centang "Aktif" hanya ada di formulir ubah
            d.save()
            log(request, "hr", "attendance_device_create" if created else "attendance_device_update", d, None, {"code": d.code, "name": d.name, "active": d.active})
            messages.success(request, f"Mesin {d.name} {'ditambahkan' if created else 'diperbarui'}.")
        return redirect("att_devices")
    rows, unknown, idle = device_status()
    return render(request, "attendance/devices.html", {"rows": rows, "unknown": unknown, "idle": idle, "idle_days": DEVICE_IDLE_DAYS, "protocols": AttendanceDevice.PROTOCOLS,
                                                        "connections": AttendanceDevice.CONNECTIONS, "default_days": getattr(settings, "ATTENDANCE_SYNC_DAYS", 3),
                                                        "stored_users": DeviceUser.objects.count(), "stored_fingers": DeviceFingerprint.objects.count()})
