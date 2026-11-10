from datetime import date, timedelta
from django.core.management.base import BaseCommand
from apps.core import notify
from apps.core.models import Role
from apps.it.models import Asset, Domain, License

REMINDER_DAYS = (60, 30, 14, 7, 1)


class Command(BaseCommand):
    help = "Jalankan harian via cron: pengingat garansi aset, lisensi software, domain/SSL (60/30/14/7/1 hari) ke akun IT"
    def handle(self, *a, **k):
        n = 0
        for d in REMINDER_DAYS:
            t = date.today() + timedelta(days=d)
            items = [(f"Garansi {x.tag}", f"/it/assets/{x.pk}/") for x in Asset.objects.exclude(status="dihapus").filter(warranty_until=t)]
            items += [(f"Lisensi {x.name}", f"/it/licenses/{x.pk}/") for x in License.objects.filter(expires=t)]
            items += [(f"{x.get_kind_display()} {x.name}", f"/it/domains/{x.pk}/") for x in Domain.objects.filter(expires=t)]
            for name, link in items:
                title = f"{name} habis dalam {d} hari"
                notify.send((Role.IT, Role.SUPERADMIN), "it", title, link, skip_if_unread_title=title); n += 1
        self.stdout.write(f"{n} pengingat IT diproses.")
