"""Halaman Absensi (Tahap 6, putaran 48). HRD/Superadmin: semuanya. Admin Departemen: rekap harian/bulanan/per karyawan departemennya (baca saja, tanpa log scan mentah,
tanpa impor/PIN/hari libur). Poli: 403. Semua aturan hitung ada di attendance_calc; semua aturan impor di attendance_import."""
import re
from datetime import date, datetime, time, timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Q
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core import tabular
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from . import attendance_calc as calc, attendance_check as checks, attendance_import as imp
from .attendance_reports import STATUS_FILTERS, STATUS_LABELS
from .models import AttendanceCheck, AttendanceDaily as AD, AttendanceImport, AttendancePin, AttendanceRaw, Department, Employee, Holiday, Shift

both = lambda fn: login_required(require_roles(Role.HRD, Role.DEPT_ADMIN)(fn))
hrd = lambda fn: login_required(require_roles(Role.HRD)(fn))
PILL = {"hadir": "approved", "libur": "nonaktif", "libur_nasional": "nonaktif", "cuti": "pending", "cuti_hamil": "pending", "izin": "pending", "sakit": "pending", "alfa": "rejected", "tanpa_scan": "rejected"}
MONTHS = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def _date(s, default=None):
    try: return date.fromisoformat((s or "").strip())
    except ValueError: return default


def _month(s):
    m = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", s or "")
    y, mo = (int(m[1]), int(m[2])) if m else (timezone.localdate().year, timezone.localdate().month)
    a = date(y, mo, 1); b = (date(y + (mo == 12), mo % 12 + 1, 1)) - timedelta(days=1)
    return a, b, f"{y:04d}-{mo:02d}", f"{MONTHS[mo - 1]} {y}"


def _hhmm(dt): return timezone.localtime(dt).strftime("%H:%M") if dt else ""


def _freshness():
    """(tanggal scan terakhir, hari sejak itu) — untuk peringatan data basi."""
    last = AttendanceRaw.objects.order_by("-punched_at").values_list("punched_at", flat=True).first()
    if not last: return None, None
    d = timezone.localtime(last).date(); return d, (timezone.localdate() - d).days


# ---------------------------------------------------------------- rekap harian
@both
def daily_list(request):
    g, is_admin = request.GET, request.user.role == Role.DEPT_ADMIN
    last = calc.latest_date(); d1 = _date(g.get("from")) or _date(g.get("date")) or last or timezone.localdate() - timedelta(days=1); d2 = _date(g.get("to")) or d1
    if d2 < d1: d1, d2 = d2, d1
    if (d2 - d1).days > 92: d2 = d1 + timedelta(days=92)
    qs = scope_by_department(request.user, AD.objects.select_related("employee", "department", "shift").filter(date__gte=d1, date__lte=d2)).order_by("-date", "department__name", "employee__name")
    status = g.get("status", "")
    if status in STATUS_FILTERS: qs = qs.filter(STATUS_FILTERS[status])     # absent · late · early · incomplete · leave · ot · review (satu sumber dengan Kontrol Harian & Shift Harian)
    elif status in dict(AD.STATUSES): qs = qs.filter(status=status)
    if g.get("shift", "").isdigit(): qs = qs.filter(shift_id=int(g["shift"]))
    if g.get("dept", "").isdigit() and not is_admin: qs = qs.filter(department_id=int(g["dept"]))
    if g.get("q", "").strip(): q = g["q"].strip()[:60]; qs = qs.filter(Q(employee__nik__icontains=q) | Q(employee__name__icontains=q))
    if g.get("export"):
        rows = [[a.employee.nik, a.employee.name, a.department.name if a.department else "", a.date.isoformat(), a.get_status_display(), a.shift.code or a.shift.name if a.shift else "", _hhmm(a.first_in), _hhmm(a.last_out),
                 a.scan_count, a.late_minutes, a.early_minutes, a.overtime_minutes, a.standby_minutes, "ya" if a.incomplete else "", a.conflict, a.notes] for a in qs[:50000]]
        log(request, "hr", "attendance_daily_export", None, None, {"rows": len(rows), "from": d1.isoformat(), "to": d2.isoformat()})
        return tabular.export_response(f"absensi-harian-{d1}-{d2}", ["nik", "nama", "departemen", "tanggal", "status", "shift", "masuk", "pulang", "scan", "terlambat_menit", "pulang_cepat_menit",
                                       "lembur_menit", "standby_menit", "tidak_lengkap", "konflik", "catatan"], rows, request, sheet="Absensi harian", num_cols=(8, 9, 10, 11, 12))
    page = Paginator(qs, 50).get_page(g.get("page", 1))
    for a in page: a.pill = PILL.get(a.status, "pending"); a.fi, a.lo = _hhmm(a.first_in), _hhmm(a.last_out)
    base = scope_by_department(request.user, AD.objects.filter(date__gte=d1, date__lte=d2))
    k = base.aggregate(**{n: Count("id", filter=f) for n, f in
                          (("total", Q()), ("hadir", Q(status="hadir")), ("telat", Q(late_minutes__gt=0)), ("review", Q(status="tanpa_scan") | Q(incomplete=True) | ~Q(conflict="")), ("alfa", Q(status="alfa")), ("absent", STATUS_FILTERS["absent"]))})
    qd = g.copy(); qd.pop("page", None); qd.pop("export", None)
    ld, stale = _freshness()
    return render(request, "attendance/daily.html", {"page": page, "qs": qd.urlencode(), "k": k, "d1": d1, "d2": d2, "f": {"status": status, "q": g.get("q", ""), "dept": g.get("dept", ""), "shift": g.get("shift", "")}, "shifts": Shift.objects.filter(active=True).order_by("start"), "status_opts": STATUS_LABELS,
                  "statuses": AD.STATUSES, "depts": Department.objects.order_by("name"), "is_admin": is_admin, "last_scan": ld, "stale": stale, "act": any(g.get(x) for x in ("status", "q", "dept", "shift"))})


# ---------------------------------------------------------------- rekap bulanan
@both
def monthly(request):
    a, b, mval, mlabel = _month(request.GET.get("month")); is_admin = request.user.role == Role.DEPT_ADMIN
    qs = scope_by_department(request.user, AD.objects.filter(date__gte=a, date__lte=b))
    dept = request.GET.get("dept", "")
    if dept.isdigit() and not is_admin: qs = qs.filter(department_id=int(dept))
    rows = calc.summary(qs)
    fmt = request.GET.get("format")
    if request.GET.get("export"):
        head = ["nik", "nama", "departemen", "hari_kerja", "hadir", "terlambat_kali", "terlambat_menit", "pulang_cepat_kali", "pulang_cepat_menit", "alfa", "izin", "sakit", "cuti", "cuti_hamil",
                "tanpa_scan_belum_validasi", "lembur_menit", "standby_menit"]
        data = [[r["employee__nik"], r["employee__name"], r["department__name"] or "", r["hari"], r["hadir"], r["terlambat"], r["terlambat_menit"], r["pulang_cepat"], r["pulang_cepat_menit"], r["alfa"], r["izin"],
                 r["sakit"], r["cuti"], r["cuti_hamil"], r["tanpa_scan"], r["lembur"], r["standby"]] for r in rows]
        log(request, "hr", "attendance_monthly_export", None, None, {"month": mval, "rows": len(data)})
        return tabular.export_response(f"rekap-absensi-{mval}", head, data, request, sheet=f"Absensi {mval}", num_cols=tuple(range(3, 17)))
    tot = {k: sum(r[k] for r in rows) for k in ("hadir", "terlambat", "alfa", "tanpa_scan", "izin", "sakit", "cuti", "lembur", "perlu_tinjau")}
    page = Paginator(rows, 100).get_page(request.GET.get("page", 1))
    qd = request.GET.copy(); qd.pop("page", None); qd.pop("export", None)
    return render(request, "attendance/monthly.html", {"page": page, "rows": rows, "tot": tot, "month": mval, "mlabel": mlabel, "is_admin": is_admin, "dept": dept, "depts": Department.objects.order_by("name"), "qs": qd.urlencode()})


# ---------------------------------------------------------------- per karyawan (kalender bulan)
@both
def employee_month(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "shift"), pk)
    a, b, mval, mlabel = _month(request.GET.get("month")); full = request.user.role != Role.DEPT_ADMIN
    days = list(AD.objects.filter(employee=e, date__gte=a, date__lte=b).select_related("shift").order_by("date"))
    for x in days: x.pill = PILL.get(x.status, "pending"); x.fi, x.lo = _hhmm(x.first_in), _hhmm(x.last_out)
    scans = {}
    if full:   # log mentah hanya HRD (jam tiap ketukan); Admin Departemen cukup melihat rekap
        for at in AttendanceRaw.objects.filter(employee=e, punched_at__gte=timezone.make_aware(datetime.combine(a, time.min)),
                                               punched_at__lt=timezone.make_aware(datetime.combine(b + timedelta(days=2), time.min))).values_list("punched_at", flat=True):
            l = timezone.localtime(at); scans.setdefault(l.date(), []).append(l.strftime("%H:%M"))
        for x in days: x.scans = " · ".join(scans.get(x.date, []))
        log(request, "hr", "attendance_employee_view", e)
    s = (calc.summary(AD.objects.filter(employee=e, date__gte=a, date__lte=b)) or [None])[0]
    prev = (a - timedelta(days=1)).strftime("%Y-%m"); nxt = (b + timedelta(days=1)).strftime("%Y-%m")
    return render(request, "attendance/employee.html", {"e": e, "days": days, "s": s, "month": mval, "mlabel": mlabel, "prev": prev, "nxt": nxt, "full": full, "pins": e.pins.all() if full else []})


# ---------------------------------------------------------------- olah ulang + minta validasi (HRD)
@hrd
@require_POST
def reprocess(request):
    p = request.POST; d1 = _date(p.get("from")); d2 = _date(p.get("to"), d1)
    if not d1 or not d2 or d2 < d1: messages.error(request, "Tanggal tidak valid."); return redirect("att_daily")
    if (d2 - d1).days > 92: messages.error(request, "Rentang olah ulang maksimal 93 hari."); return redirect("att_daily")
    ids = list(Employee.objects.filter(department_id=int(p["dept"])).values_list("pk", flat=True)) if p.get("dept", "").isdigit() else None
    n_emp = len(ids) if ids is not None else Employee.objects.count()
    if n_emp * ((d2 - d1).days + 1) > 120000: messages.error(request, "Terlalu besar untuk diolah dari web. Jalankan `python manage.py process_attendance --from … --to …` di server."); return redirect("att_daily")
    r = calc.process(d1, d2, ids)
    log(request, "hr", "attendance_process", None, None, {"from": d1.isoformat(), "to": r["end"].isoformat(), "rows": r["rows"], "changed": r["changed"]})
    messages.success(request, f"Rekap diolah: {r['rows']} baris karyawan-tanggal ({r['changed']} berubah). Hari ini & masa depan tidak diolah.")
    return redirect(f"/absensi/?from={d1}&to={d2}")


@hrd
@require_POST
def ask_validation(request):
    """Minta validasi Admin Departemen untuk semua anomali (tanpa scan / satu scan) pada rentang & filter departemen. Pasangan yang sudah punya permintaan aktif dilewati."""
    p = request.POST; d1 = _date(p.get("from")); d2 = _date(p.get("to"), d1)
    if not d1 or not d2 or d2 < d1 or (d2 - d1).days > 30: messages.error(request, "Rentang tidak valid (maks 31 hari)."); return redirect("att_daily")
    qs = AD.objects.filter(date__gte=d1, date__lte=d2).filter(Q(status="tanpa_scan") | Q(incomplete=True)).select_related("employee__department")
    if p.get("dept", "").isdigit(): qs = qs.filter(department_id=int(p["dept"]))
    rows = list(qs[:600])
    live = set(AttendanceCheck.objects.filter(date__gte=d1, date__lte=d2).exclude(status="dibatalkan").values_list("employee_id", "date"))
    by, skipped, noadmin = {}, 0, set()
    adm = {}
    for r in rows:
        if (r.employee_id, r.date) in live: skipped += 1; continue
        if r.department_id not in adm: adm[r.department_id] = bool(checks._admins(r.department_id))
        if not adm[r.department_id]: noadmin.add(r.employee.department.name); continue
        by.setdefault(r.date, []).append(r.employee)
    made = 0
    try:
        with transaction.atomic():
            for d, emps in by.items():
                for i in range(0, len(emps), 100): made += len(checks.create_checks(request.user, emps[i:i + 100], [d], "Mohon konfirmasi: mesin tidak mencatat scan lengkap."))
    except ValueError as e: messages.error(request, str(e)); return redirect("att_daily")
    log(request, "hr", "attendance_ask_validation", None, None, {"created": made, "skipped": skipped, "from": d1.isoformat(), "to": d2.isoformat()})
    msg = f"{made} permintaan validasi dikirim ke Admin Departemen." + (f" {skipped} sudah punya permintaan aktif." if skipped else "")
    if noadmin: msg += " Dilewati (departemen belum punya Admin): " + ", ".join(sorted(noadmin)) + "."
    (messages.success if made else messages.warning)(request, msg)
    return redirect("/validasi/")


# ---------------------------------------------------------------- impor
@hrd
def import_home(request):
    imp.purge_stale()
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f: messages.error(request, "Pilih berkas dari Fingerspot Personnel (.xlsx, .xls, .csv, .txt, .dat).")
        else:
            try:
                i = imp.stage_upload(f, request.user); log(request, "hr", "attendance_import_upload", None, None, {"file": i.original_name[:100], "size": i.size})
                return redirect("att_import_detail", pk=i.pk)
            except ValueError as e: messages.error(request, str(e))
    ld, stale = _freshness()
    return render(request, "attendance/import_home.html", {"history": AttendanceImport.objects.exclude(state="dibatalkan").select_related("created_by")[:20], "last_scan": ld, "stale": stale, "max_mb": imp.MAX_BYTES // 1024 // 1024})


@hrd
def import_detail(request, pk):
    i = AttendanceImport.objects.filter(pk=pk).first()
    if not i: raise Http404
    if i.state != "draft": return render(request, "attendance/import_done.html", {"i": i})
    ctx = {"i": i, "res": None}
    try:
        if request.method == "POST":
            act = request.POST.get("action")
            if act == "cancel": imp.cancel(i); messages.info(request, "Impor dibatalkan; berkas sementara dihapus."); return redirect("att_import")
            grid_w = max(len(r) for r in imp.load_grid(i)[:200]); m = imp.mapping_from_post(request.POST, grid_w) if request.POST.get("map_posted") else None
            if act == "commit":
                imp.commit(i, request.user, ignore_invalid=bool(request.POST.get("ignore_invalid")), mapping=m)
                log(request, "hr", "attendance_import", None, None, {"file": i.original_name[:100], "rows": i.rows_total, "new": i.taps_new, "dup": i.taps_duplicate, "invalid": i.rows_invalid, "unmapped_pins": i.pins_unmapped})
                if i.state == "selesai": _process_after_import(request, i)
                else: messages.info(request, i.note)
                return redirect("att_import_detail", pk=i.pk)
            ctx["res"] = imp.analyze(i, m)
        else: ctx["res"] = imp.analyze(i)
    except ValueError as e:
        messages.error(request, str(e))
        try:  # tetap tampilkan pemetaan agar bisa diperbaiki
            g = imp.load_grid(i); m = i.mapping; head = g[m["header_row"]] if m.get("header_row", -1) >= 0 else []; body = g[m["header_row"] + 1:] if m.get("header_row", -1) >= 0 else g
            ctx["res"] = {"ex": {"errors": [], "invalid": 0, "rows": 0, "taps": []}, "m": m, "layout": i.layout, "unmapped": [], "mapped_count": 0, "preview": [], "failed": True,
                          "columns": [{"i": c, "title": (str(head[c]).strip() if c < len(head) and head[c] not in (None, "") else f"Kolom {c + 1}"),
                                       "sample": ", ".join(str(r[c])[:19] for r in body[:3] if c < len(r) and r[c] not in (None, ""))} for c in range(max(len(r) for r in g[:200]))]}
        except ValueError: return redirect("att_import")
    return render(request, "attendance/import_preview.html", ctx)


@hrd
def import_errors(request, pk):
    """Unduh daftar baris gagal impor (nomor baris di berkas, alasan, saran) agar bisa diperbaiki di berkas lalu diunggah ulang."""
    i = AttendanceImport.objects.filter(pk=pk).first()
    if not i: raise Http404
    rows = (i.report or {}).get("rows") or []
    hint = {c: h for c, (_, h) in imp.REASONS.items()}
    data = [[r["row"], r["msg"], r.get("pin", ""), r.get("value", ""), hint.get(r.get("code"), "")] for r in rows]
    log(request, "hr", "attendance_import_errors_export", None, None, {"import": i.pk, "rows": len(data)})
    return tabular.export_response(f"impor-gagal-{i.pk}", ["baris_di_berkas", "masalah", "pin", "nilai_waktu", "saran"], data, request, sheet="Baris gagal", num_cols=(0,))


def _process_after_import(request, i):
    """Hitung rekap untuk rentang berkas (± 1 hari untuk shift malam) bila ukurannya wajar di dalam request web; selain itu serahkan ke `process_attendance`."""
    d1, d2 = i.date_min - timedelta(days=1), i.date_max + timedelta(days=1)
    if Employee.objects.count() * ((d2 - d1).days + 1) > 120000:
        messages.warning(request, "Scan tersimpan. Rentang terlalu besar untuk diolah dari web: jalankan `python manage.py process_attendance --from %s --to %s` (atau tunggu cron harian)." % (d1, d2)); return
    r = calc.process(d1, d2); messages.success(request, f"Impor selesai: {i.taps_new} scan baru; rekap diolah ({r['rows']} baris karyawan-tanggal).")


# ---------------------------------------------------------------- pemetaan PIN
@hrd
def pins(request):
    g = request.GET; res = None
    if request.method == "POST":
        act = request.POST.get("action")
        try:
            if act == "link":
                e = Employee.objects.filter(nik=request.POST.get("nik", "").strip()).first()
                if not e: raise ValueError("NIK tidak ditemukan.")
                n = imp.link_pin(request.user, request.POST.get("pin", ""), e, "manual"); log(request, "hr", "attendance_pin_link", e, None, {"pin": request.POST.get("pin", "")[:40], "scans": n})
                if n: _reprocess_scans(e, request.POST.get("pin", ""))
                messages.success(request, f"PIN dipetakan ke {e.nik} {e.name}; {n} scan lama ikut terhubung.")
            elif act == "move":
                e = Employee.objects.filter(nik=request.POST.get("nik", "").strip()).first()
                if not e: raise ValueError("NIK tujuan tidak ditemukan.")
                old, n = imp.move_pin(request.user, request.POST.get("pin", ""), e, request.POST.get("reason", ""))
                log(request, "hr", "attendance_pin_move", e, {"from": old.nik}, {"pin": request.POST.get("pin", "")[:40], "scans": n})
                _reprocess_scans(e, request.POST.get("pin", ""), also=old); messages.success(request, f"PIN dipindah dari {old.nik} ke {e.nik} ({n} scan).")
            elif act in ("check", "import"):
                f = request.FILES.get("file")
                if not f: raise ValueError("Pilih berkas CSV/XLSX (kolom pin, nik).")
                res = imp.import_pin_table(request.user, f.read(tabular.MAX_BYTES + 1), f.name, act == "import")
                if act == "import" and res["ok"]: log(request, "hr", "attendance_pin_import", None, None, {"rows": res["total"], "created": res["created"]}); messages.success(request, f"{res['created']} PIN dipetakan; {res['linked']} scan lama terhubung.")
        except ValueError as e: messages.error(request, str(e))
        except AttendancePin.DoesNotExist: messages.error(request, "PIN tidak ada di tabel pemetaan.")
        if res is None: return redirect("att_pins")
    qs = AttendancePin.objects.select_related("employee__department").order_by("pin")
    if g.get("q", "").strip(): q = g["q"].strip()[:40]; qs = qs.filter(Q(pin__icontains=q) | Q(employee__nik__icontains=q) | Q(employee__name__icontains=q))
    orphans = list(AttendanceRaw.objects.filter(employee__isnull=True).values("pin").annotate(n=Count("id"), last=Max("punched_at")).order_by("-n")[:50])
    return render(request, "attendance/pins.html", {"page": Paginator(qs, 50).get_page(g.get("page", 1)), "orphans": orphans, "q": g.get("q", ""), "res": res,
                                                    "no_pin": Employee.objects.exclude(status="nonaktif").exclude(pins__isnull=False).count()})


def _reprocess_scans(emp, pin, also=None):
    """Hitung ulang rekap rentang scan milik PIN untuk karyawan terkait (maks 93 hari terakhir dari scan tertua)."""
    r = AttendanceRaw.objects.filter(pin=pin).aggregate(a=Min("punched_at"), b=Max("punched_at"))
    if not r["a"]: return
    d1 = max(timezone.localtime(r["a"]).date(), timezone.localdate() - timedelta(days=92)); d2 = timezone.localtime(r["b"]).date()
    ids = [emp.pk] + ([also.pk] if also else []); calc.process(d1, d2, ids)


# ---------------------------------------------------------------- hari libur
@hrd
def holidays(request):
    y = int(request.GET["year"]) if request.GET.get("year", "").isdigit() and 2000 < int(request.GET["year"]) < 2100 else timezone.localdate().year
    if request.method == "POST":
        p = request.POST; act = p.get("action")
        try:
            if act == "add":
                d = _date(p.get("date")); name = p.get("name", "").strip()[:100]
                if not d or not name: raise ValueError("Tanggal dan nama wajib diisi.")
                if Holiday.objects.filter(date=d).exists(): raise ValueError("Tanggal itu sudah ada di daftar.")
                kind = p.get("kind") if p.get("kind") in dict(Holiday.KINDS) else "nasional"; ap = p.get("applies") if p.get("applies") in dict(Holiday.APPLIES) else "non_rotation"
                h = Holiday.objects.create(date=d, name=name, kind=kind, applies=ap, created_by=request.user); log(request, "hr", "holiday_add", None, None, {"date": d.isoformat(), "name": name})
                messages.success(request, f"{name} ({d:%d-%m-%Y}) ditambahkan. Olah ulang rekap tanggal itu agar berlaku.")
            elif act in ("toggle", "delete"):
                h = Holiday.objects.get(pk=int(p.get("id", 0)))
                if act == "delete":
                    if h.date < timezone.localdate(): raise ValueError("Hari libur yang sudah lewat tidak dihapus (nonaktifkan saja) agar rekap lama dapat ditelusuri.")
                    h.delete(); log(request, "hr", "holiday_delete", None, {"date": h.date.isoformat()}, None)
                else: h.active = not h.active; h.save(update_fields=["active"]); log(request, "hr", "holiday_toggle", None, None, {"date": h.date.isoformat(), "active": h.active})
                messages.success(request, "Tersimpan. Olah ulang rekap tanggal itu agar berlaku.")
        except (ValueError, Holiday.DoesNotExist) as e: messages.error(request, str(e) if isinstance(e, ValueError) else "Data tidak ditemukan.")
        return redirect(f"/absensi/libur/?year={y}")
    return render(request, "attendance/holidays.html", {"items": Holiday.objects.filter(date__year=y), "year": y, "kinds": Holiday.KINDS, "applies": Holiday.APPLIES})


# ---------------------------------------------------------------- tutup periode (tutup buku)
@hrd
def periods_page(request):
    """Daftar bulan + checklist akhir bulan. HRD & Superadmin menutup; HANYA Superadmin membuka kembali."""
    from . import periods
    from .models import PeriodClose
    sup = request.user.role == Role.SUPERADMIN
    if request.method == "POST":
        per, act = request.POST.get("period", ""), request.POST.get("action")
        if not periods.valid(per): messages.error(request, "Periode tidak valid."); return redirect("att_periods")
        row = PeriodClose.objects.filter(period=per).first()
        if act == "close":
            blockers = [i for i in periods.checklist(per) if i["blocking"] and not i["ok"]]
            if row and row.closed: messages.info(request, f"Periode {per} sudah ditutup.")
            elif blockers and request.POST.get("force") != "1": messages.error(request, "Belum bisa ditutup: " + "; ".join(f"{i['label']} ({i['n']})" for i in blockers) + ". Selesaikan dulu, atau centang 'Tutup paksa'.")
            else:
                PeriodClose.objects.update_or_create(period=per, defaults=dict(closed=True, note=request.POST.get("note", "").strip()[:300], closed_by=request.user, closed_at=timezone.now(), reopened_by=None, reopened_at=None))
                log(request, "hr", "period_close", None, None, {"period": per, "forced": bool(blockers)}); messages.success(request, f"Periode {per} ditutup. Rekap absensi dan tagihan Poli bulan itu terkunci.")
        elif act == "reopen":
            if not sup: messages.error(request, "Hanya Superadmin yang boleh membuka kembali periode.")
            elif row and row.closed:
                row.closed, row.reopened_by, row.reopened_at = False, request.user, timezone.now(); row.save(update_fields=["closed", "reopened_by", "reopened_at"])
                log(request, "hr", "period_reopen", None, None, {"period": per}); messages.success(request, f"Periode {per} dibuka kembali.")
        return redirect("att_periods")
    today = timezone.localdate(); rows, y, m = [], today.year, today.month
    saved = {r.period: r for r in PeriodClose.objects.all()}
    for _ in range(12):
        per = periods.key(date(y, m, 1)); cl = periods.checklist(per) if per == request.GET.get("period") or per not in saved or saved[per].closed is False else []
        rows.append({"period": per, "label": f"{MONTHS[m - 1]} {y}", "row": saved.get(per), "closed": bool(saved.get(per) and saved[per].closed), "checklist": cl,
                     "blocking": sum(1 for i in cl if i["blocking"] and not i["ok"]), "warn": sum(1 for i in cl if not i["blocking"] and not i["ok"])})
        m -= 1
        if m == 0: m, y = 12, y - 1
    return render(request, "attendance/periods.html", {"rows": rows, "sup": sup})
