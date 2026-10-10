from django.core.management.base import BaseCommand
from apps.hrd import services


class Command(BaseCommand):
    help = ("Nonaktifkan karyawan yang hari terakhir kerjanya sudah lewat (catatan Karyawan Keluar yang belum diterapkan). Idempoten; jalankan harian (lihat scripts/cron-hris.example). "
            "Riwayat status tercatat dengan tanggal efektif = tanggal keluar.")

    def handle(self, *a, **k):
        n = services.apply_due_separations(None)
        self.stdout.write(f"{n} karyawan dinonaktifkan.")
