from django.core.management.base import BaseCommand
from django.db import transaction
from apps.hr.models import Employee
from apps.hrd.models import BpjsMembership, BpjsStatusLog


class Command(BaseCommand):
    help = ("Isi awal status BPJS dari nomor yang sudah ada di data karyawan: nomor terisi & belum ada status → 'aktif' sejak tanggal masuk. "
            "Idempoten (tidak menyentuh status yang sudah dicatat). Gunakan --dry-run untuk simulasi.")

    def add_arguments(self, p): p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, dry_run=False, **k):
        made = {"kes": 0, "tk": 0}
        with transaction.atomic():
            for e in Employee.objects.iterator(chunk_size=500):
                for scheme, number in (("kes", e.bpjs_kes), ("tk", e.bpjs_tk)):
                    if not number or BpjsMembership.objects.filter(employee=e, scheme=scheme).exists(): continue
                    BpjsMembership.objects.create(employee=e, scheme=scheme, status="aktif", effective_date=e.join_date, note="Inisialisasi dari nomor yang sudah ada")
                    BpjsStatusLog.objects.create(employee=e, scheme=scheme, old_status="", new_status="aktif", effective_date=e.join_date, note="Inisialisasi dari nomor yang sudah ada")
                    made[scheme] += 1
            if dry_run: transaction.set_rollback(True)
        self.stdout.write(f"{'[SIMULASI] ' if dry_run else ''}Dibuat: BPJS K={made['kes']}, TK={made['tk']}")
