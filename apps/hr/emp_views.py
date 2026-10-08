"""HR Core UI (Tahap 2): karyawan (CRUD + soft delete + riwayat), kontrak, master. Penulisan: HRD/Superadmin saja."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles
from .emp_forms import MASTERS, ContractForm, EmployeeForm
from datetime import timedelta
from django.utils import timezone
from . import leave, schedule
from .models import ChangeRequest, Contract, Employee, EmployeeHistory, ShiftAssignment

TRACKED = ("department", "position", "status", "shift", "shift_group")  # perubahan field ini selalu masuk EmployeeHistory
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


def _rows(user, e):
    """Field yang tampil ditentukan di sini per role (bukan di template) agar tidak ada kebocoran tak sengaja."""
    rows = [("NIK", e.nik), ("Nama", e.name), ("Jenis kelamin", e.get_gender_display()),
            ("Departemen", e.department.name), ("Jabatan", e.position.name if e.position else "-")]
    if user.role == Role.POLI: return rows  # identitas minimum
    rows += [("Status", e.status), ("Tanggal masuk", e.join_date), ("Shift", e.shift.name if e.shift else "-"), ("Kelompok shift", e.shift_group.code if e.shift_group_id else "-"),
             ("Atasan", f"{e.supervisor.nik} · {e.supervisor.name}" if e.supervisor_id else "-")]
    if user.role == Role.DEPT_ADMIN: return rows  # tanpa data sensitif
    return rows + [("Status pernikahan", e.marital_status or "-"), ("Pendidikan", e.education or "-"), ("Alamat", e.address or "-"),
                   ("Telepon", e.phone or "-"), ("NIK KTP", e.nik_ktp or "-"), ("BPJS Kesehatan", e.bpjs_kes or "-"),
                   ("BPJS Ketenagakerjaan", e.bpjs_tk or "-"), ("NPWP", e.npwp or "-"), ("Bank", e.bank_name or "-"), ("No. rekening", e.bank_account or "-")]


@login_required
@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def employee_detail_page(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.select_related("department", "position", "shift", "shift_group", "supervisor"), pk)
    full = request.user.role in (Role.HRD, Role.SUPERADMIN)
    if full: log(request, "hr", "view_sensitive", e)
    ctx = {"e": e, "rows": _rows(request.user, e), "full": full}
    if request.user.role != Role.POLI:  # saldo cuti & jadwal bukan data sensitif; Admin Dept hanya lewat get_scoped_or_404 di atas
        today = timezone.localdate()
        ctx["leave_year"] = today.year
        ctx["leave_balance"], ctx["leave_available"] = leave.balance(e, today.year), leave.available(e, today.year)
        ctx["schedule_days"] = schedule.schedule_range(e, today, 14)  # jadwal efektif 14 hari ke depan (rotasi/shift tetap + penyesuaian tukar)
        ctx["schedule_rows"] = ShiftAssignment.objects.filter(employee=e, date__gte=today - timedelta(days=7)).select_related("shift").order_by("date")[:30]
    if request.user.role in (Role.POLI, Role.SUPERADMIN):  # riwayat Poli hanya untuk Poli (Superadmin lolos seperti di /poli/); akses tercatat, isi medis tidak masuk audit
        from apps.poli.pages import medical_summary
        log(request, "poli", "view_history", e); ctx["medical"] = medical_summary(e)
    if full:
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
    return render(request, "employee_form.html", {"form": form, "title": "Karyawan baru"})


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
    return render(request, "employee_form.html", {"form": form, "title": f"Ubah {e.nik}", "e": e})


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
    return render(request, "master_list.html", ctx)


def rotation_context():
    """Tabel rotasi kelompok (docs/jadwal_shift_2026.md) untuk halaman Master Shift: per pola, kelompok × Senin…Minggu, + peringatan bila jumlah per hari tidak sesuai aturan."""
    from . import rotation_table as rt
    from .models import Shift, ShiftGroup, ShiftRotation
    cells = {}
    for r in ShiftRotation.objects.select_related("group", "shift"): cells.setdefault(r.group.code, [None] * 7)[r.weekday] = (r.shift.code or r.shift.name) if r.shift_id else None
    blocks = []
    for pattern, title, note in ((rt.P2, "Pola 2 shift — kelompok A–G", "Pagi 3 kelompok · Siang 3 kelompok · Libur 1 kelompok setiap hari (tanpa Malam)"),
                                 (rt.P3, "Pola 3 shift / PACK — kelompok A_pack–G_pack", "Pagi 2 · Siang 2 · Malam 2 · Libur 1 kelompok setiap hari")):
        groups = list(ShiftGroup.objects.filter(pattern=pattern).order_by("code").values_list("code", flat=True))
        table = {g: cells.get(g, [None] * 7) for g in groups}
        blocks.append({"title": title, "note": note, "days": rt.DAY_NAMES, "rows": [(g, table[g]) for g in groups],
                       "issues": rt.check(table, pattern) if groups else ["Belum ada tabel rotasi — impor lewat tombol di atas atau jalankan migrasi."]})
    gs = list(Shift.objects.filter(is_gs=True, active=True).order_by("end").values_list("code", "start", "end"))
    return {"rotation_blocks": blocks, "gs_rows": gs}


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
    """Ekspor master (departemen/jabatan/shift) atau, untuk kind=rotasi, tabel rotasi kelompok shift. Kolom sama dengan template impor."""
    from apps.core import tabular
    from .models import ShiftRotation
    if kind == "rotasi":
        names = ["senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu"]
        header = ["group", "weekday", "shift_code"]
        rows = [[r.group.code, names[r.weekday], r.shift.code if r.shift_id else ""] for r in ShiftRotation.objects.select_related("group", "shift").order_by("group__pattern", "group__code", "weekday")]
    else:
        label, M, _ = _master(kind)
        if kind == "department": header = ["code", "name", "parent_code"]; rows = [[d.code, d.name, d.parent.code if d.parent_id else ""] for d in M.objects.select_related("parent").order_by("code")]
        elif kind == "position": header = ["name", "level"]; rows = [[p.name, p.level] for p in M.objects.order_by("name")]
        else: header = ["code", "name", "start", "end", "crosses_midnight", "is_gs", "active"]; rows = [[x.code, x.name, x.start, x.end, x.crosses_midnight, x.is_gs, x.active] for x in M.objects.order_by("start", "code")]
    log(request, "hr", "master_export", None, None, {"kind": kind, "rows": len(rows)})
    return tabular.export_response(f"master-{kind}", header, rows, request, sheet=kind)
