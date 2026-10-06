"""Dokumen karyawan (KTP, ijazah, dst). HRD/Superadmin saja: dokumen identitas = data sensitif. Unduhan & hapus diaudit."""
import os
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import get_scoped_or_404, require_roles
from .models import Employee, EmployeeDocument

MAX_UPLOAD = 5 * 1024 * 1024
SIGNATURES = {".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
              ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",)}  # ekstensi harus cocok dengan isi file (bukan sekadar nama)


class DocumentForm(forms.Form):
    kind = forms.ChoiceField(choices=EmployeeDocument.KINDS, label="Jenis dokumen")
    title = forms.CharField(max_length=150, required=False, label="Judul (opsional)")
    file = forms.FileField(label="File (PDF/DOCX/XLSX/PNG/JPG, maks 5 MB)")

    def clean_file(self):
        f = self.cleaned_data["file"]; ext = os.path.splitext(f.name)[1].lower()
        if ext not in SIGNATURES: raise forms.ValidationError("Tipe file tidak diizinkan.")
        if f.size > MAX_UPLOAD: raise forms.ValidationError("Ukuran file maksimal 5 MB.")
        if f.size == 0: raise forms.ValidationError("File kosong.")
        head = f.read(8); f.seek(0)
        if not any(head.startswith(sig) for sig in SIGNATURES[ext]): raise forms.ValidationError("Isi file tidak sesuai dengan ekstensinya.")
        return f


def _emp(request, pk): return get_scoped_or_404(request.user, Employee.objects.all(), pk)


def _doc(e, doc_id):
    d = EmployeeDocument.objects.filter(pk=doc_id, employee=e, deleted_at__isnull=True).first()
    if not d: raise Http404
    return d


@login_required
@require_roles(Role.HRD)
def document_new(request, pk):
    e = _emp(request, pk)
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        with transaction.atomic():
            d = EmployeeDocument.objects.create(employee=e, kind=form.cleaned_data["kind"], title=form.cleaned_data["title"].strip() or f.name[:150],
                                                file=f, original_name=f.name[:200], size=f.size, uploaded_by=request.user)
            log(request, "hr", "document_upload", d, None, {"employee": e.nik, "kind": d.kind, "name": d.original_name, "size": d.size})
        messages.success(request, "Dokumen diunggah.")
        return redirect("employee_detail_page", pk=e.pk)
    return render(request, "document_form.html", {"form": form, "e": e})


@login_required
@require_roles(Role.HRD)
def document_download(request, pk, doc_id):
    e = _emp(request, pk); d = _doc(e, doc_id)
    log(request, "hr", "document_download", d, None, {"employee": e.nik, "kind": d.kind})  # akses dokumen identitas selalu tercatat
    return FileResponse(d.file.open("rb"), as_attachment=True, filename=d.original_name)


@require_POST
@login_required
@require_roles(Role.HRD)
def document_delete(request, pk, doc_id):
    e = _emp(request, pk); d = _doc(e, doc_id)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Alasan penghapusan dokumen wajib diisi."); return redirect("employee_detail_page", pk=e.pk)
    from django.utils import timezone
    with transaction.atomic():
        d.deleted_at, d.deleted_by, d.delete_reason = timezone.now(), request.user, reason[:300]
        d.save(update_fields=["deleted_at", "deleted_by", "delete_reason"])  # file di disk TIDAK dihapus (retensi/backup)
        log(request, "hr", "document_delete", d, {"deleted": False}, {"deleted": True, "reason": reason[:300]})
    messages.success(request, "Dokumen dihapus (soft delete).")
    return redirect("employee_detail_page", pk=e.pk)
