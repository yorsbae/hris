"""MCU karyawan baru (satu data per karyawan, tidak berulang). HRD/Superadmin menginput & mengedit (di info karyawan); Poliklinik hanya melihat (daftar /poli/mcu/ + unduh berkas). Admin Departemen: tidak ada akses (403).
Audit mencatat siapa/kapan/jenis, TIDAK mencatat kesimpulan atau catatan (isi medis tidak masuk audit)."""
import os
from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles
from .doc_views import MAX_UPLOAD, SIGNATURES
from .models import Employee, MedicalCheckup

MCU_EXT = (".pdf", ".png", ".jpg", ".jpeg")


class CheckupForm(forms.ModelForm):
    class Meta:
        model = MedicalCheckup
        fields = ["checkup_date", "provider", "result", "notes", "file"]
        widgets = {"checkup_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), "notes": forms.Textarea(attrs={"rows": 3, "maxlength": 500})}

    def clean_checkup_date(self):
        d = self.cleaned_data["checkup_date"]
        if d > date.today(): raise forms.ValidationError("Tanggal MCU tidak boleh di masa depan.")
        return d

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f or not hasattr(f, "name") or not hasattr(f, "read") or getattr(f, "_committed", False): return f   # tidak ada file baru
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in MCU_EXT: raise forms.ValidationError("Berkas harus PDF, JPG, atau PNG.")
        if f.size > MAX_UPLOAD: raise forms.ValidationError("Ukuran file maksimal 5 MB.")
        head = f.read(8); f.seek(0)
        if not any(head.startswith(sig) for sig in SIGNATURES[ext]): raise forms.ValidationError("Isi file tidak sesuai dengan ekstensinya.")
        return f


@login_required
@require_roles(Role.HRD)
def mcu_edit(request, pk):
    """Input MCU (bila belum ada) atau ubah MCU yang sudah ada; satu data per karyawan."""
    e = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    inst = MedicalCheckup.objects.filter(employee=e).first()
    form = CheckupForm(request.POST or None, request.FILES or None, instance=inst)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            m = form.save(commit=False); m.employee = e
            if inst is None: m.created_by = request.user
            m.updated_by = request.user
            f = request.FILES.get("file")
            if f: m.original_name, m.size = f.name[:200], f.size
            m.save()
            log(request, "hr", "mcu_update" if inst else "mcu_create", m, None, {"employee": e.nik, "date": str(m.checkup_date)})  # kesimpulan & catatan tidak masuk audit
        messages.success(request, "MCU disimpan. Poliklinik dapat melihatnya.")
        return redirect(f"/employees/{e.pk}/#mcu")
    return render(request, "mcu_form.html", {"form": form, "e": e, "editing": inst is not None})


@login_required
@require_roles(Role.HRD, Role.POLI)
def mcu_file(request, pk):
    e = get_scoped_or_404(request.user, Employee.objects.all(), pk)
    m = MedicalCheckup.objects.filter(employee=e).exclude(file="").first()
    if not m: raise Http404
    log(request, "hr", "mcu_download", m, None, {"employee": e.nik})
    return FileResponse(m.file.open("rb"), as_attachment=True, filename=m.original_name or "mcu")


@login_required
@require_roles(Role.POLI)
def mcu_list(request):
    """Daftar MCU karyawan baru untuk Poliklinik (hanya lihat)."""
    g = request.GET; f = {k: g.get(k, "").strip() for k in ("q", "result")}
    qs = MedicalCheckup.objects.filter(employee__deleted_at__isnull=True).select_related("employee", "employee__department")
    if f["q"]: qs = qs.filter(Q(employee__name__icontains=f["q"]) | Q(employee__nik__startswith=f["q"]))
    if f["result"] in dict(MedicalCheckup.RESULTS): qs = qs.filter(result=f["result"])
    page = Paginator(qs.order_by("-checkup_date", "-id"), 50).get_page(g.get("page", 1))
    log(request, "poli", "view_mcu_list", None, None, {"rows": page.paginator.count})
    qd = g.copy(); qd.pop("page", None)
    return render(request, "poli/mcu_list.html", {"page": page, "f": f, "results": MedicalCheckup.RESULTS, "qs": qd.urlencode()})
