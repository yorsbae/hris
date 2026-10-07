import json
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role
from apps.core.scope import require_roles
from . import services
from .models import Diagnosis

@require_POST
@require_roles(Role.POLI)  # superadmin lolos otomatis; HRD & Admin Dept DITOLAK
def record_create(request):
    """API JSON (dipertahankan). Aturan sama dengan halaman /poli/records/new/ lewat services.create_record."""
    try:
        d = json.loads(request.body)
        if not isinstance(d, dict): raise ValueError("Format tidak valid")
        emp = services.active_employee(pk=d.get("employee_id"))
        diag = None
        if d.get("diagnosis_id") is not None:
            diag = Diagnosis.objects.filter(pk=d["diagnosis_id"]).first()
            if diag is None: raise ValueError("Diagnosa tidak ditemukan")
        lines = []
        for p in d.get("prescriptions", []):
            q = p.get("qty")
            if not isinstance(q, int) or isinstance(q, bool): raise ValueError("Jumlah obat harus bilangan bulat")
            lines.append((p.get("medicine_id"), q, p.get("dosage", "")))
        exam = d.get("exam", {})
        if not isinstance(exam, dict): raise ValueError("exam harus objek")
        r, _ = services.create_record(request.user, emp, d.get("kind"), d.get("complaint", ""), exam, diag, d.get("treatment", ""), lines)
    except (ValueError, TypeError, KeyError, AttributeError) as e:  # json.JSONDecodeError adalah ValueError
        return JsonResponse({"detail": str(e)}, status=400)
    log(request, "poli", "create_record", r, None, {"kind": r.kind, "diagnosis": r.diagnosis.code if r.diagnosis_id else None, "prescriptions": r.prescriptions.count()})
    return JsonResponse({"id": r.pk}, status=201)

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone as tz
from .models import MedicalRecord, SickLeaveLetter, LetterCounter
from .pdf import sick_leave_pdf

@require_roles(Role.POLI)
def letter_pdf(request, record_id):
    """Buat (sekali per rekam medis) lalu kirim PDF surat izin pulang; pencetakan diaudit."""
    rec = get_object_or_404(MedicalRecord.objects.select_related("employee__department", "created_by"), pk=record_id)
    with transaction.atomic():
        letter = SickLeaveLetter.objects.filter(record=rec).first() or SickLeaveLetter.objects.create(
            record=rec, number=LetterCounter.next_number("sip", tz.now()))
    log(request, "poli", "print_letter", letter)
    resp = HttpResponse(sick_leave_pdf(letter), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{letter.number.replace("/", "-")}.pdf"'
    return resp
