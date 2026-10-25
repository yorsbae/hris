from datetime import date, timedelta
from apps.core import notify
from django.core.management.base import BaseCommand
from apps.core.models import Notification, User, Role
from apps.hr.models import Contract, REMINDER_DAYS

class Command(BaseCommand):
    help = "Jalankan harian via cron: reminder kontrak 90/60/30/14/7 hari"
    def handle(self, *a, **k):
        for d in REMINDER_DAYS:
            # hanya karyawan aktif: yang sudah keluar (Karyawan Keluar, putaran 38) tidak perlu diingatkan kontraknya habis
            for c in Contract.objects.filter(status="aktif", employee__status="aktif", end=date.today() + timedelta(days=d)).select_related("employee"):
                notify.send((Role.HRD,), "contract", f"Kontrak {c.employee.name} habis dalam {d} hari", f"/employees/{c.employee_id}/", skip_if_unread_title=f"Kontrak {c.employee.name} habis dalam {d} hari")
