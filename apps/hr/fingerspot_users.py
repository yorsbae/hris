"""Kelola User Fingerspot — logika bisnis (tanpa HTTP).

Halaman ini menggantikan "Pemetaan PIN". Satu daftar gabungan atas tiga sumber, kunci = PIN:
  (a) DeviceUser      : user mesin tersimpan di HRIS (hasil Download user / tambah manual),
  (b) AttendancePin   : pemetaan PIN → karyawan,
  (c) AttendanceRaw   : PIN yatim di scanlog (belum terhubung ke karyawan).

Siklus hidup user mesin:  aktif ──nonaktifkan──▶ nonaktif ──hapus──▶ (hilang dari HRIS)
  • nonaktif  = tetap tersimpan (pemetaan, riwayat scan, sidik jari utuh) tetapi TIDAK ikut Upload user ke mesin dan
                tidak muncul di antrean "belum dipetakan". Dipakai untuk karyawan keluar / PIN sampah / PIN uji.
  • hapus     = hanya untuk user nonaktif; membuang user + template sidik jari dari HRIS. Pemetaan & scan lama TIDAK disentuh.
                (Bila user masih ada di mesin, Download user berikutnya akan membuatnya kembali — hapus di mesin dulu.)
Semua fungsi mengubah data hanya lewat jalur ini dan melempar ValueError berisi pesan untuk pengguna."""
from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from . import attendance_import as imp
from .models import AttendancePin, AttendanceRaw, DeviceFingerprint, DeviceUser, Employee

TABS = [("all", "Semua"), ("unmapped", "Belum dipetakan"), ("mapped", "Terpetakan"), ("attention", "Perlu tindakan"), ("inactive", "Nonaktif"), ("nopin", "Karyawan tanpa PIN")]
TAB_KEYS = {k for k, _ in TABS}
SORTS = [("pin", "PIN"), ("name", "Nama"), ("dept", "Departemen"), ("updated", "Terakhir diperbarui")]
NO_DEPT = "0"          # nilai filter departemen untuk baris yang belum dipetakan (belum punya departemen)
MAX_BULK = 500


def _pin_key(p): return (0, int(p), "") if p.isdigit() else (1, 0, p)


# ---------------------------------------------------------------- daftar gabungan
def build_rows():
    """Semua PIN yang dikenal HRIS sebagai satu daftar. Tidak mengubah data. Bidang `state`: unmapped | mapped | inactive;
    `attention` = user masih AKTIF padahal karyawannya sudah nonaktif (kandidat dinonaktifkan)."""
    users = {u.pin: u for u in DeviceUser.objects.all()}
    maps = {m.pin: m for m in AttendancePin.objects.select_related("employee__department")}
    orphan = {r["pin"]: r for r in AttendanceRaw.objects.filter(employee__isnull=True).values("pin").annotate(n=Count("id"), last=Max("punched_at"))}
    fingers = dict(DeviceFingerprint.objects.values_list("user__pin").annotate(c=Count("id")))
    pins = set(users) | set(maps) | set(orphan)
    sug = imp.name_suggestions([p for p in pins if p not in maps])
    rows = []
    for pin in pins:
        u, m, o = users.get(pin), maps.get(pin), orphan.get(pin)
        emp = m.employee if m else None
        active = u.active if u else True
        state = "inactive" if not active else ("mapped" if emp else "unmapped")
        reason = "Karyawan sudah nonaktif, user mesin masih aktif" if (active and emp is not None and emp.status == "nonaktif") else ""
        s = sug.get(pin, {}) if emp is None else {}
        rows.append({"pin": pin, "u": u, "m": m, "emp": emp, "dept": emp.department if emp else None, "state": state, "attention": bool(reason), "reason": reason,
                     "name": (u.name if u else "") or s.get("device_name", ""), "in_user": u is not None, "in_scan": o is not None,
                     "n": o["n"] if o else None, "last": o["last"] if o else None, "sug": s, "fingers": fingers.get(pin, 0),
                     "priv": u.privilege if u else 0, "card": u.card if u else 0, "active": active, "updated": u.updated_at if u else None})
    return rows


def _ints(values):
    out = set()
    for v in values:
        if str(v).isdigit(): out.add(int(v))
    return out


def parse_filters(g):
    """GET → dict filter yang sudah dibersihkan (nilai tak dikenal dibuang, bukan error)."""
    tab = g.get("tab") if g.get("tab") in TAB_KEYS else "all"
    sort = g.get("sort") if g.get("sort") in dict(SORTS) else "pin"
    return {"tab": tab, "q": g.get("q", "").strip()[:40], "dept": sorted(_ints(g.getlist("dept"))),
            "origin": g.get("origin") if g.get("origin") in ("scan", "noscan") else "",
            "match": g.get("match") if g.get("match") in ("ok", "ambigu", "tidak ada", "tanpa nama") else "",
            "src": g.get("src") if g.get("src") in dict(AttendancePin.SOURCES) else "",
            "priv": g.get("priv") if g.get("priv") in ("admin", "user") else "",
            "fp": g.get("fp") if g.get("fp") in ("ada", "tidak") else "",
            "sort": sort, "desc": g.get("desc") == "1"}


def count_filters(f): return sum(bool(f[k]) for k in ("q", "dept", "origin", "match", "src", "priv", "fp"))


def _passes(r, f):
    """Semua filter KECUALI tab (supaya jumlah di tiap tab mengikuti filter yang aktif)."""
    if f["q"]:
        q = f["q"].lower()
        hay = (r["pin"], r["name"], r["emp"].nik if r["emp"] else "", r["emp"].name if r["emp"] else "")
        if not any(q in (h or "").lower() for h in hay): return False
    if f["dept"]:
        want = set(f["dept"]); have = r["dept"].pk if r["dept"] else int(NO_DEPT)
        if have not in want: return False
    if f["origin"] and (r["state"] != "unmapped" or r["in_scan"] != (f["origin"] == "scan")): return False
    if f["match"] and (r["state"] != "unmapped" or r["sug"].get("status") != f["match"]): return False
    if f["src"] and (not r["m"] or r["m"].source != f["src"]): return False
    if f["priv"] and (r["priv"] >= 1) != (f["priv"] == "admin"): return False
    if f["fp"] and (r["fingers"] > 0) != (f["fp"] == "ada"): return False
    return True


def _in_tab(r, tab):
    return tab == "all" or r["state"] == tab or (tab == "attention" and r["attention"])


def _sorted(rows, f):
    key = {"pin": lambda r: _pin_key(r["pin"]), "name": lambda r: ((r["name"] or (r["emp"].name if r["emp"] else "")).lower() or "￿", _pin_key(r["pin"])),
           "dept": lambda r: ((r["dept"].name.lower() if r["dept"] else "￿"), _pin_key(r["pin"])),
           "updated": lambda r: (r["updated"] is None, -(r["updated"].timestamp()) if r["updated"] else 0, _pin_key(r["pin"]))}[f["sort"]]
    out = sorted(rows, key=key)
    if f["desc"] and f["sort"] != "updated": out.reverse()
    return out


def nopin_employees(f):
    """Karyawan aktif yang belum punya PIN sama sekali (kandidat dibuatkan user mesin). Ikut filter q & departemen."""
    qs = Employee.objects.exclude(status="nonaktif").exclude(pins__isnull=False).select_related("department").order_by("department__name", "name")
    if f["q"]:
        from django.db.models import Q
        qs = qs.filter(Q(nik__icontains=f["q"]) | Q(name__icontains=f["q"]))
    ds = [d for d in f["dept"] if d != int(NO_DEPT)]
    if f["dept"] and not ds: return qs.none()
    if ds: qs = qs.filter(department_id__in=ds)
    return qs


def overview(g):
    """→ (rows tab aktif sudah terurut, jumlah per tab, statistik ringkas, filter). Satu kali bangun daftar per permintaan."""
    f = parse_filters(g); allrows = build_rows(); rows = [r for r in allrows if _passes(r, f)]
    tabs = {k: sum(1 for r in rows if _in_tab(r, k)) for k in ("all", "unmapped", "mapped", "attention", "inactive")}
    tabs["nopin"] = nopin_employees(f).count()
    stat = {"users": sum(1 for r in allrows if r["in_user"]), "mapped": sum(1 for r in allrows if r["state"] == "mapped"), "unmapped": sum(1 for r in allrows if r["state"] == "unmapped"),
            "scan": sum(1 for r in allrows if r["state"] == "unmapped" and r["in_scan"]), "noscan": sum(1 for r in allrows if r["state"] == "unmapped" and not r["in_scan"]),
            "auto": sum(1 for r in allrows if r["state"] == "unmapped" and r["sug"].get("status") == "ok"), "inactive": sum(1 for r in allrows if r["state"] == "inactive"),
            "attention": sum(1 for r in allrows if r["attention"]), "fingers": sum(r["fingers"] for r in allrows)}
    stat["nopin"] = nopin_employees({**f, "q": "", "dept": []}).count()
    return (_sorted([r for r in rows if _in_tab(r, f["tab"])], f) if f["tab"] != "nopin" else []), tabs, stat, f


def scan_stats(rows):
    """Jumlah & scan terakhir untuk PIN pada halaman yang tampil (hanya satu query kecil, bukan seluruh tabel)."""
    need = [r["pin"] for r in rows if r["n"] is None]
    if not need: return
    got = {x["pin"]: x for x in AttendanceRaw.objects.filter(pin__in=need).values("pin").annotate(n=Count("id"), last=Max("punched_at"))}
    for r in rows:
        if r["n"] is None: x = got.get(r["pin"]); r["n"], r["last"] = (x["n"], x["last"]) if x else (0, None)


def export_rows(rows):
    st = {"unmapped": "Belum dipetakan", "mapped": "Terpetakan", "inactive": "Nonaktif"}
    return [[r["pin"], r["name"], r["emp"].nik if r["emp"] else "", r["emp"].name if r["emp"] else "", r["dept"].name if r["dept"] else "", st[r["state"]],
             "Ya" if r["attention"] else "", "Admin mesin" if r["priv"] else "User", r["fingers"], r["card"] or ""] for r in rows]


EXPORT_HEADER = ["pin", "nama_di_mesin", "nik", "nama_karyawan", "departemen", "status", "perlu_tindakan", "privilege", "jumlah_jari", "kartu"]


# ---------------------------------------------------------------- aksi
def _pins(raw):
    out = []
    for x in raw:
        x = imp.clean_key(x)
        if x and x not in out: out.append(x)
    if not out: raise ValueError("Pilih minimal satu user (centang baris).")
    if len(out) > MAX_BULK: raise ValueError(f"Maksimal {MAX_BULK} user sekali proses.")
    return out


def set_active(pins, active, reason="", actor=None):
    """Aktifkan / nonaktifkan user mesin. PIN yang belum punya DeviceUser (mis. PIN yatim di scanlog, atau PIN = NIK yang belum pernah diunduh)
    dibuatkan catatan kosong agar statusnya tercatat. Nonaktif WAJIB beralasan. → jumlah yang berubah."""
    pins = _pins(pins); reason = (reason or "").strip()[:200]
    if not active and not reason: raise ValueError("Alasan menonaktifkan wajib diisi.")
    names = {m.pin: m.employee.name[:80] for m in AttendancePin.objects.filter(pin__in=pins).select_related("employee")}
    n = 0
    with transaction.atomic():
        have = {u.pin: u for u in DeviceUser.objects.select_for_update().filter(pin__in=pins)}
        for pin in pins:
            u = have.get(pin)
            if u is None:
                if active: continue
                u = DeviceUser(pin=pin[:24], name=names.get(pin, ""), source_device="manual")
            if u.pk and u.active == active: continue
            u.active = active; u.deactivated_at = None if active else timezone.now(); u.deactivated_reason = "" if active else reason; u.save(); n += 1
    return n


def deactivate_resigned(actor=None):
    """Nonaktifkan semua user mesin AKTIF yang karyawannya sudah nonaktif (alur keluar karyawan). → jumlah."""
    pins = [m.pin for m in AttendancePin.objects.filter(employee__status="nonaktif")]
    pins = list(DeviceUser.objects.filter(pin__in=pins, active=True).values_list("pin", flat=True))
    return set_active(pins, False, "Karyawan nonaktif", actor) if pins else 0


def delete_users(pins):
    """Hapus user mesin NONAKTIF dari HRIS (beserta template sidik jari). Pemetaan & scan lama tidak disentuh. → (jumlah dihapus, daftar PIN dilewati)."""
    pins = _pins(pins); done, skipped = 0, []
    with transaction.atomic():
        for u in DeviceUser.objects.select_for_update().filter(pin__in=pins):
            if u.active: skipped.append(u.pin); continue
            u.delete(); done += 1
    return done, skipped


def unlink_pin(pin, reason):
    """Lepas pemetaan PIN (salah petakan). Hanya bila belum ada scan yang terhubung lewat PIN itu; kalau sudah ada, pakai Pindahkan agar riwayat tidak yatim."""
    pin = imp.clean_key(pin); reason = (reason or "").strip()
    if not reason: raise ValueError("Alasan wajib diisi.")
    m = AttendancePin.objects.select_related("employee").filter(pin=pin).first()
    if not m: raise ValueError("PIN tidak ada di tabel pemetaan.")
    n = AttendanceRaw.objects.filter(pin=pin).count()
    if n: raise ValueError(f"PIN {pin} sudah punya {n} scan terhubung ke {m.employee.nik}. Gunakan Pindahkan agar riwayat scan ikut berpindah.")
    emp = m.employee; m.delete(); return emp


def create_for_employees(niks, actor):
    """Buat user mesin (PIN = NIK) + pemetaan untuk karyawan aktif yang belum punya PIN. Sidik jari didaftarkan di mesin lalu Download user.
    → (dibuat, [(nik, alasan lewat)], jumlah PIN non-angka)."""
    niks = [n.strip() for n in niks if n and n.strip()][:MAX_BULK]
    if not niks: raise ValueError("Pilih minimal satu karyawan (centang baris).")
    emps = {e.nik: e for e in Employee.objects.exclude(status="nonaktif").filter(nik__in=niks)}
    made, skipped, nonnum = 0, [], 0
    for nik in niks:
        e = emps.get(nik)
        if not e: skipped.append((nik, "tidak ditemukan / nonaktif")); continue
        pin = imp.clean_key(e.nik)
        if not pin or len(pin) > 24: skipped.append((nik, "NIK tidak bisa dipakai sebagai PIN")); continue
        if AttendancePin.objects.filter(employee=e).exists(): skipped.append((nik, "sudah punya PIN")); continue
        if AttendancePin.objects.filter(pin=pin).exists(): skipped.append((nik, f"PIN {pin} sudah dipakai karyawan lain")); continue
        with transaction.atomic():
            u, new = DeviceUser.objects.get_or_create(pin=pin, defaults={"name": e.name[:80], "source_device": "manual"})
            if not new and not u.active: u.active, u.deactivated_at, u.deactivated_reason = True, None, ""; u.save()
            imp.link_pin(actor, pin, e, "nik", "Dibuat dari Kelola User Fingerspot")
        made += 1; nonnum += (not pin.isdigit())
    return made, skipped, nonnum
