from datetime import date, timedelta
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from .models import Role, User, AuditLog, Notification
from .scope import require_roles

@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def dashboard(request):
    from apps.hr.models import Employee, Contract, ChangeRequest
    from apps.poli.models import MedicalRecord, Medicine, Referral
    u, today = request.user, date.today()
    notif = Notification.objects.filter(user=u, is_read=False).count()
    if u.role == Role.SUPERADMIN or u.role == Role.HRD:
        c = Contract.objects.filter(status="aktif")
        d = {"employees": Employee.objects.count(), "active": Employee.objects.filter(status="aktif").count(),
             "contract_expired": c.filter(end__lt=today).count(),
             **{f"contract_le_{n}": c.filter(end__gte=today, end__lte=today + timedelta(days=n)).count() for n in (7, 30, 60)},
             "pending_requests": ChangeRequest.objects.filter(status="pending").count()}
        from apps.hrd.models import BpjsMembership, MaternityLeave, WarningLetter
        d.update(maternity_active=MaternityLeave.objects.filter(state="aktif", start_date__lte=today, end_date__gte=today).count(),
                 warnings_active=WarningLetter.objects.filter(revoked_at__isnull=True, issue_date__lte=today, valid_until__gte=today).count(),
                 bpjs_inactive=BpjsMembership.objects.filter(status="nonaktif", employee__status="aktif", employee__deleted_at__isnull=True).count())
        if u.role == Role.SUPERADMIN:
            d.update(visits_today=MedicalRecord.objects.filter(visit_at__date=today).count(),  # agregat saja (tanpa nama pasien)
                     users=User.objects.filter(is_active=True).count(),
                     audit_24h=AuditLog.objects.filter(created_at__gte=timezone.now() - timedelta(days=1)).count())
    elif u.role == Role.DEPT_ADMIN:
        from django.db.models import Count
        by = ChangeRequest.objects.filter(department=u.department_id).values("status").annotate(n=Count("id"))
        d = {"employees": Employee.objects.filter(department=u.department_id, status="aktif").count(),
             "my_requests": {r["status"]: r["n"] for r in by}}
    else:  # Poli
        d = {"visits_today": MedicalRecord.objects.filter(visit_at__date=today).count(),
             "accidents_month": MedicalRecord.objects.filter(kind="kecelakaan_kerja", visit_at__year=today.year, visit_at__month=today.month).count(),
             "open_referrals": Referral.objects.exclude(status__in=("selesai", "batal")).count(),
             "low_stock": Medicine.objects.filter(stock__lte=F("min_stock")).count()}
    return JsonResponse({"role": u.role, "unread_notifications": notif, **d})

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
@login_required
def home(request):
    if request.user.role not in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN, Role.POLI):
        return redirect("/admin/")
    return render(request, "home.html")
