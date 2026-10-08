"""Impor karyawan dari CSV atau XLSX. Semua-atau-tidak-sama-sekali: satu baris salah → tidak ada yang tersimpan.
Memakai ulang EmployeeForm agar aturan validasi identik dengan input manual."""
from apps.core import tabular
from django.db import transaction
from .emp_forms import EmployeeForm
from .models import Department, Employee, Position, Shift, ShiftGroup

COLUMNS = ["nik", "name", "gender", "join_date", "department_code", "position", "shift", "status", "marital_status", "education",
           "shift_group", "gs_short", "address", "phone", "nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account", "supervisor_nik"]
REQUIRED = ["nik", "name", "gender", "join_date", "department_code"]
MAX_ROWS, MAX_BYTES = tabular.MAX_ROWS, tabular.MAX_BYTES
EXAMPLE = ["EMP-0001", "Budi Santoso", "L", "2024-01-15", "PROD", "Staff", "Pagi", "aktif", "", "", "", "", "", "", "", "", "", "", "", "", ""]


def template_csv(): return tabular.to_bytes(COLUMNS, [EXAMPLE], "csv")[0].decode("utf-8")  # (BOM di awal; pembaca CSV memakai utf-8-sig)


def template_rows(): return COLUMNS, EXAMPLE


def _read(raw, filename=""):
    return tabular.read_table(raw, filename, REQUIRED)


def run(raw, commit, filename=""):
    """Return dict: ok, created, total, errors[(baris, nik, [pesan])], fatal."""
    try: rows = _read(raw, filename)
    except ValueError as e: return {"ok": False, "fatal": str(e), "errors": [], "total": 0, "created": 0}
    deps = {d.code.upper(): d for d in Department.objects.all()}
    poss = {p.name.lower(): p for p in Position.objects.all()}
    shifts = {s.name.lower(): s for s in Shift.objects.all()}; shifts.update({s.code.lower(): s for s in Shift.objects.exclude(code="")})  # nama ATAU kode shift
    groups = {g.code.lower(): g for g in ShiftGroup.objects.all()}
    db_niks = set(Employee.all_objects.values_list("nik", flat=True)); file_niks = {r.get("nik", "") for r in rows}
    seen, errors, valid = set(), [], []
    for i, r in enumerate(rows, start=2):  # baris 1 = header
        errs = []
        for k, v in r.items():
            if v[:1] in ("=", "@"): errs.append(f"Kolom {k}: tidak boleh diawali '{v[:1]}' (risiko formula spreadsheet).")
        d, p, s = deps.get(r.get("department_code", "").upper()), poss.get(r.get("position", "").lower()), shifts.get(r.get("shift", "").lower())
        if not d: errs.append(f"Departemen dengan kode '{r.get('department_code', '')}' tidak ada.")
        if r.get("position") and not p: errs.append(f"Jabatan '{r['position']}' tidak ada (buat dulu di Master).")
        g = groups.get(r.get("shift_group", "").lower())
        if r.get("shift_group") and not g: errs.append(f"Kelompok shift '{r['shift_group']}' tidak ada (A–G atau A_pack–G_pack; isi lewat impor tabel rotasi).")
        if r.get("gs_short") and r["gs_short"] not in ("12", "14"): errs.append("gs_short harus 14 atau 12.")
        if r.get("shift") and not s: errs.append(f"Shift '{r['shift']}' tidak ada (buat dulu di Master).")
        nik = r.get("nik", "")
        if nik in seen: errs.append("NIK dobel di dalam file.")
        seen.add(nik)
        sup = r.get("supervisor_nik", "")
        if sup and (sup == nik or (sup not in db_niks and sup not in file_niks)): errs.append("Atasan tidak ditemukan / sama dengan karyawan ini.")
        if not errs:
            form = EmployeeForm({**{c: r.get(c, "") for c in COLUMNS if c not in ("department_code", "position", "shift", "shift_group", "gs_short", "supervisor_nik")},
                                 "shift_group": g.pk if g else "", "gs_short": r.get("gs_short") or "14",
                                 "status": r.get("status") or "aktif", "department": d.pk, "position": p.pk if p else "", "shift": s.pk if s else "",
                                 "supervisor_nik": ""})  # atasan diselesaikan sendiri (bisa menunjuk baris lain di file yang sama)
            if form.is_valid(): valid.append((form, sup))
            else: errs += [f"{f}: {m}" for f, ms in form.errors.items() for m in ms]
        if errs: errors.append((i, nik, errs))
    res = {"ok": not errors, "errors": errors, "total": len(rows), "created": 0, "fatal": ""}
    if errors or not commit: return res
    with transaction.atomic():
        objs = []
        for form, _ in valid:
            o = form.save(commit=False); objs.append(o)
        Employee.objects.bulk_create(objs, batch_size=500)
        pk = dict(Employee.objects.filter(nik__in=[o.nik for o in objs]).values_list("nik", "pk"))
        fix = []
        for (form, sup), o in zip(valid, objs):
            if sup: fix.append(Employee(pk=pk[o.nik], supervisor_id=pk.get(sup) or Employee.objects.get(nik=sup).pk))
        Employee.objects.bulk_update(fix, ["supervisor"], batch_size=500)
    res["created"] = len(objs)
    return res
