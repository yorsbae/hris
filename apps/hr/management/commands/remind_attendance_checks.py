"""Jalankan harian via cron: pengingat validasi kehadiran (putaran 27). Ke Admin Departemen saat batas = besok atau sudah lewat;
ke HRD (pembuat permintaan) saat terlambat. Satu notifikasi belum-dibaca per penerima per jenis → tidak menumpuk bila dijalankan berulang."""
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from apps.core.models import Notification, Role, User
from apps.hr.models import AttendanceCheck as AC


def _once(user, title):
    """Buat bila belum ada notifikasi belum-dibaca dengan judul yang sama untuk penerima ini. True bila dibuat."""
    if Notification.objects.filter(user=user, kind="absensi", title=title, is_read=False).exists(): return False
    Notification.objects.create(user=user, kind="absensi", title=title, link="/validasi/"); return True


def run(today=None):
    today = today or date.today(); sent = 0
    open_ = AC.objects.filter(status__in=("diminta", "dikembalikan"))
    for dept_id in set(open_.filter(due_date__lte=today + timedelta(days=1)).values_list("department_id", flat=True)):
        soon = open_.filter(department_id=dept_id, due_date=today + timedelta(days=1)).count()
        late = open_.filter(department_id=dept_id, due_date__lt=today).count()
        for u in User.objects.filter(role=Role.DEPT_ADMIN, department_id=dept_id, is_active=True):
            if late: sent += _once(u, f"Validasi kehadiran: {late} permintaan TERLAMBAT dijawab")
            elif soon: sent += _once(u, f"Validasi kehadiran: {soon} permintaan jatuh tempo besok")
    late_by = {}
    for r in open_.filter(due_date__lt=today).values_list("requested_by_id", flat=True): late_by[r] = late_by.get(r, 0) + 1
    for uid, n in late_by.items():
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u: sent += _once(u, f"Validasi kehadiran: {n} permintaan Anda belum dijawab Admin (terlambat)")
    return sent


class Command(BaseCommand):
    help = "Pengingat validasi kehadiran (jalankan harian via cron)"
    def handle(self, *a, **k): self.stdout.write(f"{run()} notifikasi dikirim")
