"""Muat daftar mesin Fingerspot (hasil Export 'Mesin' di Fingerspot Personnel) ke master mesin HRIS. Idempoten (kunci = SN).
  python manage.py import_mesin                          # berkas bawaan repo: apps/hr/seed/mesin_fingerspot.csv (32 mesin)
  python manage.py import_mesin daftar_baru.xls --no-sync-enable   # berkas lain; mesin baru TIDAK langsung ikut tarik otomatis
Mesin yang sudah ada hanya diperbarui nama/IP/No Mesin; kunci komunikasi & status aktif tidak diubah."""
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.hr.attendance_sync import import_machine_list
from apps.hr.models import AttendanceDevice

DEFAULT = Path(__file__).resolve().parents[2] / "seed" / "mesin_fingerspot.csv"


class Command(BaseCommand):
    help = "Impor daftar mesin Fingerspot dari berkas Export Fingerspot Personnel."

    def add_arguments(self, p):
        p.add_argument("file", nargs="?", default=str(DEFAULT))
        p.add_argument("--protocol", default="fk", choices=[v for v, _ in AttendanceDevice.PROTOCOLS]); p.add_argument("--port", type=int, default=5005)
        p.add_argument("--no-sync-enable", action="store_true", help="mesin baru jangan langsung diberi 'Tarik otomatis'")

    def handle(self, *a, **o):
        f = Path(o["file"])
        if not f.is_file(): raise CommandError(f"Berkas tidak ditemukan: {f}")
        before = set(AttendanceDevice.objects.values_list("code", flat=True))
        try: made, upd, warn = import_machine_list(f.read_bytes(), f.name, o["protocol"], o["port"])
        except ValueError as e: raise CommandError(str(e))
        if o["no_sync_enable"]: AttendanceDevice.objects.exclude(code__in=before).update(sync_enabled=False)
        for w in warn: self.stderr.write(f"! {w}")
        self.stdout.write(f"Mesin: {made} baru, {upd} diperbarui, {len(warn)} perlu diperiksa. Total {AttendanceDevice.objects.count()} mesin.")
