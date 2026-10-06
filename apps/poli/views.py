import json
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.core.audit import log
from apps.core.models import Role, Notification, User
from apps.core.scope import require_roles
from .models import MedicalRecord, Prescription, dispense

@require_POST
@require_roles(Role.POLI)  # superadmin lolos otomatis; HRD & Admin Dept DITOLAK
def record_create(request):
    d = json.loads(request.body)
    try:
        with transaction.atomic():
            r = MedicalRecord.objects.create(employee_id=d["employee_id"], kind=d["kind"], visit_at=timezone.now(),
                complaint=d.get("complaint", ""), exam=d.get("exam", {}), diagnosis_id=d.get("diagnosis_id"), created_by=request.user)
            for p in d.get("prescriptions", []):
                m = dispense(p["medicine_id"], p["qty"], request.user, ref=f"MR{r.pk}")
                Prescription.objects.create(record=r, medicine=m, qty=p["qty"], dosage=p.get("dosage", ""))
                if m.stock <= m.min_stock:
                    Notification.objects.bulk_create([Notification(user=u, kind="stock", title=f"Stok minimum: {m.name}") for u in User.objects.filter(role=Role.POLI)])
    except ValueError as e:
        return JsonResponse({"detail": str(e)}, status=400)
    log(request, "poli", "create_record", r)
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
