from datetime import date
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from apps.core.models import AuditLog
from apps.hr import leave
from apps.hr.models import Employee, LeaveLedger


class Command(BaseCommand):
    help = ("Beri jatah cuti tahunan (settings.ANNUAL_LEAVE_DAYS) kepada karyawan aktif yang masa kerjanya >= ANNUAL_LEAVE_MIN_MONTHS pada tanggal --as-of "
            "(bawaan: 1 Januari tahun tersebut). Idempoten: yang sudah punya jatah tahun itu dilewati. Karyawan yang baru berhak di tengah tahun diberi jatah "
            "manual lewat halaman Saldo cuti. Gunakan --dry-run untuk simulasi.")

    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=date.today().year)
        p.add_argument("--as-of", type=date.fromisoformat, default=None, help="YYYY-MM-DD; bawaan 1 Januari --year")
        p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, year, as_of, dry_run, **k):
        if not 2000 <= year <= 2100: raise CommandError("Tahun tidak masuk akal.")
        as_of = as_of or date(year, 1, 1)
        granted = skipped = ineligible = 0
        with transaction.atomic():
            have = set(LeaveLedger.objects.filter(year=year, kind=LeaveLedger.GRANT).values_list("employee_id", flat=True))
            for e in Employee.objects.filter(status="aktif").iterator(chunk_size=500):
                if e.pk in have: skipped += 1; continue
                if not leave.eligible(e, as_of): ineligible += 1; continue
                LeaveLedger.objects.create(employee=e, year=year, kind=LeaveLedger.GRANT, days=settings.ANNUAL_LEAVE_DAYS,
                                           note=f"Jatah tahunan {year} (otomatis, masa kerja dihitung per {as_of:%d-%m-%Y})")
                granted += 1
            if not dry_run and granted:
                AuditLog.objects.create(user=None, module="hr", action="leave_grant_batch", object_type="LeaveLedger",
                                        after={"year": year, "as_of": as_of.isoformat(), "granted": granted, "days": str(settings.ANNUAL_LEAVE_DAYS)})
            if dry_run: transaction.set_rollback(True)
        self.stdout.write(f"{'[SIMULASI] ' if dry_run else ''}Tahun {year}: diberi jatah={granted}, sudah punya={skipped}, belum berhak={ineligible}")
