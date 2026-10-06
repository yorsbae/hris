"""Impor karyawan dari CSV. Semua-atau-tidak-sama-sekali: satu baris salah → tidak ada yang tersimpan.
Memakai ulang EmployeeForm agar aturan validasi identik dengan input manual."""
import csv, io
from django.db import transaction
from .emp_forms import EmployeeForm
from .models import Department, Employee, Position, Shift

COLUMNS = ["nik", "name", "gender", "join_date", "department_code", "position", "shift", "status", "marital_status", "education",
           "address", "phone", "nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account", "supervisor_nik"]
REQUIRED = ["nik", "name", "gender", "join_date", "department_code"]
MAX_ROWS, MAX_BYTES = 5000, 5 * 1024 * 1024
EXAMPLE = ["EMP-0001", "Budi Santoso", "L", "2024-01-15", "PROD", "Staff", "Pagi", "aktif", "", "", "", "", "", "", "", "", "", "", ""]


def template_csv():
    out = io.StringIO(); w = csv.writer(out); w.writerow(COLUMNS); w.writerow(EXAMPLE); return out.getvalue()


def _read(raw):
    if len(raw) > MAX_BYTES: raise ValueError("File terlalu besar (maks 5 MB).")
    try: text = raw.decode("utf-8-sig")  # utf-8-sig membuang BOM dari Excel
    except UnicodeDecodeError: raise ValueError("File harus berenkode UTF-8 (di Excel: Save As → CSV UTF-8).")
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") > first.count(",") else ","  # Excel Indonesia memakai titik-koma
    rd = csv.DictReader(io.StringIO(text), delimiter=delim)
    header = [h.strip().lower() for h in (rd.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing: raise ValueError("Kolom wajib tidak ada: " + ", ".join(missing))
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rd]
    if not rows: raise ValueError("File tidak berisi data.")
    if len(rows) > MAX_ROWS: raise ValueError(f"Maksimal {MAX_ROWS} baris per impor.")
    return rows


def run(raw, commit):
    """Return dict: ok, created, total, errors[(baris, nik, [pesan])], fatal."""
    try: rows = _read(raw)
    except ValueError as e: return {"ok": False, "fatal": str(e), "errors": [], "total": 0, "created": 0}
    deps = {d.code.upper(): d for d in Department.objects.all()}
    poss = {p.name.lower(): p for p in Position.objects.all()}
    shifts = {s.name.lower(): s for s in Shift.objects.all()}
    db_niks = set(Employee.all_objects.values_list("nik", flat=True)); file_niks = {r.get("nik", "") for r in rows}
    seen, errors, valid = set(), [], []
    for i, r in enumerate(rows, start=2):  # baris 1 = header
        errs = []
        for k, v in r.items():
            if v[:1] in ("=", "@"): errs.append(f"Kolom {k}: tidak boleh diawali '{v[:1]}' (risiko formula spreadsheet).")
        d, p, s = deps.get(r.get("department_code", "").upper()), poss.get(r.get("position", "").lower()), shifts.get(r.get("shift", "").lower())
        if not d: errs.append(f"Departemen dengan kode '{r.get('department_code', '')}' tidak ada.")
        if r.get("position") and not p: errs.append(f"Jabatan '{r['position']}' tidak ada (buat dulu di Master).")
        if r.get("shift") and not s: errs.append(f"Shift '{r['shift']}' tidak ada (buat dulu di Master).")
        nik = r.get("nik", "")
        if nik in seen: errs.append("NIK dobel di dalam file.")
        seen.add(nik)
        sup = r.get("supervisor_nik", "")
        if sup and (sup == nik or (sup not in db_niks and sup not in file_niks)): errs.append("Atasan tidak ditemukan / sama dengan karyawan ini.")
        if not errs:
            form = EmployeeForm({**{c: r.get(c, "") for c in COLUMNS if c not in ("department_code", "position", "shift", "supervisor_nik")},
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
