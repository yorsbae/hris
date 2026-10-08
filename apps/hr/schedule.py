"""Jadwal efektif per tanggal dan penerapan tukar shift/libur.

Prinsip VISION: master shift (`Employee.shift`, `Employee.shift_group`) TIDAK berubah oleh tukar shift/libur. Tukar yang sudah
dilaksanakan hanya menulis `ShiftAssignment` (penyesuaian per tanggal, append-only).

Jadwal efektif karyawan pada tanggal D (urutan prioritas):
  1. penyesuaian `ShiftAssignment` pada D (hasil tukar yang sudah dilaksanakan);
  2. rotasi kelompok: bila karyawan punya `shift_group` dan tabel `ShiftRotation` kelompok itu punya baris untuk hari-D
     (shift kosong = libur kelompok);
  3. shift tetap/GS (`Employee.shift`), dengan hari libur global `settings.REGULAR_OFF_WEEKDAYS` (Minggu).
     GS masuk 08:00 dan pulang 16:00 (GS-16); pada hari kerja tepat sebelum hari libur GS jam pulang menjadi 14:00 (GS-14) atau 12:00 (GS-12)
     menurut `Employee.gs_short`.

Tukar jadwal (`tukar_shift`, `tukar_libur`) punya dua mode, ditentukan oleh ada/tidaknya rekan (`payload.partner_id`):
  • 1 orang (tanpa rekan)
      tukar_shift : pemohon pindah ke shift lain pada tanggal itu.
      tukar_libur : pemohon memindahkan hari liburnya sendiri — `date` (libur) menjadi masuk, `date_to` (kerja) menjadi libur.
  • 2 orang (dengan rekan; keduanya ditulis dalam SATU pengajuan dan dilaksanakan bersama atau tidak sama sekali)
      tukar_shift : pada `date` keduanya sama-sama masuk dengan shift berbeda → shift saling ditukar.
      tukar_libur : pada `date` pemohon libur & rekan masuk; pada `date_to` rekan libur & pemohon masuk → libur saling ditukar
                    (pemohon masuk di shift rekan pada `date`; rekan masuk di shift pemohon pada `date_to`).
"""
from datetime import date, timedelta
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from .models import ChangeRequest, Employee, Shift, ShiftAssignment, ShiftRotation

SWAP_TYPES = ("tukar_shift", "tukar_libur")
LIVE = ("submitted", "pending", "approved")  # pengajuan yang belum dilaksanakan tetapi masih hidup


def _d(v): return v if isinstance(v, date) else date.fromisoformat(v)


def regular_off(d: date) -> bool: return d.weekday() in settings.REGULAR_OFF_WEEKDAYS


class gs_shorts:
    """{'14': Shift GS-14, '12': Shift GS-12} — shift GS untuk hari kerja sebelum libur GS. Dimuat MALAS (1 query, hanya bila ada karyawan GS
    yang jatuh pada hari tersebut) sehingga jumlah query jadwal untuk non-GS tidak berubah."""
    def __init__(self): self._m = None
    def get(self, key):
        if self._m is None: self._m = {s.code[3:]: s for s in Shift.objects.filter(code__in=("GS-14", "GS-12"))}
        return self._m.get(key)


def _fixed_shift(employee: Employee, d: date, gs=None):
    """Shift tetap pada tanggal d. GS: hari kerja tepat sebelum hari libur GS (mis. Sabtu) memakai GS-14 atau GS-12 sesuai `Employee.gs_short`;
    hari lain memakai shift GS-nya (GS-16, 08–16). Bila shift GS pendek belum ada di master, tetap memakai shift GS karyawan."""
    sh = employee.shift
    if sh and sh.is_gs and regular_off(d + timedelta(days=1)):
        sh = (gs if gs is not None else gs_shorts()).get(employee.gs_short) or sh
    return sh


def _base(employee: Employee, d: date, rotation=None, gs=None) -> dict:
    """Jadwal dasar tanpa penyesuaian. `rotation` (dict weekday→baris) dan `gs` dipakai agar rentang tanggal tidak menanyakan DB berulang."""
    if employee.shift_group_id:
        row = (rotation if rotation is not None else {r.weekday: r for r in ShiftRotation.objects.select_related("shift").filter(group_id=employee.shift_group_id)}).get(d.weekday())
        if row: return {"off": row.shift_id is None, "shift": row.shift, "via": "rotation"}  # tanpa baris untuk hari itu → jatuh ke shift tetap
    if regular_off(d): return {"off": True, "shift": None, "via": "fixed"}
    return {"off": False, "shift": _fixed_shift(employee, d, gs), "via": "fixed"}


def base_schedule(employee: Employee, d: date) -> dict:
    """Jadwal dasar (rotasi kelompok / shift tetap) tanpa penyesuaian tukar."""
    return _base(employee, d)


def _with_override(employee, d, a, base):
    if not a: return {**base, "source": "master", "kind": "off" if base["off"] else "work", "request_id": None}
    if a.kind == ShiftAssignment.OFF: return {"off": True, "shift": None, "via": base["via"], "source": "override", "kind": a.kind, "request_id": a.request_id}
    return {"off": False, "shift": a.shift or employee.shift, "via": base["via"], "source": "override", "kind": a.kind, "request_id": a.request_id}


def effective_schedule(employee: Employee, d: date) -> dict:
    """{'off': bool, 'shift': Shift|None, 'source': 'override'|'master', 'via': 'rotation'|'fixed', 'kind': str, 'request_id': int|None}"""
    a = ShiftAssignment.objects.select_related("shift").filter(employee=employee, date=d).first()
    return _with_override(employee, d, a, _base(employee, d))


def schedule_range(employee: Employee, start: date, days: int) -> list:
    """Jadwal efektif `days` hari berturut-turut mulai `start` dengan jumlah query tetap (2), bukan per hari."""
    rot = {r.weekday: r for r in ShiftRotation.objects.select_related("shift").filter(group_id=employee.shift_group_id)} if employee.shift_group_id else {}
    ov = {a.date: a for a in ShiftAssignment.objects.select_related("shift").filter(employee=employee, date__gte=start, date__lt=start + timedelta(days=days))}
    gs, out = gs_shorts(), []
    for i in range(days):
        d = start + timedelta(days=i)
        out.append({"date": d, **_with_override(employee, d, ov.get(d), _base(employee, d, rot, gs))})
    return out


def schedule_grid(employees, start: date, days: int = 7):
    """[(karyawan, [sel harian])] untuk banyak karyawan dengan jumlah query tetap (rotasi + penyesuaian), bukan per karyawan."""
    emps = list(employees); rot = {}; gs = gs_shorts()
    for r in ShiftRotation.objects.select_related("shift").filter(group_id__in={e.shift_group_id for e in emps if e.shift_group_id}): rot.setdefault(r.group_id, {})[r.weekday] = r
    ov = {(a.employee_id, a.date): a for a in ShiftAssignment.objects.select_related("shift").filter(employee__in=emps, date__gte=start, date__lt=start + timedelta(days=days))}
    return [(e, [{"date": d, **_with_override(e, d, ov.get((e.pk, d)), _base(e, d, rot.get(e.shift_group_id, {}), gs))} for d in (start + timedelta(days=i) for i in range(days))]) for e in emps]


def dates_of(type_, payload):
    keys = ("date", "date_to") if type_ == "tukar_libur" else ("date",)
    return [payload[k] for k in keys if payload.get(k)]


def live_conflict(employee, type_, payload, exclude_pk=None):
    """Tanggal yang sudah dipakai pengajuan tukar lain yang masih hidup (submitted/pending/approved) — baik sebagai pemohon maupun sebagai
    REKAN — atau penyesuaian yang sudah ada untuk karyawan ini."""
    mine = set(dates_of(type_, payload))
    qs = ChangeRequest.objects.filter(type__in=SWAP_TYPES, status__in=LIVE).filter(Q(employee=employee) | Q(payload__partner_id=employee.pk))
    for r in qs.exclude(pk=exclude_pk):
        hit = mine & set(dates_of(r.type, r.payload))
        if hit: return min(hit)
    exist = ShiftAssignment.objects.filter(employee=employee, date__in=[_d(x) for x in mine]).order_by("date").first()
    return exist.date.isoformat() if exist else None


def _work(shift, as_shift=True):
    """(kind, shift) untuk 'masuk': shift eksplisit bila diketahui; WORK (= shift master) bila tidak ada/tidak perlu."""
    return (ShiftAssignment.SHIFT, shift) if (shift is not None and as_shift) else (ShiftAssignment.WORK, None)


def build_rows(type_, emp, partner, p):
    """Baris penyesuaian [(karyawan, tanggal, kind, shift|None)] dihitung dari jadwal efektif SAAT INI. ValueError berisi alasan bila tukar tidak valid.
    Dipakai oleh form (saat mengajukan) dan oleh `apply` (saat melaksanakan) agar aturannya satu."""
    d1 = _d(p["date"]); S, W, O = ShiftAssignment.SHIFT, ShiftAssignment.WORK, ShiftAssignment.OFF
    if type_ == "tukar_shift":
        if not partner:  # 1 orang: pindah shift
            shift = Shift.objects.get(pk=p["shift_id"]); cur = effective_schedule(emp, d1)
            if cur["off"]: raise ValueError(f"{d1:%d-%m-%Y} adalah hari libur karyawan; gunakan tukar libur.")
            if cur["shift"] and cur["shift"].pk == shift.pk: raise ValueError("Shift tujuan sama dengan shift reguler karyawan saat ini.")
            return [(emp, d1, S, shift)]
        a, b = effective_schedule(emp, d1), effective_schedule(partner, d1)  # 2 orang: saling tukar shift
        if a["off"] or b["off"]: raise ValueError(f"Tukar shift hanya untuk dua karyawan yang sama-sama masuk pada {d1:%d-%m-%Y}; untuk libur gunakan tukar libur.")
        if not a["shift"] or not b["shift"]: raise ValueError("Shift salah satu karyawan belum diatur; tidak ada yang bisa ditukar.")
        if a["shift"].pk == b["shift"].pk: raise ValueError(f"Kedua karyawan sudah berada di shift yang sama ({a['shift'].name}) pada {d1:%d-%m-%Y}.")
        return [(emp, d1, S, b["shift"]), (partner, d1, S, a["shift"])]
    d2 = _d(p["date_to"])
    if d1 == d2: raise ValueError("Dua tanggal harus berbeda.")
    if not partner:  # 1 orang: pindahkan libur sendiri (date → masuk, date_to → libur)
        c1, c2 = effective_schedule(emp, d1), effective_schedule(emp, d2)
        if not c1["off"]: raise ValueError(f"{d1:%d-%m-%Y} bukan hari libur karyawan ini, jadi tidak bisa dijadikan hari masuk.")
        if c2["off"]: raise ValueError(f"{d2:%d-%m-%Y} sudah hari libur; pilih hari kerja yang akan dijadikan libur.")
        # karyawan rotasi tidak punya shift master yang jelas pada hari libur kelompoknya → masuk memakai shift hari yang ditukar; shift tetap: shift reguler
        kind, sh = _work(c2["shift"], as_shift=bool(emp.shift_group_id))
        return [(emp, d1, kind, sh), (emp, d2, O, None)]
    a1, b1, a2, b2 = effective_schedule(emp, d1), effective_schedule(partner, d1), effective_schedule(emp, d2), effective_schedule(partner, d2)  # 2 orang
    if not (a1["off"] and not b1["off"]): raise ValueError(f"Pada {d1:%d-%m-%Y} {emp.name} harus sedang libur dan {partner.name} masuk.")
    if not (b2["off"] and not a2["off"]): raise ValueError(f"Pada {d2:%d-%m-%Y} {partner.name} harus sedang libur dan {emp.name} masuk.")
    (k1, s1), (k2, s2) = _work(b1["shift"]), _work(a2["shift"])  # masuk menggantikan shift orang yang libur
    return [(emp, d1, k1, s1), (partner, d1, O, None), (emp, d2, O, None), (partner, d2, k2, s2)]


def partner_of(req):
    pid = req.payload.get("partner_id")
    return Employee.all_objects.filter(pk=pid).first() if pid else None


def apply(req: ChangeRequest, user):
    """Dipanggil dari services._execute (di dalam transaksi). Gagal dengan ValueError bila tidak aman diterapkan. Semua baris (kedua karyawan
    pada mode 2 orang) ditulis bersama atau tidak sama sekali."""
    p, today = req.payload, timezone.localdate()
    dates = [_d(x) for x in dates_of(req.type, p)]
    for d in dates:
        if d < today:
            raise ValueError(f"Tanggal {d:%d-%m-%Y} sudah lewat; tukar jadwal tidak dapat diterapkan. Batalkan pengajuan ini lalu ajukan ulang bila perlu.")
    ids = sorted({req.employee_id, *([p["partner_id"]] if p.get("partner_id") else [])})  # kunci baris berurutan pk → tidak saling deadlock
    emps = {e.pk: e for e in Employee.all_objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    emp, partner = emps[req.employee_id], emps.get(p.get("partner_id"))
    for e in (emp, partner):
        if e and (e.deleted_at or e.status != "aktif"): raise ValueError("Karyawan sudah tidak aktif." if e is emp else f"Rekan tukar ({e.name}) sudah tidak aktif.")
    if p.get("partner_id") and not partner: raise ValueError("Rekan tukar tidak ditemukan.")
    for e in (emp, partner):
        if not e: continue
        for d in dates:
            if ShiftAssignment.objects.filter(employee=e, date=d).exists():
                raise ValueError(f"Tanggal {d:%d-%m-%Y} sudah punya penyesuaian jadwal dari pengajuan lain ({e.name}).")
    for e, d, kind, shift in build_rows(req.type, emp, partner, p):
        ShiftAssignment.objects.create(employee=e, date=d, kind=kind, shift=shift, request=req, created_by=user)
