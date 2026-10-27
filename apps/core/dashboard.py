from datetime import date, timedelta
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from .models import Role, User, AuditLog, Notification
from .scope import require_roles

@require_roles(Role.HRD, Role.DEPT_ADMIN, Role.POLI)
def dashboard(request):
    from apps.hr.models import Employee, ChangeRequest
    from apps.poli.models import MedicalRecord, Medicine, Referral
    u, today = request.user, date.today()
    notif = Notification.objects.filter(user=u, is_read=False).count()
    if u.role == Role.SUPERADMIN or u.role == Role.HRD:
        from apps.hr.views import _contract_filter  # satu sumber dengan filter "Kontrak" di daftar Karyawan (klik kartu → daftar yang sama, jumlah baris = angka kartu)
        act = Employee.objects.filter(status="aktif")
        d = {"employees": Employee.objects.count(), "active": act.count(),
             "contract_expired": _contract_filter(act, "expired").count(),
             "contract_le_7": _contract_filter(act, "le7").count(), "contract_le_30": _contract_filter(act, "expiring").count(), "contract_le_60": _contract_filter(act, "le60").count(),
             "pending_requests": ChangeRequest.objects.filter(status="pending").count()}
        from apps.hr.models import StaffOrder
        d.update(approved_unexecuted=ChangeRequest.objects.filter(status="approved").count(),   # sudah disetujui tetapi belum "Laksanakan" → belum masuk absensi/saldo/jadwal
                 orders_pending=StaffOrder.objects.filter(status="menunggu").count())            # Order Karyawan dari departemen yang menunggu jawaban HRD
        from apps.hrd.models import BpjsMembership, MaternityLeave, WarningLetter
        d.update(maternity_active=MaternityLeave.objects.filter(state="aktif", start_date__lte=today, end_date__gte=today).values("employee").distinct().count(),
                 warnings_active=WarningLetter.objects.filter(revoked_at__isnull=True, issue_date__lte=today, valid_until__gte=today).count(),
                 bpjs_inactive=Employee.objects.filter(status="aktif", bpjs_memberships__status="nonaktif").distinct().count())  # per KARYAWAN (bukan per program) = baris daftar BPJS ?bpjs=nonaktif
        from apps.hrd.models import Separation  # putaran 38d: tugas Karyawan Keluar (hanya angka; yang bisa ditindaklanjuti sekarang)
        sep = Separation.objects.filter(voided_at__isnull=True)
        d.update(sep_paklaring=sep.filter(applied_at__isnull=False, paklaring_number="").count(),                                  # sudah keluar, paklaring belum terbit
                 sep_tali=sep.filter(tali_asih__gt=0, tali_asih_paid_on__isnull=True).count(),                                      # tali asih ditetapkan, belum dibayar
                 sep_soon=sep.filter(applied_at__isnull=True, last_date__gte=today, last_date__lte=today + timedelta(days=7)).count())  # hari terakhir kerja dalam 7 hari
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
             "open_referrals": Referral.objects.filter(status__in=("diajukan", "dirujuk")).count(),
             "low_stock": Medicine.objects.filter(stock__lte=F("min_stock")).count()}
        if u.department_id:  # putaran 48/49: akun Poli = admin departemen Poli → tugas pengajuan & validasi kehadiran departemennya (hanya angka)
            from apps.hr.models import AttendanceCheck as AC
            d["dept_req_pending"] = ChangeRequest.objects.filter(department=u.department_id, status__in=("submitted", "pending")).count()  # link: /requests/?status=open
            d["attcheck_todo"] = AC.objects.filter(department=u.department_id, status__in=("diminta", "dikembalikan")).count()
    if u.role in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN):  # putaran 27: tugas P7 Validasi kehadiran (hanya angka; scope departemen untuk Admin)
        from apps.hr.models import AttendanceCheck as AC
        from .scope import scope_by_department
        q = scope_by_department(u, AC.objects.all()); open_ = q.filter(status__in=("diminta", "dikembalikan"))
        if u.role == Role.DEPT_ADMIN: d["attcheck_todo"] = open_.count()
        else: d["attcheck_to_verify"] = q.filter(status="dijawab").count()
        d["attcheck_late"] = open_.filter(due_date__lt=today).count()
        from apps.hr.models import AttendanceDaily as AD, AttendanceRaw
        from django.db.models import Q
        rv = scope_by_department(u, AD.objects.filter(date__gte=today - timedelta(days=7))).filter(Q(status="tanpa_scan") | Q(incomplete=True) | ~Q(conflict="")).count()  # Tahap 6
        if rv: d["att_review"] = rv
        if u.role != Role.DEPT_ADMIN:
            n = AttendanceRaw.objects.filter(employee__isnull=True).values("pin").distinct().count()
            if n: d["att_unmapped"] = n
    return JsonResponse({"role": u.role, "unread_notifications": notif, **d})

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
@login_required
def home(request):
    if request.user.role == Role.PAYROLL: return redirect("/payroll/")   # putaran 57: beranda akun Payroll = ringkasan Payroll
    if request.user.role not in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN, Role.POLI):
        return redirect("/admin/")
    return render(request, "home.html")
