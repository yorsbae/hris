"""Impor karyawan dari CSV atau XLSX. Semua-atau-tidak-sama-sekali: satu baris salah → tidak ada yang tersimpan.
Memakai ulang EmployeeForm agar aturan validasi identik dengan input manual."""
from apps.core import tabular
from django.db import IntegrityError, transaction
from .emp_forms import EmployeeForm, IDENT_FIELDS, identifier_owners, norm_ident
from .models import Department, Employee, Position, Shift, ShiftGroup

COLUMNS = ["nik", "name", "gender", "join_date", "department_code", "position", "shift", "status", "marital_status", "education",
           "shift_group", "gs_short", "address", "phone", "nik_ktp", "bpjs_kes", "bpjs_tk", "npwp", "bank_name", "bank_account", "supervisor_nik", "birth_date",
           "pay_basis", "grade", "grade_tier", "off_day"]
REQUIRED = ["nik", "name", "gender", "join_date", "department_code"]
MAX_ROWS, MAX_BYTES = tabular.MAX_ROWS, tabular.MAX_BYTES
EXAMPLE = ["EMP-0001", "Budi Santoso", "L", "2024-01-15", "PROD", "Staff", "", "aktif", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "bulanan", "9", "A", ""]
DAYS = {n.lower(): i for i, n in enumerate(["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"])}   # kolom off_day (hari libur GS / GS/N)


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
    groups = {}
    for g_ in ShiftGroup.objects.all(): groups[g_.code.lower()] = groups[g_.label.lower().replace(" ", "")] = g_   # kode (A, A_pack) ATAU nama tampilan (A7, A7Pack, GS, GS/N)
    db_niks = set(Employee.all_objects.values_list("nik", flat=True)); file_niks = {r.get("nik", "") for r in rows}
    seen, errors, valid = set(), [], []
    gs_default = Shift.objects.filter(is_gs=True, active=True).order_by("-end", "pk").first()
    owners = identifier_owners(); file_ident = {f: {} for f in IDENT_FIELDS}  # pengenal pribadi yang sudah dipakai: di DB (semua karyawan, termasuk terhapus) dan di baris file sebelumnya
    for i, r in enumerate(rows, start=2):  # baris 1 = header
        errs = []
        for k, v in r.items():
            if v[:1] in ("=", "@"): errs.append(f"Kolom {k}: tidak boleh diawali '{v[:1]}' (risiko formula spreadsheet).")
        d, p, s = deps.get(r.get("department_code", "").upper()), poss.get(r.get("position", "").lower()), shifts.get(r.get("shift", "").lower())
        if not d: errs.append(f"Departemen dengan kode '{r.get('department_code', '')}' tidak ada.")
        if r.get("position") and not p: errs.append(f"Jabatan '{r['position']}' tidak ada (buat dulu di Master).")
        g = groups.get(r.get("shift_group", "").lower().replace(" ", ""))
        if r.get("shift_group") and not g: errs.append(f"Shift '{r['shift_group']}' tidak ada (GS, GS/N, A7–G7, A7Pack–G7Pack).")
        if not g and s and s.is_gs: g = groups.get("gs")           # kolom shift berisi GS-16/GS-14/GS-12 → shift 'GS'
        if r.get("pay_basis") and r["pay_basis"].lower() not in ("harian", "bulanan"): errs.append("pay_basis harus harian atau bulanan.")
        if r.get("off_day") and r["off_day"].lower() not in DAYS: errs.append("off_day harus Senin, Selasa, Rabu, Kamis, Jumat, Sabtu atau Minggu.")
        if r.get("gs_short") and r["gs_short"] not in ("12", "14"): errs.append("gs_short harus 14 atau 12.")
        if r.get("shift") and not s: errs.append(f"Shift '{r['shift']}' tidak ada (buat dulu di Master).")
        nik = r.get("nik", "")
        if nik in seen: errs.append("NIK dobel di dalam file.")
        seen.add(nik)
        sup = r.get("supervisor_nik", "")
        if sup and (sup == nik or (sup not in db_niks and sup not in file_niks)): errs.append("Atasan tidak ditemukan / sama dengan karyawan ini.")
        for f, label in IDENT_FIELDS.items():
            n = norm_ident(r.get(f))
            if n and n in file_ident[f] and file_ident[f][n] != nik: errs.append(f"{f}: {label} sama dengan baris NIK {file_ident[f][n]} di file ini (orang yang sama terdaftar dua kali).")
            elif n: file_ident[f].setdefault(n, nik)
        if not errs:
            form = EmployeeForm({**{c: r.get(c, "") for c in COLUMNS if c not in ("department_code", "position", "shift", "shift_group", "gs_short", "supervisor_nik", "off_day", "pay_basis")},
                                 "shift_choice": g.pk if g else "", "gs_short": r.get("gs_short") or "14", "pay_basis": (r.get("pay_basis") or "bulanan").lower(),
                                 "off_weekday": DAYS[r["off_day"].lower()] if r.get("off_day") else "",
                                 "status": r.get("status") or "aktif", "department": d.pk, "position": p.pk if p else "",
                                 "supervisor_nik": ""})  # atasan diselesaikan sendiri (bisa menunjuk baris lain di file yang sama)
            form.owners = owners; form.gs_shift = gs_default; form.legacy_shift = s if (s and not s.is_gs and not g) else None   # shift tetap non-GS tanpa kelompok (format lama) tetap diterima
            if form.is_valid(): valid.append((form, sup))
            else: errs += [f"{f}: {m}" for f, ms in form.errors.items() for m in ms]
        if errs: errors.append((i, nik, errs))
    res = {"ok": not errors, "errors": errors, "total": len(rows), "created": 0, "fatal": ""}
    if errors or not commit: return res
    try: return _commit(valid, res)
    except IntegrityError:  # NIK yang sama disimpan pihak lain di antara pratinjau dan simpan → tolak semuanya, jangan setengah
        return {**res, "ok": False, "created": 0, "errors": [(0, "", ["Ada NIK yang baru saja terdaftar oleh pengguna lain. Tidak ada yang disimpan; periksa ulang file lalu coba lagi."])]}


def _commit(valid, res):
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
