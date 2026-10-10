from datetime import date, timedelta
from django.core.management.base import BaseCommand
from apps.core.models import Notification, User, Role
from apps.hr.models import Contract, REMINDER_DAYS

class Command(BaseCommand):
    help = "Jalankan harian via cron: reminder kontrak 90/60/30/14/7 hari"
    def handle(self, *a, **k):
        hrd = list(User.objects.filter(role=Role.HRD))
        for d in REMINDER_DAYS:
            # hanya karyawan aktif: yang sudah keluar (Karyawan Keluar, putaran 38) tidak perlu diingatkan kontraknya habis
            for c in Contract.objects.filter(status="aktif", employee__status="aktif", end=date.today() + timedelta(days=d)).select_related("employee"):
                Notification.objects.bulk_create([Notification(user=u, kind="contract",
                    title=f"Kontrak {c.employee.name} habis dalam {d} hari") for u in hrd])
