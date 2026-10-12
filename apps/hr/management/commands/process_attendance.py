"""Olah rekap absensi harian dari log mesin (cron harian, mis. 00:30). Bawaan: 3 hari terakhir (menangkap impor yang terlambat). Idempoten.
  python manage.py process_attendance                      # 3 hari terakhir
  python manage.py process_attendance --days 31            # sebulan terakhir
  python manage.py process_attendance --from 2026-10-01 --to 2026-10-31"""
from datetime import date, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from apps.hr import attendance_calc as calc, attendance_import as imp


class Command(BaseCommand):
    help = "Hitung rekap absensi harian dari log mesin + jadwal + pengajuan + validasi + hari libur."

    def add_arguments(self, p):
        p.add_argument("--days", type=int, default=3); p.add_argument("--from", dest="d1"); p.add_argument("--to", dest="d2")

    def handle(self, *a, **o):
        today = timezone.localdate()
        try:
            d2 = date.fromisoformat(o["d2"]) if o["d2"] else today - timedelta(days=1)
            d1 = date.fromisoformat(o["d1"]) if o["d1"] else d2 - timedelta(days=max(o["days"], 1) - 1)
        except ValueError: raise CommandError("Tanggal harus berformat YYYY-MM-DD.")
        r = calc.process(d1, d2); gone = imp.purge_stale()
        self.stdout.write(f"Rekap diolah {d1} s/d {r['end']}: {r['rows']} baris, {r['changed']} berubah, {r['employees']} karyawan. Draft impor kedaluwarsa dibersihkan: {gone}.")
