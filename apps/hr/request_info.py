"""Info karyawan di form pengajuan: kondisi saat ini, tujuan, dan (tukar shift/libur) jadwal karyawan + rekan pada tanggalnya.
Hanya membaca. Cakupan sama dengan form pengajuan: Admin Departemen hanya melihat karyawan departemennya (di luar scope = tidak ditemukan)."""
from datetime import date
from apps.core.scope import DEPT_ACTORS
from . import schedule
from .forms import FIELDS_BY_TYPE
from .models import Department, Employee, Position, Shift, ShiftGroup


def _emp(user, nik):
    nik = (nik or "").strip()
    if not nik: return None
    qs = Employee.objects.select_related("department", "position", "shift", "shift_group").filter(nik=nik, status="aktif")
    if user.role in DEPT_ACTORS: qs = qs.filter(department_id=user.department_id)
    return qs.first()


def shift_label(e):
    """Nama 'Shift' karyawan: kelompok (GS, GS/N, A7, A7Pack…) bila ada; selain itu shift tetap."""
    return e.shift_label if (e.shift_group_id or e.shift_id) else "Belum diatur"


def _day(e, d):
    s = schedule.effective_schedule(e, d)
    if s["off"] or not s["shift"]: return "Libur"
    sh = s["shift"]
    return f"{sh.name} ({sh.start:%H:%M}–{sh.end:%H:%M})"


def _d(v):
    try: return date.fromisoformat(v) if v else None
    except ValueError: return None


def _pk(model, v):
    return model.objects.filter(pk=int(v)).first() if str(v or "").isdigit() and int(v) < 2**31 else None


def _person(e, title, days):
    rows = [["Nama", f"{e.name} ({e.nik})"], ["Departemen · Jabatan", f"{e.department.name} · {e.position.name if e.position_id else '—'}"],
            ["Shift", shift_label(e)], ["Status karyawan", e.get_pay_basis_display()]]
    for d in days: rows.append([f"Jadwal {d:%d-%m-%Y}", _day(e, d)])
    return {"title": title, "rows": rows}


def build(user, q):
    """q: dict parameter GET (type, nik, partner_nik, date, date_to, shift, shift_group, department, position, pay_basis, status)."""
    t = q.get("type", "")
    emp = _emp(user, q.get("nik"))
    if not emp or t not in FIELDS_BY_TYPE: return {"blocks": []}
    swap = t in schedule.SWAP_TYPES
    partner = _emp(user, q.get("partner_nik")) if swap and q.get("partner_nik") != q.get("nik") else None
    days = [x for x in ([_d(q.get("date"))] + ([_d(q.get("date_to"))] if t == "tukar_libur" else [])) if x]
    blocks = [_person(emp, "Karyawan", days if swap else [])]
    if partner: blocks.append(_person(partner, "Rekan tukar", days))
    if t == "tukar_shift" and days:
        d, a = days[0], _day(emp, days[0])
        if partner: blocks.append({"title": "Setelah ditukar", "rows": [[emp.name, _day(partner, d)], [partner.name, a]], "note": f"{d:%d-%m-%Y}: kedua jadwal dipertukarkan."})
        else:
            g = _pk(ShiftGroup, q.get("shift_group"))
            if g:
                sh = schedule.shift_of_group(emp, g, d)
                blocks.append({"title": "Setelah ditukar", "rows": [[emp.name, f"{g.label} — " + (f"{sh.name} ({sh.start:%H:%M}–{sh.end:%H:%M})" if sh else "libur")]], "note": f"Hanya {d:%d-%m-%Y}; sebelumnya: {a}."})
    elif t == "tukar_libur" and len(days) == 2 and partner:
        blocks.append({"title": "Setelah ditukar", "rows": [[emp.name, f"Masuk {days[0]:%d-%m-%Y}, libur {days[1]:%d-%m-%Y}"], [partner.name, f"Libur {days[0]:%d-%m-%Y}, masuk {days[1]:%d-%m-%Y}"]]})
    elif t == "shift":
        g = _pk(ShiftGroup, q.get("shift_group"))
        if g: blocks.append({"title": "Perubahan", "rows": [["Shift", f"{shift_label(emp)} → {g.label}"], ["Pola", g.get_pattern_display()]]})
    elif t == "status_kerja":
        v = q.get("pay_basis")
        if v in dict(Employee.PAY_BASIS): blocks.append({"title": "Perubahan", "rows": [["Status karyawan", f"{emp.get_pay_basis_display()} → {dict(Employee.PAY_BASIS)[v]}"]]})
    elif t == "status":
        if q.get("status") in ("aktif", "nonaktif"): blocks.append({"title": "Perubahan", "rows": [["Status", f"{emp.status} → {q['status']}"]]})
    elif t in ("mutasi_dept", "rotasi"):
        o = _pk(Department, q.get("department"))
        if o: blocks.append({"title": "Perubahan", "rows": [["Departemen", f"{emp.department.name} → {o.name}"]]})
    elif t in ("mutasi_jabatan", "promosi", "demosi"):
        o = _pk(Position, q.get("position"))
        if o: blocks.append({"title": "Perubahan", "rows": [["Jabatan", f"{emp.position.name if emp.position_id else '—'} → {o.name}"]]})
    return {"blocks": blocks}
