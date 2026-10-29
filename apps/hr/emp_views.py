"""HR Core UI (Tahap 2): karyawan (CRUD + soft delete + riwayat), kontrak, master. Penulisan: HRD/Superadmin saja."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles, scope_by_department
from .emp_forms import MASTERS, STATUSES, ContractForm, EmployeeForm
from .views import BULK_MAX
from datetime import date, timedelta
from django.utils import timezone
from . import leave, schedule
from .models import ChangeRequest, Contract, Employee, EmployeeHistory, MedicalCheckup, ShiftAssignment

TRACKED = ("department", "position", "status", "shift", "shift_group", "pay_basis", "off_weekday", "gsn_week", "grade", "grade_tier")  # perubahan field ini selalu masuk EmployeeHistory
ACTIVE_REQ = ("submitted", "pending", "approved")
HRD_ROLES = (Role.HRD,)  # Superadmin otomatis lolos di require_roles


def _snap(e):
    s = {f: ("" if getattr(e, f) is None else str(getattr(e, f))) for f in EmployeeForm.Meta.fields}
    s["supervisor"] = e.supervisor.nik if e.supervisor_id else ""
    return s


def _audit_diff(before, after):
    """Diff untuk audit; nilai kolom sensitif TIDAK PERNAH disimpan di audit, hanya penanda 'diubah'."""
    b, a = {}, {}
    for k in after:
        if before.get(k) == after[k]: continue
        if k in Employee.SENSITIVE: b[k], a[k] = "***", "*** (diubah)"
        else: b[k], a[k] = before.get(k), after[k]
    return b, a


def _shift_patterns():
    """{id kelompok: pola} untuk form karyawan: JS menyembunyikan kolom khusus GS bila Shift = kelompok rotasi (A7…G7, A7Pack…G7Pack)."""
    from .models import ShiftGroup
    return {str(pk): pat for pk, pat in ShiftGroup.objects.values_list("pk", "pattern")}


def _rows(user, e):
    """Field yang tampil ditentukan di sini per role (bukan di template) agar tidak ada kebocoran tak sengaja."""
    rows = [("NIK", e.nik), ("Nama", e.name), ("Jenis kelamin", e.get_gender_display()),
            ("Departemen", e.department.name), ("Jabatan", e.position.name if e.position else "-")]
    if user.role == Role.POLI:   # info pelayanan Poli: identitas + Shift, atasan, alamat, NIK KTP, BPJS K/TK (nomor + status); TANPA gaji/grade/rekening/NPWP. Akses tercatat di audit (view_sensitive).
        from apps.poli.services import patient_overview
        rows += [("Shift", e.shift_label), ("Atasan", f"{e.supervisor.nik} · {e.supervisor.name}" if e.supervisor_id else "-"), ("Alamat", e.address or "-"), ("NIK KTP", e.nik_ktp or "-")]
        nums = {"kes": e.bpjs_kes, "tk": e.bpjs_tk}
        return rows + [(b["label"], f"{nums[k] or 'nomor kosong'} · {b['status']}") for k, b in patient_overview(e)["bpjs"].items()]
    rows += [("Status", e.status), ("Status karyawan", e.get_pay_basis_display()), ("Tanggal masuk", e.join_date), ("Shift", e.shift_label),
             ("Atasan", f"{e.supervisor.nik} · {e.supervisor.name}" if e.supervisor_id else "-")]
    if e.gsn_pattern:   # GS/N: pola mingguan Senin…Minggu
        names = dict(Employee.GSN_CODES)
        rows.append(("Pola GS/N", "\n".join(f"{n}: {names[c].split(' (')[0]}" for n, c in zip(("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"), e.gsn_pattern))))
    elif not e.is_rotation and e.shift_group_id or (e.shift_id and e.shift.is_gs):  # GS: libur masing-masing orang
        rows.append(("Hari libur", e.get_off_weekday_display() if e.off_weekday is not None else "Minggu (libur umum)"))
    if user.role == Role.DEPT_ADMIN: return rows  # tanpa data sensitif
    rows.append(("Grade", f"{e.grade} {e.grade_tier}".strip() if e.grade is not None else "-"))
    return rows + [("Status pernikahan", e.marital_status or "-"), ("Pendidikan", e.education or "-"), ("Alamat", e.address or "-"),
                   ("Tanggal lahir", f"{e.birth_date:%d-%m-%Y} ({e.age()} tahun)" if e.birth_date else "-"), ("Telepon", e.phone or "-"), ("NIK KTP", e.nik_ktp or "-"), ("BPJS Kesehatan", e.bpjs_kes or "-"),
                   ("BPJS Ketenagakerjaan", e.bpjs_tk or "-"), ("NPWP", e.npwp or "-"), ("Bank", e.bank_name or "-"), ("No. rekening", e.bank_account or "-")]


@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employee_detail_page(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "position", "shift", "shift_group", "supervisor"), pk)
    full = request.user.role in (Role.HRD, Role.SUPERADMIN)
    if full or request.user.role == Role.POLI: log(request, "hr", "view_sensitive", e)
    ctx = {"e": e, "rows": _rows(request.user, e), "full": full}
    if request.user.role != Role.POLI:  # saldo cuti & jadwal bukan data sensitif; Admin Dept hanya lewat get_scoped_or_404 di atas
        today = timezone.localdate()
        ctx["leave_year"] = today.year
        ctx["leave_balance"], ctx["leave_available"] = leave.balance(e, today.year), leave.available(e, today.year)
        ctx["schedule_days"] = schedule.schedule_range(e, today, 14)  # jadwal efektif 14 hari ke depan (rotasi/shift tetap + penyesuaian tukar)
        from . import attendance_calc as _calc
        from .models import AttendanceDaily as _AD
        ctx["att"] = (_calc.summary(_AD.objects.filter(employee=e, date__year=today.year, date__month=today.month)) or [None])[0]  # rekap absensi bulan ini (Tahap 6)
        ctx["schedule_rows"] = ShiftAssignment.objects.filter(employee=e, date__gte=today - timedelta(days=7)).select_related("shift").order_by("date")[:30]
    if full or request.user.role == Role.POLI:  # MCU: HRD menginput, Poli hanya melihat (Admin Dept tidak melihat)
        ctx["mcu"] = MedicalCheckup.objects.filter(employee=e).first(); ctx["show_mcu"] = True
    if request.user.role in (Role.POLI, Role.SUPERADMIN):  # riwayat Poli hanya untuk Poli (Superadmin lolos seperti di /poli/); akses tercatat, isi medis tidak masuk audit
        from apps.poli.pages import medical_summary
        log(request, "poli", "view_history", e); ctx["medical"] = medical_summary(e)
    if full:
        from apps.hrd.models import Separation  # impor di dalam fungsi: apps.hrd.models sudah mengimpor apps.hr.models
        ctx["separation"] = Separation.objects.filter(employee=e, voided_at__isnull=True).first()  # catatan Karyawan Keluar aktif (putaran 38c): tautan dari detail karyawan
        ctx["history"] = e.history.order_by("-effective_date", "-id")[:50]
        ctx["contracts"] = e.contracts.order_by("-start")
        ctx["documents"] = e.documents.filter(deleted_at__isnull=True)
    return render(request, "employee_detail.html", ctx)


@login_required
@require_roles(*HRD_ROLES)
def employee_new(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            e = form.save()
            b, a = _audit_diff({}, _snap(e))
            log(request, "hr", "employee_create", e, None, a)
        messages.success(request, f"Karyawan {e.nik} dibuat.")
        return redirect("employee_detail_page", pk=e.pk)
    return render(request, "employee_form.html", {"form": form, "title": "Karyawan baru", "shift_patterns": _shift_patterns()})


@login_required
@require_roles(*HRD_ROLES)
def employee_edit(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "position", "shift", "shift_group", "supervisor"), pk)
    before = _snap(e)  # ambil SEBELUM form dibuat: ModelForm mengubah instance saat validasi
    form = EmployeeForm(request.POST or None, instance=e)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            e = form.save(); after = _snap(e)
            eff = form.cleaned_data["effective_date"]
            for f in TRACKED:  # histori: tidak menimpa data lama
                if before[f] != after[f]:
                    EmployeeHistory.objects.create(employee=e, field=f, old_value=before[f], new_value=after[f], effective_date=eff, changed_by=request.user)
            b, a = _audit_diff(before, after)
            if a: log(request, "hr", "employee_update", e, b, a)
        messages.success(request, "Perubahan disimpan." if a else "Tidak ada perubahan.")
        return redirect("employee_detail_page", pk=e.pk)
    return render(request, "employee_form.html", {"form": form, "title": f"Ubah {e.nik}", "e": e, "shift_patterns": _shift_patterns()})


@login_required
@require_roles(*HRD_ROLES)
def employee_delete(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    blockers = ChangeRequest.objects.filter(employee=e, status__in=ACTIVE_REQ).count()
    err = ""
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if blockers: err = f"Masih ada {blockers} pengajuan yang berjalan. Selesaikan/tolak dulu."
        elif not reason: err = "Alasan wajib diisi."
        else:
            with transaction.atomic():
                e.soft_delete(request.user, reason)
                log(request, "hr", "employee_delete", e, {"deleted": False}, {"deleted": True, "reason": reason[:300]})
            messages.success(request, f"{e.nik} dihapus (soft delete; dapat dipulihkan Superadmin).")
            return redirect("/employees/")
    return render(request, "employee_delete.html", {"e": e, "blockers": blockers, "err": err})


# ---- Aksi massal (checkbox di daftar karyawan): hapus (soft delete) & ubah status. Dua langkah: pratinjau + konfirmasi, lalu eksekusi. ----
BULK_ACTIONS = {"delete": "Hapus karyawan", "status": "Ubah status karyawan"}
BULK_PREVIEW = 100  # baris pratinjau di halaman konfirmasi (sisanya hanya dihitung)
BULK_SKIP_NIKS = 10  # NIK yang disebut di pesan "dilewati"


def _chunks(seq, n=500):
    """Potong daftar agar query `IN (...)` aman di SQLite maupun PostgreSQL."""
    for i in range(0, len(seq), n): yield seq[i:i + n]


def _parse_ids(raw):
    """'3,1,3,x' -> [3, 1]: unik, urutan dipertahankan, token bukan angka ASCII dibuang."""
    out, seen = [], set()
    for t in (raw or "").split(","):
        t = t.strip()
        if t.isascii() and t.isdigit() and int(t) not in seen: seen.add(int(t)); out.append(int(t))
    return out


def _bulk_load(user, ids, lock=False):
    """Karyawan terpilih (hanya yang masih ada & terjangkau scope) + himpunan id yang masih punya pengajuan berjalan."""
    emps = []
    for part in _chunks(ids):
        qs = scope_by_department(user, Employee.objects.select_related("department", "position")).filter(pk__in=part)
        emps += list(qs.select_for_update(of=("self",)) if lock else qs)
    emps.sort(key=lambda e: (e.name.lower(), e.pk))
    busy = set()
    for part in _chunks([e.pk for e in emps]):
        busy.update(ChangeRequest.objects.filter(employee_id__in=part, status__in=ACTIVE_REQ).values_list("employee_id", flat=True))
    return emps, busy


def _niks(emps):
    """'A1, A2, … (+N lagi)' untuk pesan ringkas."""
    s = ", ".join(e.nik for e in emps[:BULK_SKIP_NIKS])
    return s + (f", … (+{len(emps) - BULK_SKIP_NIKS} lagi)" if len(emps) > BULK_SKIP_NIKS else "")


@login_required
@require_roles(*HRD_ROLES)
def employee_bulk(request):
    """POST saja. Tanpa `confirm`: tampilkan pratinjau (siapa yang kena, siapa yang dilewati) + isian alasan/status. Dengan `confirm`: validasi ulang di server
    lalu eksekusi dalam satu transaksi; yang tidak memenuhi syarat DILEWATI (dilaporkan), bukan menggagalkan seluruh aksi. Hasil per karyawan masuk audit + riwayat."""
    if request.method != "POST": return redirect("/employees/")
    action = request.POST.get("action", "")
    ids = _parse_ids(request.POST.get("ids"))
    if action not in BULK_ACTIONS: messages.error(request, "Aksi massal tidak dikenal."); return redirect("/employees/")
    if not ids: messages.error(request, "Pilih minimal satu karyawan dulu."); return redirect("/employees/")
    if len(ids) > BULK_MAX: messages.error(request, f"Terlalu banyak karyawan terpilih ({len(ids)}); maksimal {BULK_MAX} per aksi. Persempit dengan filter."); return redirect("/employees/")
    reason = request.POST.get("reason", "").strip()
    status = request.POST.get("status", "")
    eff_raw = request.POST.get("effective_date", "")
    err = ""
    if request.POST.get("confirm"):
        eff = date.today()
        if action == "delete" and not reason: err = "Alasan wajib diisi."
        if action == "status":
            if status not in dict(STATUSES): err = "Pilih status tujuan."
            try: eff = date.fromisoformat(eff_raw) if eff_raw else date.today()
            except ValueError: err = "Tanggal efektif tidak valid."
        if not err:
            with transaction.atomic():
                emps, busy = _bulk_load(request.user, ids, lock=True)  # dihitung ulang di dalam transaksi + baris dikunci
                missing = len(ids) - len(emps)
                if action == "delete":
                    targets, skipped = [e for e in emps if e.pk not in busy], [e for e in emps if e.pk in busy]
                    for e in targets:
                        e.soft_delete(request.user, reason)
                        log(request, "hr", "employee_delete", e, {"deleted": False}, {"deleted": True, "reason": reason[:300], "bulk": True})
                    done_msg = f"{len(targets)} karyawan dihapus (soft delete; dapat dipulihkan Superadmin)."
                    skip_msg = f"{len(skipped)} dilewati karena masih ada pengajuan berjalan: {_niks(skipped)}." if skipped else ""
                else:
                    targets, skipped = [e for e in emps if e.status != status], [e for e in emps if e.status == status]
                    for e in targets:
                        old, e.status = e.status, status
                        e.save(update_fields=["status"])
                        EmployeeHistory.objects.create(employee=e, field="status", old_value=old, new_value=status, effective_date=eff, changed_by=request.user)
                        log(request, "hr", "employee_update", e, {"status": old}, {"status": status, "bulk": True})
                    done_msg = f"Status {len(targets)} karyawan diubah menjadi {status}."
                    skip_msg = f"{len(skipped)} dilewati karena sudah berstatus {status}: {_niks(skipped)}." if skipped else ""
                log(request, "hr", "employee_bulk", None, None, {"action": action, "requested": len(ids), "done": len(targets), "skipped": len(skipped), "missing": missing})
            if targets: messages.success(request, done_msg)
            else: messages.warning(request, "Tidak ada karyawan yang diubah.")
            if skip_msg: messages.warning(request, skip_msg)
            if missing: messages.warning(request, f"{missing} karyawan terpilih sudah tidak ada/sudah dihapus.")
            return redirect("/employees/")
    emps, busy = _bulk_load(request.user, ids)
    ctx = {"action": action, "title": BULK_ACTIONS[action], "ids": ",".join(str(e.pk) for e in emps), "total": len(emps), "missing": len(ids) - len(emps),
           "preview": emps[:BULK_PREVIEW], "more": max(len(emps) - BULK_PREVIEW, 0), "busy": busy, "blocked": len(busy) if action == "delete" else 0,
           "err": err, "reason": reason, "status": status, "statuses": STATUSES, "effective_date": eff_raw or date.today().isoformat()}
    return render(request, "employee_bulk.html", ctx)


@login_required
@require_roles(*HRD_ROLES)
def contract_new(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    form = ContractForm(request.POST or None, employee=e)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            c = form.save(commit=False); c.employee, c.status = e, "aktif"
            prev = form.cleaned_data["previous"]; c.previous = prev
            c.save()
            if prev:
                prev.status = "diperpanjang"; prev.save(update_fields=["status"])
            EmployeeHistory.objects.create(employee=e, field="contract", old_value=prev.number if prev else "", new_value=c.number,
                                           effective_date=c.start, changed_by=request.user)
            log(request, "hr", "contract_renew" if prev else "contract_create", c, {"previous": prev.number} if prev else None,
                {"number": c.number, "kind": c.kind, "start": str(c.start), "end": str(c.end) if c.end else None})
        messages.success(request, f"Kontrak {c.number} disimpan.")
        return redirect("employee_detail_page", pk=e.pk)
    return render(request, "contract_form.html", {"form": form, "e": e})


def _master(kind):
    if kind not in MASTERS: raise Http404
    return MASTERS[kind]


@login_required
@require_roles(*HRD_ROLES)
def master_list(request, kind):
    label, M, _ = _master(kind)
    ctx = {"kind": kind, "label": label, "items": M.objects.all().order_by("name" if kind != "shift" else "start"), "kinds": {k: v[0] for k, v in MASTERS.items()}}
    if kind == "shift": ctx.update(rotation_context())
    if kind == "department":  # tiap departemen punya admin sendiri: tampilkan admin aktifnya, tandai yang kosong
        from apps.core.user_services import dept_admin_map
        m = dept_admin_map()
        for d in ctx["items"]: d.admin_names = m.get(d.pk, [])
        ctx["no_admin"] = [d for d in ctx["items"] if not d.admin_names]
    return render(request, "master_list.html", ctx)


def rotation_context():
    """Tabel rotasi kelompok (docs/jadwal_shift_2026.md) untuk halaman Master Shift: per pola, kelompok × Senin…Minggu, + peringatan bila jumlah per hari tidak sesuai aturan."""
    from . import rotation_table as rt
    from .models import Shift, ShiftGroup, ShiftRotation
    cells = {}
    for r in ShiftRotation.objects.select_related("group", "shift"): cells.setdefault(r.group.code, [None] * 7)[r.weekday] = (r.shift.code or r.shift.name) if r.shift_id else None
    blocks = []
    for pattern, title, note in ((rt.P2, "Pola 2 shift: kelompok A7–G7", "Pagi 3 kelompok · Siang 3 kelompok · Libur 1 kelompok setiap hari (tanpa Malam)"),
                                 (rt.P3, "Pola 3 shift / PACK: kelompok A7Pack–G7Pack", "Pagi 2 · Siang 2 · Malam 2 · Libur 1 kelompok setiap hari")):
        groups = list(ShiftGroup.objects.filter(pattern=pattern).order_by("code").values_list("code", flat=True))
        table = {g: cells.get(g, [None] * 7) for g in groups}
        names = {g.code: g.label for g in ShiftGroup.objects.filter(pattern=pattern)}
        blocks.append({"title": title, "note": note, "days": rt.DAY_NAMES, "rows": [(names.get(g, g), table[g]) for g in groups],
                       "issues": rt.check(table, pattern) if groups else ["Belum ada tabel rotasi: impor lewat tombol di atas atau jalankan migrasi."]})
    gs = list(Shift.objects.filter(is_gs=True, active=True).order_by("end").values_list("code", "start", "end"))
    return {"rotation_blocks": blocks, "gs_rows": gs, "gs_blocks": gs_blocks()}


def gs_blocks():
    """GS dan GS/N di bagian rotasi: tidak ada pola mingguan bersama — libur tiap karyawan berbeda. Tampilkan jumlah karyawan aktif per hari libur (bantu HRD menjaga sebaran)."""
    from django.db.models import Count
    from . import rotation_table as rt
    from .models import ShiftGroup
    out = []
    for g in ShiftGroup.objects.filter(pattern__in=(ShiftGroup.GS, ShiftGroup.GSN)).order_by("pattern"):
        got = {r["off_weekday"]: r["n"] for r in Employee.objects.filter(shift_group=g, status="aktif").values("off_weekday").annotate(n=Count("pk"))}
        cells = [got.get(i, 0) for i in range(7)]; unset = got.get(None, 0)
        cells[6] += unset   # belum diatur = libur umum (Minggu)
        if g.pattern == ShiftGroup.GSN:   # GS/N: hitung dari pola mingguan (bisa >1 hari libur per orang)
            cells = [0] * 7; unset = 0
            for w, off in Employee.objects.filter(shift_group=g, status="aktif").values_list("gsn_week", "off_weekday"):
                if isinstance(w, list) and len(w) == 7: 
                    for i, c in enumerate(w): cells[i] += c == "LIBUR"
                else: cells[6 if off is None else off] += 1; unset += off is None
        avg = sum(cells) / 7
        warn = [rt.DAY_NAMES[i] for i, n in enumerate(cells) if n > max(2, 2 * avg)]   # hari libur terlalu menumpuk → pabrik kekurangan orang di hari itu
        out.append({"group": g, "cells": cells, "days": rt.DAY_NAMES, "total": sum(got.values()), "unset": unset, "warn": warn,
                    "note": "Berangkat GS 08:00–16:00; pada tanggal tertentu bisa bertugas di shift Pagi/Siang/Malam lewat pengajuan tukar shift. Libur tiap karyawan berbeda." if g.pattern == ShiftGroup.GSN
                    else "Semua masuk 08:00–16:00; hari libur mingguan diatur per karyawan (Senin–Minggu)."})
    return out


@login_required
@require_roles(*HRD_ROLES)
def master_form(request, kind, pk=None):
    label, M, F = _master(kind)
    obj = M.objects.filter(pk=pk).first() if pk else None
    if pk and not obj: raise Http404
    before = {f: str(getattr(obj, f)) for f in F.Meta.fields} if obj else None
    form = F(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            o = form.save()
            log(request, "hr", f"master_{'update' if obj else 'create'}", o, before, {f: str(getattr(o, f)) for f in F.Meta.fields})
        messages.success(request, f"{label} disimpan.")
        return redirect("master_list", kind=kind)
    return render(request, "master_form.html", {"form": form, "kind": kind, "label": label, "obj": obj})


@login_required
@require_roles(*HRD_ROLES)
def master_export(request, kind):
    """Ekspor master (departemen/jabatan/shift). Kolom sama dengan template impor."""
    from apps.core import tabular
    label, M, _ = _master(kind)
    if kind == "department": header = ["code", "name", "parent_code"]; rows = [[d.code, d.name, d.parent.code if d.parent_id else ""] for d in M.objects.select_related("parent").order_by("code")]
    elif kind == "position": header = ["name", "level"]; rows = [[p.name, p.level] for p in M.objects.order_by("name")]
    else: header = ["code", "name", "start", "end", "crosses_midnight", "is_gs", "active"]; rows = [[x.code, x.name, x.start, x.end, x.crosses_midnight, x.is_gs, x.active] for x in M.objects.order_by("start", "code")]
    log(request, "hr", "master_export", None, None, {"kind": kind, "rows": len(rows)})
    return tabular.export_response(f"master-{kind}", header, rows, request, sheet=kind)


@login_required
@require_roles(*HRD_ROLES)
def rotation_edit(request):
    """Ubah tabel rotasi kelompok langsung di halaman (menggantikan impor/ekspor, putaran 20). Satu form berisi seluruh sel (kelompok × Senin…Minggu);
    tiap sel = shift rotasi aktif (non-GS) atau Libur. Pola 2 shift tidak boleh memakai shift yang melewati tengah malam. Simpan dalam satu transaksi;
    penyimpangan dari aturan jumlah per hari (docs/jadwal_shift_2026.md) hanya DIPERINGATKAN (HRD boleh menyimpang sementara), bukan ditolak."""
    from . import rotation_table as rt
    from .models import Shift, ShiftGroup, ShiftRotation
    shifts = list(Shift.objects.filter(active=True, is_gs=False).order_by("start", "code"))
    by_code = {s.code: s for s in shifts if s.code}
    groups = list(ShiftGroup.objects.filter(pattern__in=(rt.P2, rt.P3)).order_by("pattern", "code"))   # GS & GS/N tidak punya tabel rotasi
    cur = {(r.group_id, r.weekday): (r.shift.code if r.shift_id else "") for r in ShiftRotation.objects.select_related("shift")}
    errors, posted = [], {}
    if request.method == "POST":
        if request.POST.get("action") == "reset":  # kembalikan ke tabel resmi
            with transaction.atomic():
                before = {f"{g}|{w}": c for (g, w), c in cur.items()}
                for pattern in (rt.P2, rt.P3):
                    for code, cells in rt.official(pattern).items():
                        g, _ = ShiftGroup.objects.get_or_create(code=code, defaults={"pattern": pattern})
                        for wd, sc in enumerate(cells):
                            sh = by_code.get(sc) if sc else None
                            if sc and not sh: errors.append(f"Shift {sc} tidak ada/nonaktif; sel {code} {rt.DAY_NAMES[wd]} dilewati."); continue
                            ShiftRotation.objects.update_or_create(group=g, weekday=wd, defaults={"shift": sh})
                log(request, "hr", "rotation_reset", None, None, {"groups": len(groups)})
            messages.success(request, "Tabel rotasi dikembalikan ke aturan resmi 2026." + (" Ada sel dilewati." if errors else ""))
            if not errors: return redirect("master_list", kind="shift")
        else:
            changes, new = [], {}
            for g in groups:
                for wd in range(7):
                    key = f"c_{g.pk}_{wd}"; v = (request.POST.get(key) or "").strip(); posted[key] = v
                    sh = None
                    if v:
                        sh = by_code.get(v)
                        if not sh: errors.append(f"{g.code} {rt.DAY_NAMES[wd]}: shift '{v}' tidak valid."); continue
                        if g.pattern == ShiftGroup.P2 and sh.crosses_midnight:
                            errors.append(f"{g.code} {rt.DAY_NAMES[wd]}: pola 2 shift tidak boleh memakai {sh.code} (melewati tengah malam)."); continue
                    new[(g.pk, wd)] = sh
                    if cur.get((g.pk, wd), None) != (sh.code if sh else ""): changes.append((g, wd, sh))
            if not errors:
                with transaction.atomic():
                    for g, wd, sh in changes: ShiftRotation.objects.update_or_create(group=g, weekday=wd, defaults={"shift": sh})
                    log(request, "hr", "rotation_update", None, {f"{g.code}|{rt.DAY_NAMES[wd]}": cur.get((g.pk, wd), "-") or "libur" for g, wd, sh in changes},
                        {f"{g.code}|{rt.DAY_NAMES[wd]}": (sh.code if sh else "libur") for g, wd, sh in changes})
                messages.success(request, f"Tabel rotasi disimpan ({len(changes)} sel berubah)." if changes else "Tidak ada perubahan.")
                return redirect("master_list", kind="shift")
    blocks = []
    for pattern, title in ((ShiftGroup.P2, "Pola 2 shift: kelompok A–G"), (ShiftGroup.P3, "Pola 3 shift / PACK: kelompok A_pack–G_pack")):
        rows = []
        for g in (x for x in groups if x.pattern == pattern):
            rows.append((g, [{"name": f"c_{g.pk}_{wd}", "value": posted.get(f"c_{g.pk}_{wd}", cur.get((g.pk, wd), ""))} for wd in range(7)]))
        blocks.append({"title": title, "rows": rows,
                       "options": [s for s in shifts if pattern == ShiftGroup.P3 or not s.crosses_midnight]})
    return render(request, "rotation_edit.html", {"blocks": blocks, "days": rt.DAY_NAMES, "errors": errors})
