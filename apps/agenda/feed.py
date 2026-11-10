"""Sumber data Kalender per peran (putaran 66). Satu fungsi `build` mengumpulkan entri dari modul lain; HAK LIHAT ditentukan di sini (server), bukan di tampilan:
- Superadmin & HRD: semua lapisan, semua departemen (filter departemen opsional).
- Admin Departemen & Poli: hanya karyawan departemennya (+ agenda yang ditujukan kepadanya).
- Payroll: hanya libur, agenda, dan JUMLAH karyawan cuti per hari (tanpa nama).
- IT: libur, agenda, dan masa berlaku aset/lisensi/domain (tanpa data karyawan).
Data medis (Poli) tidak pernah masuk kalender."""
from datetime import date, timedelta
from django.urls import reverse
from django.db.models import Q
from apps.core.models import Role
from apps.core.scope import DEPT_ACTORS

# (kunci, label, kelas warna) — warna = pill di kalender; urutan = urutan filter
LAYERS = [("ajuan", "Pengajuan cuti (menunggu)", "c-ajuan"), ("cuti", "Karyawan cuti / izin", "c-cuti"), ("hamil", "Cuti hamil", "c-hamil"), ("libur", "Hari libur", "c-libur"),
          ("rapat", "Rapat", "c-rapat"), ("event", "Event", "c-event"), ("pelatihan", "Pelatihan", "c-latih"), ("pengingat", "Pengingat", "c-ingat"),
          ("ultah", "Ulang tahun", "c-ultah"), ("kontrak", "Kontrak berakhir", "c-kontrak"), ("it", "Masa berlaku IT", "c-it")]
LABEL = {k: l for k, l, _ in LAYERS}; COLOR = {k: c for k, _, c in LAYERS}
LEAVE_TYPES = {"izin": "Izin", "cuti": "Cuti", "sakit": "Sakit", "izin_khusus": "Izin khusus"}
PENDING, GRANTED = ("submitted", "pending"), ("approved", "executed")


def allowed_layers(user):
    r = user.role
    if r in (Role.SUPERADMIN, Role.HRD): base = {"ajuan", "cuti", "hamil", "libur", "rapat", "event", "pelatihan", "pengingat", "ultah", "kontrak"}
    elif r in DEPT_ACTORS: base = {"ajuan", "cuti", "hamil", "libur", "rapat", "event", "pelatihan", "pengingat", "ultah"}
    elif r == Role.PAYROLL: base = {"cuti", "libur", "rapat", "event", "pelatihan", "pengingat"}
    else: base = {"libur", "rapat", "event", "pelatihan", "pengingat"}
    if r in (Role.SUPERADMIN, Role.IT): base.add("it")
    return [k for k, _, _ in LAYERS if k in base]


def can_see_names(user): return user.role in (Role.SUPERADMIN, Role.HRD, *DEPT_ACTORS)
def can_filter_dept(user): return user.role in (Role.SUPERADMIN, Role.HRD)


def visible_events(user, qs):
    if user.role == Role.SUPERADMIN: return qs
    q = Q(audience="semua") | Q(created_by=user) | Q(audience="role", audience_role=user.role)
    if user.department_id: q |= Q(audience="dept", department_id=user.department_id)
    return qs.filter(q)


def _days(a, b, lo, hi):
    a, b = max(a, lo), min(b, hi); d = a
    while d <= b: yield d; d += timedelta(days=1)


def _iso(v):
    try: return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError): return None


def build(user, lo, hi, layers=None, dept=None):
    """Daftar entri {date, layer, title, sub, link, color, emp} untuk rentang lo..hi (inklusif). `layers` = subset yang diminta (diperpotongkan dengan hak lihat)."""
    from apps.hr.models import ChangeRequest, Contract, Employee, Holiday
    from apps.hrd.models import MaternityLeave
    from .models import AgendaEvent
    on = set(allowed_layers(user)) & (set(layers) if layers is not None else {k for k, _, _ in LAYERS})
    names = can_see_names(user); scope_dept = None
    if user.role in DEPT_ACTORS: scope_dept = user.department_id or -1
    elif dept and can_filter_dept(user): scope_dept = dept
    out = []
    def add(d, layer, title, sub="", link="", emp=None): out.append({"date": d, "layer": layer, "title": title, "sub": sub, "link": link, "color": COLOR[layer], "emp": emp})
    def by_dept(qs, f="department_id"): return qs.filter(**{f: scope_dept}) if scope_dept else qs

    if on & {"ajuan", "cuti"}:
        qs = by_dept(ChangeRequest.objects.filter(type__in=list(LEAVE_TYPES), status__in=PENDING + GRANTED).select_related("employee", "department"))
        for r in qs:
            p = r.payload or {}; a = _iso(p.get("start_date") or p.get("date")); b = _iso(p.get("end_date")) or a
            if not a or b < lo or a > hi: continue
            layer = "ajuan" if r.status in PENDING else "cuti"
            if layer not in on: continue
            who = r.employee.name if names else "Karyawan"
            for d in _days(a, b, lo, hi): add(d, layer, f"{who} · {LEAVE_TYPES[r.type]}", r.department.name, f"/requests/{r.pk}/" if names else "", r.employee_id)
    if "hamil" in on:
        for ml in by_dept(MaternityLeave.objects.filter(state="aktif", start_date__lte=hi, end_date__gte=lo).select_related("employee"), "employee__department_id"):
            for d in _days(ml.start_date, ml.end_date, lo, hi): add(d, "hamil", f"{ml.employee.name} · Cuti hamil" if names else "Karyawan · Cuti hamil", "", "", ml.employee_id)
    if "libur" in on:
        for h in Holiday.objects.filter(active=True, date__range=(lo, hi)): add(h.date, "libur", h.name, h.get_kind_display(), "/absensi/libur/" if user.role in (Role.SUPERADMIN, Role.HRD) else "")
    kinds = [k for k in ("rapat", "event", "pelatihan", "pengingat") if k in on]
    if kinds:
        for e in visible_events(user, AgendaEvent.objects.filter(kind__in=kinds, start_date__lte=hi).select_related("department")):
            if e.last < lo: continue
            t = f"{e.start_time:%H:%M} " if e.start_time else ""
            for d in _days(e.start_date, e.last, lo, hi): add(d, e.kind, t + e.title, e.location, f"/calendar/event/{e.pk}/")
    if "ultah" in on:
        for e in by_dept(Employee.objects.exclude(status="nonaktif").filter(birth_date__isnull=False)):
            for y in {lo.year, hi.year}:
                try: d = e.birth_date.replace(year=y)
                except ValueError: d = date(y, 2, 28)
                if lo <= d <= hi: add(d, "ultah", f"{e.name}", f"Ulang tahun ke-{y - e.birth_date.year}", "", e.pk)
    if "kontrak" in on:
        for c in Contract.objects.filter(status="aktif", end__range=(lo, hi)).select_related("employee"):
            if not scope_dept or c.employee.department_id == scope_dept: add(c.end, "kontrak", c.employee.name, "Kontrak berakhir", reverse("employee_detail_page", args=[c.employee_id]), c.employee_id)
    if "it" in on:
        from apps.it.models import Asset, Domain, License
        for a in Asset.objects.exclude(status="dihapus").filter(warranty_until__range=(lo, hi)): add(a.warranty_until, "it", f"Garansi {a.tag}", a.name, f"/it/assets/{a.pk}/")
        for x in License.objects.filter(expires__range=(lo, hi)): add(x.expires, "it", f"Lisensi {x.name}", "", f"/it/licenses/{x.pk}/")
        for x in Domain.objects.filter(expires__range=(lo, hi)): add(x.expires, "it", f"{x.get_kind_display()} {x.name}", "", f"/it/domains/{x.pk}/")
    if user.role == Role.PAYROLL:   # hanya jumlah per hari, tanpa nama
        agg = {}
        for e in [x for x in out if x["layer"] == "cuti"]: agg.setdefault(e["date"], set()).add(e["emp"])
        out = [x for x in out if x["layer"] != "cuti"] + [{"date": d, "layer": "cuti", "title": f"{len(s)} karyawan cuti", "sub": "", "link": "", "color": COLOR["cuti"], "emp": None} for d, s in agg.items()]
    return out


def heat(entries):
    """{date: jumlah karyawan berbeda yang tidak masuk (cuti/izin disetujui + cuti hamil)}; Payroll: jumlah dari entri agregat."""
    h = {}
    for e in entries:
        if e["layer"] in ("cuti", "hamil"):
            if e["emp"] is None: h[e["date"]] = h.get(e["date"], 0) + int(e["title"].split()[0])
            else: h.setdefault(e["date"], set()).add(e["emp"])
    return {d: (len(v) if isinstance(v, set) else v) for d, v in h.items()}
